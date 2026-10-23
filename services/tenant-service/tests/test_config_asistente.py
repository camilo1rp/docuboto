# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""``GET/PUT /api/v1/config/asistente`` (ADR-037): la bandera la cambia la ENTIDAD.

El PUT exige ``USUA_PERM_ADMIN`` nivel 3, toca SOLO la fila del slug del request,
asienta el cambio (Canal A) en la misma transacción y no hace nada —ni asienta—
si el valor pedido ya era el vigente.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app

URL = "/api/v1/config/asistente"
ENCENDER = {"habilitado": True, "declaracion_transmision": True}
_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


class _Estado:
    """Fila simulada de ``public.tenants`` + el RBAC del llamante, con la SQL a la vista."""

    def __init__(self, *, habilitado: bool | None, nivel: int) -> None:
        self.habilitado = habilitado
        self.nivel = nivel
        self.updates: list[tuple[str, bool]] = []
        self.en_tx = False
        self.commit = True
        #: ``politica_tratamiento_version`` en ``sgd_param_admin`` (JSON); ``None`` = sin fila.
        self.politica: str | None = '"v1.0"'


def _conn(est: _Estado) -> AsyncMock:
    conn = AsyncMock()

    async def fetchval(sql: str, *a: object) -> object:
        if "auth_users" in sql:  # require_permission(USUA_PERM_ADMIN, min_crud)
            return est.nivel >= a[-1]
        if "FOR UPDATE" in sql:
            assert est.en_tx, "la lectura con bloqueo debe ir dentro de la transacción"
            return est.habilitado
        if "asistente_habilitado" in sql:
            return est.habilitado
        if "sgd_param_admin" in sql:
            assert a == ("politica_tratamiento_version",)
            return est.politica
        return None

    async def execute(sql: str, *a: object) -> str:
        assert est.en_tx, "el UPDATE debe ir dentro de la transacción"
        if "UPDATE public.tenants" in sql:
            slug, valor = a
            est.updates.append((slug, valor))
            est.habilitado = valor
        return "UPDATE 1"

    @asynccontextmanager
    async def tx() -> AsyncGenerator[None, None]:
        estado_previo = est.habilitado
        est.en_tx = True
        try:
            yield
        except BaseException:
            est.habilitado = estado_previo  # rollback simulado
            raise
        finally:
            est.en_tx = False

    conn.fetchval.side_effect = fetchval
    conn.execute.side_effect = execute
    conn.transaction = MagicMock(side_effect=lambda *a, **k: tx())
    return conn


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[tuple[AsyncClient, _Estado, AsyncMock], None]:
    est = _Estado(habilitado=False, nivel=3)
    conn = _conn(est)

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as append:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.audit_append = append  # type: ignore[attr-defined]
            yield ac, est, conn
    app.dependency_overrides.clear()


def _acciones(client: AsyncClient) -> list[str]:
    return [c.kwargs.get("action") for c in client.audit_append.await_args_list]  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("nivel", [0, 1, 2])
async def test_put_exige_admin_nivel_3(ctx, nivel: int) -> None:
    client, est, _ = ctx
    est.nivel = nivel
    resp = await client.put(URL, json=ENCENDER, headers=_HDRS)
    assert resp.status_code == 403
    assert est.habilitado is False and est.updates == []
    # El asiento de la DENEGACIÓN (best-effort) sí puede existir; el del cambio, no.
    assert "tenant.asistente_cambiado" not in _acciones(client)


