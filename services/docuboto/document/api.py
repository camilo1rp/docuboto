# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que otros dominios piden a document. Importan este módulo, no los repositorios.

`mover_flujo` is self-contained so the split workflow image can copy just this
file. The process binds `abrir_flujo_si_hay` in the unified lifespan; document
tests and the split document image leave it unbound and keep the Redis consumer.

Las lecturas de sistema (inventario de anexos, clasificados, huellas, registro
para la pasarela) eran rutas `/api/v1/internal/documentos/*` selladas con token
interno. En un solo proceso son llamadas a función (ADR-047 §1): el token sobra
y lo que queda es lo que cada ruta comprobaba en la BD.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from orpycamcp_common import audit
from orpycamcp_common.database import acquire_tenant

from document.core.causales_reserva import CAUSALES
from document.core.redis_client import get_redis_or_none
from document.repositories.document_repository import DocumentRepository
from document.schemas.internal import (
    AnexoInternalItem,
    CausalReservaInternal,
    ClasificadoInternalItem,
    RadicadoMetaItem,
)
from storage import api as storage_api

logger = logging.getLogger("document.api")


class DocumentUnavailableError(Exception):
    """No se pudo leer lo pedido. El llamante nunca lo toma por «vacío»."""


class PurgeIncompleteError(Exception):
    """La purga no se completó: los documentos siguen existiendo.

    Clase propia y no `DocumentUnavailableError`: los demás fallos degradan un
    listado; este obliga a abortar el expediente sin escribir su lápida.
    """


class DocumentNotFoundError(Exception):
    """El radicado, borrador o anexo no existe."""


class DraftNotFoundError(DocumentNotFoundError):
    """El borrador no existe. Subclase: el firmante merece un mensaje más útil."""


