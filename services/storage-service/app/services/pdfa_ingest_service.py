# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación PDF/A en el flujo de ingesta/captura de anexos (E07, E10
Increment C2b follow-up, RF-PRE-02, ISO 19005, Ley 594/2000 + Acuerdo AGN
060/2001).

Hasta este follow-up, veraPDF solo corría al EMPAQUETAR el AIP
(`app.services.aip_service`) — tarde: un anexo podía vivir semanas/años en
`files` sin que nadie supiera si era PDF/A conforme. Este módulo cablea la
misma infraestructura de validación (`app.services.pdfa`) al punto de
CAPTURA, `StorageService.upload` (el choke point de toda subida, keyed por
`file_id`).

Dos modos, gateados AMBOS por el master switch
`settings.preservation_pdfa_validation_enabled` (si está en False, ninguno
de los dos corre — comportamiento idéntico al de antes de este follow-up):

- **advisory** (default, `settings.preservation_pdfa_ingest_mode`): la
  validación es ASÍNCRONA best-effort, corre DESPUÉS de responder al
  cliente (`run_ingest_validation_background`, invocado como
  `fastapi.BackgroundTasks` desde `routers/storage.py`). Fundamento
  archivístico: PDF/A es requisito de PRESERVACIÓN, no de radicación —
  rechazar en la puerta rompería la captura de comunicaciones oficiales
  que llegan no-PDF/A (correo, escaneo). Coherente con la postura advisory
  ya usada en el empaquetado del AIP (E10 Increment C2a).
- **strict** (opt-in): la validación es SÍNCRONA dentro de
  `StorageService.upload`, ANTES de subir a MinIO/persistir la fila —
  `non_conformant` aborta la subida (422). Quien activa este modo asume el
  coste JVM/subprocess en el hot path.

El veredicto se persiste en DOS sitios, igual que en el empaquetado del AIP:
1. `files.pdfa_status`/`pdfa_profile`/`pdfa_checked_at` (migración 013) — el
   verdicto DURABLE por objeto, reusado por
   `AipService.empaquetar_aip` si el perfil coincide (ver
   `aip_service._reused_pdfa_result`).
2. `preservacion_evento` tipo=VALIDACION_PDFA, `object_ref=file_id` — MISMO
   tipo/llave que usa el AIP, para que ambos rastros convivan en la misma
   consulta `GET /preservacion/eventos?object_ref=<file_id>`.

Perfil de ingesta = `settings.pdfa_profile` (global, "2b" por defecto): en
captura no se conoce todavía serie/expediente, así que no hay perfil
por-serie que aplicar.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

from app.core.config import settings
from app.core.minio_client import get_minio
from app.repositories.file_repository import FileRepository, InvalidTenantSlugError
from app.repositories.preservacion_repository import PreservacionRepository
from app.services.pdfa import (
    PDFA_RESULTADO_MAP,
    PdfaResult,
    get_pdfa_validator,
    run_pdfa_validation,
)

logger = logging.getLogger(__name__)

_EVT_TIPO = "VALIDACION_PDFA"

# Identificador de agente para el rastro PREMIS/audit cuando el veredicto lo
# fija el worker asíncrono (nadie hay un usuario real detrás) — mismo
# espíritu que `SYSTEM_RECONCILER_ID` de otros servicios, pero aquí
# `preservacion_evento.agente` es TEXT libre, no una FK a `auth_users`.
WORKER_AGENT = "system/pdfa-ingest-worker"

# Firma de archivo PDF (magic bytes) — se detecta por CONTENIDO, nunca por el
# `mime_type` que declara el cliente en el multipart (no es fiable). Mismo
# criterio que `app.services.aip_service._looks_like_pdf`.
_PDF_MAGIC = b"%PDF"


def looks_like_pdf(content: bytes) -> bool:
    return content.startswith(_PDF_MAGIC)


class PdfaNonConformantError(Exception):
    """Modo estricto (`PRESERVATION_PDFA_INGEST_MODE=strict`): el contenido
    subido no es PDF/A conforme contra el perfil de ingesta -- la subida se
    rechaza ANTES de tocar MinIO/BD. El router traduce esto a HTTP 422."""


async def _download_object(minio, bucket: str, object_key: str) -> bytes:
    obj = await minio.get_object(bucket, object_key)
    try:
        return await obj.read()
    finally:
        close = getattr(obj, "close", None)
        if close is not None:
            close()


async def validate_ingest_content(content: bytes) -> PdfaResult:
    """Ejecuta el validador PDF/A activo sobre `content` con el perfil de
    ingesta (`settings.pdfa_profile`, global). NUNCA lanza — ver
    `run_pdfa_validation`. Compartida por el camino síncrono estricto
    (`StorageService.upload`) y el worker asíncrono best-effort de abajo.
    """
    validator = get_pdfa_validator(settings)
    return await run_pdfa_validation(validator, content, profile=settings.pdfa_profile)


