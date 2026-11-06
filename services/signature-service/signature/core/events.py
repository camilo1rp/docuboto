# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Publisher de eventos de firma sobre Redis Streams (E06, ADR-016, ADR-010).

signature-service ya CONSUME ``orpycamcp.document.events`` (ver
``app/workers/document_consumer.py``); este módulo es su primer PUBLISHER —
emite en ``orpycamcp.signature.events`` cuando el índice electrónico de un
expediente queda firmado con XAdES-B (sello institucional). Consumo aguas
abajo (p. ej. que notification-service avise al responsable del expediente) es
evolutivo; el Incremento 1 solo necesita la emisión, con fines de
observabilidad/auditoría distribuida (ADR-010) y para dejar el enganche listo.

Best-effort por diseño: la persistencia de la firma en `firma` (Postgres) ya
se comprometió antes de llamar aquí — un fallo de Redis nunca debe revertir ni
bloquear una firma ya persistida. El router captura cualquier excepción y solo
registra un warning (mismo patrón que ``document-service`` en
``_emit_created``).
"""

from __future__ import annotations

from typing import Any

from orpycamcp_common import events

STREAM = "orpycamcp.signature.events"


async def publish_challenge_emitido(
    redis: Any,
    *,
    tenant_slug: str,
    actor: str | None,
    challenge_id: str,
    solicitud_id: str,
    expira_at: str,
) -> None:
    """Emite ``firma.challenge.emitido`` (E06 Inc.2, RF-FIR-13, idempotente por challenge_id).

    Best-effort — igual criterio que ``publish_indice_firmado``: el challenge ya
    quedó persistido en Postgres y el correo ya se envió antes de llamar aquí; un
    fallo de Redis no debe deshacer ninguna de las dos cosas. NUNCA incluye el OTP
    ni su hash (invariante rector del Inc.2).
    """
    await events.emit(
        redis,
        STREAM,
        "firma.challenge.emitido",
        tenant_slug=tenant_slug,
        actor=actor,
        dedup_key=challenge_id,
        payload={
            "challenge_id": challenge_id,
            "solicitud_id": solicitud_id,
            "factor_tipo": "otp_email",
            "expira_at": expira_at,
        },
    )


async def publish_indice_firmado(
    redis: Any,
    *,
    tenant_slug: str,
    actor: str | None,
    firma_id: str,
    expediente_id: str | None,
    indice_id: str | None,
    version: int | None,
    minio_key_firmado: str | None,
    nivel_conformidad: str,
    xades_level: str | None,
) -> None:
    """Emite ``firma.indice.firmado`` (idempotente por ``dedup_key=firma_id``)."""
    await events.emit(
        redis,
        STREAM,
        "firma.indice.firmado",
        tenant_slug=tenant_slug,
        actor=actor,
        dedup_key=firma_id,
        payload={
            "firma_id": firma_id,
            "expediente_id": expediente_id,
            "indice_id": indice_id,
            "version": version,
            "minio_key_firmado": minio_key_firmado,
            "nivel_conformidad": nivel_conformidad,
            "xades_level": xades_level,
        },
    )


async def publish_lta_renovada(
    redis: Any,
    *,
    tenant_slug: str,
    firma_id: str,
    ordinal: int,
    minio_key: str,
    xml_sha256: str,
    gen_time: str,
) -> None:
    """Emite ``firma.lta_renovada`` (B10 §8) cuando un ``ArchiveTimeStamp`` renovado queda
    ``vigente`` (storage ya registró la herencia WORM). Idempotente por ``firma:ordinal``.

    SEÑAL INFORMATIVA, SIN CONSUMIDOR POR DISEÑO (C-6, ADR-042): se emite best-effort y sin outbox, así
    que un Redis caído la pierde para siempre; por eso nada depende de ella. archive-service concilia
    por BARRIDO —compara el XML vigente de cada firma (``POST /internal/artefactos-vigentes``) con el
    que embebe su AIP y reempaqueta la diferencia—, lo que además cubre el AIP empaquetado con el
    original por un fallo transitorio, que ningún evento avisaría. El eslabón ya está comprometido; un
    fallo de Redis no lo deshace."""
    await events.emit(
        redis,
        STREAM,
        "firma.lta_renovada",
        tenant_slug=tenant_slug,
        actor=None,
        dedup_key=f"{firma_id}:{ordinal}",
        payload={
            "firma_id": firma_id,
            "ordinal": ordinal,
            "minio_key": minio_key,
            "xml_sha256": xml_sha256,
            "gen_time": gen_time,
        },
    )
