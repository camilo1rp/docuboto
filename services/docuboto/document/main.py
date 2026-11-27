# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
document-service — FastAPI application factory.

Responsibilities:
- Radicación of official documents (Entrada, Salida, Interno)
- Atomic tracking number assignment per tenant, year, and document type
- Tenant-isolated storage via PostgreSQL schema switching (tenant_{slug})
- Anexo references — file UUIDs from storage-service only (no direct MinIO)
"""

import logging

from document.routers.anulacion import cola_router as anulacion_cola_router
from document.routers.anulacion import router as anulacion_router
from document.routers.batch import router as batch_router
from document.routers.borradores import router as borradores_router
from document.routers.cmis import router as cmis_router
from document.routers.desclasificacion import cola_router as desclasificacion_cola_router
from document.routers.desclasificacion import router as desclasificacion_router
from document.routers.documents import router as documents_router
from document.routers.export import router as export_router
from document.routers.import_ import router as import_router
from document.routers.ingest import apps_router as email_apps_router
from document.routers.ingest import bandeja_router as email_bandeja_router
from document.routers.ingest import cuentas_router as email_cuentas_router
from document.routers.metadata import router as metadata_router
from document.routers.metadata_elements import router as metadata_elements_router
from document.routers.oai import router as oai_router
from document.routers.postal import router as postal_router
from document.routers.public import router as public_router
from document.routers.reports import router as reports_router
from document.routers.respuesta import router as respuesta_router
from document.routers.search import router as search_router
from document.routers.signatures import router as signatures_router
from document.routers.terceros import router as terceros_router
from document.routers.trabajo import router as trabajo_router

logger = logging.getLogger(__name__)


DOMAIN_ROUTERS = (
    batch_router,
    desclasificacion_router,  # Antes que documents: `/causales-reserva` vs `/{document_id}`.
    desclasificacion_cola_router,
    documents_router,
    metadata_router,
    metadata_elements_router,
    search_router,
    terceros_router,
    reports_router,
    public_router,
    email_cuentas_router,
    email_apps_router,
    email_bandeja_router,
    signatures_router,
    postal_router,
    oai_router,
    cmis_router,
    export_router,
    import_router,
    anulacion_router,
    anulacion_cola_router,
    respuesta_router,
    borradores_router,
    trabajo_router,
)


