# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints del PINAR — Plan Institucional de Archivos (RF-ADM-08, E14).

Acuerdo AGN 003/2015. Prefijo /api/v1/pinar. El api-gateway autentica el JWT y
propaga X-User-Id / X-Tenant-Slug / X-User-Roles server-side (nunca del cuerpo del
cliente); el actor imputable de la auditoría se toma de X-User-Id.

NOTA (authz): igual que catalogos/config, hoy NO hay gate por rol admin ni en el
gateway (solo mapea prefijo→upstream) ni in-service — es el TODO de RBAC del tenant
documentado en core/config.py. La spec §10.3 restringe la gestión al rol
administrador; cuando el JWT esté enriquecido (E08) debe añadirse el gate aquí o en
el gateway por-ruta. No se documenta un control que no exista.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from app.core.database import get_tenant_conn
from app.schemas.pinar import (
    AspectoCreate,
    AspectoResponse,
    AspectoUpdate,
    EjeResponse,
    InstrumentoRefCreate,
    InstrumentoRefResponse,
    ObjetivoCreate,
    ObjetivoResponse,
    ObjetivoUpdate,
    PlanAprobar,
    PlanCreate,
    PlanResponse,
    PlanUpdate,
    PriorizacionResult,
    PriorizacionSet,
    ProyectoCreate,
    ProyectoResponse,
    ProyectoUpdate,
    SeguimientoCreate,
    SeguimientoResponse,
    TableroEje,
    TableroObjetivo,
    TableroResponse,
)
from app.services.pinar_service import PinarConflict, PinarNotFound, PinarService

router = APIRouter(prefix="/api/v1/pinar", tags=["pinar"])


def _actor(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def _require_actor(request: Request) -> uuid.UUID:
    """Los actos oficiales del ciclo de vida del plan deben ser IMPUTABLES
    (spec §10.3.4). Sin X-User-Id válido → 400, fail-closed (nunca un asiento
    con actor NULL sobre un acto administrativo)."""
    actor = _actor(request)
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "actor_requerido",
                "detail": "X-User-Id ausente o inválido; el acto debe ser imputable.",
                "status": 400,
            },
        )
    return actor


def _svc(
    request: Request, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> PinarService:
    return PinarService(conn, request.headers.get("X-Tenant-Slug"))


def _not_found(exc: PinarNotFound) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": exc.detail, "status": 404},
    )


def _conflict(exc: PinarConflict) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": exc.error, "detail": exc.detail, "status": 409},
    )


# ── Ejes (catálogo) ───────────────────────────────────────────────────────────


@router.get("/ejes", response_model=list[EjeResponse])
async def list_ejes(svc: PinarService = Depends(_svc)) -> list[EjeResponse]:
    return [EjeResponse(**dict(r)) for r in await svc.list_ejes()]


# ── Plan ──────────────────────────────────────────────────────────────────────


@router.post("/planes", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: PlanCreate, request: Request, svc: PinarService = Depends(_svc)
) -> PlanResponse:
    row = await svc.create_plan(payload.model_dump(), _require_actor(request))
    return PlanResponse.model_validate(dict(row))


@router.get("/planes", response_model=list[PlanResponse])
async def list_planes(
    estado: str | None = Query(None),
    svc: PinarService = Depends(_svc),
) -> list[PlanResponse]:
    return [PlanResponse.model_validate(dict(r)) for r in await svc.list_plans(estado)]


@router.get("/planes/{plan_id}", response_model=PlanResponse)
async def get_plan(plan_id: uuid.UUID, svc: PinarService = Depends(_svc)) -> PlanResponse:
    try:
        return PlanResponse.model_validate(dict(await svc.get_plan(plan_id)))
    except PinarNotFound as exc:
        raise _not_found(exc)


