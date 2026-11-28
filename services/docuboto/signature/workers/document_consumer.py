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

from orpycamcp_common import events
from orpycamcp_common.database import acquire_tenant

from docuboto.config import signature as settings
from docuboto.core import bus
from signature.core.constants import DLQ_GROUP
from signature.jobs import deadletter_retention
from signature.repositories.cadena_repository import CadenaRepository
from signature.repositories.dead_letter_repository import DeadLetterRepository
from signature.services import invalidacion

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
# Sourced from signature.core.constants (single source of truth shared with
# app/jobs/deadletter_retention.py and signature/routers/admin_deadletter.py) — see
# the docstring there for why this can't just be a local literal.
GROUP = DLQ_GROUP
_SERVICE = "signature-service"

#: Cadencia del mantenimiento diario (retención de dead-letters terminales).
DLQ_RETENTION_INTERVAL: float = float(settings.retention_interval_seconds)


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
    (admin_deadletter) — el replay marca ``replayed=True`` y atribuye el acto al
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


async def _persist_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Durable persistence callback for dead-lettered events (R-4, ADR-021).

    Writes the dead-letter event atomically to ``evento_dead_letter`` (Postgres)
    and ``audit_log`` **inside a single transaction**.  The callback is passed to
    ``reclaim_stale`` as ``on_dead_letter``; under the new R-4 contract the helper
    gates the confirmation on this callback's success: if this function raises, the
    delivery stays pending for the next sweep — there is no dead-letter without a durable
    trace.

    Edge case — permanently malformed event (no ``tenant_slug``):
        Cannot be written to any tenant schema; retrying forever would create an
        infinite loop.  We log the error and **return normally** so the helper can
        confirm the delivery (consuming the poison pill).  The error log is the only
        operational record of this degenerate case; the event itself stays in
        ``eventos`` until the purge.

    Idempotency:
        ``INSERT … ON CONFLICT (origin_group, event_id) DO NOTHING RETURNING event_id`` — a
        second call with the same ``event_id`` returns None (no row), and the
        ``audit_log`` write is skipped.  This prevents a double-trace if the insert
        succeeded but the confirmation failed (at-least-once redelivery).

    Transient errors (DB down, pool exhausted, etc.):
        Exceptions are **not caught** here.  They propagate to the helper which
        logs a warning and does not confirm — the delivery stays pending and
        will be retried on the next reclaim sweep.  This is what closes R-4.

    R-9: ``actor`` is ``"system"`` — the worker performs this act, not the
    original user.  The original actor is preserved in
    ``payload["envelope"]["actor"]`` for forensic reconstruction.

    R-5/R-6: ``payload`` in ``audit_log`` carries the complete original envelope
    (including its business payload) so the immutable Postgres row is self-
    sufficient (Ley 594/2000 art. 4 and art. 19).
    """
    import json

    event_id: str | None = envelope.get("event_id")
    tenant_slug: str | None = envelope.get("tenant_slug")

    if not tenant_slug:
        # Permanently malformed: no tenant schema to write to.
        # Log and return so the helper can confirm the poison pill — retrying
        # an event without a tenant_slug would loop forever.
        logger.error(
            "Cannot persist dead-letter: envelope missing tenant_slug "
            "(event_id=%s) — consuming poison pill",
            event_id,
        )
        return

    # Extract business identifiers defensively; may be absent in malformed events.
    evt_payload: dict = envelope.get("payload") or {}
    tracking_number: str | None = evt_payload.get("tracking_number")
    doc_id: str | None = evt_payload.get("id")

    # Self-sufficient audit payload: failure + complete original envelope
    # + business identifiers for fast querying without JSON traversal.
    audit_payload = {
        "failure": failure_meta,
        "envelope": {
            "event_id": event_id,
            "event_type": envelope.get("event_type"),
            "correlation_id": envelope.get("correlation_id"),
            "tenant_slug": tenant_slug,
            "occurred_at": envelope.get("occurred_at"),
            "actor": envelope.get("actor"),  # original actor preserved (R-9)
            "payload": evt_payload,
        },
        "business": {
            "tracking_number": tracking_number,
            "doc_id": doc_id,
        },
    }

    # Transient errors propagate — do NOT catch here (R-4 contract).
    async with acquire_tenant(tenant_slug) as conn:
        repo = DeadLetterRepository(conn)
        async with conn.transaction():
            row = await repo.insert_if_new(
                event_id=event_id or "",
                event_type=envelope.get("event_type"),
                correlation_id=envelope.get("correlation_id"),
                tenant_slug=tenant_slug,
                # Serialize the whole envelope as the canonical payload record.
                payload_json=json.dumps(audit_payload["envelope"], default=str),
                failure_json=json.dumps(failure_meta, default=str),
                origin_stream=failure_meta.get("dead_letter_origin_stream"),
                origin_group=failure_meta.get("dead_letter_group"),
            )
            if row is not None:
                # INSERT was real — write the immutable audit trace in the same tx.
                if _AUDIT_AVAILABLE:
                    await _audit.append(
                        conn,
                        tenant_slug=tenant_slug,
                        service=_SERVICE,
                        action="evento.dead_letter",
                        object_type="evento",
                        object_ref=event_id,
                        actor="system",   # R-9: worker-level system act
                        canal="system",
                        payload=audit_payload,
                    )
                logger.info(
                    "Dead-letter persisted: event_id=%s tenant=%s type=%s",
                    event_id,
                    tenant_slug,
                    envelope.get("event_type"),
                )
            else:
                # ON CONFLICT — already durably stored; idempotent no-op.
                logger.debug(
                    "Dead-letter already persisted (idempotent): event_id=%s tenant=%s",
                    event_id,
                    tenant_slug,
                )


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
        periodico=deadletter_retention.run_once,
        intervalo_periodico=DLQ_RETENTION_INTERVAL,
    )
