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

import logging
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from orpycamcp_common import audit

from app.clients import storage_client
from app.clients.storage_client import StoragePurgaError
from app.core.causales_reserva import CAUSALES
from app.core.database import get_tenant_conn
from app.core.internal_auth import require_internal_token
from app.repositories.document_repository import DocumentRepository
from app.schemas.internal import (
    AnexoInternalItem,
    AnexosInternalRequest,
    CausalReservaInternal,
    ClasificadoInternalItem,
    ClasificadosInternalResponse,
    PurgaInternalRequest,
    PurgaInternalResponse,
    RadicadoMetaItem,
)
from app.services.purga_service import PurgaService

logger = logging.getLogger("document.internal")

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


@router.get(
    "/clasificados",
    response_model=ClasificadosInternalResponse,
    summary="Radicados nivel >= 2 (causal, plazo, fecha) y catálogo de causales — content-free",
)
async def clasificados(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ClasificadosInternalResponse:
    """Acto de SISTEMA (sin gate de clearance, como `/anexos`): el índice del art. 20
    es un registro público y COMPLETO, ortogonal al clearance de lectura. Lo compone
    archive-service cruzando estas filas con el cuadro de clasificación. La consulta no
    selecciona número, asunto ni fundamento (`DocumentRepository.clasificados_internal`)."""
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DocumentRepository(conn, tenant_slug)
    return ClasificadosInternalResponse(
        causales=[
            CausalReservaInternal(
                codigo=c.codigo,
                articulo=c.articulo,
                literal=c.literal,
                descripcion=c.descripcion,
                nivel=c.nivel,
                plazo_max_meses=c.plazo_max_meses,
            )
            for c in CAUSALES.values()
        ],
        radicados=[ClasificadoInternalItem(**dict(r)) for r in await repo.clasificados_internal()],
    )


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
            canal="system",
        )
    return {"borrador_id": str(borrador_id), "estado": "aprobado", "cambiado": True}


@router.get(
    "/radicados/{ref}/huella",
    summary=(
        "SHA-256 del documento PRINCIPAL de un radicado "
        "(acto de sistema; lo consulta signature-service al abrir y al firmar una cadena)"
    ),
)
async def huella_radicado(
    ref: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """Qué sella una cadena de firma sobre un radicado: el CONTENIDO, no el nombre.

    AREA-04-09 Media-2. Antes la cadena firmaba el identificador
    `"radicado:{id}"`: una firma sobre el nombre sigue siendo válida después de
    que el documento cambie. Ahora signature-service fija esta huella al abrir
    la cadena y la relee antes de cada firma; si cambió, `409 documento_modificado`.

    `ref` es el número de radicado o su UUID. La huella es la que storage-service
    CALCULÓ al subir el fichero (no se confía en una columna de este servicio ni
    en el cliente). Sin gate de clearance —respuesta content-free, llamante de
    sistema sellado con `X-Internal-Token`—; el no-read-up lo aplica signature-service.

    Fail-closed: `404` si el radicado no existe, `409 radicado_sin_documento` si
    no tiene documento principal y `409 anexo_sin_huella` si storage no conoce el fichero (no se
    firma lo que no se puede verificar); `503 huella_no_verificable` si storage
    no responde.
    """
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    found = await DocumentRepository(conn, tenant_slug).get_documento_principal_para_firma(ref)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "radicado_not_found",
                "detail": f"Radicado {ref} no encontrado",
                "status": 404,
            },
        )
    radicado, anexo = found
    if anexo is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "radicado_sin_documento",
                "detail": (
                    "El radicado no tiene un documento principal: no hay contenido "
                    "que firmar. Un anexo de soporte no cuenta. Adjunte el documento "
                    "principal antes de abrir la cadena."
                ),
                "status": 409,
            },
        )
    try:
        huella = await storage_client.fetch_file_sha256(
            tenant_slug=tenant_slug, file_id=anexo["file_id"]
        )
    except storage_client.StorageContentError as exc:
        # El texto de la excepción puede llevar el cuerpo de storage-service y su
        # host interno: va al log, no a quien consulta.
        logger.warning("huella del radicado %s: storage no respondió: %s", ref, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "huella_no_verificable",
                "detail": "No se pudo resolver la huella del documento: storage-service no respondió.",
                "status": 503,
            },
        ) from exc
    if not huella:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "anexo_sin_huella",
                "detail": "El documento no tiene huella registrada: no se firma lo que no se verifica.",
                "status": 409,
            },
        )
    return {
        "radicado_id": str(radicado["id"]),
        "tracking_number": radicado["tracking_number"],
        "anexo_id": str(anexo["id"]),
        "file_id": str(anexo["file_id"]),
        "huella_sha256": huella.lower(),
    }
