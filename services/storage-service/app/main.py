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

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import request_log
from orpycamcp_common import secrets as common_secrets

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.core.minio_client import get_minio, init_minio
from app.jobs.format_migration import run_once as _format_migration_once
from app.routers.health import router as health_router
from app.routers.preservacion import router as preservacion_router
from app.routers.render import router as render_router
from app.routers.storage import router as storage_router
from app.services.storage_service import StorageService

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
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


async def _format_migration_loop() -> None:
    """Barrido periódico de migración de formato (E10, RF-PRE-04).

    Mismo molde que las barridas de archive-service: kill-switch propio,
    cadencia propia y **nunca muere por una excepción**. La cadencia por defecto
    es diaria porque la obsolescencia no llega de un día para otro; lo que la
    barrida recorta cada vez es la cola de lo que entró nuevo.

    **No decide qué está obsoleto.** Eso lo declara la entidad en
    `FORMAT_MIGRATION_MIME_TYPES`, y vacío significa no migrar nada.
    """
    while True:
        try:
            await _format_migration_once(
                descargar=_descargar_para_migracion,
                subir_factory=_subir_para_migracion,
            )
        except Exception:
            logger.warning("Fallo en la barrida de migración de formato", exc_info=True)
        await asyncio.sleep(settings.format_migration_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("storage-service starting up")

    # Sin credenciales propias de MinIO no se arranca: las de desarrollo están
    # publicadas en este repositorio y en el CLAUDE.md, y un despliegue que las
    # heredara dejaría el almacén de anexos —los documentos oficiales— accesible
    # con una clave conocida.
    common_secrets.require_secrets(
        {
            "MINIO_ACCESS_KEY": settings.minio_access_key,
            "MINIO_SECRET_KEY": settings.minio_secret_key,
        },
        servicio="storage-service",
    )

    # Guard de arranque de preservación WORM (E10 Increment A): en producción,
    # un modo de retención distinto de COMPLIANCE es evadible por un usuario con
    # el permiso bypass-governance de MinIO — envenenar/perder la inmutabilidad
    # del índice electrónico firmado es irreversible, así que se rechaza el
    # arranque en vez de arrancar con una garantía WORM débil sin que nadie lo note.
    preservation_warnings = settings.preservation_startup_guard()
    if preservation_warnings:
        # Fail-closed ante ambigüedad: cualquier `app_env` que no sea explícitamente
        # un entorno de no-producción reconocido se trata COMO producción para este
        # guard. Un `app_env` mal escrito o ausente (p. ej. "prod", "prd", "") no debe
        # degradar en silencio la garantía WORM irreversible del índice firmado.
        _NON_PROD_ENVS = {"development", "dev", "local", "test", "testing", "ci"}
        if settings.app_env.strip().lower() not in _NON_PROD_ENVS:
            raise RuntimeError(
                f"Config de preservación WORM insegura (app_env={settings.app_env!r} "
                "no es un entorno de no-producción reconocido): "
                + "; ".join(preservation_warnings)
            )
        for warning in preservation_warnings:
            logger.warning("PRESERVATION CONFIG WARNING: %s", warning)

    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    init_minio(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_use_ssl,
    )
    migracion_task: asyncio.Task[None] | None = None
    if settings.format_migration_enabled:
        migracion_task = asyncio.create_task(_format_migration_loop())

    yield
    logger.info("storage-service shutting down")
    if migracion_task is not None:
        migracion_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await migracion_task
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — Storage Service",
    description=(
        "Manages file storage for radicado anexos. Uploads to MinIO with "
        "SHA-256 deduplication per tenant. Issues presigned URLs for downloads."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
app.add_middleware(request_log.RequestLogMiddleware, servicio="storage-service")

app.include_router(storage_router)
app.include_router(preservacion_router)
app.include_router(render_router)
app.include_router(health_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "Ocurrió un error inesperado",
            "status": 500,
        },
    )
