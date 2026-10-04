# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`/health/ready` avisa si falta la partición del mes siguiente de `audit_log` (AREA-03-06).

El cron de `scripts/audit_partitions.py` es externo y su fallo no se veía en la
supervisión existente. La sonda es informativa: marca `degraded` pero NO cambia el
código HTTP (el servicio puede atender tráfico), y si no puede medir, no acusa.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core import audit_partitions as ap


def test_el_mes_siguiente_salta_de_diciembre_a_enero() -> None:
    assert ap.particion_del_mes_siguiente(datetime(2026, 12, 15, tzinfo=UTC)) == "audit_log_2027_01"
    assert ap.particion_del_mes_siguiente(datetime(2026, 10, 3, tzinfo=UTC)) == "audit_log_2026_11"


@pytest.mark.asyncio
async def test_estado_segun_exista_o_no_la_particion() -> None:
    conn = AsyncMock()
    ahora = datetime(2026, 10, 3, tzinfo=UTC)
    conn.fetchval.return_value = True
    assert await ap.estado_particion_siguiente(conn, ahora) == "ok"
    assert conn.fetchval.await_args.args[1] == "audit_log_2026_11"
    conn.fetchval.return_value = False
    assert await ap.estado_particion_siguiente(conn, ahora) == "degraded"


@pytest.mark.asyncio
async def test_si_la_consulta_falla_no_se_acusa() -> None:
    conn = AsyncMock()
    conn.fetchval.side_effect = RuntimeError("conexión caída")
    assert await ap.estado_particion_siguiente(conn) == "unknown"


@pytest.mark.asyncio
async def test_ready_expone_degraded_sin_tumbar_la_disponibilidad(client: AsyncClient) -> None:
    with (
        patch("app.routers.health._ping_db", new_callable=AsyncMock),
        patch("app.routers.health._audit_particiones", new_callable=AsyncMock, return_value="degraded"),
        patch("app.services.auth_service.AuthService.keycloak_healthy", new_callable=AsyncMock, return_value=True),
    ):
        r = await client.get("/health/ready")
    assert r.status_code == 200, "una partición ausente es una alerta de operación, no un 503"
    cuerpo = r.json()
    assert cuerpo["status"] == "ready"
    assert cuerpo["audit_particiones"] == "degraded"
    assert cuerpo["degraded"] == ["audit_particiones"]


@pytest.mark.asyncio
async def test_ready_con_particion_al_dia_no_marca_degraded(client: AsyncClient) -> None:
    with (
        patch("app.routers.health._ping_db", new_callable=AsyncMock),
        patch("app.routers.health._audit_particiones", new_callable=AsyncMock, return_value="ok"),
        patch("app.services.auth_service.AuthService.keycloak_healthy", new_callable=AsyncMock, return_value=True),
    ):
        r = await client.get("/health/ready")
    assert r.status_code == 200 and "degraded" not in r.json()
    assert r.json()["audit_particiones"] == "ok"


@pytest.mark.asyncio
async def test_ready_sin_base_no_consulta_las_particiones(client: AsyncClient) -> None:
    with (
        patch("app.routers.health._ping_db", new_callable=AsyncMock, side_effect=RuntimeError("down")),
        patch("app.routers.health._audit_particiones", new_callable=AsyncMock) as sonda,
        patch("app.services.auth_service.AuthService.keycloak_healthy", new_callable=AsyncMock, return_value=True),
    ):
        r = await client.get("/health/ready")
    assert r.status_code == 503
    sonda.assert_not_awaited()
