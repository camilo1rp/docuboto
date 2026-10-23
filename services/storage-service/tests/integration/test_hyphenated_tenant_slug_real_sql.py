# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Remediación de auditoría Media (E07): `FileRepository` (y por extensión
`StorageService`, que la envuelve) rechazaba CUALQUIER `tenant_slug` con
guion -- la forma canónica y habitual de un slug real en TODO el resto de
OrpycaMCP (`app.core.database.tenant_schema`, `tenant-service`) -- porque
usaba su propia regex heredada `^[a-z0-9_]+$`, sin normalizar, en vez de
`app.core.database.TENANT_SLUG_RE` (`^[a-z0-9][a-z0-9-]{0,54}$`, con
normalización '-'->'_'). Consecuencia real: `POST /storage/upload` devolvía
500 para TODO tenant con slug guionado.

Este archivo prueba, contra Postgres REAL (mismo criterio que el resto de
`tests/integration/` de este servicio -- un mock de `conn` no puede probar
que `SET search_path` confina de verdad al schema correcto):

  1. `FileRepository` directamente: acepta un slug crudo con guion y
     escribe/lee del schema NORMALIZADO (`tenant_<slug con _>`), nunca del
     schema literal con guion (que ni siquiera es un identificador Postgres
     válido sin comillas, y que `tenant_ctx` jamás provisiona).
  2. `StorageService.upload` de punta a punta (MinIO mockeado, mismo
     criterio que `test_aip_concurrent_real_sql.py`): un tenant hyphenado
     sube un archivo con normalidad y la fila aterriza en el schema
     correcto.
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.file_repository import FileRepository
from app.services.storage_service import StorageService

from .conftest import DSN

pytestmark = pytest.mark.integration

_CONTENT = b"contenido real de un anexo (itest slug guionado)"
_SHA256 = hashlib.sha256(_CONTENT).hexdigest()


def _make_mock_minio() -> MagicMock:
    minio = MagicMock()
    minio.bucket_exists = AsyncMock(return_value=True)
    minio.make_bucket = AsyncMock()
    minio.put_object = AsyncMock(return_value=MagicMock(version_id="v1"))
    return minio


@pytest.mark.integration
async def test_file_repository_accepts_hyphenated_slug_and_writes_normalized_schema(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    assert "-" in slug, "tenant_ctx debería seguir generando slugs con guion (itest-<hex8>)"

    repo = FileRepository(conn, slug)  # no debe lanzar InvalidTenantSlugError
    file_id = uuid.uuid4()
    await repo.create(
        file_id=file_id,
        filename="oficio.pdf",
        mime_type="application/pdf",
        file_size=len(_CONTENT),
        sha256=_SHA256,
        bucket=f"orpycamcp-{slug}-documents",
        object_key=f"{file_id}/oficio.pdf",
        uploaded_by=None,
    )

    fetched = await repo.get_by_id(file_id)
    assert fetched is not None
    assert fetched.sha256 == _SHA256

    # Verificación física, desde una conexión INDEPENDIENTE, de que la fila
    # vive en el schema NORMALIZADO (`tenant_schema()`, guion -> guion
    # bajo) -- no en un schema literal con guion, que Postgres ni siquiera
    # aceptaría sin comillas dobles y que `tenant_ctx` jamás provisiona.
    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        schema = tenant_schema(slug)
        assert schema == f"tenant_{slug.replace('-', '_')}"
        assert "-" not in schema, "el schema normalizado no debe conservar guiones"
        await verify_conn.execute(f'SET search_path = "{schema}"')
        row = await verify_conn.fetchrow(
            "SELECT sha256, bucket FROM files WHERE id = $1", file_id
        )
    finally:
        await verify_conn.execute("SET search_path = public")
        await verify_conn.close()

    assert row is not None
    assert row["sha256"] == _SHA256
    assert row["bucket"] == f"orpycamcp-{slug}-documents"


@pytest.mark.integration
async def test_storage_service_upload_end_to_end_with_hyphenated_tenant(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`StorageService.upload` (el camino REAL que ejercita `POST
    /storage/upload`, ver `app/routers/storage.py::upload_file`) de punta a
    punta para un tenant con slug guionado. MinIO mockeado -- lo que se
    prueba aquí es que `FileRepository` (envuelto por `StorageService`) ya
    no rechaza el slug crudo, y que la fila aterriza en el schema correcto,
    no la subida física a un MinIO real."""
    slug, conn = tenant_ctx
    assert "-" in slug

    mock_minio = _make_mock_minio()
    service = StorageService(conn, slug)  # no debe lanzar InvalidTenantSlugError

    with patch("app.services.storage_service.get_minio", return_value=mock_minio):
        response, created, schedule_pdfa = await service.upload(
            filename="contrato.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=_CONTENT,
            uploaded_by=None,
        )

    assert created is True
    assert schedule_pdfa is False  # no es PDF -- el gate PDF/A nunca se activa
    assert response.sha256 == _SHA256

    # `put_object` se llamó con el bucket derivado del slug CRUDO (el bucket
    # de MinIO, a diferencia del schema de Postgres, no se normaliza).
    mock_minio.put_object.assert_awaited_once()
    bucket_arg = mock_minio.put_object.await_args.args[0]
    assert bucket_arg == f"orpycamcp-{slug}-documents"

    # La fila vive en el schema NORMALIZADO -- confirmación end-to-end de
    # que `StorageService`/`FileRepository` quedan reparados para tenants
    # hyphenados, no solo el worker de ingesta PDF/A.
    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        schema = tenant_schema(slug)
        await verify_conn.execute(f'SET search_path = "{schema}"')
        row = await verify_conn.fetchrow(
            "SELECT sha256 FROM files WHERE id = $1", response.id
        )
    finally:
        await verify_conn.execute("SET search_path = public")
        await verify_conn.close()

    assert row is not None
    assert row["sha256"] == _SHA256
