# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate any-of (`require_any_permission`): fail-closed, con asiento de denegación.

La fuente única vive en `orpycamcp_common.rbac`. Aquí se prueba la FÁBRICA con un
`get_conn` de juguete (sin servicio), incluida una mutación: si el gate exigiera
TODOS los brazos en vez de UNO, o dejara pasar sin identidad, estos tests caen.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from orpycamcp_common import rbac


def _app(concedidos: set[str]) -> tuple[FastAPI, AsyncMock]:
    conn = AsyncMock()

    async def _fetchval(sql: str, *args: object) -> object:
        return bool(set(args[1]) & concedidos)  # type: ignore[call-overload]

    conn.fetchval.side_effect = _fetchval
    conn.transaction = MagicMock(return_value=AsyncMock())

    async def get_conn() -> AsyncMock:
        return conn

    req = rbac.make_require_any_permission(service="svc-test", get_conn=get_conn)
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(req("PERM_A", "PERM_B"))])
    async def x() -> dict[str, bool]:
        return {"ok": True}

    return app, conn


async def _get(app: FastAPI, headers: dict[str, str]) -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return (await c.get("/x", headers=headers)).status_code


_H = {"X-Tenant-Slug": "demo"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("concedidos", "esperado"),
    [({"PERM_A"}, 200), ({"PERM_B"}, 200), ({"PERM_A", "PERM_B"}, 200), ({"PERM_C"}, 403), (set(), 403)],
)
async def test_basta_uno(concedidos: set[str], esperado: int) -> None:
    app, _ = _app(concedidos)
    assert await _get(app, {**_H, "X-User-Id": str(uuid.uuid4())}) == esperado


@pytest.mark.asyncio
async def test_sin_identidad_o_identidad_rota_es_403() -> None:
    app, conn = _app({"PERM_A", "PERM_B"})
    assert await _get(app, _H) == 403
    assert await _get(app, {**_H, "X-User-Id": "no-es-uuid"}) == 403
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_denegacion_deja_asiento_solo_si_hay_identidad() -> None:
    app, _ = _app(set())
    uid = str(uuid.uuid4())
    with patch("orpycamcp_common.audit.append_denial", new=AsyncMock()) as ap:
        assert await _get(app, {**_H, "X-User-Id": uid}) == 403
        ap.assert_awaited_once()
        kw = ap.await_args.kwargs
        assert kw["perm_name"] == "PERM_A|PERM_B"
        assert kw["actor"] == uid and kw["service"] == "svc-test"
        ap.reset_mock()
        assert await _get(app, _H) == 403  # anónimo: no contamina la cadena
        ap.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallo_de_auditoria_no_da_500() -> None:
    app, _ = _app(set())
    with patch("orpycamcp_common.audit.append_denial", new=AsyncMock(side_effect=RuntimeError)):
        assert await _get(app, {**_H, "X-User-Id": str(uuid.uuid4())}) == 403


def test_sin_permisos_la_fabrica_se_niega() -> None:
    req = rbac.make_require_any_permission(service="s", get_conn=lambda: None)
    with pytest.raises(ValueError):
        req()


@pytest.mark.asyncio
async def test_check_any_permission_fail_closed_sin_tablas() -> None:
    import asyncpg

    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.UndefinedTableError("x")
    assert await rbac.check_any_permission(conn, uuid.uuid4(), ("A",)) is False
    assert await rbac.check_any_permission(conn, None, ("A",)) is False
    assert await rbac.check_any_permission(conn, uuid.uuid4(), ()) is False
