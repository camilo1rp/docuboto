# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""``/api/v1/config/intelligence`` (ADR-045 §4).

Pasar a ``ppi`` exige bandera, política y una declaración que nombra al
proveedor. Habilitar RESERVADA o CLASIFICADA es otro acto. Deshabilitar y
retirar piden el borrado a la pasarela, que es quien llama al proveedor.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.pasarela import PasarelaError
from app.core.database import get_tenant_conn
from app.main import app

URL = "/api/v1/config/intelligence"
SHA = "ab" * 32
VISTA: dict[str, Any] = {
    "sha256": SHA,
    "provider": {"id": "example-provider", "name": "Example Provider"},
    "subprocessors": [{"id": "model-host-a", "name": "Example Model Host"}],
    "routing": [
        {
            "level": "public",
            "hosting": "hosted",
            "model_aliases": ["triage-small"],
            "subprocessors": ["model-host-a"],
            "prompt_and_output_retention": "none",
            "trains_on_customer_data": False,
        },
        {
            "level": "reserved",
            "hosting": "installation",
            "model_aliases": ["drafting-large"],
            "subprocessors": [],
            "prompt_and_output_retention": "none",
            "trains_on_customer_data": False,
        },
    ],
}
TEXTO = "Example Provider example-provider Example Model Host model-host-a"
_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


class _Estado:
    def __init__(self) -> None:
        self.nivel = 3
        self.ausente = False
        self.politica: str | None = '"v1"'
        self.suspendido = False
        self.en_tx = False
        self.fila: dict[str, Any] = {
            "asistente_habilitado": True,
            "intelligence_provider": "local",
            "intelligence_levels": [1],
            "intelligence_send_metadata": False,
        }
        self.updates: list[tuple[Any, ...]] = []


def _conn(est: _Estado) -> AsyncMock:
    conn = AsyncMock()

    async def fetchval(sql: str, *a: object) -> object:
        if "auth_users" in sql:
            return est.nivel >= a[-1]
        if "sgd_param_admin" in sql:
            return est.politica
        if "intelligence_state" in sql:
            return est.suspendido
        return None

    async def fetchrow(sql: str, *_a: object) -> dict[str, Any] | None:
        if "public.tenants" not in sql:
            return None
        if "FOR UPDATE" in sql:
            assert est.en_tx, "el bloqueo va dentro de la transacción"
        if est.ausente:
            return None
        return dict(est.fila)

    async def execute(sql: str, *a: object) -> str:
        assert est.en_tx, "el UPDATE va dentro de la transacción"
        if "UPDATE public.tenants" in sql:
            est.updates.append(a)
            est.fila["intelligence_provider"] = a[1]
            est.fila["intelligence_levels"] = list(a[2])  # type: ignore[arg-type]
            est.fila["intelligence_send_metadata"] = a[3]
        return "UPDATE 1"

    @asynccontextmanager
    async def tx() -> AsyncGenerator[None, None]:
        previo = copy.deepcopy(est.fila)
        est.en_tx = True
        try:
            yield
        except BaseException:
            est.fila = previo
            raise
        finally:
            est.en_tx = False

    conn.fetchval.side_effect = fetchval
    conn.fetchrow.side_effect = fetchrow
    conn.execute.side_effect = execute
    conn.transaction = lambda *a, **k: tx()
    return conn


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[tuple[Any, ...], None]:
    est = _Estado()
    conn = _conn(est)
    capacidades = AsyncMock(return_value=VISTA)
    aceptar = AsyncMock()
    purgar = AsyncMock(return_value={"accepted": True, "purge_ref": "p-1"})

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with (
        patch("app.clients.pasarela.capacidades", new=capacidades),
        patch("app.clients.pasarela.aceptar", new=aceptar),
        patch("app.clients.pasarela.purgar", new=purgar),
        patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as append,
    ):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.audit_append = append  # type: ignore[attr-defined]
            ac.capacidades = capacidades  # type: ignore[attr-defined]
            ac.aceptar = aceptar  # type: ignore[attr-defined]
            ac.purgar = purgar  # type: ignore[attr-defined]
            yield ac, est, capacidades, aceptar, purgar
    app.dependency_overrides.clear()


def _acciones(client: AsyncClient) -> list[str]:
    llamadas = client.audit_append.await_args_list  # type: ignore[attr-defined]
    return [c.kwargs.get("action") for c in llamadas]


