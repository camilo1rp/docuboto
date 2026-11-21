# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
storage-service — FastAPI application factory.

Responsibilities:
- File upload/download for radicado anexos (attachments)
- SHA-256 deduplication per tenant to avoid redundant MinIO storage
- Presigned URL generation — the service never proxies file bytes
- Tenant-isolated storage: PostgreSQL schema per tenant + MinIO bucket per tenant
"""

import logging

from storage.core.config import settings
from storage.core.minio_client import get_minio
from storage.routers.preservacion import router as preservacion_router
from storage.routers.storage import router as storage_router
from storage.services.storage_service import StorageService

logger = logging.getLogger(__name__)


async def _descargar_para_migracion(record) -> bytes:
    """Los bytes del origen. Mismo camino que la ruta manual del router: el job
    decide a QUIÉN le toca migrar, no cómo se hablan MinIO y la tabla."""
    minio = get_minio()
    respuesta = await minio.get_object(record.bucket, record.object_key)
    try:
        return await respuesta.read()
    finally:
        close = getattr(respuesta, "close", None)
        if close:
            close()


def _subir_para_migracion(conn, slug: str):
    """El derivado entra como ARTEFACTO DE SISTEMA, no como anexo: nadie lo
    aportó, lo produjo la preservación."""

    async def _subir(origen, contenido: bytes, formato: str):
        nombre = f"{origen.filename.rsplit('.', 1)[0]}-{formato.replace('/', '')}.pdf"
        respuesta, _creado, _ = await StorageService(conn, slug).upload(
            filename=nombre,
            mime_type="application/pdf",
            content=contenido,
            uploaded_by=None,
            formatos_admitidos=settings.allowed_system_mime_set,
        )
        return respuesta.id

    return _subir


DOMAIN_ROUTERS = (storage_router, preservacion_router)


