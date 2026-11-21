# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Preservación digital: plan + eventos PREMIS (E10)."""

from __future__ import annotations

import json
import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from orpycamcp_common.clearance import require_vetted_permission as require_permission
from orpycamcp_common.database import get_vetted_conn as get_db_conn
from orpycamcp_common.usuario import id_opcional, username_de

from storage.core.config import settings
from storage.core.minio_client import get_minio
from storage.repositories.preservacion_repository import PreservacionRepository
from storage.schemas.preservacion import (
    EstadoResponse,
    EventoCreate,
    EventoResponse,
    MigracionResponse,
    MigrarFormatoRequest,
    PlanCreate,
    PlanResponse,
)
from storage.services.format_migration_service import (
    FileNotFoundForMigrationError,
    FormatMigrationService,
    MigradorNoDisponibleError,
)
from storage.services.format_migration_service import (
    # Alias explícito: `preservation_worm_service` exporta otra excepción con
    # el MISMO nombre, y dejar que una tape a la otra haría que un `except`
    # capturara la equivocada sin que nada avisara.
    FixityMismatchError as MigracionFixityMismatchError,
)
from storage.services.storage_service import StorageService

router = APIRouter(prefix="/api/v1/preservacion", tags=["preservacion"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "Falta la cabecera X-Tenant-Slug",
                "status": 400,
            },
        )
    return x_tenant_slug


def _actor(request: Request) -> str | None:
    """Actor de un evento de preservación: identidad ESTABLE, con el nombre al lado.

    Antes se sellaba el nombre de usuario a secas. No es forjable —lo inyecta el
    gateway desde el token validado— pero sí es MUTABLE en el sentido que importa
    aquí: un nombre se renombra y se reasigna, y estos eventos son metadatos de
    preservación que han de sostenerse durante décadas. Dentro de diez años,
    «jperez» puede ser otra persona.

    Se sella el identificador, que no cambia nunca, y se acompaña del nombre para
    que el registro siga siendo legible sin tener que resolver un UUID a mano.
    """
    uid = (str(u) if (u := id_opcional(request)) else None)
    nombre = username_de(request)
    if uid and nombre:
        return f"{uid} ({nombre})"
    return uid or nombre


def _actor_uuid(request: Request) -> uuid.UUID | None:
    """El actor como UUID, para las columnas que lo son.

    `_actor` devuelve TEXTO porque prefiere `X-Username`, que es lo que se lee
    mejor en un evento PREMIS. Aquí hace falta el identificador: un
    `X-Username` no convertible resuelve a `None` en vez de reventar la
    petición — la migración es un acto de preservación y no debe fallar por el
    formato de una cabecera."""
    crudo = (str(u) if (u := id_opcional(request)) else None)
    if not crudo:
        return None
    try:
        return uuid.UUID(crudo)
    except ValueError:
        return None