@pytest.mark.asyncio
async def test_get_devuelve_local_y_exige_nivel_3(ctx) -> None:
    client, est, *_ = ctx
    resp = await client.get(URL, headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["provider"] == "local"
    assert resp.json()["levels"] == [1]
    assert resp.json()["send_metadata"] is False
    est.nivel = 1
    assert (await client.get(URL, headers=_HDRS)).status_code == 403


@pytest.mark.asyncio
async def test_pasar_a_ppi_sin_declaracion_no_escribe(ctx) -> None:
    client, est, capacidades, *_ = ctx
    resp = await client.put(URL, json={"provider": "ppi"}, headers=_HDRS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "declaracion_requerida"
    assert est.updates == []
    assert capacidades.await_count == 0
    assert "tenant.intelligence_changed" not in _acciones(client)


@pytest.mark.asyncio
async def test_pasar_a_ppi_exige_bandera_y_politica(ctx) -> None:
    client, est, *_ = ctx
    est.fila["asistente_habilitado"] = False
    cuerpo = {"provider": "ppi", "transmission_statement": TEXTO}
    resp = await client.put(URL, json=cuerpo, headers=_HDRS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "bandera_apagada"
    est.fila["asistente_habilitado"] = True
    est.politica = None
    resp = await client.put(URL, json=cuerpo, headers=_HDRS)
    assert resp.json()["detail"]["error"] == "politica_no_publicada"
    assert est.updates == []


@pytest.mark.asyncio
async def test_la_declaracion_tiene_que_nombrar_al_proveedor(ctx) -> None:
    client, est, *_ = ctx
    resp = await client.put(
        URL,
        json={"provider": "ppi", "transmission_statement": "Example Provider"},
        headers=_HDRS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "declaracion_incompleta"
    assert "example-provider" in resp.json()["detail"]["faltan"]
    assert est.updates == []


@pytest.mark.asyncio
async def test_pasar_a_ppi_habilita_solo_publica_y_asienta(ctx) -> None:
    client, est, capacidades, aceptar, _purgar = ctx
    est.fila["intelligence_levels"] = [1, 2]
    resp = await client.put(
        URL,
        json={"provider": "ppi", "transmission_statement": TEXTO, "send_metadata": True},
        headers=_HDRS,
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["provider"] == "ppi" and cuerpo["levels"] == [1]
    assert cuerpo["send_metadata"] is True and cuerpo["changed"] is True
    assert est.updates == [("demo", "ppi", [1], True)]
    assert capacidades.await_args.args == ("demo",)
    assert aceptar.await_args.args == ("demo", 1, SHA)
    assert _acciones(client) == [
        "tenant.intelligence_changed",
        "tenant.intelligence_metadata_changed",
    ]
    asiento = client.audit_append.await_args_list[0].kwargs["payload"]  # type: ignore[attr-defined]
    assert asiento["capabilities_sha256"] == SHA
    assert asiento["politica_tratamiento_version"] == "v1"
    assert asiento["transmission_statement"] == TEXTO
    assert asiento["levels_antes"] == [1, 2] and asiento["levels_despues"] == [1]


@pytest.mark.asyncio
async def test_una_ruta_que_entrena_no_deja_pasar(ctx) -> None:
    client, est, capacidades, *_ = ctx
    mala = copy.deepcopy(VISTA)
    mala["routing"][0]["trains_on_customer_data"] = True
    capacidades.return_value = mala
    resp = await client.put(
        URL, json={"provider": "ppi", "transmission_statement": TEXTO}, headers=_HDRS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "ruta_no_admitida"
    assert est.updates == []


@pytest.mark.asyncio
async def test_volver_a_local_no_llama_al_proveedor(ctx) -> None:
    client, est, capacidades, aceptar, purgar = ctx
    est.fila["intelligence_provider"] = "ppi"
    est.fila["intelligence_levels"] = [1, 2]
    resp = await client.put(URL, json={"provider": "local"}, headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["provider"] == "local"
    assert resp.json()["levels"] == [1, 2]
    assert capacidades.await_count == 0
    assert aceptar.await_count == 0
    assert purgar.await_count == 0
    assert _acciones(client) == ["tenant.intelligence_changed"]


@pytest.mark.asyncio
async def test_el_mismo_valor_no_es_un_acto(ctx) -> None:
    client, est, capacidades, *_ = ctx
    resp = await client.put(URL, json={"provider": "local"}, headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["changed"] is False
    assert est.updates == []
    assert capacidades.await_count == 0
    assert _acciones(client) == []


@pytest.mark.asyncio
async def test_si_la_pasarela_no_registra_el_cambio_de_columna_sigue(ctx) -> None:
    client, est, _capacidades, aceptar, _purgar = ctx
    aceptar.side_effect = PasarelaError(503, "pasarela_no_disponible")
    resp = await client.put(
        URL, json={"provider": "ppi", "transmission_statement": TEXTO}, headers=_HDRS
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "declaracion_no_registrada"
    assert est.fila["intelligence_provider"] == "ppi"


@pytest.mark.asyncio
async def test_habilitar_reservada_nombra_la_ruta_de_ese_nivel(ctx) -> None:
    client, est, _capacidades, aceptar, purgar = ctx
    est.fila["intelligence_provider"] = "ppi"
    resp = await client.put(
        f"{URL}/levels",
        json={"level": 2, "enabled": True, "statement": "installation drafting-large"},
        headers=_HDRS,
    )
    assert resp.status_code == 200
    assert resp.json()["levels"] == [1, 2]
    assert aceptar.await_args.args == ("demo", 2, SHA)
    assert purgar.await_count == 0
    asiento = client.audit_append.await_args.kwargs["payload"]  # type: ignore[attr-defined]
    assert asiento["level"] == 2 and asiento["despues"] is True


@pytest.mark.asyncio
async def test_habilitar_un_nivel_en_local_no_escribe(ctx) -> None:
    client, est, *_ = ctx
    resp = await client.put(
        f"{URL}/levels",
        json={"level": 2, "enabled": True, "statement": "installation drafting-large"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proveedor_local"
    assert est.updates == []


@pytest.mark.asyncio
async def test_deshabilitar_ordena_el_borrado_de_ese_nivel(ctx) -> None:
    client, est, capacidades, aceptar, purgar = ctx
    est.fila["intelligence_provider"] = "ppi"
    est.fila["intelligence_levels"] = [1, 2]
    resp = await client.put(
        f"{URL}/levels", json={"level": 2, "enabled": False}, headers=_HDRS
    )
    assert resp.status_code == 200
    assert resp.json()["levels"] == [1]
    assert resp.json()["purge"] == "accepted"
    assert capacidades.await_count == 0
    assert aceptar.await_count == 0
    assert purgar.await_args.kwargs["levels"] == [2]
    assert purgar.await_args.kwargs["reason"] == "host_request"
    purgar.return_value = {"accepted": False}
    est.fila["intelligence_levels"] = [1, 2]
    resp = await client.put(
        f"{URL}/levels", json={"level": 3, "enabled": False}, headers=_HDRS
    )
    # 3 no estaba habilitado: no es un acto.
    assert resp.json()["changed"] is False
    assert purgar.await_count == 1


@pytest.mark.asyncio
async def test_retirar_asienta_solo_si_el_proveedor_acepta(ctx) -> None:
    client, est, *_resto = ctx
    _capacidades, _aceptar, purgar = _resto
    resp = await client.post(f"{URL}/withdraw", headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["purge_ref"] == "p-1"
    assert purgar.await_args.kwargs["levels"] is None
    assert purgar.await_args.kwargs["reason"] == "withdrawn"
    assert _acciones(client) == ["tenant.intelligence_withdrawn"]
    assert est.updates == []
    purgar.side_effect = PasarelaError(503, "pasarela_no_disponible")
    resp = await client.post(f"{URL}/withdraw", headers=_HDRS)
    assert resp.status_code == 502
    assert _acciones(client) == ["tenant.intelligence_withdrawn"]


@pytest.mark.asyncio
async def test_un_slug_en_el_cuerpo_no_cuenta(ctx) -> None:
    client, est, *_ = ctx
    resp = await client.put(
        URL,
        json={"provider": "ppi", "transmission_statement": TEXTO, "slug": "otra"},
        headers=_HDRS,
    )
    assert resp.status_code == 200
    assert est.updates[0][0] == "demo"
    asiento = client.audit_append.await_args_list[0]  # type: ignore[attr-defined]
    assert asiento.kwargs["tenant_slug"] == "demo"
