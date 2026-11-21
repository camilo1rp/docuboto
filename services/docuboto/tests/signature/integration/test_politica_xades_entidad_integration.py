# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""La política XAdES por entidad contra Postgres REAL (AREA-04-12).

`leer_param` es SQL sobre `sgd_param_admin` (`120_sgd_param_admin.sql` de la cadena
única, que siembra la clave en `"clasificados"`, ADR-054): lo que un doble de conexión no ve es el
`jsonb` real (`'"clasificados"'::jsonb` llega como TEXTO JSON), la tabla inexistente,
y que una clave ausente da `None`.
"""

from __future__ import annotations

import asyncpg
import pytest
from orpycamcp_common.database import tenant_schema

from docuboto.config import signature as settings
from signature.core import params_entidad

pytestmark = pytest.mark.asyncio


async def _en_tenant(tenant_ctx) -> asyncpg.Connection:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return conn


async def test_la_clave_nace_en_clasificados_gana_al_entorno_y_cada_valor_se_aplica(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await _en_tenant(tenant_ctx)
    # ADR-054 §1: la entidad nace en "clasificados" — ni sin decidir ni en "off".
    assert await conn.fetchval(
        "SELECT value #>> '{}' FROM sgd_param_admin WHERE key = 'firma_personal_require_xades'"
    ) == "clasificados", "la cadena tiene que sembrar firma_personal_require_xades en clasificados"
    # El valor de la entidad gana sobre el entorno, aunque el entorno sea más laxo.
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "clasificados"

    for valor in ("clasificados", "todos", "off"):
        await conn.execute(
            "UPDATE sgd_param_admin SET value = to_jsonb($1::text) WHERE key = $2",
            valor, "firma_personal_require_xades",
        )
        assert await params_entidad.modo_xades_de_la_entidad(conn) == valor

    # JSON null («la entidad no lo define»): manda el entorno.
    await conn.execute(
        "UPDATE sgd_param_admin SET value = 'null'::jsonb WHERE key = 'firma_personal_require_xades'"
    )
    monkeypatch.setattr(settings, "firma_personal_require_xades", "todos")
    assert await params_entidad.leer_param(conn, "firma_personal_require_xades") is None
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "todos"


async def test_sin_fila_manda_el_entorno_y_un_valor_corrupto_es_todos(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await _en_tenant(tenant_ctx)
    await conn.execute("DELETE FROM sgd_param_admin WHERE key = 'firma_personal_require_xades'")
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    assert await params_entidad.leer_param(conn, "firma_personal_require_xades") is None
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "clasificados"

    await conn.execute(
        "INSERT INTO sgd_param_admin (key, value) VALUES "
        "('firma_personal_require_xades', '\"clasificado\"'::jsonb)"
    )
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "todos"

    await conn.execute("UPDATE sgd_param_admin SET value = 'null'::jsonb")
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "clasificados"


async def test_sin_tabla_de_parametros_respalda_el_entorno_sin_romper_la_conexion(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await _en_tenant(tenant_ctx)
    await conn.execute("DROP TABLE sgd_param_admin")
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "off"
    # La conexión sigue sana (fuera de transacción el error no la envenena).
    assert await conn.fetchval("SELECT 1") == 1