class NothingToSealError(Exception):
    """El objeto existe pero no tiene contenido que sellar (`code` dice por qué)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@asynccontextmanager
async def _tenant_conn(tenant_slug: str) -> AsyncIterator[asyncpg.Connection]:
    """Conexión del tenant; un fallo de la BD sale como `DocumentUnavailableError`."""
    try:
        async with acquire_tenant(tenant_slug) as conn:
            yield conn
    except (asyncpg.PostgresError, OSError) as exc:
        raise DocumentUnavailableError(str(exc)) from exc

_Apertura = Callable[[asyncpg.Connection, str, dict[str, Any]], Awaitable[str | None]]
_al_radicar: _Apertura | None = None


def registrar_apertura_de_flujo(fn: _Apertura | None) -> None:
    """The composition root registers workflow here. `None` clears the hook."""
    global _al_radicar
    _al_radicar = fn


async def abrir_flujo_si_hay(
    conn: asyncpg.Connection, tenant_slug: str, payload: dict[str, Any]
) -> str | None:
    """Opens the flow on the same connection as registration, if a hook is bound."""
    if _al_radicar is None:
        return None
    return await _al_radicar(conn, tenant_slug, payload)


async def mover_flujo(
    conn: asyncpg.Connection,
    *,
    actor: str | None,
    radicado_id: uuid.UUID,
    status: str,
    flujo_dept: str | None,
    flujo_dept_code: int | None,
    flujo_usuario: uuid.UUID | None,
) -> None:
    """Writes flow status and location. The caller already holds the row lock.

    `actor` is part of the contract even when this UPDATE does not audit: the
    asiento lives in the workflow acto that called this.
    """
    del actor
    await conn.execute(
        "UPDATE radicados SET status = $2, flujo_dept = $3, flujo_dept_code = $4, "
        "flujo_usuario = $5, updated_at = now() "
        "WHERE id = $1 AND (status IS DISTINCT FROM $2 "
        "OR flujo_dept IS DISTINCT FROM $3 "
        "OR flujo_dept_code IS DISTINCT FROM $4 "
        "OR flujo_usuario IS DISTINCT FROM $5)",
        radicado_id,
        status,
        flujo_dept,
        flujo_dept_code,
        flujo_usuario,
    )


# ── Lecturas de sistema que pide archive ────────────────────────────────────


async def fetch_anexos(
    *,
    tenant_slug: str,
    radicado_ids: list[uuid.UUID],
    max_clearance: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """`file_id`/`filename` de los anexos de un lote de radicados.

    `max_clearance=None` (AIP, acto de sistema): inventario TOTAL sin gate de
    clearance; filtrar por quien cerró el expediente dejaría anexos fuera en
    silencio. Con valor (exportación, RF-SEG-08) solo salen los radicados
    visibles, con entrada aunque no tengan anexos.
    """
    if not radicado_ids:
        return {}
    async with _tenant_conn(tenant_slug) as conn:
        grouped = await DocumentRepository(conn, tenant_slug).list_anexos_for_radicados_internal(
            radicado_ids, max_clearance=max_clearance
        )
    return {
        str(radicado_id): [
            AnexoInternalItem(file_id=a.file_id, filename=a.filename).model_dump(mode="json")
            for a in anexos
        ]
        for radicado_id, anexos in grouped.items()
    }


async def fetch_radicado_metadata(
    *, tenant_slug: str, radicado_ids: list[uuid.UUID], max_clearance: int
) -> dict[str, dict[str, Any]]:
    """Metadata de los radicados VISIBLES (no-read-up) para el manifiesto."""
    if not radicado_ids:
        return {}
    async with _tenant_conn(tenant_slug) as conn:
        meta = await DocumentRepository(conn, tenant_slug).list_radicado_metadata_internal(
            radicado_ids, max_clearance=max_clearance
        )
    return {str(rid): RadicadoMetaItem(**m).model_dump(mode="json") for rid, m in meta.items()}


async def fetch_clasificados(*, tenant_slug: str) -> dict[str, Any]:
    """Radicados nivel >= 2 y catálogo de causales, para el índice del art. 20.

    Acto de SISTEMA sin gate de clearance: el índice es público y completo.
    Content-free: la consulta no trae número, asunto ni fundamento.
    """
    async with _tenant_conn(tenant_slug) as conn:
        filas = await DocumentRepository(conn, tenant_slug).clasificados_internal()
    return {
        "causales": [
            CausalReservaInternal(
                codigo=c.codigo,
                articulo=c.articulo,
                literal=c.literal,
                descripcion=c.descripcion,
                nivel=c.nivel,
                plazo_max_meses=c.plazo_max_meses,
            ).model_dump(mode="json")
            for c in CAUSALES.values()
        ],
        "radicados": [ClasificadoInternalItem(**dict(r)).model_dump(mode="json") for r in filas],
    }


async def purge_radicados(
    *,
    tenant_slug: str,
    radicado_ids: list[uuid.UUID],
    expediente_id: uuid.UUID | str,
    motivo: str,
    actor: uuid.UUID | None,
) -> dict[str, int]:
    """**Destruye** radicados y anexos (E04, RF-TRD-07 fase 5).

    Devuelve el recuento real, que es lo que la lápida afirmará. Cualquier
    fallo levanta `PurgeIncompleteError`: aquí no hay degradación posible.
    """
    # Import tardío: los servicios de document importan este módulo (el gancho de flujo).
    from document.services.purga_service import PurgaService

    if not radicado_ids:
        return {"radicados_eliminados": 0, "anexos_eliminados": 0}
    try:
        async with acquire_tenant(tenant_slug) as conn:
            return await PurgaService(conn, tenant_slug, redis=get_redis_or_none()).purgar_radicados(
                radicado_ids,
                expediente_id=str(expediente_id),
                motivo=motivo,
                actor=actor,
            )
    except (storage_api.StoragePurgeError, asyncpg.PostgresError, OSError) as exc:
        raise PurgeIncompleteError(f"No se pudieron borrar los archivos: {exc}") from exc


# ── Huellas que relee signature ─────────────────────────────────────────────


async def draft_fingerprint(*, tenant_slug: str, borrador_id: uuid.UUID | str) -> dict[str, Any]:
    """Huella VIGENTE del cuerpo y del documento de un borrador.

    La mitad síncrona de la invalidación (E23, RF-PRO-11): signature la relee
    ANTES de firmar. Content-free, sin gate de clearance.
    `DraftNotFoundError` si no existe: el llamante deniega, no lo toma por
    «no ha cambiado».
    """
    try:
        ident = uuid.UUID(str(borrador_id))
    except ValueError:
        raise DraftNotFoundError(f"Borrador {borrador_id} no encontrado") from None
    async with _tenant_conn(tenant_slug) as conn:
        row = await conn.fetchrow(
            "SELECT version, cuerpo_sha256, documento_sha256, "
            "       documento_file_id IS NOT NULL AS tiene_documento "
            "FROM borrador WHERE id = $1",
            ident,
        )
    if row is None:
        raise DraftNotFoundError(f"Borrador {borrador_id} no encontrado")
    return {
        "borrador_id": str(ident),
        "version": row["version"],
        "cuerpo_sha256": row["cuerpo_sha256"],
        "documento_sha256": row["documento_sha256"],
        "tiene_documento": row["tiene_documento"],
    }


async def approve_draft(*, tenant_slug: str, borrador_id: uuid.UUID | str) -> dict[str, Any]:
    """Aprueba un borrador porque su circuito de visto bueno se completó.

    Idempotente y solo desde `borrador`: un aviso repetido no deshace una
    radicación. La autoridad es el gate de `BorradorService.aprobar`; esto
    ahorra el paso manual. Acto de sistema, pero probatorio: cambio y asiento
    van juntos (ADR-010).
    """
    ident = uuid.UUID(str(borrador_id))
    async with _tenant_conn(tenant_slug) as conn:
        row = await conn.fetchrow("SELECT estado FROM borrador WHERE id = $1", ident)
        if row is None:
            raise DraftNotFoundError(f"Borrador {borrador_id} no encontrado")
        if row["estado"] != "borrador":
            return {"borrador_id": str(ident), "estado": row["estado"], "cambiado": False}
        async with conn.transaction():
            nuevo = await conn.fetchrow(
                "UPDATE borrador SET estado = 'aprobado', updated_at = now() "
                "WHERE id = $1 AND estado = 'borrador' RETURNING version, cuerpo_sha256",
                ident,
            )
            if nuevo is None:
                return {"borrador_id": str(ident), "estado": "aprobado", "cambiado": False}
            await audit.append(
                conn,
                tenant_slug=tenant_slug,
                service="document-service",
                action="borrador.aprobado",
                object_type="borrador",
                object_ref=str(ident),
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
    return {"borrador_id": str(ident), "estado": "aprobado", "cambiado": True}


async def radicado_fingerprint(*, tenant_slug: str, ref: str) -> dict[str, Any]:
    """SHA-256 del documento PRINCIPAL de un radicado: lo que sella una cadena.

    `ref` es el número o el UUID. La huella es la que el almacén CALCULÓ al
    subir el fichero. Fail-closed: `DocumentNotFoundError` si no existe,
    `NothingToSealError` sin documento principal o sin huella, y
    `DocumentUnavailableError` si el almacén no responde.
    """
    async with _tenant_conn(tenant_slug) as conn:
        found = await DocumentRepository(conn, tenant_slug).get_documento_principal_para_firma(ref)
    if found is None:
        raise DocumentNotFoundError(f"Radicado {ref} no encontrado")
    radicado, anexo = found
    if anexo is None:
        raise NothingToSealError(
            "radicado_sin_documento", "El radicado no tiene un documento principal"
        )
    try:
        huella = await storage_api.get_file_sha256(
            tenant_slug=tenant_slug, file_id=anexo["file_id"]
        )
    except storage_api.StorageError as exc:
        logger.warning("huella del radicado %s: el almacén no respondió: %s", ref, exc)
        raise DocumentUnavailableError("el almacén no respondió") from exc
    if not huella:
        raise NothingToSealError("anexo_sin_huella", "El documento no tiene huella registrada")
    return {
        "radicado_id": str(radicado["id"]),
        "tracking_number": radicado["tracking_number"],
        "anexo_id": str(anexo["id"]),
        "file_id": str(anexo["file_id"]),
        "huella_sha256": huella.lower(),
    }


# ── Registro y bytes que transmite la pasarela de inteligencia ──────────────

_CONTENIDO = """
-- contenido:anexo
SELECT a.file_id, r.tracking_number, r.nivel_seguridad
  FROM anexos a
  JOIN radicados r ON r.id = a.radicado_id
 WHERE a.id = $1
   AND r.status <> 'anulado'
   AND r.nivel_seguridad = ANY($2::smallint[])