def _evento_response(row) -> EventoResponse:
    d = dict(row)
    raw = d.get("detalle")
    d["detalle"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return EventoResponse(**d)


@router.get(
    "/estado",
    response_model=EstadoResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def get_estado() -> EstadoResponse:
    """¿Está encendida la preservación en esta instalación?

    Es un dato de INSTANCIA (`PRESERVATION_ENABLED`), no de un expediente: no
    consulta ni filtra nada por expediente, así que no abre un oráculo
    no-read-up. Existe para que la pestaña Preservación de la UI diga «está
    desactivada en esta instalación» en vez de «sin AIP o sin visibilidad», que
    es lo que un 404 neutro hace pasar por normal cuando en realidad nada se
    empaqueta jamás. Mismo gate de lectura que `GET /plan` y que el mediador de
    archive-service para el AIP."""
    return EstadoResponse(enabled=settings.preservation_enabled)


@router.get(
    "/plan",
    response_model=PlanResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def get_plan(
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PlanResponse:
    row = await PreservacionRepository(conn, tenant_slug).current_plan()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "plan_not_found", "detail": "Sin plan de preservación", "status": 404},
        )
    return PlanResponse(**dict(row))


@router.put(
    "/plan",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def set_plan(
    payload: PlanCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PlanResponse:
    row = await PreservacionRepository(conn, tenant_slug).insert_plan(
        payload.model_dump(), creado_por=_actor(request)
    )
    return PlanResponse(**dict(row))


@router.get(
    "/plan/versions",
    response_model=list[PlanResponse],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def list_plan_versions(
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[PlanResponse]:
    rows = await PreservacionRepository(conn, tenant_slug).list_plan_versions()
    return [PlanResponse(**dict(r)) for r in rows]


@router.post(
    "/eventos",
    response_model=EventoResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def record_evento(
    payload: EventoCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> EventoResponse:
    row = await PreservacionRepository(conn, tenant_slug).insert_evento(
        payload.model_dump(), agente=_actor(request)
    )
    return _evento_response(row)


@router.post(
    "/migrar-formato",
    response_model=MigracionResponse,
    summary="Migrar un archivo a PDF/A contra la obsolescencia (RF-PRE-04)",
    # Mismo gate que proteger un artefacto: es un acto de preservación sobre el
    # archivo de un expediente, y produce un artefacto nuevo.
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def migrar_formato(
    payload: MigrarFormatoRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_actor_uuid),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> MigracionResponse:
    """Produce una copia de preservación en PDF/A **sin tocar el original**
    (Decreto 2609/2012 art. 32).

    **`503` si este despliegue no tiene convertidor**, y esa es la respuesta
    correcta: no hay migrador «stub» porque un migrador falso tendría que
    inventarse el archivo. El `503` dice qué falta instalar; fabricar una copia
    de preservación que nadie generó sería mucho peor que no migrar.

    La integridad se verifica **antes y después**, y el derivado se valida
    contra el perfil PDF/A: si no resulta conforme, la migración es `fallido` y
    los bytes se descartan. Si no hay validador, queda `no_verificado` y
    **tampoco** se promueve a copia de preservación.
    """
    service = FormatMigrationService(conn, tenant_slug)
    storage = StorageService(conn, tenant_slug)

    async def _descargar(record) -> bytes:
        minio = get_minio()
        respuesta = await minio.get_object(record.bucket, record.object_key)
        try:
            return await respuesta.read()
        finally:
            close = getattr(respuesta, "close", None)
            if close:
                close()

    async def _subir(origen, contenido: bytes, formato: str) -> uuid.UUID:
        # El derivado entra como ARTEFACTO DE SISTEMA, no como anexo: nadie lo
        # aportó, lo produjo la preservación. Por eso usa la lista de formatos
        # del sistema y no la de anexos.
        nombre = f"{origen.filename.rsplit('.', 1)[0]}-{formato.replace('/', '')}.pdf"
        respuesta, _creado, _ = await storage.upload(
            filename=nombre,
            mime_type="application/pdf",
            content=contenido,
            uploaded_by=actor,
            formatos_admitidos=settings.allowed_system_mime_set,
        )
        return respuesta.id

    try:
        registro = await service.migrar_a_pdfa(
            payload.file_id,
            perfil=payload.perfil,
            actor=actor,
            descargar=_descargar,
            subir=_subir,
        )
    except MigradorNoDisponibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "migrador_no_disponible", "detail": str(exc), "status": 503},
        ) from exc
    except FileNotFoundForMigrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": f"No existe el archivo {exc}",
                "status": 404,
            },
        ) from exc
    except MigracionFixityMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "fixity_no_coincide", "detail": str(exc), "status": 409},
        ) from exc
    return MigracionResponse(**registro)


@router.get(
    "/migraciones/{file_id}",
    response_model=list[MigracionResponse],
    summary="Cadena de procedencia de las migraciones de un archivo (RF-PRE-04)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def listar_migraciones(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[MigracionResponse]:
    """Los intentos, no solo los que salieron bien: «se intentó y no se pudo»
    es lo que dice qué documentos siguen esperando una migración que no sale."""
    repo = PreservacionRepository(conn, tenant_slug)
    return [MigracionResponse(**dict(r)) for r in await repo.list_migraciones(file_id)]