@pytest.mark.asyncio
async def test_put_con_nivel_3_enciende_y_asienta(ctx) -> None:
    """Control positivo de la fila anterior."""
    client, est, _ = ctx
    resp = await client.put(URL, json=ENCENDER, headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["habilitado"] is True and resp.json()["cambiado"] is True
    assert est.updates == [("demo", True)]
    kw = client.audit_append.await_args.kwargs
    assert kw["action"] == "tenant.asistente_cambiado"
    assert kw["object_ref"] == "demo"
    assert kw["payload"]["antes"] is False and kw["payload"]["despues"] is True
    assert kw["actor"] == _HDRS["X-User-Id"]


@pytest.mark.asyncio
async def test_put_sin_identidad_no_pasa(ctx) -> None:
    client, est, _ = ctx
    resp = await client.put(URL, json={"habilitado": True}, headers={"X-Tenant-Slug": "demo"})
    assert resp.status_code == 403
    assert est.updates == []


@pytest.mark.asyncio
async def test_solo_toca_la_fila_del_slug_del_request(ctx) -> None:
    """Un slug en el cuerpo no cuenta: el UPDATE lleva el de la cabecera (y solo ese)."""
    client, est, _ = ctx
    resp = await client.put(URL, json={**ENCENDER, "slug": "otra-entidad"}, headers=_HDRS)
    assert resp.status_code == 200
    assert est.updates == [("demo", True)]
    assert client.audit_append.await_args.kwargs["tenant_slug"] == "demo"


@pytest.mark.asyncio
async def test_mismo_valor_no_es_un_acto(ctx) -> None:
    client, est, _ = ctx
    est.habilitado = True
    resp = await client.put(URL, json=ENCENDER, headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["habilitado"] is True and resp.json()["cambiado"] is False
    assert est.updates == []
    assert "tenant.asistente_cambiado" not in _acciones(client)


@pytest.mark.asyncio
async def test_apagar_tambien_asienta(ctx) -> None:
    client, est, _ = ctx
    est.habilitado = True
    resp = await client.put(URL, json={"habilitado": False}, headers=_HDRS)
    assert resp.json()["habilitado"] is False and resp.json()["cambiado"] is True
    assert client.audit_append.await_args.kwargs["payload"] == {
        "antes": True, "despues": False, "motivo": None,
    }


@pytest.mark.asyncio
async def test_si_el_asiento_falla_el_valor_no_cambia(ctx) -> None:
    """Atomicidad: el UPDATE y el asiento son una sola transacción."""
    client, est, _ = ctx
    client.audit_append.side_effect = RuntimeError("cadena de auditoría caída")
    resp = await client.put(URL, json=ENCENDER, headers=_HDRS)
    assert resp.status_code == 500
    assert est.habilitado is False


@pytest.mark.asyncio
async def test_entidad_inexistente_404(ctx) -> None:
    client, est, _ = ctx
    est.habilitado = None
    resp = await client.put(URL, json=ENCENDER, headers=_HDRS)
    assert resp.status_code == 404
    assert est.updates == []


@pytest.mark.asyncio
async def test_get_lo_lee_cualquier_autenticado(ctx) -> None:
    client, est, _ = ctx
    est.nivel = 0
    est.habilitado = True
    resp = await client.get(URL, headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["habilitado"] is True
    est.habilitado = False
    assert (await client.get(URL, headers=_HDRS)).json()["habilitado"] is False


@pytest.mark.asyncio
async def test_cuerpo_invalido_422(ctx) -> None:
    client, est, _ = ctx
    resp = await client.put(URL, json={"habilitado": "quizá"}, headers=_HDRS)
    assert resp.status_code == 422
    assert est.updates == []


# --- A1/A2: proveedores, política publicada y declaración de transmisión ----------------------


@pytest.mark.asyncio
async def test_get_trae_los_proveedores_efectivos(ctx, monkeypatch) -> None:
    from app.core.config import settings

    client, _, _ = ctx
    monkeypatch.setattr(settings, "embedding_provider", "ollama")
    monkeypatch.setattr(settings, "ai_provider", "ollama_cloud")
    body = (await client.get(URL, headers=_HDRS)).json()
    assert body["proveedores"] == {
        "asistente": "anthropic",
        "modelo": settings.assistant_model,
        "rag": "ollama_cloud",
        "embeddings": "ollama",
        "embeddings_local": False,
    }
    monkeypatch.setattr(settings, "embedding_provider", "stub")
    assert (await client.get(URL, headers=_HDRS)).json()["proveedores"]["embeddings_local"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("cuerpo", [{"habilitado": True}, {"habilitado": True, "declaracion_transmision": False}])
async def test_encender_sin_declaracion_422(ctx, cuerpo) -> None:
    client, est, _ = ctx
    resp = await client.put(URL, json=cuerpo, headers=_HDRS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "declaracion_requerida"
    assert est.habilitado is False and est.updates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("politica", [None, "null", '""', '"   "'])
async def test_encender_sin_politica_publicada_409(ctx, politica) -> None:
    client, est, _ = ctx
    est.politica = politica
    resp = await client.put(URL, json=ENCENDER, headers=_HDRS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "politica_no_publicada"
    assert est.habilitado is False and est.updates == []


@pytest.mark.asyncio
async def test_el_asiento_de_encender_lleva_politica_proveedores_declaracion_y_motivo(ctx) -> None:
    client, est, _ = ctx
    resp = await client.put(URL, json={**ENCENDER, "motivo": "  Piloto de búsqueda  "}, headers=_HDRS)
    assert resp.status_code == 200
    p = client.audit_append.await_args.kwargs["payload"]
    assert p["politica_tratamiento_version"] == "v1.0"
    assert p["declaracion_transmision"] is True
    assert p["motivo"] == "Piloto de búsqueda"
    assert p["proveedores"]["asistente"] == "anthropic" and "embeddings_local" in p["proveedores"]


@pytest.mark.asyncio
async def test_apagar_no_pide_declaracion_ni_politica(ctx) -> None:
    client, est, _ = ctx
    est.habilitado = True
    est.politica = None
    resp = await client.put(URL, json={"habilitado": False}, headers=_HDRS)
    assert resp.status_code == 200 and resp.json()["cambiado"] is True
    assert est.habilitado is False


@pytest.mark.asyncio
async def test_pedir_el_valor_vigente_no_exige_nada(ctx) -> None:
    client, est, _ = ctx
    est.habilitado = True
    est.politica = None
    resp = await client.put(URL, json={"habilitado": True}, headers=_HDRS)
    assert resp.status_code == 200 and resp.json()["cambiado"] is False
