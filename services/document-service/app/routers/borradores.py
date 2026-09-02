# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Plantillas y borradores de Salida (paridad legado)."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.clients.storage_client import RenderFallidoError, RenderNoDisponibleError
from app.core.clearance import check_permission, require_permission, resolve_caller_clearance
from app.core.combinacion import desconocidos, vocabulario
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.borrador import (
    BorradorCreate,
    BorradorResponse,
    BorradorUpdate,
    CombinarRequest,
    CombinarResponse,
    PlantillaAlcanceUpdate,
    PlantillaCreate,
    PlantillaResponse,
)
from app.schemas.document import DocumentResponse
from app.services.borrador_service import BorradorService
from app.services.document_service import DocumentService, InvalidTenantError
from app.services.importar_ofimatico import ImportacionError
from app.services.importar_ofimatico import importar as importar_ofimatico

router = APIRouter(tags=["borradores"])

_TENANT_HEADER = "X-Tenant-Slug"


def _slug(request: Request) -> str:
    return request.headers.get(_TENANT_HEADER, "")


def _user(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


# -- plantillas --------------------------------------------------------


@router.get(
    "/api/v1/plantillas/campos",
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def listar_campos_plantilla() -> dict[str, Any]:
    """Vocabulario de marcadores admitidos (E23, RF-PRO-01).

    Va **antes** de `/api/v1/plantillas/{plantilla_id}` en el orden de
    declaración a propósito: FastAPI casa por orden y `campos` encajaría en el
    parámetro de ruta, devolviendo un 422 de UUID inválido en vez del
    vocabulario.

    Sin esta lista, escribir una plantilla es adivinar: el legado no la tenía y
    por eso los marcadores mal escritos llegaban impresos al ciudadano.
    """
    return {"campos": vocabulario()}


@router.post(
    "/api/v1/plantillas",
    response_model=PlantillaResponse,
    status_code=201,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def crear_plantilla(
    payload: PlantillaCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> PlantillaResponse:
    return await BorradorService(conn).crear_plantilla(payload, _user(request))


@router.get(
    "/api/v1/plantillas",
    response_model=list[PlantillaResponse],
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def listar_plantillas(
    request: Request,
    activo: bool | None = Query(default=None),
    todas: bool = Query(
        default=False,
        description=(
            "Solo administración: devuelve también las plantillas que el "
            "llamante no alcanza desde su dependencia."
        ),
    ),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[PlantillaResponse]:
    """Plantillas que alcanza el llamante desde sus dependencias (RF-PRO-06).

    `?todas=true` **exige permiso de administración**, y se comprueba aquí en
    vez de subir el gate del endpoint: la lista normal la necesita cualquiera
    con `PERM_RADI` para redactar. Un recorte que el propio llamante puede
    apagar con un parámetro no es un recorte, así que el parámetro no basta —
    hace falta el permiso.
    """
    user_id = _user(request)
    sin_recorte = False
    if todas:
        if not await check_permission(conn, user_id, "USUA_PERM_ADMIN"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden",
                    "detail": (
                        "Ver todas las plantillas, incluidas las de otras "
                        "dependencias, es una competencia de administracion."
                    ),
                    "status": 403,
                },
            )
        sin_recorte = True
    return await BorradorService(conn).listar_plantillas(
        activo, usuario=user_id, sin_recorte=sin_recorte
    )


@router.put(
    "/api/v1/plantillas/{plantilla_id}/alcance",
    response_model=PlantillaResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def fijar_alcance_plantilla(
    plantilla_id: uuid.UUID,
    payload: PlantillaAlcanceUpdate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> PlantillaResponse:
    """Cambia a qué dependencias alcanza una plantilla, sin tocar su contenido.

    Poner `es_global=true` **borra** la lista de dependencias: mantenerla
    guardada dejaría una asignación invisible que reaparecería al volver a
    restringir, y nadie recordaría de dónde salió.
    """
    return await BorradorService(conn).fijar_alcance(plantilla_id, payload)


@router.post(
    "/api/v1/plantillas/importar",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def importar_plantilla(file: UploadFile = File(...)) -> dict[str, Any]:
    """Convierte un `.docx`/`.odt` en cuerpo HTML (E23, RF-PRO-07).

    **No guarda nada.** Devuelve el cuerpo para que se revise en el editor antes
    de crearlo: una plantilla del Orfeo legado llega con sus marcadores dentro y
    puede traer alguno que aquí no exista, y guardar primero para descubrirlo
    después obligaría a borrar y repetir.

    Por eso la respuesta trae `marcadores_desconocidos`: los mismos que
    rechazarían el guardado, dichos ANTES.

    Gate de administración, el mismo que crear una plantilla: importar es la
    primera mitad de ese acto.
    """
    datos = await file.read()
    try:
        resultado = importar_ofimatico(file.filename or "", datos)
    except ImportacionError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "importacion_fallida", "detail": str(exc), "status": 422},
        ) from exc
    return {
        "cuerpo": resultado.cuerpo,
        "perdido": resultado.perdido,
        "marcadores_desconocidos": desconocidos(resultado.cuerpo),
    }


@router.post(
    "/api/v1/plantillas/{plantilla_id}/combinar",
    response_model=CombinarResponse,
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def combinar_plantilla(
    plantilla_id: uuid.UUID,
    payload: CombinarRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
) -> CombinarResponse:
    """Cuerpo de la plantilla con los campos ya resueltos (E23, RF-PRO-02)."""
    user_id = _user(request)
    clearance = await resolve_caller_clearance(conn, user_id)
    return await BorradorService(
        conn, request.headers.get("X-Tenant-Slug", ""), tenant_client
    ).combinar_plantilla(plantilla_id, payload, max_clearance=clearance)


@router.delete(
    "/api/v1/plantillas/{plantilla_id}",
    status_code=204,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def borrar_plantilla(
    plantilla_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    await BorradorService(conn).borrar_plantilla(plantilla_id)


# -- borradores --------------------------------------------------------


@router.post(
    "/api/v1/borradores",
    response_model=BorradorResponse,
    status_code=201,
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def crear_borrador(
    payload: BorradorCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
) -> BorradorResponse:
    user_id = _user(request)
    clearance = await resolve_caller_clearance(conn, user_id)
    return await BorradorService(
        conn, request.headers.get("X-Tenant-Slug", ""), tenant_client
    ).crear(payload, user_id, max_clearance=clearance)


@router.get(
    "/api/v1/borradores",
    response_model=list[BorradorResponse],
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def listar_borradores(
    estado: str | None = Query(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[BorradorResponse]:
    return await BorradorService(conn).listar(estado)


@router.get(
    "/api/v1/borradores/{borrador_id}",
    response_model=BorradorResponse,
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def get_borrador(
    borrador_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BorradorResponse:
    return await BorradorService(conn).get(borrador_id)


@router.patch(
    "/api/v1/borradores/{borrador_id}",
    response_model=BorradorResponse,
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def editar_borrador(
    borrador_id: uuid.UUID,
    payload: BorradorUpdate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BorradorResponse:
    return await BorradorService(conn).editar(borrador_id, payload)


@router.post(
    "/api/v1/borradores/{borrador_id}/previsualizar",
    dependencies=[Depends(require_permission("PERM_RADI"))],
    responses={200: {"content": {"application/pdf": {}}}},
)
async def previsualizar_borrador(
    borrador_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
) -> Response:
    """El documento tal como saldrá, sin persistir nada (E23, RF-PRO-05).

    Los campos que no resolvieron viajan en `X-Faltantes` en vez de en el
    cuerpo: el cuerpo es el PDF. Sin esa cabecera, la pantalla enseñaría un
    documento con huecos y nadie sabría que son huecos.
    """
    clearance = await resolve_caller_clearance(conn, _user(request))
    servicio = BorradorService(conn, _slug(request), tenant_client)
    try:
        pdf, faltantes = await servicio.previsualizar(borrador_id, max_clearance=clearance)
    except RenderNoDisponibleError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "render_no_disponible",
                "detail": f"No hay renderizador de documentos configurado. ({exc})",
                "status": 503,
            },
        ) from exc
    except RenderFallidoError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "render_fallido",
                "detail": f"No se pudo generar la previsualizacion: {exc}",
                "status": 422,
            },
        ) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "X-Faltantes": ",".join(faltantes),
            "Content-Disposition": 'inline; filename="previsualizacion.pdf"',
        },
    )


@router.post(
    "/api/v1/borradores/{borrador_id}/aprobar",
    response_model=BorradorResponse,
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def aprobar_borrador(
    borrador_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BorradorResponse:
    return await BorradorService(conn).aprobar(borrador_id)


@router.post(
    "/api/v1/borradores/{borrador_id}/radicar",
    response_model=DocumentResponse,
    status_code=201,
    summary="Radicar el borrador → genera el radicado real",
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def radicar_borrador(
    borrador_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> DocumentResponse:
    try:
        doc_service = DocumentService(conn, _slug(request), tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )
    user_id = _user(request)
    clearance = await resolve_caller_clearance(conn, user_id)
    servicio = BorradorService(conn, _slug(request), tenant_client)
    try:
        return await servicio.radicar(
            borrador_id, doc_service, user_id, max_clearance=clearance
        )
    except RenderNoDisponibleError as exc:
        # 503 y NO se radica. Degradar a «radicar sin documento» dejaría a la
        # entidad creyendo que produjo un oficio que no existe, y el fallo
        # aparecería cuando el ciudadano reclame.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "render_no_disponible",
                "detail": (
                    "No hay renderizador de documentos configurado: no se radica "
                    f"para no consumir un numero sin documento. ({exc})"
                ),
                "status": 503,
            },
        ) from exc
    except RenderFallidoError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "render_fallido",
                "detail": f"No se pudo generar el documento de salida: {exc}",
                "status": 422,
            },
        ) from exc
