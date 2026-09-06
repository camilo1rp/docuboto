# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Catálogo de terceros y actores de un radicado — E01, RF-RAD-05.

Gate `PERM_RADI` en el propio `APIRouter`, **por admisión**: quien radica es
quien registra remitentes y destinatarios, y colgarlo del router cubre también
las lecturas — un catálogo de ciudadanos con nombre, documento, dirección y
teléfono no es un dato público de la entidad.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Query, Request

from app.core.clearance import require_permission
from app.core.database import get_tenant_conn
from app.schemas.tercero import (
    ActoresVincularRequest,
    ActorResponse,
    DepartamentoResponse,
    FusionRequest,
    FusionResponse,
    GrupoDuplicados,
    MunicipioResponse,
    RadicadoDeTercero,
    TerceroCreate,
    TerceroResponse,
)
from app.services.tercero_service import TerceroService

router = APIRouter(
    prefix="/api/v1/terceros",
    tags=["terceros"],
    dependencies=[Depends(require_permission("PERM_RADI"))],
)

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"


def _tenant_slug(request: Request) -> str:
    return request.headers.get(_TENANT_HEADER, "")


def _actor(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


@router.get(
    "/divipola/departamentos",
    response_model=list[DepartamentoResponse],
    summary="Departamentos DIVIPOLA",
)
async def listar_departamentos(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DepartamentoResponse]:
    """Los 33 departamentos, primer paso de la cascada geográfica.

    Cuelga del router de terceros y hereda su gate `PERM_RADI`: el catálogo es
    público en su contenido —lo publica el DANE—, pero quien lo consume aquí es
    la ventanilla, y un endpoint más sin gate es una puerta más que vigilar sin
    que nadie la necesite.
    """
    servicio = TerceroService(conn, _tenant_slug(request))
    return [DepartamentoResponse(**d) for d in await servicio.list_departamentos()]


@router.get(
    "/divipola/municipios",
    response_model=list[MunicipioResponse],
    summary="Municipios de un departamento",
)
async def listar_municipios(
    request: Request,
    departamento: str = Query(min_length=2, max_length=2, pattern=r"^[0-9]{2}$"),
    q: str | None = Query(default=None, max_length=120),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[MunicipioResponse]:
    """Los municipios de UN departamento, opcionalmente filtrados por nombre.

    El departamento es **obligatorio**: devolver los 1.135 de una vez sería la
    lista que nadie recorre, que es justo lo que la cascada evita. El filtro por
    nombre ignora acentos — quien escribe «monteria» tiene que dar con
    «Montería», por la misma razón que la búsqueda de terceros.
    """
    servicio = TerceroService(conn, _tenant_slug(request))
    return [
        MunicipioResponse(**m)
        for m in await servicio.list_municipios(departamento, texto=q)
    ]


@router.get("", response_model=list[TerceroResponse], summary="Buscar terceros")
async def buscar_terceros(
    request: Request,
    q: str | None = Query(default=None, max_length=120),
    tipo: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[TerceroResponse]:
    """Búsqueda **sin acentos ni mayúsculas** sobre nombre y entidad.

    Es la defensa que más duplicados evita, y no es la restricción de unicidad:
    los duplicados no se crean por saltarse un índice, se crean por **no
    encontrar lo que ya está**. Quien busca «Jose Perez» tiene que dar con el
    «José Pérez» que existe, o creará el trigésimo primero."""
    service = TerceroService(conn, _tenant_slug(request))
    return [TerceroResponse(**t) for t in await service.buscar(q, tipo=tipo, limit=limit)]


@router.post("", response_model=TerceroResponse, summary="Registrar un tercero")
async def crear_tercero(
    payload: TerceroCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> TerceroResponse:
    """Alta idempotente por documento de identidad.

    Si ya existe un tercero con ese documento **se devuelve ese**, con
    `creado=false`, en vez de un conflicto: quien registra quiere el tercero, y
    hacerle distinguir «ya existe» de «error» no le aporta nada. De paso cierra
    la puerta al duplicado por reintento."""
    service = TerceroService(conn, _tenant_slug(request))
    tercero, creado = await service.crear_o_recuperar(
        payload.model_dump(), actor=_actor(request)
    )
    return TerceroResponse(**tercero, creado=creado)


@router.get(
    "/radicado/{radicado_id}/actores",
    response_model=list[ActorResponse],
    summary="Actores de un radicado, con el contacto CONGELADO del envío",
)
async def list_actores(
    radicado_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[ActorResponse]:
    """Devuelve **lo que se usó**, no lo vigente en el catálogo.

    Es la diferencia entre decir a dónde se envió y decir dónde vive hoy esa
    persona; la segunda respuesta a una pregunta sobre el pasado es información
    errónea."""
    service = TerceroService(conn, _tenant_slug(request))
    return [ActorResponse(**a) for a in await service.list_actores(radicado_id)]


@router.post(
    "/radicado/{radicado_id}/actores",
    response_model=list[dict],
    summary="Vincular actores a un radicado (congela su contacto)",
    dependencies=[Depends(require_permission("PERM_RADI", min_crud=3))],
)
async def vincular_actores(
    radicado_id: uuid.UUID,
    payload: ActoresVincularRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[dict]:
    """Ata los actores **congelando los datos de contacto usados**.

    Cada entrada puede traer el contacto corregido para este envío: al radicar
    se busca a alguien y se le ajusta el correo o la dirección, porque pasa. Ese
    ajuste se congela aquí y **no toca la ficha del catálogo**.

    Repetir el mismo destinatario en la misma dirección es un **no-op**, no un
    error; el mismo destinatario en otra dirección **sí entra**, porque enviar a
    dos direcciones de la misma persona es un envío legítimo."""
    service = TerceroService(conn, _tenant_slug(request))
    return await service.vincular_actores(
        radicado_id,
        [a.model_dump() for a in payload.actores],
        actor=_actor(request),
    )


# ---------------------------------------------------------------------------
# Administración del directorio: duplicados y fusión (RF-RAD-05)
# ---------------------------------------------------------------------------
# Hasta ahora un tercero solo podía NACER —como efecto colateral de elegir un
# destinatario al radicar— y no había dónde listarlos, revisarlos ni consolidar
# los que sobraban. El Orfeo del que deriva este sistema acabó con el mismo
# señor treinta veces; la búsqueda sin tildes evita que sigan apareciendo, pero
# los que ya están necesitan una salida.
@router.get(
    "/duplicados",
    response_model=list[GrupoDuplicados],
    summary="Fichas que probablemente son la misma persona",
)
async def listar_duplicados(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[GrupoDuplicados]:
    """Agrupa por documento (certeza) y por nombre normalizado (sospecha).

    **Solo informa; no fusiona nada.** Dos fichas con el mismo nombre pueden ser
    dos personas distintas, y consolidarlas automáticamente convertiría a dos
    ciudadanos en uno sin que nadie lo hubiera decidido.
    """
    service = TerceroService(conn, _tenant_slug(request))
    return [GrupoDuplicados(**g) for g in await service.grupos_duplicados(limit=limit)]


@router.get(
    "/{tercero_id}/radicados",
    response_model=list[RadicadoDeTercero],
    summary="En qué radicados aparece una ficha",
)
async def radicados_de_tercero(
    tercero_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[RadicadoDeTercero]:
    """Lo que hace falta para decidir una fusión: quién escribió y qué se envió.

    Sin esto, elegir cuál de dos fichas sobrevive sería a ciegas.
    """
    service = TerceroService(conn, _tenant_slug(request))
    return [RadicadoDeTercero(**r) for r in await service.radicados_de(tercero_id, limit=limit)]


@router.post(
    "/{tercero_id}/fusionar",
    response_model=FusionResponse,
    summary="Consolidar un duplicado en esta ficha (IRREVERSIBLE)",
    # `crud>=3` y no el nivel de lectura por defecto: fusionar altera el
    # catálogo de la entidad y reapunta vínculos de radicados ya existentes.
    dependencies=[Depends(require_permission("PERM_RADI", min_crud=3))],
)
async def fusionar_terceros(
    tercero_id: uuid.UUID,
    payload: FusionRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FusionResponse:
    """El de la ruta SOBREVIVE; el del cuerpo se absorbe.

    Los `radicado_actor` del absorbido pasan a apuntar a la superviviente, y la
    absorbida queda marcada —no borrada— para no perder el rastro. **Las copias
    congeladas no se tocan**: siguen diciendo con qué datos se atendió cada
    envío, que es justo lo que la copia existe para conservar.

    Irreversible: no hay «deshacer fusión». La pantalla lo confirma con una
    frase tecleada antes de llamar aquí.
    """
    service = TerceroService(conn, _tenant_slug(request))
    return FusionResponse(
        **await service.fusionar(tercero_id, payload.absorbe_id, actor=_actor(request))
    )
