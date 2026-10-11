# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de dependencias (organigrama) por tenant (E14).

CRUD jerárquico. E01 consume `GET /dependencias/by-codigo/{codigo}` para numerar
radicados por dependencia. Las escrituras exigen `USUA_PERM_ADMIN`
(`app/core/authz.py`); los GET quedan solo autenticados — E01 y otros
consumidores internos los necesitan para resolver dependencias por código.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core import audit as audit_core
from app.core.authz import require_permission
from app.core.database import get_tenant_conn
from app.repositories.dependencias_repository import DependenciaRepository
from app.schemas.dependencias import (
    DependenciaCreate,
    DependenciaNode,
    DependenciaResponse,
    DependenciaUpdate,
    MapeoNombreCreate,
    MapeoNombreResponse,
)

router = APIRouter(prefix="/api/v1/dependencias", tags=["dependencias"])


def _conflict(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "conflict", "detail": detail, "status": 409},
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": "No existe la dependencia", "status": 404},
    )


def _bad_parent() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": "invalid_parent", "detail": "La dependencia padre indicada no existe", "status": 400},
    )


@router.get("", response_model=list[DependenciaResponse])
async def list_dependencias(
    activo: bool | None = Query(None),
    parent_id: uuid.UUID | None = Query(None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DependenciaResponse]:
    repo = DependenciaRepository(conn)
    rows = await repo.list(activo=activo, parent_id=parent_id)
    return [DependenciaResponse(**dict(r)) for r in rows]


@router.get("/tree", response_model=list[DependenciaNode])
async def dependencias_tree(
    activo: bool | None = Query(None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DependenciaNode]:
    """Árbol jerárquico completo (raíces con sus hijos anidados)."""
    repo = DependenciaRepository(conn)
    rows = await repo.list(activo=activo)
    nodes: dict[uuid.UUID, DependenciaNode] = {
        r["id"]: DependenciaNode(**dict(r), children=[]) for r in rows
    }
    roots: list[DependenciaNode] = []
    for node in nodes.values():
        parent = nodes.get(node.parent_id) if node.parent_id else None
        if parent is not None:
            parent.children.append(node)
        else:
            # Sin padre, o el padre quedó fuera del filtro → se trata como raíz.
            roots.append(node)
    return roots


@router.get("/by-codigo/{codigo}", response_model=DependenciaResponse)
async def get_by_codigo(
    codigo: int, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> DependenciaResponse:
    repo = DependenciaRepository(conn)
    row = await repo.get_by_codigo(codigo)
    if row is None:
        raise _not_found()
    return DependenciaResponse(**dict(row))


@router.get("/{dependencia_id}", response_model=DependenciaResponse)
async def get_dependencia(
    dependencia_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> DependenciaResponse:
    repo = DependenciaRepository(conn)
    row = await repo.get(dependencia_id)
    if row is None:
        raise _not_found()
    return DependenciaResponse(**dict(row))


@router.post(
    "",
    response_model=DependenciaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def create_dependencia(
    payload: DependenciaCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DependenciaResponse:
    repo = DependenciaRepository(conn)
    try:
        async with conn.transaction():
            if await repo.codigo_retirado(payload.codigo):
                raise _codigo_retirado(payload.codigo)
            row = await repo.insert(
                codigo=payload.codigo,
                sigla=payload.sigla,
                nombre=payload.nombre,
                parent_id=payload.parent_id,
                depe_rad_tp2=payload.depe_rad_tp2,
                activo=payload.activo,
            )
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="dependencia.creada",
                object_type="dependencia",
                object_ref=str(row["id"]),
                actor=audit_core.actor_from(request),
                payload={"codigo": row["codigo"], "sigla": row["sigla"], "nombre": row["nombre"]},
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.CheckViolationError as exc:
        # Defensa en profundidad: el trigger de la 014 cubre cualquier otra vía.
        if "dependencia_codigo_retirado" in str(exc):
            raise _codigo_retirado(payload.codigo) from exc
        raise
    except asyncpg.UniqueViolationError:
        raise _conflict(f"Ya existe una dependencia con el código «{payload.codigo}»")
    except asyncpg.ForeignKeyViolationError:
        raise _bad_parent()
    return DependenciaResponse(**dict(row))


def _codigo_retirado(codigo: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "dependencia_codigo_retirado",
            "detail": (
                f"El código {codigo} perteneció a una dependencia eliminada y no se "
                "reutiliza (los trámites viejos que lo citan apuntarían a otra unidad): "
                "use un código nuevo"
            ),
            "status": 409,
        },
    )


async def _exigir_sin_tramite_vivo(repo: DependenciaRepository, codigo: int) -> None:
    """409 si pasos vivos o reglas de flujo referencian la dependencia por CÓDIGO.

    Solo guarda DESACTIVAR: la bandeja filtra `d.activo`, así que un paso vivo en
    una dependencia inactiva quedaría invisible. Renombrar ya no se bloquea: el
    flujo correlaciona por código (ADR-032) y el nombre es solo texto.
    """
    vivos = await repo.tramites_vivos(codigo)
    if vivos["pasos_vivos"] > 0 or vivos["reglas"] > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dependencia_con_tramite_en_curso",
                "detail": (
                    "La dependencia tiene trámite en curso o reglas de flujo que la "
                    "referencian: reasigne o cierre el trámite antes de desactivarla"
                ),
                "pasos_vivos": vivos["pasos_vivos"],
                "reglas": vivos["reglas"],
                "status": 409,
            },
        )


def _codigo_inmutable() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "error": "dependencia_codigo_inmutable",
            "detail": (
                "El código de una dependencia no se puede cambiar: cree una nueva "
                "y desactive esta"
            ),
            "status": 422,
        },
    )


@router.patch(
    "/{dependencia_id}",
    response_model=DependenciaResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def update_dependencia(
    dependencia_id: uuid.UUID,
    payload: DependenciaUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DependenciaResponse:
    if payload.parent_id == dependencia_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_parent",
                "detail": "Una dependencia no puede ser su propia dependencia padre",
                "status": 400,
            },
        )
    repo = DependenciaRepository(conn)
    fields = payload.model_dump(exclude_unset=True)
    # `motivo` no es columna: documenta el acto de reestructuración del renombre.
    motivo = fields.pop("motivo", None)
    try:
        async with conn.transaction():
            actual = await repo.get_for_update(dependencia_id)
            if actual is None:
                raise _not_found()
            if "codigo" in fields:
                # Mismo valor = no-op aceptado (el formulario reenvía el código);
                # otro valor se rechaza (ADR-032). Nunca llega al UPDATE.
                if fields.pop("codigo") != actual["codigo"]:
                    raise _codigo_inmutable()
            cambia_nombre = "nombre" in fields and fields["nombre"] != actual["nombre"]
            desactiva = fields.get("activo") is False and actual["activo"] is not False
            if desactiva:
                await _exigir_sin_tramite_vivo(repo, actual["codigo"])
            if cambia_nombre and await repo.mapeo_hacia_otro_codigo(
                fields["nombre"], actual["codigo"]
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "nombre_mapeado_a_otra_dependencia",
                        "detail": (
                            "Ese nombre tiene un mapeo manual (irreversible) hacia otra "
                            "dependencia: renombrar a él haría ambigua su resolución"
                        ),
                        "status": 409,
                    },
                )
            if cambia_nombre:
                # El nombre anterior queda resoluble: un evento viejo de la cola de
                # fallidos que solo lo trae sigue llegando a su dependencia.
                await repo.registrar_nombre_historico(
                    codigo=actual["codigo"],
                    nombre=actual["nombre"],
                    origen="renombre",
                    motivo=motivo,
                    registrado_por=audit_core.actor_from(request),
                )
            row = await repo.update(dependencia_id, fields=fields)
            if row is None:
                raise _not_found()
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="dependencia.actualizada",
                object_type="dependencia",
                object_ref=str(dependencia_id),
                actor=audit_core.actor_from(request),
                payload={
                    "fields": fields,
                    "motivo": motivo,
                    "codigo": actual["codigo"],
                    "antes": {"nombre": actual["nombre"], "activo": actual["activo"]},
                    "despues": {"nombre": row["nombre"], "activo": row["activo"]},
                },
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.CheckViolationError as exc:
        # Defensa en profundidad: el trigger de la 013 cubre cualquier otra vía.
        if "dependencia_codigo_inmutable" in str(exc):
            raise _codigo_inmutable() from exc
        raise
    except asyncpg.UniqueViolationError:
        raise _conflict("Ya existe un registro con ese código")
    except asyncpg.ForeignKeyViolationError:
        raise _bad_parent()
    return DependenciaResponse(**dict(row))


