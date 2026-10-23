# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La política de migración vive en el plan de la ENTIDAD — E10, RF-PRE-04.

Hasta ahora los formatos en riesgo solo se podían declarar con una variable de
entorno del despliegue: la misma para todas las entidades de la instalación,
intocable para el archivista, sin versión y sin autor. Eso contradecía la regla
que el propio código declaraba —que la política la decide la entidad— y hacía
imposible que dos entidades de la misma instalación tuvieran políticas
distintas.

**Por qué contra PostgreSQL real.** Lo que se prueba es la migración 016 y el
comportamiento de una columna con `DEFAULT '{}'` sobre filas que ya existían:
con dobles, el `NOT NULL DEFAULT` lo afirmaría el doble. Y el plan es
append-only versionado: que la lectura devuelva la política de la versión
VIGENTE y no la de cualquiera solo se ve con varias filas de verdad.

Corre con: `docker compose run --rm -e RUN_INTEGRATION=1 storage-service
pytest -q -m integration tests/integration`
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.preservacion_repository import PreservacionRepository

pytestmark = pytest.mark.integration


async def _en_el_tenant(conn: asyncpg.Connection, slug: str) -> None:
    """El fixture aplica las migraciones con `SET LOCAL` dentro de su propia
    transacción, así que la conexión vuelve a `public` al cederla."""
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')


def _plan(**extra) -> dict:
    base = {
        "formatos_destino": ["PDF/A-2b"],
        "num_copias": 1,
        "periodicidad_fixity_dias": 180,
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_sin_plan_no_hay_politica_de_la_entidad(tenant_ctx) -> None:
    """Un tenant recién provisionado no declara nada, y eso no es un error: el
    barrido cae entonces al valor por defecto del despliegue, si lo hay."""
    slug, conn = tenant_ctx
    assert await PreservacionRepository(conn, slug).current_formatos_origen() == []


@pytest.mark.asyncio
async def test_un_plan_ANTERIOR_a_la_migracion_016_no_declara_nada(tenant_ctx) -> None:
    """La columna llega con `DEFAULT '{}'` sobre planes que ya existían. Si
    hubiera llegado con una lista de ejemplo, la migración habría puesto a
    convertir archivos ajenos sin que nadie lo pidiera."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    await conn.execute(
        "INSERT INTO preservacion_plan (version, formatos_destino, num_copias) "
        "VALUES (1, ARRAY['PDF/A-2b'], 1)"
    )
    assert await PreservacionRepository(conn, slug).current_formatos_origen() == []


@pytest.mark.asyncio
async def test_la_politica_se_guarda_y_se_lee_del_plan(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = PreservacionRepository(conn, slug)
    fila = await repo.insert_plan(
        _plan(formatos_origen=["application/msword"]), creado_por="ana"
    )
    assert fila["formatos_origen"] == ["application/msword"]
    assert await repo.current_formatos_origen() == ["application/msword"]


@pytest.mark.asyncio
async def test_manda_la_version_VIGENTE_del_plan(tenant_ctx) -> None:
    """El plan es append-only: cambiar la política es crear una versión nueva.
    Si la lectura no ordenara por versión, una entidad que dejó de considerar
    en riesgo un formato lo seguiría convirtiendo — y al revés, que es peor:
    su declaración nueva no tendría efecto."""
    slug, conn = tenant_ctx
    repo = PreservacionRepository(conn, slug)
    await repo.insert_plan(_plan(formatos_origen=["application/msword"]), creado_por="ana")
    await repo.insert_plan(_plan(formatos_origen=["image/tiff"]), creado_por="ana")

    assert await repo.current_formatos_origen() == ["image/tiff"]
    # Y la versión anterior sigue ahí, con su autor: cambiar la política de
    # conversión del archivo de una entidad tiene que poder mirarse hacia atrás.
    versiones = await repo.list_plan_versions()
    assert [v["formatos_origen"] for v in versiones] == [["image/tiff"], ["application/msword"]]
