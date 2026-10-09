# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El SQL de «índice vigente de un expediente» contra Postgres REAL (AREA-04-09).

El endpoint interno resuelve el expediente por CÓDIGO o por UUID y devuelve el
`xml_sha256` de la ÚLTIMA versión del índice (`ORDER BY version DESC`). Un doble de
conexión no distingue «la última» de «la primera»; lo impone el `ORDER BY`, y el
aislamiento por tenant lo impone el `search_path`.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from app.core import database
from app.routers.internal_firma import huella_indice_vigente


async def _expediente(conn: asyncpg.Connection, schema: str, code: str) -> uuid.UUID:
    return await conn.fetchval(
        f'INSERT INTO "{schema}".expedientes (code, name) VALUES ($1, $2) RETURNING id',
        code, "itest huella indice",
    )


async def _indice(conn, schema, eid, version: int, sha: str) -> None:
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_indice '
        "(expediente_id, version, xml, xml_sha256, content_hash, num_documentos) "
        "VALUES ($1, $2, '<indice/>', $3, $4, 0)",
        eid, version, sha, sha[::-1],
    )


@pytest.mark.integration
async def test_devuelve_la_ultima_version_por_codigo_y_por_uuid(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    schema = database.tenant_schema(slug)
    eid = await _expediente(conn, schema, "EXP-ITEST-1")
    await _indice(conn, schema, eid, 0, "a" * 64)
    await _indice(conn, schema, eid, 2, "c" * 64)
    await _indice(conn, schema, eid, 1, "b" * 64)
    for ref in ("EXP-ITEST-1", str(eid)):
        out = await huella_indice_vigente(ref, slug, conn)
        assert out["huella_sha256"] == "c" * 64
        assert out["version"] == 2
        assert out["expediente_id"] == str(eid)
    await conn.execute("SET search_path = public")


@pytest.mark.integration
async def test_sin_indice_409_e_inexistente_404(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    schema = database.tenant_schema(slug)
    await _expediente(conn, schema, "EXP-ITEST-2")
    with pytest.raises(HTTPException) as sin_indice:
        await huella_indice_vigente("EXP-ITEST-2", slug, conn)
    assert sin_indice.value.status_code == 409
    with pytest.raises(HTTPException) as inexistente:
        await huella_indice_vigente("EXP-NO-EXISTE", slug, conn)
    assert inexistente.value.status_code == 404
    await conn.execute("SET search_path = public")


@pytest.mark.integration
async def test_el_expediente_de_otro_tenant_no_se_ve(tenant_ctx) -> None:
    slug_a, conn = tenant_ctx
    sa = database.tenant_schema(slug_a)
    slug_b = slug_a + "-b"
    sb = database.tenant_schema(slug_b)
    assert sb.startswith("tenant_itest_")  # guardarraíl del DROP de abajo
    # Un segundo tenant con las MISMAS tablas y sin datos: el aislamiento es del search_path.
    await conn.execute(f'CREATE SCHEMA "{sb}"')
    try:
        for tabla in ("expedientes", "expediente_indice"):
            await conn.execute(f'CREATE TABLE "{sb}".{tabla} (LIKE "{sa}".{tabla} INCLUDING ALL)')
        eid = await _expediente(conn, sa, "EXP-ITEST-3")
        await _indice(conn, sa, eid, 1, "d" * 64)
        for ref in ("EXP-ITEST-3", str(eid)):
            with pytest.raises(HTTPException) as exc:
                await huella_indice_vigente(ref, slug_b, conn)
            assert exc.value.status_code == 404
    finally:
        await conn.execute("SET search_path = public")
        await conn.execute(f'DROP SCHEMA IF EXISTS "{sb}" CASCADE')
