# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma declarativa + asiento son UN acto (AREA-05-03-S), con Postgres REAL.

Un mock de `transaction()` no demuestra el rollback: lo demuestra la base.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.clients import storage_client
from app.core.database import tenant_schema
from app.schemas.signature import SignatureCreate

pytestmark = pytest.mark.integration

_HUELLA = "e" * 64


async def _anexo(tenant_ctx: tuple[str, asyncpg.Connection]):
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    doc = await DocumentService(conn, slug).create(
        DocumentCreate(doc_type="E", subject="Con anexo", sender_name="x"), registered_by=None
    )
    anexo_id = await conn.fetchval(
        "INSERT INTO anexos (radicado_id, file_id, filename) VALUES ($1, $2, 'a.pdf') "
        "RETURNING id",
        doc.id,
        uuid.uuid4(),
    )
    return slug, conn, doc, anexo_id


def _storage(monkeypatch) -> None:
    async def _f(*, tenant_slug, file_id):
        return _HUELLA

    monkeypatch.setattr(storage_client, "fetch_file_sha256", _f)


@pytest.mark.asyncio
async def test_un_post_valido_deja_exactamente_un_asiento(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    from app.services.signature_service import SignatureService

    _storage(monkeypatch)
    slug, conn, doc, anexo_id = await _anexo(tenant_ctx)
    firma = await SignatureService(conn, slug).firmar(
        doc.id,
        SignatureCreate(anexo_id=anexo_id, content_hash="f" * 64),
        signer=uuid.uuid4(),
        signer_name="Ana",
        max_clearance=1,
    )
    assert firma.content_hash == _HUELLA, "la huella la pone el servidor, no el cuerpo"
    n = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'document.signature_created' AND object_ref = $2",
        slug,
        str(doc.id),
    )
    assert n == 1


@pytest.mark.asyncio
async def test_si_el_asiento_falla_la_firma_se_revierte(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    from app.services import signature_service
    from app.services.signature_service import SignatureService

    _storage(monkeypatch)
    slug, conn, doc, anexo_id = await _anexo(tenant_ctx)

    async def _falla(*a, **k):
        raise RuntimeError("audit caído")

    monkeypatch.setattr(signature_service.audit, "append", _falla)
    with pytest.raises(RuntimeError):
        await SignatureService(conn, slug).firmar(
            doc.id,
            SignatureCreate(anexo_id=anexo_id),
            signer=uuid.uuid4(),
            signer_name="Ana",
            max_clearance=1,
        )
    assert await conn.fetchval("SELECT count(*) FROM signatures WHERE radicado_id = $1", doc.id) == 0


@pytest.mark.asyncio
async def test_el_anexo_de_otro_radicado_no_se_firma_por_este(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    """`a.radicado_id = $2` va en el WHERE: firmar el anexo A bajo el radicado B es 404."""
    from fastapi import HTTPException

    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService
    from app.services.signature_service import SignatureService

    _storage(monkeypatch)
    slug, conn, _doc, anexo_id = await _anexo(tenant_ctx)
    otro = await DocumentService(conn, slug).create(
        DocumentCreate(doc_type="E", subject="Otro", sender_name="x"), registered_by=None
    )
    with pytest.raises(HTTPException) as exc:
        await SignatureService(conn, slug).firmar(
            otro.id,
            SignatureCreate(anexo_id=anexo_id),
            signer=uuid.uuid4(),
            signer_name="Ana",
            max_clearance=3,
        )
    assert exc.value.status_code == 404
    assert await conn.fetchval("SELECT count(*) FROM signatures") == 0


@pytest.mark.asyncio
async def test_el_anexo_de_un_radicado_reservado_es_404_neutro(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    from fastapi import HTTPException

    from app.services.signature_service import SignatureService

    llamadas: list = []

    async def _espia(*, tenant_slug, file_id):
        llamadas.append(file_id)
        return _HUELLA

    monkeypatch.setattr(storage_client, "fetch_file_sha256", _espia)
    slug, conn, doc, anexo_id = await _anexo(tenant_ctx)
    await conn.execute("UPDATE radicados SET nivel_seguridad = 3 WHERE id = $1", doc.id)
    with pytest.raises(HTTPException) as exc:
        await SignatureService(conn, slug).firmar(
            doc.id,
            SignatureCreate(anexo_id=anexo_id),
            signer=uuid.uuid4(),
            signer_name="Ana",
            max_clearance=1,
        )
    assert exc.value.status_code == 404
    assert llamadas == [], "no se consulta a storage por lo que el llamante no alcanza"
    assert await conn.fetchval("SELECT count(*) FROM signatures") == 0
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE action = 'document.signature_created' "
            "AND object_ref = $1",
            str(doc.id),
        )
        == 0
    )


@pytest.mark.asyncio
async def test_la_firma_heredada_con_huella_del_cliente_no_verifica(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    """Las filas anteriores al endurecimiento no pueden seguir dando `valid=true`."""
    from app.repositories.signature_repository import SignatureRepository
    from app.services.signature_service import SignatureService

    _storage(monkeypatch)
    slug, conn, doc, anexo_id = await _anexo(tenant_ctx)
    forjada = "a" * 64
    # Fila heredada: insertada como lo hacía el código anterior (sin `origen`
    # explícito la migración la marca; aquí se fuerza el valor heredado).
    await conn.execute(
        "INSERT INTO signatures (radicado_id, content_hash, origen) "
        "VALUES ($1, $2, 'cliente_no_verificado')",
        doc.id,
        forjada,
    )
    repo = SignatureRepository(conn)
    assert await repo.list_matching(doc.id, forjada) == []

    # Control positivo: la firma nueva, con huella del servidor, sí verifica.
    await SignatureService(conn, slug).firmar(
        doc.id, SignatureCreate(anexo_id=anexo_id), signer=uuid.uuid4(),
        signer_name="Ana", max_clearance=1,
    )
    assert len(await repo.list_matching(doc.id, _HUELLA)) == 1


@pytest.mark.asyncio
async def test_la_migracion_036_marca_las_filas_existentes(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El DEFAULT del ADD COLUMN cubre lo ya insertado; después pasa a `servidor`."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    default = await conn.fetchval(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = 'signatures' AND column_name = 'origen'",
        tenant_schema(slug),
    )
    assert "servidor" in default
