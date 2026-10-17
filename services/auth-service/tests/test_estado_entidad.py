# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`get_tenant_conn` no atiende a una entidad no operativa (B9-1, ADR-035).

Sin esto, suspender una entidad no le retiraba el acceso en este servicio (solo
document-service lo hacía) y un schema inexistente acababa en `UndefinedTableError`
(500). El gate va ANTES del `SET search_path`: una entidad rechazada no llega a tocar
su schema. Con un doble de conexión que devuelve la fila de `public.tenants`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import database as db


class _Conn:
    def __init__(self, fila: dict | None, error: Exception | None = None) -> None:
        self.fila = fila
        self.error = error
        self.ejecutados: list[str] = []

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        if self.error is not None:
            raise self.error
        return self.fila

    async def execute(self, sql: str, *args: object) -> None:
        self.ejecutados.append(sql)


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _CM()


def _request(pool: _Pool) -> SimpleNamespace:
    return SimpleNamespace(
        headers={"X-Tenant-Slug": "demo"},
        app=SimpleNamespace(state=SimpleNamespace(db_pool=pool)),
    )


def _fila(status: str = "active", prov: str = "active", schema: bool = True) -> dict:
    return {"status": status, "provisioning_status": prov, "hay_schema": schema}


async def _abrir(conn: _Conn, monkeypatch: pytest.MonkeyPatch):
    pool = _Pool(conn)
    # notification-service toma el pool del proceso, no de `request.app.state`.
    monkeypatch.setattr(db, "_pool", pool, raising=False)
    gen = db.get_tenant_conn(_request(pool))  # type: ignore[arg-type]
    return gen, await gen.__anext__()


@pytest.mark.parametrize(
    ("fila", "http", "error"),
    [
        (_fila(status="suspended"), 403, "tenant_inactivo"),
        (_fila(status="inactive"), 403, "tenant_inactivo"),
        (None, 403, "tenant_inactivo"),
        (_fila(prov="pending"), 503, "tenant_no_provisionado"),
        (_fila(schema=False), 503, "tenant_no_provisionado"),
    ],
)
async def test_una_entidad_no_operativa_se_rechaza_sin_tocar_su_schema(
    fila: dict | None, http: int, error: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _Conn(fila)

    with pytest.raises(HTTPException) as exc:
        await _abrir(conn, monkeypatch)

    assert exc.value.status_code == http
    assert exc.value.detail["error"] == error
    assert exc.value.detail["status"] == http
    assert conn.ejecutados == [], "el SET search_path no debe ejecutarse"


async def test_si_el_registro_no_responde_se_niega_503(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn(None, error=RuntimeError("registro caído"))

    with pytest.raises(HTTPException) as exc:
        await _abrir(conn, monkeypatch)

    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "tenant_estado_no_disponible"
    assert conn.ejecutados == []


async def test_una_entidad_operativa_recibe_su_conexion(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn(_fila())

    gen, entregada = await _abrir(conn, monkeypatch)

    assert entregada is conn
    assert conn.ejecutados == ['SET search_path = "tenant_demo", public']
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert conn.ejecutados[-1] == "SET search_path = public"


async def test_validate_dice_desconocido_si_el_registro_cae_o_no_hay_conexion() -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.auth_service import AuthService

    claims = {
        "user_id": "kc-x",
        "username": "u",
        "email": "u@e.co",
        "tenant_slug": "demo",
        "roles": [],
        "exp": 4102444800,
    }
    caida = _Conn(None, error=RuntimeError("registro caído"))
    with patch("app.services.auth_service.validate_token", new=AsyncMock(return_value=claims)):
        con_error = await AuthService().validate("tok", caida)  # type: ignore[arg-type]
        sin_conn = await AuthService().validate("tok", None)

    assert con_error.tenant_estado == sin_conn.tenant_estado == "desconocido"
    assert caida.ejecutados == []


async def test_validate_sin_tenant_no_emite_estado() -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.auth_service import AuthService

    claims = {"user_id": "kc-x", "username": "u", "roles": [], "exp": 4102444800}
    with patch("app.services.auth_service.validate_token", new=AsyncMock(return_value=claims)):
        resp = await AuthService().validate("tok", None)

    assert resp.tenant_estado is None
