# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El carril de integración corre contra la base de la pila de desarrollo.

Dos pruebas aplicaban TODO `migrations/global/` en cada corrida para asegurarse
de que existiera `public.audit_log`. La 007 hace `ALTER ROLE orpycamcp_app
NOLOGIN` sin condición, así que cada corrida dejaba a los servicios sin poder
conectar en cuanto se recreaban: medido dos veces el 2026-10-02, y cada vez la
pila caída parecía un defecto del último despliegue.

Esta prueba fija que preparar `audit_log` sobre una base ya migrada no cambia
ningún atributo de los roles. Compara antes y después, así que vale igual en la
pila (rol con LOGIN) que en un CI virgen (rol sin LOGIN).
"""

from __future__ import annotations

import asyncpg
import pytest

from tests.integration.conftest import RUN_INTEGRATION, ensure_audit_log

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]

_ROLES = "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'orpycamcp%' ORDER BY 1"


async def test_preparar_audit_log_no_cambia_los_roles(db_conn: asyncpg.Connection) -> None:
    await ensure_audit_log(db_conn)  # la primera vez puede crear la tabla en un CI virgen
    antes = [tuple(r) for r in await db_conn.fetch(_ROLES)]

    await ensure_audit_log(db_conn)
    await ensure_audit_log(db_conn)

    assert [tuple(r) for r in await db_conn.fetch(_ROLES)] == antes
    # Control positivo: la consulta mira algo (si no hubiera roles, la igualdad
    # entre dos listas vacías aprobaría cualquier cosa).
    assert antes, "no hay roles orpycamcp* en la base: la prueba no vigilaría nada"
