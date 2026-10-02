# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Purga de los binarios que una importación fallida dejó sin radicado (AREA-09-14).

Mecanismo (declarado): **cola durable + barrido**.

1. Al revertirse una importación (o al omitirse un ítem por carrera), los `file_id`
   subidos entran en `import_binario_huerfano` ('pendiente') en la MISMA transacción
   que el asiento `document.import_binarios_sin_radicado`.
2. Se intenta purgar de inmediato (`purgar(import_uuid=...)`). Storage decide: borra
   solo lo que creó esa importación y nadie referencia (storage deduplica por
   SHA-256; ver `storage-service` `purge_orphan`).
3. Si storage no responde (o responde `error` para un archivo), la fila queda
   'pendiente' con su motivo y el barrido periódico (`app/services/barrido.py`) la
   reintenta, hasta `purga_huerfanos_max_intentos`; agotado → 'agotado' para revisión.

Cada transición terminal escribe `document.import_binarios_purgados` (content-free:
file_id y resultado) en la misma transacción que el UPDATE, y solo por las filas que
de verdad transitaron.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from orpycamcp_common import audit

from app.clients import storage_client
from app.core.config import settings
from app.repositories.import_repository import ImportRepository

logger = logging.getLogger(__name__)
_SERVICE = "document-service"

_LIMITE_POR_PASADA = 500

# resultado de storage → estado terminal de la cola. `error` no está: se reintenta.
_ESTADO_TERMINAL = {
    "purgado": "purgado",
    "no_encontrado": "purgado",  # ya no estaba: es el estado que se buscaba
    "referenciado": "conservado",
    "preexistente": "conservado",
    "protegido_worm": "protegido",
}


class ImportHuerfanosService:
    def __init__(self, conn: Any, tenant_slug: str) -> None:
        self._conn = conn
        self._slug = tenant_slug
        self._repo = ImportRepository(conn)

    async def registrar(
        self,
        import_uuid: uuid.UUID,
        actor: uuid.UUID | None,
        huerfanos: list[tuple[uuid.UUID, str | None]],
        subido_desde: datetime,
    ) -> None:
        """Encola (dentro de la transacción del llamante)."""
        if huerfanos:
            await self._repo.registrar_huerfanos(import_uuid, actor, huerfanos, subido_desde)

    async def _fallar(
        self, imp: uuid.UUID, file_ids: list[uuid.UUID], error: str, actor: uuid.UUID | None
    ) -> None:
        """Un intento fallido; si agota el máximo, el paso a 'agotado' se ASIENTA.

        'agotado' es el estado que dice «ya no se reintenta, que alguien lo revise a
        mano»: un binario que se queda en el almacén sin dueño. Sin asiento, ese paso
        solo vivía en una fila que la retención puede borrar. Fila y asiento van en la
        misma transacción (Regla 6, ADR-010); solo por las filas que transitaron."""
        async with self._conn.transaction():
            agotados = await self._repo.fallar_huerfanos(
                imp, file_ids, error, settings.purga_huerfanos_max_intentos
            )
            if agotados:
                await audit.append(
                    self._conn,
                    tenant_slug=self._slug,
                    service=_SERVICE,
                    action="document.import_binario_agotado",
                    object_type="import",
                    object_ref=str(imp),
                    actor=str(actor) if actor else None,
                    payload={
                        "import_uuid": str(imp),
                        "binarios": [
                            {
                                "file_id": str(r["file_id"]),
                                "intentos": r["intentos"],
                                "ultimo_error": (r["ultimo_error"] or "")[:200],
                            }
                            for r in agotados
                        ],
                    },
                )

    async def purgar(self, *, import_uuid: uuid.UUID | None = None) -> dict[str, int]:
        """Una pasada sobre los pendientes (todos, o solo los de una importación).

        Devuelve el conteo por resultado. Nunca marca como purgado lo que storage no
        confirmó; un fallo de red deja las filas pendientes y suma un intento."""
        filas = await self._repo.huerfanos_pendientes(import_uuid, _LIMITE_POR_PASADA)
        grupos: dict[tuple[uuid.UUID, datetime, uuid.UUID | None], list[uuid.UUID]] = defaultdict(list)
        for f in filas:
            grupos[(f["import_uuid"], f["subido_desde"], f["actor"])].append(f["file_id"])

        conteo: dict[str, int] = defaultdict(int)
        for (imp, desde, actor), ids in grupos.items():
            try:
                resultados = await storage_client.purgar_huerfanos(
                    tenant_slug=self._slug,
                    file_ids=ids,
                    referencia=str(imp),
                    motivo="importación revertida: binarios sin radicado",
                    subido_desde=desde,
                    actor=actor,
                )
            except storage_client.StorageContentError as exc:
                await self._fallar(imp, ids, str(exc), actor)
                conteo["error"] += len(ids)
                logger.warning("purga de huérfanos del import %s pendiente: %s", imp, exc)
                continue

            por_id = {r.file_id: r for r in resultados}
            por_estado: dict[str, list[storage_client.ResultadoPurga]] = defaultdict(list)
            fallidos: list[storage_client.ResultadoPurga] = []
            for fid in ids:
                r = por_id.get(fid) or storage_client.ResultadoPurga(fid, "error", "sin respuesta")
                destino = _ESTADO_TERMINAL.get(r.resultado)
                if destino is None:
                    fallidos.append(r)
                else:
                    por_estado[destino].append(r)

            async with self._conn.transaction():
                transitados: list[dict[str, str]] = []
                for destino, rs in por_estado.items():
                    movidos = set(
                        await self._repo.resolver_huerfanos(imp, [r.file_id for r in rs], destino)
                    )
                    transitados += [
                        {"file_id": str(r.file_id), "resultado": r.resultado, "estado": destino}
                        for r in rs
                        if r.file_id in movidos
                    ]
                if transitados:
                    await audit.append(
                        self._conn,
                        tenant_slug=self._slug,
                        service=_SERVICE,
                        action="document.import_binarios_purgados",
                        object_type="import",
                        object_ref=str(imp),
                        actor=str(actor) if actor else None,
                        payload={"import_uuid": str(imp), "binarios": transitados},
                    )
            for r in fallidos:
                await self._fallar(imp, [r.file_id], r.detalle or r.resultado, actor)
            for r in resultados:
                conteo[r.resultado] += 1
        return dict(conteo)
