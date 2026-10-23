# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El SQL de «PDF vigente de un radicado» contra Postgres REAL (AREA-04-09 Media-2).

`DocumentRepository.get_documento_principal_para_firma`: radicado por número O por
UUID (lo que la cadena guarda como `objeto_id`), y de sus anexos SOLO el `es_principal`
más reciente (sin principal no hay documento: ningún PDF de soporte lo sustituye). Un doble de conexión
no distingue «el más reciente» de «cualquiera»: lo impone el `ORDER BY`.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository


async def _radicado(conn: asyncpg.Connection, schema: str, tracking: str) -> uuid.UUID:
    return await conn.fetchval(
        f'INSERT INTO "{schema}".radicados '
        "(tracking_number, doc_type, year, sequence, subject, tenant_code) "
        "VALUES ($1, 'E', 2026, 1, 'itest huella', 'ITEST') RETURNING id",
        tracking,
    )


async def _anexo(
    conn, schema, rid, *, mime="application/pdf", principal=False, segundos=0
) -> uuid.UUID:
    fid = uuid.uuid4()
    await conn.execute(
        f'INSERT INTO "{schema}".anexos '
        "(radicado_id, file_id, filename, mime_type, es_principal, created_at) "
        "VALUES ($1, $2, 'a.bin', $3, $4, now() + make_interval(secs => $5))",
        rid, fid, mime, principal, float(segundos),
    )
    return fid


@pytest.mark.integration
async def test_resuelve_por_numero_y_por_uuid_y_elige_el_principal_mas_reciente(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rid = await _radicado(conn, schema, "2026-ITEST-E-000900")
    await _anexo(conn, schema, rid, principal=True, segundos=0)
    reciente = await _anexo(conn, schema, rid, principal=True, segundos=10)
    # Un PDF más nuevo pero NO principal no gana al principal.
    await _anexo(conn, schema, rid, principal=False, segundos=20)
    await conn.execute(f'SET search_path = "{schema}"')
    repo = DocumentRepository(conn, slug)

    for ref in ("2026-ITEST-E-000900", str(rid)):
        radicado, anexo = await repo.get_documento_principal_para_firma(ref)
        assert radicado["id"] == rid
        assert anexo["file_id"] == reciente
    await conn.execute("SET search_path = public")


@pytest.mark.integration
async def test_sin_principal_no_hay_documento_aunque_haya_pdfs_de_soporte(tenant_ctx) -> None:
    """Un PDF de soporte NO es el documento: antes (sin principal) se tomaba «el PDF
    más reciente», y subir otro PDF cambiaba la huella y bloqueaba la cadena."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rid = await _radicado(conn, schema, "2026-ITEST-E-000901")
    await _anexo(conn, schema, rid, mime="application/pdf", segundos=0)
    await _anexo(conn, schema, rid, mime="application/pdf", segundos=5)
    await _anexo(conn, schema, rid, mime="image/png", segundos=50)
    await conn.execute(f'SET search_path = "{schema}"')
    radicado, anexo = await DocumentRepository(conn, slug).get_documento_principal_para_firma(
        "2026-ITEST-E-000901"
    )
    assert radicado["id"] == rid and anexo is None
    await conn.execute("SET search_path = public")


@pytest.mark.integration
async def test_subir_otro_pdf_no_cambia_la_huella_del_principal(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rid = await _radicado(conn, schema, "2026-ITEST-E-000903")
    principal = await _anexo(conn, schema, rid, mime="application/pdf", principal=True, segundos=0)
    await conn.execute(f'SET search_path = "{schema}"')
    repo = DocumentRepository(conn, slug)
    await _anexo(conn, schema, rid, mime="application/pdf", segundos=30)  # soporte posterior
    _, anexo = await repo.get_documento_principal_para_firma("2026-ITEST-E-000903")
    assert anexo["file_id"] == principal
    await conn.execute("SET search_path = public")


@pytest.mark.integration
async def test_radicado_sin_pdf_devuelve_anexo_none_e_inexistente_none(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rid = await _radicado(conn, schema, "2026-ITEST-E-000902")
    await _anexo(conn, schema, rid, mime="image/png")
    await conn.execute(f'SET search_path = "{schema}"')
    repo = DocumentRepository(conn, slug)
    radicado, anexo = await repo.get_documento_principal_para_firma("2026-ITEST-E-000902")
    assert radicado["id"] == rid and anexo is None
    assert await repo.get_documento_principal_para_firma("2026-ITEST-E-999999") is None
    assert await repo.get_documento_principal_para_firma(str(uuid.uuid4())) is None
    await conn.execute("SET search_path = public")


@pytest.mark.integration
async def test_otro_tenant_no_resuelve_el_radicado(two_tenant_ctx) -> None:
    slug_a, slug_b, conn = two_tenant_ctx
    sa, sb = tenant_schema(slug_a), tenant_schema(slug_b)
    rid = await _radicado(conn, sa, "2026-ITEST-E-000903")
    await _anexo(conn, sa, rid)
    await conn.execute(f'SET search_path = "{sb}"')
    repo = DocumentRepository(conn, slug_b)
    assert await repo.get_documento_principal_para_firma("2026-ITEST-E-000903") is None
    assert await repo.get_documento_principal_para_firma(str(rid)) is None
    await conn.execute("SET search_path = public")
