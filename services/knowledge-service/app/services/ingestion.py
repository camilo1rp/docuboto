# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lógica de ingesta event-driven del índice de conocimiento (E21).

Invocada DIRECTAMENTE por el worker (`app/workers/document_consumer.py`) sobre la
conexión con `search_path` ya fijado al tenant — a diferencia del endpoint HTTP
`/api/v1/knowledge/ingest`, aquí no hay request ni `X-Internal-Token`: el worker ES
el servicio interno.

Diseño del arquitecto (riesgos citados en los docstrings de cada función):
  - R1: material ya enviado a un proveedor externo de embeddings no puede "des-enviarse";
    una reclasificación a Reservado/Clasificado con proveedor externo TOMBSTONEA el
    chunk (vacía texto/embedding/metadata pero conserva la fila-centinela, ver
    `KnowledgeRepository.tombstone_by_source`) en vez de solo desclasificarlo
    localmente o borrar la fila (un DELETE liso reabriría la fuga que este mismo
    módulo cierra — ver `ingest_radicado_created`).
  - R6 (CERRADO en origen, AREA-09-05, 2026-10): `document.radicado.reclassified` ya no
    es best-effort. document-service lo inserta en su OUTBOX (`eventos_salientes`) en la
    MISMA transacción que el UPDATE del nivel y el asiento, lo publica tras el commit y
    un relé del barrido reenvía lo que no salió (Redis caído). La entrega es
    at-least-once con el MISMO `event_id`/`dedup_key`. OJO: `update_nivel_by_source` fija
    el nivel EXACTO (sin GREATEST, para permitir bajas legítimas), así que un reenvío
    TARDÍO de una reclasificación ANTERIOR (A pendiente por Redis caído, B ya aplicada)
    BAJARÍA el chunk: «inocuo» solo vale para el mismo evento repetido, no para uno
    reordenado. El cierre vive en el ORIGEN (hallazgo de auditoría, 2026-10): el relé
    de document-service (`outbox._con_nivel_vivo`) publica el `nivel_nuevo` VIGENTE de
    `radicados.nivel_seguridad`, no el que llevaba la fila al encolarse. Este módulo
    NO compara `occurred_at` (red adicional NO construida), como tampoco existe aún un
    barrido de reconciliación nivel-del-chunk vs `radicados.nivel_seguridad`.

Fuga cerrada (hallazgo tenant-security-auditor, ver tombstone_by_source): un
`created` REDELIVERED tarde (at-least-once) después de un `reclassified` que subió
el nivel ya NO puede reencarnar el chunk a un nivel más bajo ni reenviar el texto a
un proveedor externo — `ingest_radicado_created` siempre lee el nivel EXISTENTE del
chunk (si lo hay) y calcula el nivel EFECTIVO = max(nivel_del_evento,
nivel_existente) ANTES de decidir si es embebible.

Residual ALTA cerrado (config stub por defecto, gemelo del anterior en la rama
NO-externa): `update_nivel_by_source` era un `UPDATE` PLANO — si `reclassified`
llegaba ANTES que el `created` original (p. ej. el `created` falló un embed
transitorio y `reclaim_stale` lo recuperó DESPUÉS), el UPDATE afectaba 0 filas, el
nivel del `reclassified` se perdía sin dejar rastro, y el `created` tardío
insertaba el chunk sub-clasificado a su nivel de nacimiento. Ahora
`update_nivel_by_source` es también un UPSERT-con-centinela (ver su docstring en
`KnowledgeRepository`): deja un centinela cuando la fila no existe, y fija el
nivel EXACTO (sin GREATEST, permite bajas legítimas) cuando sí existe.

Residual "purga-en-anulación" cerrado (hallazgo archival-compliance-auditor): un
radicado ANULADO seguía siendo consultable en el índice semántico porque el único
mecanismo de exclusión era el tombstone POR NIVEL, reversible a propósito (una
desclasificación legítima debe poder reembeberlo). La anulación es TERMINAL (Ley
594/2000 — el radicado no se des-anula), así que necesita su PROPIO marcador
permanente: `voided` (migración 006, `KnowledgeRepository.void_by_source`).
`ingest_radicado_created` ahora lee también `voided` (vía `get_chunk_state`, que
sustituye a `get_nivel_by_source` como lectura previa) y hace SKIP TOTAL si el
chunk ya está voided — bajo CUALQUIER orden de entrega respecto al evento
`document.radicado.annulled` (`void_radicado`, nuevo handler de este módulo).
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg
from orpycamcp_common import audit