async def record_ingest_pdfa_result(
    conn: asyncpg.Connection,
    tenant_slug: str,
    file_id: uuid.UUID,
    result: PdfaResult,
    *,
    agente: str | None,
) -> None:
    """Persiste el veredicto: UPSERT en `files` (verdicto durable, PIEZA 1)
    + `preservacion_evento` (rastro PREMIS al momento de ingesta, PIEZA 2).

    `conn` ya debe estar operando dentro del tenant correcto (el llamador es
    responsable de eso — ver `run_ingest_validation_background` para el
    camino asíncrono, donde esto es el punto más sensible de aislamiento
    multi-tenant).
    """
    file_repo = FileRepository(conn, tenant_slug)
    await file_repo.update_pdfa_result(file_id, status=result.outcome, profile=result.profile)

    evt_repo = PreservacionRepository(conn, tenant_slug)
    await evt_repo.insert_evento(
        {
            "object_ref": str(file_id),
            "tipo": _EVT_TIPO,
            "resultado": PDFA_RESULTADO_MAP[result.outcome],
            "detalle": {
                "file_id": str(file_id),
                "profile": result.profile,
                "outcome": result.outcome,
                "validator_agent": result.validator_agent,
                "contexto": "ingesta",
                **result.detail,
            },
        },
        agente=agente,
    )


async def run_ingest_validation_background(
    pool: asyncpg.Pool, tenant_slug: str, file_id: uuid.UUID
) -> None:
    """Worker ASÍNCRONO best-effort (modo advisory) — programado como
    `fastapi.BackgroundTasks` desde `routers/storage.py::upload_file` (MVP;
    storage-service no produce Redis Streams hoy, ver docstring del módulo).

    **Aislamiento multi-tenant (el punto MÁS sensible, foco del auditor):**
    `tenant_slug` es el MISMO string capturado del header `X-Tenant-Slug` de
    la petición HTTP ORIGINAL que disparó la subida — pasado explícitamente
    como argumento posicional al programar la tarea (`background_tasks.add_task`),
    nunca leído de nuevo de ningún estado global ni derivado del contenido/
    body del archivo. FastAPI cierra las dependencias `yield` (incluida
    `get_db_conn`, que devuelve la conexión de la petición original al pool
    con `search_path` neutro) ANTES de ejecutar los `BackgroundTasks` — este
    worker NO puede reusar esa conexión, así que adquiere la SUYA PROPIA del
    pool (`pool.acquire()`) y dentro de ella, `FileRepository`/
    `PreservacionRepository` fijan `SET search_path` al schema de ESE tenant
    en cada llamada (mismo mecanismo de aislamiento que cualquier ruta
    request-scoped del servicio) — nunca se toca `tenant_public`/otro
    schema. Al terminar, la conexión vuelve al pool con `search_path`
    neutro (mismo patrón defensivo que `app.core.database.get_db_conn`).

    NUNCA lanza: corre DESPUÉS de que la respuesta ya se envió al cliente —
    una excepción aquí no tiene a quién propagarle un error HTTP; se loguea
    y se descarta.
    """
    try:
        async with pool.acquire() as conn:
            try:
                await _run_ingest_validation(conn, tenant_slug, file_id)
            finally:
                await conn.execute("SET search_path = public")
    except Exception:  # noqa: BLE001 — best-effort post-respuesta, nunca propaga
        logger.exception(
            "Validación PDF/A de ingesta (worker asíncrono) falló para "
            "file_id=%s tenant_slug=%s",
            file_id,
            tenant_slug,
        )


async def _run_ingest_validation(
    conn: asyncpg.Connection, tenant_slug: str, file_id: uuid.UUID
) -> None:
    try:
        file_repo = FileRepository(conn, tenant_slug)
    except InvalidTenantSlugError:
        # Defensa en profundidad: `tenant_slug` ya se validó al aceptar la
        # petición original (`_require_tenant`), pero un worker best-effort
        # no debe asumirlo — nunca debe operar sobre un schema sin validar.
        logger.warning(
            "tenant_slug inválido en worker PDF/A de ingesta (descartado sin "
            "tocar BD): %r",
            tenant_slug,
        )
        return

    record = await file_repo.get_by_id_any_state(file_id)
    if record is None:
        # El archivo pudo purgarse (disposición TRD) entre la subida y la
        # corrida del worker -- nada que validar ni que persistir.
        return

    minio = get_minio()
    content = await _download_object(minio, record.bucket, record.object_key)
    if not looks_like_pdf(content):
        # Defensa en profundidad: ya se comprobó antes de programar la
        # tarea (`StorageService.upload`), pero el objeto pudo cambiar
        # (reemplazo de versión, `StorageService.replace`) en la ventana
        # entre encolar y correr.
        return

    result = await validate_ingest_content(content)
    await record_ingest_pdfa_result(conn, tenant_slug, file_id, result, agente=WORKER_AGENT)
