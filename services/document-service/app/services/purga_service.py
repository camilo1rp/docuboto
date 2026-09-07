# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Purga de radicados por disposición final de TRD — E04, RF-TRD-07 fase 5.

**Este módulo destruye.** Es el único de document-service que lo hace, y lo hace
solo por esta vía: un lote de eliminación avalado por el Comité, con acta
firmada, ejecutado desde archive-service. No hay endpoint público, no lo enruta
el gateway y el gate es `X-Internal-Token` (D-02).

El orden importa y no es arbitrario:

    bytes en MinIO  →  filas del radicado  →  asiento  →  evento

**Primero los bytes.** Si se borrara la fila y luego fallara MinIO, quedarían
objetos huérfanos que nadie sabe ya a qué radicado pertenecían: imposibles de
encontrar y, por tanto, imposibles de eliminar — el peor desenlace posible para
una obligación de destruir. Al revés, un fallo entre los bytes y la fila deja un
radicado sin sus anexos, que es visible, reintentable y converge.

**El asiento va en la MISMA transacción que el borrado de la fila.** Es la única
constancia que quedará de que ese radicado existió: si se escribiera aparte, un
fallo entre ambos borraría el documento sin dejar rastro de haberlo borrado.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from orpycamcp_common import audit, events

from app.clients import storage_client
from app.repositories.document_repository import DocumentRepository

logger = logging.getLogger(__name__)

_SERVICE = "document-service"
_STREAM = "orpycamcp.document.events"


class PurgaService:
    def __init__(
        self, conn: asyncpg.Connection, tenant_slug: str, redis: Any | None = None
    ) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = DocumentRepository(conn, tenant_slug)
        # Se inyecta, como en `AnulacionService`: el evento es best-effort y sin
        # Redis el borrado tiene que seguir ocurriendo igual.
        self._redis = redis

    async def purgar_radicados(
        self,
        radicado_ids: list[uuid.UUID],
        *,
        expediente_id: str,
        motivo: str,
        actor: uuid.UUID | None,
    ) -> dict[str, int]:
        """Destruye los radicados y sus anexos. Devuelve el recuento real.

        **El recuento es real, no el pedido**: es el número que la lápida va a
        afirmar para siempre, y afirmar «14 radicados eliminados» cuando fueron
        12 convierte la lápida en una declaración falsa sobre un acto que ya no
        se puede comprobar.

        Un `StoragePurgaError` **sube tal cual** y no se degrada: significa que
        los bytes siguen existiendo, y quien orquesta tiene que abortar este
        expediente sin escribir su lápida.
        """
        # Sin recorte por clearance: es un acto de sistema sobre un lote que ya
        # pasó por el aval del Comité. Recortarlo aquí dejaría vivos justo los
        # documentos clasificados que la TRD ordena destruir.
        anexos_por_radicado = await self._repo.list_anexos_for_radicados_internal(radicado_ids)
        file_ids = [
            a.file_id
            for anexos in anexos_por_radicado.values()
            for a in anexos
            if a.file_id is not None
        ]

        # Los bytes COMPARTIDOS con un radicado que no se dispone NO se destruyen.
        #
        # El almacén deduplica por huella, así que dos radicados con el mismo
        # documento apuntan a la misma fila de fichero y no hay contador de
        # referencias en ninguna parte. Sin este recorte, disponer un expediente
        # borraba el anexo de OTRO vigente que lo referenciaba: el segundo
        # quedaba con una fila que ya no resuelve y una descarga que devuelve el
        # 404 neutro, indistinguible de «no existe» y por tanto ni siquiera
        # diagnosticable.
        #
        # No altera el recuento de la lápida: lo que se cuenta son RADICADOS
        # eliminados, y esos se eliminan igual. Lo que sobrevive son unos bytes
        # que otro expediente vigente sigue necesitando, y que se irán con él
        # cuando le llegue su disposición.
        compartidos = await self._repo.file_ids_referenciados_fuera(file_ids, radicado_ids)
        if compartidos:
            logger.info(
                "Disposición: %d anexo(s) no se destruyen porque otro radicado "
                "fuera del lote los referencia.",
                len(compartidos),
            )
            file_ids = [f for f in file_ids if f not in compartidos]

        # 1. Los bytes primero (ver la cabecera del módulo).
        purgados = await storage_client.purgar_disposicion(
            tenant_slug=self._tenant_slug,
            file_ids=file_ids,
            expediente_id=expediente_id,
            motivo=motivo,
            actor=actor,
        )

        # 2. Las filas, cada una con su asiento en la MISMA transacción.
        # Los números se leen ANTES de borrar: después la fila ya no está para
        # preguntárselo, y el asiento diría que se destruyó un UUID.
        numeros = await self._repo.list_tracking_numbers_internal(radicado_ids)
        eliminados: list[tuple[uuid.UUID, str | None]] = []
        for radicado_id in radicado_ids:
            tracking = numeros.get(radicado_id)
            async with self._conn.transaction():
                borrado = await self._repo.purge_radicado(radicado_id)
                if not borrado:
                    # Ya no estaba: un reintento tras un fallo a medias. No se
                    # cuenta ni se audita dos veces.
                    continue
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="document.radicado_eliminado_trd",
                    object_type="radicado",
                    object_ref=str(radicado_id),
                    actor=str(actor) if actor else None,
                    payload={
                        # El número de radicado es lo ÚNICO que se conserva del
                        # documento, y es lo que hace la traza utilizable: sin
                        # él, el asiento diría que se destruyó un UUID.
                        "tracking_number": tracking,
                        "expediente_id": expediente_id,
                        "motivo": motivo,
                    },
                )
            eliminados.append((radicado_id, tracking))

        # 3. El evento, best-effort y DESPUÉS del commit: la purga de las copias
        #    derivadas no puede revertir una destrucción que ya ocurrió.
        for radicado_id, tracking in eliminados:
            await self._emitir_purgado(radicado_id, tracking, actor)

        return {
            "radicados_eliminados": len(eliminados),
            "anexos_eliminados": len(purgados),
        }

    async def _emitir_purgado(
        self, radicado_id: uuid.UUID, tracking_number: str | None, actor: uuid.UUID | None
    ) -> None:
        """Publica `document.radicado.purged` para que las copias derivadas se
        purguen (knowledge-service/pgvector, E21).

        Best-effort y **nunca revierte**: los bytes ya no están. Un fallo aquí
        deja una copia derivada viva, que es un problema real pero menor que
        deshacer una destrucción que ya es irreversible — y converge por
        reentrega, porque el consumidor es idempotente.
        """
        if self._redis is None or not tracking_number:
            return
        try:
            await events.emit(
                self._redis,
                _STREAM,
                "document.radicado.purged",
                tenant_slug=self._tenant_slug,
                actor=str(actor) if actor else None,
                dedup_key=f"{tracking_number}:purged",
                payload={"id": str(radicado_id), "tracking_number": tracking_number},
            )
        except Exception:  # noqa: BLE001 — evento best-effort, ver docstring.
            logger.warning(
                "No se pudo publicar la purga del radicado %s", tracking_number, exc_info=True
            )