from app.core.clearance import (
    CLASIFICADA,
    RESERVADA,
    is_embeddable,
    is_local_embedding_provider,
)
from app.core.config import settings
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embeddings import embed, to_pgvector

logger = logging.getLogger(__name__)

_SERVICE = "knowledge-service"
_SOURCE_TYPE = "radicado"


async def ingest_radicado_created(
    conn: asyncpg.Connection, *, tenant_slug: str, payload: dict
) -> None:
    """Ingiere un `document.radicado.created` (propagación de ACL vía evento).

    El nivel de seguridad viaja en el propio payload del evento (document-service
    lo añade en `_emit_created`) — nunca se hace fetch-back HTTP al radicado, tanto
    por costo como porque el nivel en el momento del evento es la fuente de verdad
    correcta (una reclasificación posterior llega como su propio evento).

    FAIL-CLOSED: si `nivel_seguridad` está ausente del payload (evento de una
    versión anterior de document-service, o payload corrupto), se asume el nivel
    máximo (CLASIFICADA) — ante la duda, el material NO se embebe con un proveedor
    externo y queda en el nivel más restrictivo.

    Nivel EFECTIVO (cierre de fuga, ver docstring del módulo): el nivel del propio
    evento NO es, por sí solo, suficiente para decidir si el material es embebible.
    Un `created` reprocesado TARDE (redelivery at-least-once) puede traer un nivel
    más BAJO que el que un `reclassified` posterior ya aplicó al chunk (el evento
    `created` original no conoce esa reclasificación futura). Por eso, ANTES de
    embeber, se lee el estado actual del chunk (si existe,
    `KnowledgeRepository.get_chunk_state`) y se calcula
    `nivel_efectivo = max(nivel_del_evento, nivel_existente)`: es ese nivel
    EFECTIVO — no el crudo del evento — el que decide si se embebe (D-02) y el que
    se persiste. El `search_path` ya fija el tenant y el worker procesa
    secuencialmente dentro de su consumer-group (sin concurrencia intra-tenant),
    así que este read-then-write es seguro.

    PURGA PERMANENTE por anulación (residual "purga-en-anulación", ver docstring
    del módulo): si el chunk ya existe y está `voided` (radicado ANULADO), este
    `created` — llegue en el orden que llegue, incluso un redelivery muy tardío —
    hace SKIP TOTAL: no embebe, no des-voidea, no toca el chunk. La anulación es
    TERMINAL, así que este guard no admite excepciones (a diferencia del guard de
    nivel, que sí puede reembeber material tras una desclasificación).
    """
    tracking_number = payload.get("tracking_number")
    if not isinstance(tracking_number, str) or not tracking_number:
        # Fail-closed: `payload.get` devuelve `None` si el evento llega sin
        # numero de radicado, y ese `None` viajaba hasta el repositorio como
        # `source_ref`. Sin identificador no hay fragmento al que aplicar esto
        # —ni para embeber, ni para reclasificar, ni para anular— asi que se
        # descarta el evento con traza en vez de tocar la tabla a ciegas.
        logger.warning("document.radicado.created sin tracking_number en el payload — evento descartado")
        return
    nivel = payload.get("nivel_seguridad")
    if nivel is None:
        logger.warning(
            "document.radicado.created sin nivel_seguridad (tracking_number=%s) — "
            "fail-closed a CLASIFICADA (%d)",
            tracking_number,
            CLASIFICADA,
        )
        nivel = CLASIFICADA

    repo = KnowledgeRepository(conn)
    state = await repo.get_chunk_state(_SOURCE_TYPE, tracking_number)

    if state is not None and state["voided"]:
        # El radicado fue ANULADO (evento `document.radicado.annulled` ya
        # procesado, o el centinela voided fue dejado por un `annulled` que llegó
        # ANTES que este `created`). El estado `anulado` es TERMINAL — no hay
        # "nivel efectivo" que calcular ni material que embeber: SKIP incondicional.
        await audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action="knowledge.ingest_skipped_voided",
            object_type=_SOURCE_TYPE,
            object_ref=tracking_number,
            actor="system",
            canal="system",
            payload={
                "nivel_seguridad": nivel,
                "motivo": "radicado_anulado_purga_permanente",
            },
        )
        return

    nivel_existente = state["nivel_seguridad"] if state is not None else None
    nivel_efectivo = max(nivel, nivel_existente) if nivel_existente is not None else nivel

    if not is_embeddable(nivel_efectivo, settings.embedding_provider):
        # No es un fallo del pipeline: es la política D-02/RF-SEG-08 aplicándose.
        # No se embebe (ni se reenvía al proveedor externo); se asegura/refresca el
        # tombstone al nivel EFECTIVO (cubre tanto la primera ingesta de un
        # radicado ya clasificado como un `created` tardío tras un `reclassified`
        # que subió el nivel — ver docstring del módulo), se deja traza, y el
        # worker hace XACK (no DLQ).
        await repo.tombstone_by_source(_SOURCE_TYPE, tracking_number, nivel_efectivo)
        await audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action="knowledge.ingest_skipped_classified",
            object_type=_SOURCE_TYPE,
            object_ref=tracking_number,
            actor="system",
            canal="system",
            payload={
                "nivel_seguridad": nivel,
                "nivel_efectivo": nivel_efectivo,
                "embedding_provider": settings.embedding_provider,
                "motivo": "material_reservado_o_clasificado_proveedor_externo",
            },
        )
        return

    texto = "\n".join(
        v
        for v in (
            payload.get("subject"),
            payload.get("sender_name"),
            payload.get("sender_entity"),
        )
        if v
    )
    # `asyncio.to_thread`: con `local_st`/`ollama` la llamada es CPU/red-bloqueante
    # (inferencia del modelo o HTTP síncrono, ver `embeddings.py`); el worker
    # procesa secuencialmente pero comparte el loop con el resto del servicio
    # (endpoints HTTP) — no debe bloquearlo. Con `stub` es puro overhead
    # despreciable (hash determinista).
    literal = to_pgvector(await asyncio.to_thread(embed, texto))
    await repo.upsert_chunk(
        source_type=_SOURCE_TYPE,
        source_ref=tracking_number,
        texto=texto,
        embedding_literal=literal,
        metadata={
            "id": payload.get("id"),
            "tracking_number": tracking_number,
            "doc_type": payload.get("doc_type"),
            "doc_class": payload.get("doc_class"),
            "dest_dept": payload.get("dest_dept"),
            "dest_dept_code": payload.get("dest_dept_code"),
            "origin_dept": payload.get("origin_dept"),
        },
        acl={},
        nivel_seguridad=nivel_efectivo,
    )


