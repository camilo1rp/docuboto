# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Worker: consumes ``orpycamcp.document.events``.

Two event types are handled:

* ``document.radicado.reclassified`` — re-syncs firma_solicitud.nivel_seguridad (below).
* ``documento.contenido_cambiado`` (B10) — invalidates the circuits of a borrador
  that no longer rest on its CURRENT text hash; see ``app.services.invalidacion``.

On each ``document.radicado.reclassified`` event, updates firma_solicitud rows
whose objeto_tipo='radicado' and objeto_id matches the reclassified radicado
(by tracking_number or UUID text).  This keeps the clearance snapshot in
firma_solicitud consistent with the authoritative value in radicados without
requiring a cross-service join on every signature read.

Pattern mirrors workflow-service/workflow/workers/document_consumer.py.
"""

from __future__ import annotations

import logging
from functools import partial

from orpycamcp_common import events
from orpycamcp_common.database import acquire_tenant

from docuboto.core import bus, dead_letter
from signature.core.constants import DLQ_GROUP
from signature.repositories.cadena_repository import CadenaRepository
from signature.services import invalidacion

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
# Sourced from signature.core.constants: the same value the dead-letter console filters on.
GROUP = DLQ_GROUP
_SERVICE = "signature-service"


async def apply_reclassification(
    conn,
    tenant_slug: str,
    payload: dict,
    *,
    actor: str | None = None,
    replayed: bool = False,
) -> int:
    """Core de reclasificación (H3): re-sincroniza firma_solicitud.nivel_seguridad.

    ``conn`` debe llegar con el ``search_path`` ya fijado al tenant y, para la
    atomicidad UPDATE+audit, dentro de una transacción abierta por el caller.
    Reutilizado por el worker (``_handle``) y por el endpoint de replay
    (``replay``, la consola del dead-letter) — el replay marca ``replayed=True`` y atribuye el acto al
    admin que lo dispara (``actor``).

    Devuelve el número de filas actualizadas (0 = no-op idempotente o payload
    incompleto).
    """
    nivel_nuevo = payload.get("nivel_nuevo")
    if nivel_nuevo is None:
        return 0

    tracking_number: str | None = payload.get("tracking_number")
    doc_id: str | None = payload.get("id")

    # Build a deduplicated list of known identifiers for this radicado.
    objeto_ids = list({x for x in [tracking_number, doc_id] if x is not None})
    if not objeto_ids:
        return 0

    repo = CadenaRepository(conn)
    n = await repo.actualizar_nivel_seguridad("radicado", objeto_ids, int(nivel_nuevo))

    if n > 0 and _AUDIT_AVAILABLE:
        audit_payload: dict = {"nivel_nuevo": nivel_nuevo, "filas": n}
        if replayed:
            audit_payload["replayed"] = True
        await _audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action="firma.solicitud_reclasificada",
            object_type="radicado",
            object_ref=tracking_number,
            actor=actor,
            payload=audit_payload,
            canal="system",
        )
        logger.info(
            "Re-synced %d firma_solicitud row(s) for radicado %s → nivel %s",
            n,
            tracking_number,
            nivel_nuevo,
        )
    return n


EVENT_RECLASSIFIED = "document.radicado.reclassified"
EVENT_CONTENIDO_CAMBIADO = "documento.contenido_cambiado"
HANDLED_EVENT_TYPES = frozenset({EVENT_RECLASSIFIED, EVENT_CONTENIDO_CAMBIADO})


async def apply_contenido_cambiado(
    conn,
    tenant_slug: str,
    payload: dict,
    *,
    event_id: str | None,
    actor: str = "system",
    replayed: bool = False,
) -> int:
    """Core de ``documento.contenido_cambiado`` (B10, ítem 2).

    El evento solo es el DISPARADOR: la huella vigente se pregunta a document-service
    en este momento y se invalida lo que no recae sobre ella, así que ni el orden de
    llegada ni una reentrega cambian el resultado. ``HuellaNoVerificableError`` se
    propaga a propósito (sin confirmar → reclamación → DLQ).

    Reutilizado por el worker y por el replay de la DLQ (mismo núcleo, igual que
    ``apply_reclassification``). Devuelve las solicitudes invalidadas.
    """
    borrador_id = payload.get("borrador_id")
    if not borrador_id:
        logger.warning("Evento contenido_cambiado sin borrador_id — ignorado")
        return 0
    vigente = await invalidacion.resolver_huella_vigente(str(borrador_id), tenant_slug)
    return await invalidacion.invalidar_por_contenido(
        conn,
        tenant_slug=tenant_slug,
        objeto_id=str(borrador_id),
        vigente=vigente,
        origen="replay" if replayed else "evento",
        event_id=event_id,
        actor=actor,
    )


async def apply_event(
    conn,
    tenant_slug: str,
    event_type: str | None,
    payload: dict,
    *,
    event_id: str | None,
    actor: str | None = None,
    replayed: bool = False,
) -> int:
    """Despacha un evento al núcleo que le corresponde (worker y replay comparten esto)."""
    if event_type == EVENT_RECLASSIFIED:
        return await apply_reclassification(
            conn, tenant_slug, payload, actor=actor, replayed=replayed
        )
    if event_type == EVENT_CONTENIDO_CAMBIADO:
        return await apply_contenido_cambiado(
            conn,
            tenant_slug,
            payload,
            event_id=event_id,
            actor=actor or "system",
            replayed=replayed,
        )
    return 0


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    event_type = envelope.get("event_type")
    if event_type not in HANDLED_EVENT_TYPES:
        return

    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}

    if not tenant_slug:
        logger.warning("Evento %s sin tenant_slug — ignorado", event_type)
        return

    if event_type == EVENT_CONTENIDO_CAMBIADO:
        # Sin tx externa: la huella se pregunta por red y la invalidación abre la
        # suya (UPDATE + asiento atómicos). Un error aquí NO se traga.
        async with acquire_tenant(tenant_slug) as conn:
            await apply_contenido_cambiado(
                conn, tenant_slug, payload, event_id=envelope.get("event_id")
            )
        return

    if payload.get("nivel_nuevo") is None:
        logger.warning("Evento reclassified sin nivel_nuevo — ignorado")
        return

    if not payload.get("tracking_number") and not payload.get("id"):
        logger.warning("Evento reclassified sin tracking_number ni id — ignorado")
        return

    async with acquire_tenant(tenant_slug) as conn:
        # M-2: UPDATE and audit are atomic — both commit or both roll back.
        async with conn.transaction():
            await apply_reclassification(
                conn,
                tenant_slug,
                payload,
                actor=envelope.get("actor"),
                replayed=False,
            )


async def replay(
    conn,
    tenant_slug: str,
    *,
    event_id: str,
    event_type: str | None,
    envelope: dict,
    actor: str,
    origin_stream: str | None = None,
) -> dict:
    """Reproceso desde la consola del dead-letter, en la transacción del llamante.

    Marca ``replayed=True`` y atribuye el acto a quien lo reprocesa.
    """
    await apply_event(
        conn,
        tenant_slug,
        event_type,
        envelope.get("payload") or {},
        event_id=event_id,
        actor=actor,
        replayed=True,
    )
    return {}


#: El `on_dead_letter` del bus: la escritura única del aparcamiento (ADR-049 §4).
_persist_dead_letter = partial(dead_letter.persistir, group=GROUP, service=_SERVICE)


async def run() -> None:
    """Bucle del grupo `signature-service` sobre el bus en PostgreSQL (ADR-049).

    Confirma solo si `_handle` tuvo éxito; un fallo deja la entrega pendiente
    para la reclamación, que a la quinta la aparca con `_persist_dead_letter`.
    """
    await bus.consumir(
        group=GROUP,
        streams=(DOCUMENT_STREAM,),
        handler=_handle,
        on_dead_letter=_persist_dead_letter,
        status_name="signature",
    )
