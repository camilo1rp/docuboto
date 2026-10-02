# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints internos (D-02, `X-Internal-Token`) — servicio-a-servicio
únicamente, nunca alcanzables desde el gateway (que solo enruta
`/api/v1/documents/*`, ver `api-gateway/app/routers/proxy.py`).

`POST /anexos` (E10 debt, cableado del AIP OAIS al cierre del expediente):
resuelve el `file_id` real de los anexos de un lote de radicados para que
archive-service pueda armar el payload de `POST /preservacion/aip` en
storage-service — SIN el gate de clearance que sí aplica
`GET /documents/{id}/anexos` (no-read-up, RF-SEG-08), porque este es un acto
de SISTEMA que necesita el inventario TOTAL, no lo que el usuario que cerró
el expediente vería. Único llamador legítimo: `archive-service`
(`app/clients/document_client.py`), scoped al tenant de la llamada
(`X-Tenant-Slug`, `get_tenant_conn`).
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from orpycamcp_common import audit

from app.clients.storage_client import StoragePurgaError
from app.core.database import get_tenant_conn
from app.core.internal_auth import require_internal_token
from app.repositories.document_repository import DocumentRepository
from app.schemas.internal import (
    AnexoInternalItem,
    AnexosInternalRequest,
    PurgaInternalRequest,
    PurgaInternalResponse,
    RadicadoMetaItem,
)
from app.services.purga_service import PurgaService

router = APIRouter(
    prefix="/api/v1/internal/documentos",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


@router.post(
    "/anexos",
    response_model=dict[str, list[AnexoInternalItem]],
    summary="Inventario TOTAL de anexos de un lote de radicados (acto de sistema, sin gate de clearance)",
)
async def anexos_batch(
    payload: AnexosInternalRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, list[AnexoInternalItem]]:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DocumentRepository(conn, tenant_slug)
    grouped = await repo.list_anexos_for_radicados_internal(
        payload.radicado_ids, max_clearance=payload.max_clearance
    )
    return {
        str(radicado_id): [
            AnexoInternalItem(file_id=a.file_id, filename=a.filename) for a in anexos
        ]
        for radicado_id, anexos in grouped.items()
    }


@router.post(
    "/metadata",
    response_model=dict[str, RadicadoMetaItem],
    summary="Metadata (asunto/tipo) de un lote de radicados VISIBLES — para el manifiesto de exportación",
)
async def metadata_batch(
    payload: AnexosInternalRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, RadicadoMetaItem]:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DocumentRepository(conn, tenant_slug)
    # El manifiesto es un camino de LECTURA de usuario → siempre clearance-aware
    # (no-read-up); si el llamante omite max_clearance se asume sin restricción (3).
    clearance = payload.max_clearance if payload.max_clearance is not None else 3
    meta = await repo.list_radicado_metadata_internal(payload.radicado_ids, max_clearance=clearance)
    return {str(rid): RadicadoMetaItem(**m) for rid, m in meta.items()}


@router.post(
    "/purgar",
    response_model=PurgaInternalResponse,
    summary="Purgar radicados por disposición final de TRD (acto de sistema, destructivo)",
)
async def purgar_radicados(
    payload: PurgaInternalRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> PurgaInternalResponse:
    """**Destruye.** Es el único camino de document-service que borra un
    radicado, y solo lo alcanza archive-service ejecutando un lote de
    eliminación avalado y con acta firmada (E04, RF-TRD-07 fase 5).

    Un fallo al borrar los bytes **sube como 502 y no se degrada**: significa
    que los documentos siguen existiendo, y quien orquesta tiene que abortar
    ese expediente sin escribir su lápida. Una lápida sobre bytes vivos es peor
    que no haber eliminado.
    """
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    service = PurgaService(conn, tenant_slug, redis=getattr(request.app.state, "redis", None))
    try:
        recuento = await service.purgar_radicados(
            payload.radicado_ids,
            expediente_id=payload.expediente_id,
            motivo=payload.motivo,
            actor=payload.actor,
        )
    except StoragePurgaError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "purga_incompleta",
                "detail": (
                    f"No se pudieron borrar los archivos: {exc}. No se eliminó "
                    "ningún radicado."
                ),
                "status": 502,
            },
        ) from exc
    return PurgaInternalResponse(**recuento)