@router.post(
    "/{dependencia_id}/mapeos-nombre",
    response_model=MapeoNombreResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def create_mapeo_nombre(
    dependencia_id: uuid.UUID,
    payload: MapeoNombreCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> MapeoNombreResponse:
    """Decisión humana para un nombre ambiguo/huérfano del backfill (ADR-032).

    Salida operativa del RAISE de la migración 019 de workflow: el administrador
    declara que `nombre` ES esta dependencia y reejecuta la migración. Queda en
    `dependencia_nombre_historico` (solo inserción) y en `audit_log`, con actor.

    IRREVERSIBLE: el historial es de solo inserción y el mapeo pasa a ser LA
    respuesta de `resolver_codigo_dependencia` para ese nombre; no hay endpoint
    para deshacerlo. Por eso se rechaza (409) mapear a una dependencia inactiva y
    mapear el nombre vigente de OTRA dependencia activa (secuestraría su
    resolución). Si la propia destino también lleva ese nombre (homónimas) sí se
    admite: es justamente el caso que el mapeo desambigua.
    """
    repo = DependenciaRepository(conn)
    async with conn.transaction():
        actual = await repo.get_for_update(dependencia_id)
        if actual is None:
            raise _not_found()
        if not actual["activo"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "dependencia_inactiva",
                    "detail": (
                        "No se puede mapear un nombre a una dependencia inactiva: el mapeo "
                        "es irreversible y dejaría el trámite apuntando a una unidad fuera de bandeja"
                    ),
                    "status": 409,
                },
            )
        if actual["nombre"] != payload.nombre and await repo.nombre_de_otra_activa(
            payload.nombre, actual["codigo"]
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "nombre_de_otra_dependencia",
                    "detail": (
                        "Ese es el nombre vigente de otra dependencia activa: un mapeo "
                        "(irreversible) no puede redirigirlo"
                    ),
                    "status": 409,
                },
            )
        # Solo se mapean nombres HUÉRFANOS (0 candidatos) o AMBIGUOS (>1): un nombre que
        # ya resuelve limpio a otra dependencia (por nombre vigente, renombre o mapeo
        # previo) no se redirige — el mapeo es irreversible y gana a la resolución.
        resuelto, candidatos = await repo.resolucion_actual(payload.nombre)
        if candidatos == 1 and resuelto != actual["codigo"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "nombre_resuelto_a_otra_dependencia",
                    "detail": (
                        "Ese nombre ya se resuelve sin ambigüedad a otra dependencia: solo "
                        "se mapean nombres huérfanos o ambiguos"
                    ),
                    "status": 409,
                },
            )
        await repo.registrar_nombre_historico(
            codigo=actual["codigo"],
            nombre=payload.nombre,
            origen="mapeo_manual",
            motivo=payload.motivo,
            registrado_por=audit_core.actor_from(request),
        )
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="dependencia.mapeo_nombre",
            object_type="dependencia",
            object_ref=str(dependencia_id),
            actor=audit_core.actor_from(request),
            payload={"codigo": actual["codigo"], "nombre": payload.nombre, "motivo": payload.motivo},
            canal=audit_core.CANAL_PETICION,
        )
    return MapeoNombreResponse(codigo=actual["codigo"], nombre=payload.nombre, origen="mapeo_manual")