async def apply_reclassification(
    conn: asyncpg.Connection, *, tenant_slug: str, payload: dict
) -> None:
    """Aplica un `document.radicado.reclassified` al chunk indexado (si existe).

    Idempotente: reprocesar el mismo evento reclassified repite la misma
    UPSERT/UPDATE sin efecto adicional.

    R1: si el nuevo nivel es Reservado/Clasificado Y el proveedor de embeddings es
    externo, el chunk se TOMBSTONEA (`KnowledgeRepository.tombstone_by_source`):
    se vacía texto/embedding/metadata (no basta con marcarlo — el material ya pudo
    salir hacia el proveedor externo en la ingesta original, y no debe permanecer
    expuesto a un re-embed futuro con ese mismo proveedor) pero la fila-centinela
    se CONSERVA a propósito — un DELETE liso anularía el candado GREATEST de
    `upsert_chunk`/`tombstone_by_source` y permitiría que un `created` REDELIVERED
    tarde reencarnara el chunk a un nivel inferior (ver docstring del módulo y de
    `tombstone_by_source`). En cualquier otro caso (baja de nivel, o proveedor
    local — `is_local_embedding_provider`, E21 Inc.1) se fija el nivel exacto con
    `update_nivel_by_source`, que desde el cierre del residual ALTA es también un
    UPSERT-con-centinela: cubre el mismo reordenamiento inverso que
    `tombstone_by_source` (un `reclassified` que llega ANTES que el `created`
    original ya no pierde el nivel por un UPDATE de 0 filas — ver su docstring).

    FAIL-CLOSED ante `nivel_nuevo` ausente: a diferencia de `created` (que ya era
    fail-closed a CLASIFICADA), la versión anterior de esta función leía
    `payload["nivel_nuevo"]` directo — un `KeyError` que sí escalaba a DLQ, pero
    mientras tanto el chunk seguía expuesto en su nivel viejo (más bajo), una
    ventana de fuga temporal asimétrica con `created`. Ahora, si `nivel_nuevo` está
    ausente, se asume el nivel máximo (CLASIFICADA) — el nivel de un chunk NUNCA
    debe bajar salvo por un `reclassified` explícito con `nivel_nuevo` presente.
    Ese fail-close deja además un asiento best-effort en `audit_log`
    (`knowledge.reclassify_malformed_failclose`) — un evento de reclasificación
    malformado que se fuerza al nivel máximo es un evento de seguridad relevante,
    no solo una línea de log de aplicación. Requiere `tenant_slug` (recibido del
    worker, que ya lo lee del sobre — igual que `ingest_radicado_created`).
    """
    tracking_number = payload.get("tracking_number")
    if not isinstance(tracking_number, str) or not tracking_number:
        # Fail-closed: `payload.get` devuelve `None` si el evento llega sin
        # numero de radicado, y ese `None` viajaba hasta el repositorio como
        # `source_ref`. Sin identificador no hay fragmento al que aplicar esto
        # —ni para embeber, ni para reclasificar, ni para anular— asi que se
        # descarta el evento con traza en vez de tocar la tabla a ciegas.
        logger.warning("document.radicado.reclassified sin tracking_number en el payload — evento descartado")
        return
    nuevo = payload.get("nivel_nuevo")
    if nuevo is None:
        logger.warning(
            "document.radicado.reclassified sin nivel_nuevo (tracking_number=%s) — "
            "fail-closed a CLASIFICADA (%d)",
            tracking_number,
            CLASIFICADA,
        )
        nuevo = CLASIFICADA
        try:
            await audit.append(
                conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action="knowledge.reclassify_malformed_failclose",
                object_type=_SOURCE_TYPE,
                object_ref=tracking_number,
                actor="system",
                canal="system",
                payload={
                    "motivo": "nivel_nuevo_ausente_en_evento_reclassified",
                    "nivel_aplicado": CLASIFICADA,
                },
            )
        except Exception:
            # Best-effort: un fallo al auditar no debe romper la aplicación del
            # fail-close (que sí es de seguridad y debe seguir su curso).
            logger.warning(
                "Fallo dejando traza de reclassified malformado (best-effort)",
                exc_info=True,
            )

    repo = KnowledgeRepository(conn)
    if nuevo >= RESERVADA and not is_local_embedding_provider(settings.embedding_provider):
        await repo.tombstone_by_source(_SOURCE_TYPE, tracking_number, nuevo)
    else:
        await repo.update_nivel_by_source(_SOURCE_TYPE, tracking_number, nuevo)


