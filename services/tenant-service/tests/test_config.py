# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de configuración por tenant (E14): festivos, días hábiles, parámetros.

Las escrituras (POST/DELETE festivos, seed, PUT params) exigen `USUA_PERM_ADMIN`
(hallazgo Crítico de la auditoría 2026-07: el servicio no tenía NINGÚN gate) y
dejan asiento Canal A (`public.audit_log`); se patchea `app.core.audit._audit.append`
para no ejercer la cadena de hash real. Los GET (festivos, business-days/calculate,
params) quedan solo autenticados — el cálculo de plazos legales los consume para
cualquier usuario radicador, no solo administradores.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app

_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


def _holiday(
    *,
    holiday_id: uuid.UUID | None = None,
    fecha: date = date(2026, 1, 1),
    descripcion: str = "Año Nuevo",
    tipo: str = "festivo",
    is_national: bool = True,
) -> dict:
    return {
        "id": holiday_id or uuid.uuid4(),
        "fecha": fecha,
        "descripcion": descripcion,
        "tipo": tipo,
        "is_national": is_national,
    }


@asynccontextmanager
async def _noop_tx() -> AsyncGenerator[None, None]:
    yield


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # Gate RBAC (USUA_PERM_ADMIN) concedido por defecto.
    conn.fetchval.return_value = True
    conn.execute.return_value = "DELETE 0"
    conn.transaction = MagicMock(side_effect=lambda *a, **k: _noop_tx())

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as audit_append:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ac.audit_append = audit_append  # type: ignore[attr-defined]
            yield ac, conn
    app.dependency_overrides.clear()


# --- lecturas: solo autenticadas, sin gate de rol -----------------------------