@router.delete(
    "/{dependencia_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def delete_dependencia(
    dependencia_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = DependenciaRepository(conn)
    try:
        async with conn.transaction():
            actual = await repo.get_for_update(dependencia_id)
            if actual is None:
                raise _not_found()
            # Dentro de la tx y con la fila bloqueada: un hijo creado entre la
            # comprobación y el DELETE ya no se cuela.
            if await repo.has_children(dependencia_id):
                raise _conflict(
                    "La dependencia tiene dependencias hijas: reasígnelas o elimínelas primero"
                )
            refs = await repo.referencias(actual["codigo"])
            if refs:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "dependencia_con_historia",
                        "detail": (
                            "La dependencia está citada por trámites, radicados o usuarios: "
                            "una dependencia usada no se elimina (trazabilidad, Ley 594); "
                            "desactívela"
                        ),
                        "referencias": refs,
                        "status": 409,
                    },
                )
            # El código retirado y el DELETE son UN acto: sin la huella, otra
            # dependencia podría heredar el código y los trámites viejos que lo citan.
            await repo.registrar_retirada(
                codigo=actual["codigo"],
                nombre=actual["nombre"],
                retirado_por=audit_core.actor_from(request),
            )
            deleted = await repo.delete(dependencia_id)
            if not deleted:
                raise _not_found()
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="dependencia.eliminada",
                object_type="dependencia",
                object_ref=str(dependencia_id),
                actor=audit_core.actor_from(request),
                payload={
                    "codigo": actual["codigo"],
                    "antes": {"nombre": actual["nombre"], "activo": actual["activo"]},
                },
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.ForeignKeyViolationError:
        raise _conflict("Otros registros hacen referencia a la dependencia")