async def void_radicado(conn: asyncpg.Connection, *, tenant_slug: str, payload: dict) -> None:
    """Aplica un `document.radicado.annulled` — y, desde E04/RF-TRD-07 fase 5, un
    `document.radicado.purged`: purga PERMANENTE del chunk indexado.

    **Por qué la ELIMINACIÓN por TRD no usa `delete_by_source` aunque el
    documento ya no exista.** Borrar la fila parecía lo correcto —el radicado se
    destruyó, no queda nada que representar— y es justo lo contrario: bajo
    entrega *at-least-once*, un `created` reentregado DESPUÉS del DELETE
    reinsertaría el fragmento desde cero, sin marcador, y el texto del documento
    eliminado volvería a ser consultable. El `DELETE` borraría el propio
    centinela que hace la purga irreversible. Así que se reutiliza el centinela
    `voided=true`, que **no conserva texto, ni embedding, ni metadatos**: solo el
    número de radicado, que es lo que hace que la purga se sostenga.

    A diferencia de `apply_reclassification` (que puede subir Y bajar el nivel de
    un chunk, porque una reclasificación siempre es potencialmente reversible), la
    anulación de un radicado es TERMINAL (Ley 594/2000 — el radicado no se
    des-anula): `void_by_source` marca `voided = true` de forma permanente, y
    `ingest_radicado_created` respeta ese marcador incondicionalmente (SKIP total,
    ver su docstring) sin importar el orden de entrega respecto a este evento
    (idempotente: reprocesar el mismo `annulled` repite la misma UPSERT sin efecto
    adicional).

    El payload del evento es mínimo (`{id, tracking_number}`, ver
    `document_service.py::AnulacionService._emit_annulled`) — no hay `nivel` que
    leer aquí; el fail-safe al nivel máximo vive en `void_by_source`.
    """
    tracking_number = payload.get("tracking_number")
    if not isinstance(tracking_number, str) or not tracking_number:
        # Fail-closed: `payload.get` devuelve `None` si el evento llega sin
        # numero de radicado, y ese `None` viajaba hasta el repositorio como
        # `source_ref`. Sin identificador no hay fragmento al que aplicar esto
        # —ni para embeber, ni para reclasificar, ni para anular— asi que se
        # descarta el evento con traza en vez de tocar la tabla a ciegas.
        logger.warning("document.radicado.annulled sin tracking_number en el payload — evento descartado")
        return
    repo = KnowledgeRepository(conn)
    await repo.void_by_source(_SOURCE_TYPE, tracking_number)
