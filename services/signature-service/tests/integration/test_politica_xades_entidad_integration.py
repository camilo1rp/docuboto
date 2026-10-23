# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""La política XAdES por entidad contra Postgres REAL (AREA-04-12).

`leer_param` es SQL sobre `sgd_param_admin` (tabla que crea tenant-service, no
signature-service): lo que un doble de conexión no ve es el `jsonb` real
(`'"clasificados"'::jsonb` llega como TEXTO JSON), la tabla inexistente de un
tenant que aún no migró tenant-service, y que una clave ausente da `None`.
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core import params_entidad
from app.core.config import settings
from app.core.database import _schema as tenant_schema

pytestmark = pytest.mark.asyncio

# Mismo DDL que tenant-service 002_params.sql (solo lo que lee signature-service).
_DDL = """
CREATE TABLE sgd_param_admin (
    key        VARCHAR(100) NOT NULL PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID
)
"""


async def _en_tenant(tenant_ctx) -> asyncpg.Connection:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return conn


async def test_la_clave_sembrada_en_null_deja_mandar_al_entorno_y_cada_valor_se_aplica(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await _en_tenant(tenant_ctx)
    await conn.execute(_DDL)
    # El INSERT de tenant-service 010_params_firma.sql: JSON null = «la entidad no
    # lo define». Sembrar "off" anulaba el respaldo del entorno en silencio.
    await conn.execute(
        "INSERT INTO sgd_param_admin (key, value) VALUES "
        "('firma_personal_require_xades', 'null'::jsonb) ON CONFLICT (key) DO NOTHING"
    )
    monkeypatch.setattr(settings, "firma_personal_require_xades", "todos")
    assert await params_entidad.leer_param(conn, "firma_personal_require_xades") is None
    # Sembrada en null: manda el ENTORNO (aquí estricto), no un "off" heredado.
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "todos"

    for valor in ("clasificados", "todos", "off"):
        await conn.execute(
            "UPDATE sgd_param_admin SET value = to_jsonb($1::text) WHERE key = $2",
            valor, "firma_personal_require_xades",
        )
        assert await params_entidad.modo_xades_de_la_entidad(conn) == valor


async def test_sin_fila_manda_el_entorno_y_un_valor_corrupto_es_todos(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await _en_tenant(tenant_ctx)
    await conn.execute(_DDL)
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
    conn = await _en_tenant(tenant_ctx)  # este schema NO tiene sgd_param_admin
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    assert await params_entidad.modo_xades_de_la_entidad(conn) == "off"
    # La conexión sigue sana (fuera de transacción el error no la envenena).
    assert await conn.fetchval("SELECT 1") == 1