@pytest.mark.asyncio
async def test_list_holidays_no_role_gate(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetch.return_value = [_holiday()]
    resp = await client.get("/api/v1/config/holidays")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_calculate_business_days_no_role_gate(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, _ = client_conn
    resp = await client.get(
        "/api/v1/business-days/calculate", params={"from": "2026-01-01", "days": 5}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_params_no_role_gate(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.get("/api/v1/config/params")
    assert resp.status_code == 200


# --- gate RBAC ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_holiday_forbidden_without_permission(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = False
    resp = await client.post(
        "/api/v1/config/holidays",
        json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"},
        headers=_HDRS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_holiday_forbidden_without_identity(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/config/holidays", json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"}
    )
    assert resp.status_code == 403


# --- festivos: create/delete/seed --------------------------------------------


@pytest.mark.asyncio
async def test_create_holiday(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _holiday()
    resp = await client.post(
        "/api/v1/config/holidays",
        json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert client.audit_append.await_args.kwargs["action"] == "config.festivo_creado"
    assert client.audit_append.await_args.kwargs["actor"] == _HDRS["X-User-Id"]


@pytest.mark.asyncio
async def test_create_holiday_duplicate_409(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    resp = await client.post(
        "/api/v1/config/holidays",
        json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_holiday(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 1"
    resp = await client.delete(f"/api/v1/config/holidays/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 204
    assert client.audit_append.await_args.kwargs["action"] == "config.festivo_eliminado"


@pytest.mark.asyncio
async def test_delete_holiday_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 0"
    resp = await client.delete(f"/api/v1/config/holidays/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 404
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_holidays(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "INSERT 0 1"
    resp = await client.post(
        "/api/v1/config/holidays/seed",
        json={"year_from": 2026, "year_to": 2026},
        headers=_HDRS,
    )
    assert resp.status_code == 202
    kw = client.audit_append.await_args.kwargs
    assert kw["action"] == "config.festivos_sembrados"
    assert kw["object_ref"] == "2026-2026" and kw["payload"]["created"] >= 0


@pytest.mark.asyncio
async def test_seed_holidays_invalid_range(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/config/holidays/seed",
        json={"year_from": 2027, "year_to": 2026},
        headers=_HDRS,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_range"


# --- parámetros ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_param(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.put(
        "/api/v1/config/params/dias_respuesta_pqr", json={"value": 15}, headers=_HDRS
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == 15
    assert client.audit_append.await_args.kwargs["action"] == "config.parametro_actualizado"
    assert client.audit_append.await_args.kwargs["object_ref"] == "dias_respuesta_pqr"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "valor",
    ["javascript:alert(1)", "JAVASCRIPT:x", "data:text/html,x", "//evil", "ftp://x.example",
     "https://a b", 5, None],
)
async def test_la_url_de_la_politica_solo_admite_http_s(
    client_conn: tuple[AsyncClient, AsyncMock], valor
) -> None:
    """Se enlaza en la página pública: `javascript:` sería XSS almacenado (B2)."""
    client, _ = client_conn
    resp = await client.put(
        "/api/v1/config/params/politica_tratamiento_url", json={"value": valor}, headers=_HDRS
    )
    assert resp.status_code == 422
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", ["https://entidad.example/privacidad", "http://localhost/p", ""])
async def test_la_url_de_la_politica_admite_https_http_y_vacia(
    client_conn: tuple[AsyncClient, AsyncMock], valor
) -> None:
    client, _ = client_conn
    resp = await client.put(
        "/api/v1/config/params/politica_tratamiento_url", json={"value": valor}, headers=_HDRS
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_set_param_asienta_antes_y_despues(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """AREA-10-14: el asiento lleva el valor vigente ANTES del cambio."""
    client, conn = client_conn
    # RBAC, lectura previa FOR UPDATE (jsonb llega como texto)
    conn.fetchval.side_effect = [True, "6"]
    resp = await client.put("/api/v1/config/params/consec_digits", json={"value": 8}, headers=_HDRS)
    assert resp.status_code == 200
    payload = client.audit_append.await_args.kwargs["payload"]
    assert payload["antes"] == 6
    assert payload["despues"] == 8
    assert payload["existia"] is True
    assert "FOR UPDATE" in conn.fetchval.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_set_param_nuevo_asienta_antes_nulo(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = [True, None]
    resp = await client.put("/api/v1/config/params/otro", json={"value": 1}, headers=_HDRS)
    assert resp.status_code == 200
    payload = client.audit_append.await_args.kwargs["payload"]
    assert payload["antes"] is None and payload["existia"] is False


@pytest.mark.asyncio
async def test_set_param_forbidden_without_permission(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = False
    resp = await client.put(
        "/api/v1/config/params/dias_respuesta_pqr", json={"value": 15}, headers=_HDRS
    )
    assert resp.status_code == 403


# --- política XAdES de la firma, por entidad (AREA-04-12) -----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", ["off", "clasificados", "todos"])
async def test_set_param_firma_xades_admite_su_vocabulario(
    client_conn: tuple[AsyncClient, AsyncMock], valor: str
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = [True, '"off"']
    resp = await client.put(
        "/api/v1/config/params/firma_personal_require_xades", json={"value": valor}, headers=_HDRS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["value"] == valor
    assert client.audit_append.await_args.kwargs["object_ref"] == "firma_personal_require_xades"


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", ["clasificado", "TODOS", "", True, 2, ["todos"]])
async def test_set_param_firma_xades_rechaza_lo_que_signature_no_reconoce(
    client_conn: tuple[AsyncClient, AsyncMock], valor: object
) -> None:
    """signature-service aplica `todos` ante un valor desconocido: aceptarlo aquí
    bloquearía la firma de la entidad entera en vez de avisar a quien lo escribe."""
    client, conn = client_conn
    resp = await client.put(
        "/api/v1/config/params/firma_personal_require_xades", json={"value": valor}, headers=_HDRS
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "valor_invalido"
    # Ni se upserta ni se asienta: la validación va antes de tocar nada.
    assert client.audit_append.await_count == 0
    assert not any("INSERT" in str(c) for c in conn.execute.await_args_list)


# --- escritura de parámetros: nivel y motivo (auditoría 2026-10-08) --------------


@pytest.mark.asyncio
async def test_set_param_exige_nivel_3_no_basta_el_modo_lectura(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un administrador de solo lectura (crud=1) relajaba la política de firma."""
    client, conn = client_conn
    # El RBAC real filtra `gp.crud >= $3`: un admin nivel 1 con $3=3 no pasa.
    conn.fetchval.side_effect = lambda sql, *a: (a[-1] <= 1) if "auth_users" in sql else None
    resp = await client.put(
        "/api/v1/config/params/firma_personal_require_xades",
        json={"value": "off", "motivo": "una justificación suficiente"},
        headers=_HDRS,
    )
    assert resp.status_code == 403
    # Solo queda el asiento de la DENEGACIÓN; ninguno del cambio.
    acciones = [c.kwargs.get("action") for c in client.audit_append.await_args_list]
    assert "config.parametro_actualizado" not in acciones
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["firma_personal_require_xades", "plazo_dias", "otro"])
async def test_set_param_null_es_422_valor_nulo(
    client_conn: tuple[AsyncClient, AsyncMock], key: str
) -> None:
    """JSON null lo escribe la siembra; por la API borraría la decisión de la entidad."""
    client, conn = client_conn
    resp = await client.put(f"/api/v1/config/params/{key}", json={"value": None}, headers=_HDRS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "valor_nulo"
    assert client.audit_append.await_count == 0
    conn.execute.assert_not_awaited()


_ESCRITURAS_FESTIVOS = [
    ("post", "/api/v1/config/holidays", {"fecha": "2026-01-01", "descripcion": "Año Nuevo"}),
    ("delete", f"/api/v1/config/holidays/{uuid.uuid4()}", None),
    ("post", "/api/v1/config/holidays/seed", {"year_from": 2026, "year_to": 2026}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), _ESCRITURAS_FESTIVOS)
async def test_festivos_exigen_nivel_3_no_basta_el_nivel_2(
    client_conn: tuple[AsyncClient, AsyncMock], metodo: str, ruta: str, cuerpo: dict | None
) -> None:
    """El calendario fija los plazos legales de la entidad: lectura/edición no bastan."""
    client, conn = client_conn
    conn.fetchval.side_effect = lambda sql, *a: (a[-1] <= 2) if "auth_users" in sql else None
    kw = {"headers": _HDRS} | ({"json": cuerpo} if cuerpo is not None else {})
    resp = await getattr(client, metodo)(ruta, **kw)
    assert resp.status_code == 403
    acciones = [c.kwargs.get("action") for c in client.audit_append.await_args_list]
    assert not any(a and a.startswith("config.festivo") for a in acciones)
    conn.execute.assert_not_awaited()


def _fetchval_antes(antes: str | None):  # type: ignore[no-untyped-def]
    def _f(sql: str, *a: object) -> object:
        return antes if "sgd_param_admin" in sql else True

    return _f


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("antes", "nuevo"),
    [
        ('"todos"', "off"),
        ('"todos"', "clasificados"),
        ('"clasificados"', "off"),
        (None, "off"),  # sin fila: no se sabe qué regía -> se supone la más estricta
        ("null", "clasificados"),  # JSON null = manda el entorno, que puede ser `todos`
        ('"basura"', "off"),
    ],
)
async def test_relajar_la_politica_exige_motivo(
    client_conn: tuple[AsyncClient, AsyncMock], antes: str | None, nuevo: str
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = _fetchval_antes(antes)
    for cuerpo in ({"value": nuevo}, {"value": nuevo, "motivo": "  corto "}):
        resp = await client.put(
            "/api/v1/config/params/firma_personal_require_xades", json=cuerpo, headers=_HDRS
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "motivo_requerido"
    # Ni se escribe ni se asienta.
    assert client.audit_append.await_count == 0
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_relajar_con_motivo_queda_en_el_asiento(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = _fetchval_antes('"todos"')
    resp = await client.put(
        "/api/v1/config/params/firma_personal_require_xades",
        json={"value": "clasificados", "motivo": "  Aún sin certificados enrolados en sede B  "},
        headers=_HDRS,
    )
    assert resp.status_code == 200, resp.text
    payload = client.audit_append.await_args.kwargs["payload"]
    assert payload["relaja_politica"] is True
    assert payload["motivo"] == "Aún sin certificados enrolados en sede B"
    assert payload["antes"] == "todos" and payload["despues"] == "clasificados"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("antes", "nuevo"),
    [('"off"', "todos"), ('"off"', "clasificados"), ('"clasificados"', "todos"),
     ('"todos"', "todos"), (None, "todos")],
)
async def test_endurecer_la_politica_no_exige_motivo(
    client_conn: tuple[AsyncClient, AsyncMock], antes: str | None, nuevo: str
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = _fetchval_antes(antes)
    resp = await client.put(
        "/api/v1/config/params/firma_personal_require_xades",
        json={"value": nuevo},
        headers=_HDRS,
    )
    assert resp.status_code == 200, resp.text
    payload = client.audit_append.await_args.kwargs["payload"]
    assert payload["relaja_politica"] is False and payload["motivo"] is None


@pytest.mark.asyncio
async def test_otro_parametro_no_exige_motivo(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = _fetchval_antes("6")
    resp = await client.put("/api/v1/config/params/consec_digits", json={"value": 4}, headers=_HDRS)
    assert resp.status_code == 200
    assert client.audit_append.await_args.kwargs["payload"]["relaja_politica"] is False