@router.patch("/planes/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> PlanResponse:
    try:
        row = await svc.update_plan(
            plan_id, payload.model_dump(exclude_unset=True), _require_actor(request)
        )
        return PlanResponse.model_validate(dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.post("/planes/{plan_id}/aprobar", response_model=PlanResponse)
async def aprobar_plan(
    plan_id: uuid.UUID,
    payload: PlanAprobar,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> PlanResponse:
    try:
        row = await svc.aprobar(plan_id, payload.acto_administrativo, _require_actor(request))
        return PlanResponse.model_validate(dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.post("/planes/{plan_id}/ejecutar", response_model=PlanResponse)
async def ejecutar_plan(
    plan_id: uuid.UUID, request: Request, svc: PinarService = Depends(_svc)
) -> PlanResponse:
    try:
        row = await svc.ejecutar(plan_id, _require_actor(request))
        return PlanResponse.model_validate(dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.post("/planes/{plan_id}/cerrar", response_model=PlanResponse)
async def cerrar_plan(
    plan_id: uuid.UUID, request: Request, svc: PinarService = Depends(_svc)
) -> PlanResponse:
    try:
        row = await svc.cerrar(plan_id, _require_actor(request))
        return PlanResponse.model_validate(dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


# ── Aspectos críticos ─────────────────────────────────────────────────────────


@router.post(
    "/planes/{plan_id}/aspectos",
    response_model=AspectoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_aspecto(
    plan_id: uuid.UUID,
    payload: AspectoCreate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> AspectoResponse:
    try:
        row = await svc.create_aspecto(plan_id, payload.model_dump(), _actor(request))
        return AspectoResponse(**dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.get("/planes/{plan_id}/aspectos", response_model=list[AspectoResponse])
async def list_aspectos(
    plan_id: uuid.UUID, svc: PinarService = Depends(_svc)
) -> list[AspectoResponse]:
    try:
        return [AspectoResponse(**dict(r)) for r in await svc.list_aspectos(plan_id)]
    except PinarNotFound as exc:
        raise _not_found(exc)


@router.patch("/aspectos/{aspecto_id}", response_model=AspectoResponse)
async def update_aspecto(
    aspecto_id: uuid.UUID,
    payload: AspectoUpdate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> AspectoResponse:
    try:
        row = await svc.update_aspecto(
            aspecto_id, payload.model_dump(exclude_unset=True), _actor(request)
        )
        return AspectoResponse(**dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.delete("/aspectos/{aspecto_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aspecto(
    aspecto_id: uuid.UUID, request: Request, svc: PinarService = Depends(_svc)
) -> None:
    try:
        await svc.delete_aspecto(aspecto_id, _actor(request))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


# ── Priorización ──────────────────────────────────────────────────────────────


@router.put("/planes/{plan_id}/priorizacion", status_code=status.HTTP_204_NO_CONTENT)
async def set_priorizacion(
    plan_id: uuid.UUID,
    request: Request,
    matriz: list[PriorizacionSet] = Body(...),
    svc: PinarService = Depends(_svc),
) -> None:
    filas = [
        (fila.aspecto_id, [(c.eje_code, c.impacto) for c in fila.celdas]) for fila in matriz
    ]
    try:
        await svc.set_priorizacion_plan(plan_id, filas, _actor(request))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.get("/planes/{plan_id}/priorizacion", response_model=list[PriorizacionResult])
async def get_priorizacion(
    plan_id: uuid.UUID, svc: PinarService = Depends(_svc)
) -> list[PriorizacionResult]:
    try:
        rows = await svc.priorizacion_del_plan(plan_id)
    except PinarNotFound as exc:
        raise _not_found(exc)
    import json

    result = []
    for r in rows:
        impactos_raw = r["impactos"]
        impactos = json.loads(impactos_raw) if isinstance(impactos_raw, str) else impactos_raw
        result.append(
            PriorizacionResult(
                aspecto_id=r["aspecto_id"],
                descripcion=r["descripcion"],
                prioridad=r["prioridad"],
                impactos={int(k): int(v) for k, v in dict(impactos).items()},
            )
        )
    return result


# ── Objetivos ─────────────────────────────────────────────────────────────────


@router.post(
    "/planes/{plan_id}/objetivos",
    response_model=ObjetivoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_objetivo(
    plan_id: uuid.UUID,
    payload: ObjetivoCreate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> ObjetivoResponse:
    try:
        row = await svc.create_objetivo(plan_id, payload.model_dump(), _actor(request))
        return ObjetivoResponse(**dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.get("/planes/{plan_id}/objetivos", response_model=list[ObjetivoResponse])
async def list_objetivos(
    plan_id: uuid.UUID, svc: PinarService = Depends(_svc)
) -> list[ObjetivoResponse]:
    try:
        return [ObjetivoResponse(**dict(r)) for r in await svc.list_objetivos(plan_id)]
    except PinarNotFound as exc:
        raise _not_found(exc)


@router.patch("/objetivos/{objetivo_id}", response_model=ObjetivoResponse)
async def update_objetivo(
    objetivo_id: uuid.UUID,
    payload: ObjetivoUpdate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> ObjetivoResponse:
    try:
        row = await svc.update_objetivo(
            objetivo_id, payload.model_dump(exclude_unset=True), _actor(request)
        )
        return ObjetivoResponse(**dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.delete("/objetivos/{objetivo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_objetivo(
    objetivo_id: uuid.UUID, request: Request, svc: PinarService = Depends(_svc)
) -> None:
    try:
        await svc.delete_objetivo(objetivo_id, _actor(request))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


# ── Proyectos ─────────────────────────────────────────────────────────────────


@router.post(
    "/planes/{plan_id}/proyectos",
    response_model=ProyectoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_proyecto(
    plan_id: uuid.UUID,
    payload: ProyectoCreate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> ProyectoResponse:
    try:
        row = await svc.create_proyecto(plan_id, payload.model_dump(), _actor(request))
        return ProyectoResponse.model_validate(dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.get("/planes/{plan_id}/proyectos", response_model=list[ProyectoResponse])
async def list_proyectos(
    plan_id: uuid.UUID, svc: PinarService = Depends(_svc)
) -> list[ProyectoResponse]:
    try:
        return [ProyectoResponse.model_validate(dict(r)) for r in await svc.list_proyectos(plan_id)]
    except PinarNotFound as exc:
        raise _not_found(exc)


@router.patch("/proyectos/{proyecto_id}", response_model=ProyectoResponse)
async def update_proyecto(
    proyecto_id: uuid.UUID,
    payload: ProyectoUpdate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> ProyectoResponse:
    try:
        row = await svc.update_proyecto(
            proyecto_id, payload.model_dump(exclude_unset=True), _actor(request)
        )
        return ProyectoResponse.model_validate(dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.delete("/proyectos/{proyecto_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proyecto(
    proyecto_id: uuid.UUID, request: Request, svc: PinarService = Depends(_svc)
) -> None:
    try:
        await svc.delete_proyecto(proyecto_id, _actor(request))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.put("/proyectos/{proyecto_id}/aspectos", response_model=list[uuid.UUID])
async def set_proyecto_aspectos(
    proyecto_id: uuid.UUID,
    request: Request,
    aspecto_ids: list[uuid.UUID] = Body(...),
    svc: PinarService = Depends(_svc),
) -> list[uuid.UUID]:
    try:
        return await svc.set_proyecto_aspectos(proyecto_id, aspecto_ids, _actor(request))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


# ── Seguimiento (append-only) ─────────────────────────────────────────────────


@router.post(
    "/proyectos/{proyecto_id}/seguimiento",
    response_model=SeguimientoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_seguimiento(
    proyecto_id: uuid.UUID,
    payload: SeguimientoCreate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> SeguimientoResponse:
    try:
        row = await svc.add_seguimiento(proyecto_id, payload.model_dump(), _actor(request))
        return SeguimientoResponse.model_validate(dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.get("/proyectos/{proyecto_id}/seguimiento", response_model=list[SeguimientoResponse])
async def list_seguimiento(
    proyecto_id: uuid.UUID, svc: PinarService = Depends(_svc)
) -> list[SeguimientoResponse]:
    try:
        return [
            SeguimientoResponse.model_validate(dict(r))
            for r in await svc.list_seguimiento(proyecto_id)
        ]
    except PinarNotFound as exc:
        raise _not_found(exc)


# ── Articulación con otros instrumentos ───────────────────────────────────────


@router.post(
    "/planes/{plan_id}/instrumentos",
    response_model=InstrumentoRefResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_instrumento(
    plan_id: uuid.UUID,
    payload: InstrumentoRefCreate,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> InstrumentoRefResponse:
    try:
        row = await svc.create_instrumento(plan_id, payload.model_dump(), _actor(request))
        return InstrumentoRefResponse(**dict(row))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


@router.get("/planes/{plan_id}/instrumentos", response_model=list[InstrumentoRefResponse])
async def list_instrumentos(
    plan_id: uuid.UUID, svc: PinarService = Depends(_svc)
) -> list[InstrumentoRefResponse]:
    try:
        return [
            InstrumentoRefResponse(**dict(r)) for r in await svc.list_instrumentos(plan_id)
        ]
    except PinarNotFound as exc:
        raise _not_found(exc)


@router.delete(
    "/planes/{plan_id}/instrumentos/{instrumento_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_instrumento(
    plan_id: uuid.UUID,
    instrumento_id: uuid.UUID,
    request: Request,
    svc: PinarService = Depends(_svc),
) -> None:
    try:
        await svc.delete_instrumento(plan_id, instrumento_id, _actor(request))
    except PinarNotFound as exc:
        raise _not_found(exc)
    except PinarConflict as exc:
        raise _conflict(exc)


# ── Tableros / mapa de ruta ───────────────────────────────────────────────────


@router.get("/planes/{plan_id}/tablero", response_model=TableroResponse)
async def get_tablero(plan_id: uuid.UUID, svc: PinarService = Depends(_svc)) -> TableroResponse:
    try:
        data = await svc.tablero(plan_id)
    except PinarNotFound as exc:
        raise _not_found(exc)
    return TableroResponse(
        plan_id=data["plan_id"],
        por_objetivo=[TableroObjetivo(**dict(r)) for r in data["por_objetivo"]],
        por_eje=[TableroEje(**dict(r)) for r in data["por_eje"]],
    )


@router.get("/planes/{plan_id}/mapa-ruta", response_model=list[ProyectoResponse])
async def get_mapa_ruta(
    plan_id: uuid.UUID, svc: PinarService = Depends(_svc)
) -> list[ProyectoResponse]:
    try:
        return [ProyectoResponse.model_validate(dict(r)) for r in await svc.mapa_ruta(plan_id)]
    except PinarNotFound as exc:
        raise _not_found(exc)