@router.get(
    "/borradores/{borrador_id}/huella",
    summary=(
        "Huella VIGENTE del cuerpo y del documento de un borrador "
        "(acto de sistema; lo consulta signature-service antes de resolver un turno)"
    ),
)
async def huella_borrador(
    borrador_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """Lo que hace falta para saber si una firma sigue recayendo sobre el texto.

    **Es la mitad síncrona de la invalidación** (E23, RF-PRO-11). La otra mitad
    es un evento, y un consumidor de eventos puede retrasarse: si la garantía
    viviera solo ahí, existiría una ventana en la que alguien resuelve su turno
    sobre el texto viejo. Por eso signature-service relee esto ANTES de firmar y
    compara con el `payload_sha256` de la solicitud.

    Sin gate de clearance, y hay que decir por qué: el llamante es
    signature-service actuando como sistema, sellado con `X-Internal-Token`, y
    la respuesta es **content-free** — dos huellas, una versión y un booleano.
    No revela asunto, remitente ni cuerpo, así que no hay nada que recortar por
    nivel de seguridad. Un gate aquí obligaría a propagar la identidad del
    firmante a una consulta que no la necesita.

    `404` si el borrador no existe: signature-service lo traduce a
    «no se puede comprobar» y **deniega**, no a «no ha cambiado».
    """
    row = await conn.fetchrow(
        "SELECT version, cuerpo_sha256, documento_sha256, "
        "       documento_file_id IS NOT NULL AS tiene_documento "
        "FROM borrador WHERE id = $1",
        borrador_id,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "borrador_not_found",
                "detail": f"Borrador {borrador_id} no encontrado",
                "status": 404,
            },
        )
    return {
        "borrador_id": str(borrador_id),
        "version": row["version"],
        "cuerpo_sha256": row["cuerpo_sha256"],
        "documento_sha256": row["documento_sha256"],
        "tiene_documento": row["tiene_documento"],
    }


@router.post(
    "/borradores/{borrador_id}/aprobar",
    summary=(
        "Aprobar un borrador porque su circuito se completó "
        "(acto de sistema; lo pide signature-service al resolverse el último turno)"
    ),
)
async def aprobar_borrador_internal(
    borrador_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """Cierra el circuito en la única marca que radicar mira.

    Cuando el último turno de una cadena de visto bueno se resuelve, quien
    aprobó el borrador fue el circuito, no una persona pulsando un botón: si el
    estado se quedara en `borrador`, alguien tendría que ir a marcarlo a mano y
    el acto de aprobar volvería a estar separado del acto que lo justifica.

    **Es best-effort por diseño, y puede serlo sin abrir ninguna ventana.** Si
    esta llamada se pierde, el borrador se queda en `borrador` y el botón
    «Aprobar» de la pantalla vuelve a funcionar — porque el gate de
    `BorradorService.aprobar` consulta a signature-service el estado real y ve
    el circuito ya cerrado. La autoridad es la consulta; esto solo ahorra el
    paso manual. Lo contrario —fiarlo todo a este aviso— sería la copia rancia
    que el gate existe para no tener.

    Idempotente y sin efecto fuera del estado inicial: solo promueve desde
    `borrador`. Un borrador ya `aprobado` o ya `radicado` se queda como está,
    porque un aviso repetido no puede deshacer una radicación.
    """
    row = await conn.fetchrow("SELECT estado FROM borrador WHERE id = $1", borrador_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "borrador_not_found",
                "detail": f"Borrador {borrador_id} no encontrado",
                "status": 404,
            },
        )
    if row["estado"] != "borrador":
        return {"borrador_id": str(borrador_id), "estado": row["estado"], "cambiado": False}
    # Acto de sistema, pero acto probatorio: cambio y asiento van juntos (ADR-010).
    async with conn.transaction():
        nuevo = await conn.fetchrow(
            "UPDATE borrador SET estado = 'aprobado', updated_at = now() "
            "WHERE id = $1 AND estado = 'borrador' RETURNING version, cuerpo_sha256",
            borrador_id,
        )
        if nuevo is None:
            return {"borrador_id": str(borrador_id), "estado": "aprobado", "cambiado": False}
        await audit.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug", ""),
            service="document-service",
            action="borrador.aprobado",
            object_type="borrador",
            object_ref=str(borrador_id),
            actor=None,
            payload={
                "estado_anterior": "borrador",
                "estado_nuevo": "aprobado",
                "version": nuevo["version"],
                "cuerpo_sha256": nuevo["cuerpo_sha256"],
                "origen": "circuito_completado",
            },
        )
    return {"borrador_id": str(borrador_id), "estado": "aprobado", "cambiado": True}