"""

_REGISTRO = """
-- registro:radicado
SELECT id, tracking_number, doc_type, subject, doc_class, dest_dept_code,
       status, registered_at, due_date, sender_name, sender_entity,
       metadata, updated_at, nivel_seguridad
  FROM radicados
 WHERE id = $1
   AND status <> 'anulado'
   AND nivel_seguridad = ANY($2::smallint[])
"""

_ANEXOS = """
-- registro:anexos
SELECT a.id, a.filename, a.mime_type, a.file_size, a.es_principal, a.folios,
       f.sha256
  FROM anexos a
  JOIN files f ON f.id = a.file_id
 WHERE a.radicado_id = $1
"""


async def _niveles(conn: asyncpg.Connection, slug: str) -> list[int]:
    politica = await conn.fetchrow(
        "SELECT intelligence_levels FROM public.tenants WHERE slug = $1",
        slug,
    )
    if politica is None:
        return []
    return [int(n) for n in (politica["intelligence_levels"] or [])]


def _iso(valor: object) -> str | None:
    if valor is None:
        return None
    return valor.isoformat() if hasattr(valor, "isoformat") else str(valor)


async def _traza_transmision(
    conn: asyncpg.Connection,
    *,
    slug: str,
    numero: str,
    action: str,
    payload: dict[str, object],
) -> None:
    """Historial de acceso: el registro o los bytes salieron al proveedor.

    Best-effort: una traza que falla no retiene lo que ya se leyó.
    """
    try:
        await audit.append(
            conn,
            tenant_slug=slug,
            service="document-service",
            action=action,
            object_type="radicado",
            object_ref=numero,
            actor="system",
            canal="system",
            payload=payload,
        )
    except Exception:
        logger.warning("no se pudo dejar la traza de la transmisión", exc_info=True)


async def read_radicado_record(
    *, tenant_slug: str, radicado_id: uuid.UUID
) -> dict[str, Any] | None:
    """La fila oficial y sus anexos, si el nivel vivo sigue habilitado.

    `None` si no existe, está anulado o su nivel ya no se transmite.
    """
    async with _tenant_conn(tenant_slug) as conn:
        fila = await conn.fetchrow(_REGISTRO, radicado_id, await _niveles(conn, tenant_slug))
        if fila is None:
            return None
        anexos = await conn.fetch(_ANEXOS, radicado_id)
        await _traza_transmision(
            conn,
            slug=tenant_slug,
            numero=str(fila["tracking_number"]),
            action="document.registro_transmitido",
            payload={
                "detalle": "transmitido al proveedor de inteligencia",
                "radicado_id": str(radicado_id),
                "nivel_seguridad": int(fila["nivel_seguridad"]),
            },
        )
    return {
        "id": str(fila["id"]),
        "tracking_number": fila["tracking_number"],
        "doc_type": fila["doc_type"],
        "subject": fila["subject"],
        "doc_class": fila["doc_class"],
        "dest_dept_code": fila["dest_dept_code"],
        "status": fila["status"],
        "registered_at": _iso(fila["registered_at"]),
        "due_date": _iso(fila["due_date"]),
        "sender_name": fila["sender_name"],
        "sender_entity": fila["sender_entity"],
        "metadata": fila["metadata"] if isinstance(fila["metadata"], dict) else {},
        "updated_at": _iso(fila["updated_at"]),
        "anexos": [
            {
                "id": str(anexo["id"]),
                "filename": anexo["filename"],
                "mime_type": anexo["mime_type"],
                "file_size": anexo["file_size"],
                "es_principal": bool(anexo["es_principal"]),
                "folios": anexo["folios"],
                "sha256": anexo["sha256"],
            }
            for anexo in anexos
        ],
    }


async def read_anexo_content(
    *, tenant_slug: str, anexo_id: uuid.UUID, max_bytes: int
) -> bytes | None:
    """Los bytes de un anexo, si el nivel vivo del radicado sigue habilitado.

    `None` si no existe, está anulado o su nivel ya no se transmite. Por encima
    de `max_bytes` no hay cuerpo parcial: `DocumentUnavailableError`.
    """
    async with _tenant_conn(tenant_slug) as conn:
        fila = await conn.fetchrow(_CONTENIDO, anexo_id, await _niveles(conn, tenant_slug))
        if fila is None:
            return None
        try:
            stream = await storage_api.open_file_stream(
                tenant_slug=tenant_slug, file_id=fila["file_id"]
            )
        except storage_api.StorageError as exc:
            logger.warning("el contenido de %s no salió del almacén", anexo_id)
            raise DocumentUnavailableError("el almacén no respondió") from exc
        if stream is None:
            return None
        trozos: list[bytes] = []
        total = 0
        try:
            async for trozo in stream.aiter_bytes():
                total += len(trozo)
                if total > max_bytes:
                    raise DocumentUnavailableError("el anexo supera el tope")
                trozos.append(trozo)
        finally:
            await stream.aclose()
        await _traza_transmision(
            conn,
            slug=tenant_slug,
            numero=str(fila["tracking_number"]),
            action="document.contenido_transmitido",
            payload={
                "detalle": "transmitido al proveedor de inteligencia",
                "anexo_id": str(anexo_id),
                "nivel_seguridad": int(fila["nivel_seguridad"]),
            },
        )
    return b"".join(trozos)


async def search(
    *, tenant_slug: str, actor: uuid.UUID | None, canal: str, q: str, size: int
) -> list[dict[str, Any]]:
    """Hits de texto completo COMO el usuario: su clearance y su traza.

    La misma consulta que `GET /api/v1/search` (RBAC y no-read-up sobre el
    nivel vivo del radicado), así que quien la pide no duplica ninguna barrera.
    """
    from document.services.search_service import search_radicados

    async with _tenant_conn(tenant_slug) as conn:
        _total, hits = await search_radicados(
            conn, tenant_slug=tenant_slug, caller_id=actor, canal=canal, q=q, size=size
        )
    return [h.model_dump(mode="json") for h in hits]
