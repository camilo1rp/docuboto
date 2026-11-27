# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de envíos postales (E20). search_path al tenant ya fijado por la conexión."""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

_COLS = (
    "id, radicado_id, operador, guia, estado, destinatario, direccion, "
    "motivo_devolucion, acuse_file_id, acuse_sha256, created_at, updated_at"
)
# Prefixed for JOIN queries to avoid column-name ambiguity with the radicados table.
# `r.tracking_number` viaja con el resto: las CUATRO consultas ya hacen el JOIN
# a `radicados` —lo exige el no-read-up (RF-SEG-08)— y no se llevaban el único
# dato que identifica el envío para una persona. La pantalla de envíos mostraba
# «Radicado: 43632304-cdea-47c2-…», un identificador interno que quien despacha
# la correspondencia no puede cotejar con el papel que tiene en la mano.
_COLS_S = (
    "s.id, s.radicado_id, r.tracking_number, s.operador, s.guia, s.estado, "
    "s.destinatario, s.direccion, s.motivo_devolucion, s.acuse_file_id, s.acuse_sha256, "
    "s.created_at, s.updated_at"
)


class PostalRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        radicado_id: uuid.UUID,
        operador: str,
        guia: str,
        destinatario: str | None,
        direccion: str | None,
        max_clearance: int,
    ) -> asyncpg.Record | None:
        """Registra el envío solo si el radicado es visible para el clearance dado.

        No-read-up (RF-SEG-08) alineado con el resto del archivo, donde las cinco
        rutas de lectura ya filtran. El gate va como `WHERE EXISTS` del INSERT: si
        fuera una consulta previa, el evento `registrado` de la misma transacción
        podría escribirse sobre un radicado reclasificado en el intervalo.
        Devuelve `None` si el radicado no existe o supera el clearance — el
        servicio lo traduce al mismo 404 que la violación de clave foránea.
        """
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                f"""
                INSERT INTO postal_shipment
                    (radicado_id, operador, guia, destinatario, direccion)
                SELECT $1, $2, $3, $4, $5
                WHERE EXISTS (
                    SELECT 1 FROM radicados WHERE id = $1 AND nivel_seguridad <= $6
                )
                RETURNING {_COLS}
                """,
                radicado_id,
                operador,
                guia,
                destinatario,
                direccion,
                max_clearance,
            )
            if row is None:
                return None
            await self._conn.execute(
                "INSERT INTO postal_shipment_event (shipment_id, estado, descripcion) "
                "VALUES ($1, 'registrado', 'Envío registrado')",
                row["id"],
            )
        return row

    async def get(self, shipment_id: uuid.UUID, clearance: int = 3) -> asyncpg.Record | None:
        # RF-SEG-08: join radicados to enforce no-read-up; clearance=3 (CLASIFICADA)
        # means no restriction and is used by internal callers (e.g. update_estado).
        return await self._conn.fetchrow(
            f"SELECT {_COLS_S} FROM postal_shipment s "
            "JOIN radicados r ON r.id = s.radicado_id "
            "WHERE s.id = $1 AND r.nivel_seguridad <= $2",
            shipment_id,
            clearance,
        )

    async def list_by_radicado(
        self, radicado_id: uuid.UUID, clearance: int = 3
    ) -> list[asyncpg.Record]:
        # RF-SEG-08: only return shipments whose parent radicado is within clearance.
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS_S} FROM postal_shipment s "
                "JOIN radicados r ON r.id = s.radicado_id "
                "WHERE s.radicado_id = $1 AND r.nivel_seguridad <= $2 "
                "ORDER BY s.created_at",
                radicado_id,
                clearance,
            )
        )

    async def events(self, shipment_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT estado, descripcion, ts, ocurrido_operador_at FROM postal_shipment_event "
                "WHERE shipment_id = $1 ORDER BY ts ASC",
                shipment_id,
            )
        )

    async def list_all_paginated(
        self, *, estado: str | None, page: int, size: int, clearance: int
    ) -> tuple[list[asyncpg.Record], int]:
        """Listado paginado transversal del tenant, filtrado por clearance y opcionalmente estado.

        RF-SEG-08: COUNT and SELECT use the same JOIN/filter so X-Total-Count never
        leaks the existence of shipments above the caller's clearance level.
        """
        offset = (page - 1) * size
        if estado is None:
            total: int = await self._conn.fetchval(
                "SELECT COUNT(*) FROM postal_shipment s "
                "JOIN radicados r ON r.id = s.radicado_id "
                "WHERE r.nivel_seguridad <= $1",
                clearance,
            )
            rows = list(
                await self._conn.fetch(
                    f"SELECT {_COLS_S} FROM postal_shipment s "
                    "JOIN radicados r ON r.id = s.radicado_id "
                    "WHERE r.nivel_seguridad <= $1 "
                    "ORDER BY s.created_at DESC LIMIT $2 OFFSET $3",
                    clearance,
                    size,
                    offset,
                )
            )
        else:
            total = await self._conn.fetchval(
                "SELECT COUNT(*) FROM postal_shipment s "
                "JOIN radicados r ON r.id = s.radicado_id "
                "WHERE r.nivel_seguridad <= $1 AND s.estado = $2",
                clearance,
                estado,
            )
            rows = list(
                await self._conn.fetch(
                    f"SELECT {_COLS_S} FROM postal_shipment s "
                    "JOIN radicados r ON r.id = s.radicado_id "
                    "WHERE r.nivel_seguridad <= $1 AND s.estado = $2 "
                    "ORDER BY s.created_at DESC LIMIT $3 OFFSET $4",
                    clearance,
                    estado,
                    size,
                    offset,
                )
            )
        return rows, total

    async def update_estado(
        self,
        shipment_id: uuid.UUID,
        estado: str,
        descripcion: str | None,
        motivo_devolucion: str | None = None,
        *,
        estado_previo: str,
        max_clearance: int | None,
        acuse_file_id: uuid.UUID | None = None,
        acuse_sha256: str | None = None,
        ts: datetime | None = None,
        ocurrido_operador_at: datetime | None = None,
    ) -> asyncpg.Record | None:
        """Aplica la transición SOLO si el envío sigue en `estado_previo`.

        La máquina de estados se valida en el servicio contra una lectura previa;
        entre esa lectura y este UPDATE puede colarse el callback del operador
        (`devuelto`, terminal) y, sin la condición de estado en el `WHERE`, el
        último en escribir pisaba un estado terminal. Con ella, quien llega
        segundo recibe `None` y el servicio responde 409.

        `max_clearance` va en el mismo `WHERE` (RF-SEG-08: el gate es la
        mutación, no una comprobación previa). `None` solo lo pasa el callback
        autenticado del operador: es un acto de SISTEMA, no una lectura humana
        (ver `find_by_guia`); es explícito y sin valor por defecto para que
        olvidarlo no abra el gate en silencio.

        `ts` (B10): instante en que el evento OCURRIÓ, para el historial. Solo el
        drenaje de callbacks diferidos lo pasa (la RECEPCIÓN del aviso, no el drenaje);
        `None` = ahora. `ocurrido_operador_at`: la fecha del hecho postal que declaró el
        operador (campo firmado del callback), guardada aparte; `None` si no la mandó.
        """
        async with self._conn.transaction():
            # `motivo_devolucion` sólo se fija cuando el llamante lo pasa (el
            # servicio lo hace únicamente para 'devuelto'); COALESCE conserva el
            # motivo previo si esta transición no lo trae.
            row = await self._conn.fetchrow(
                f"UPDATE postal_shipment SET estado = $2, updated_at = now(), "
                f"motivo_devolucion = COALESCE($3, motivo_devolucion), "
                f"acuse_file_id = COALESCE($6, acuse_file_id), "
                f"acuse_sha256 = COALESCE($7, acuse_sha256) "
                f"WHERE id = $1 AND estado = $4 "
                f"AND ($5::int IS NULL OR EXISTS ("
                f"SELECT 1 FROM radicados r WHERE r.id = postal_shipment.radicado_id "
                f"AND r.nivel_seguridad <= $5)) "
                f"RETURNING {_COLS}",
                shipment_id,
                estado,
                motivo_devolucion,
                estado_previo,
                max_clearance,
                acuse_file_id,
                acuse_sha256.lower() if acuse_sha256 else None,
            )
            if row is None:
                return None
            await self._conn.execute(
                "INSERT INTO postal_shipment_event "
                "(shipment_id, estado, descripcion, ts, ocurrido_operador_at) "
                "VALUES ($1, $2, $3, COALESCE($4, now()), $5)",
                shipment_id,
                estado,
                descripcion,
                ts,
                ocurrido_operador_at,
            )
        return row

    # ------------------------------------------------------------------
    # E20 F5 — callback entrante del operador
    # ------------------------------------------------------------------
    async def get_operator_credential(self, operador: str) -> asyncpg.Record | None:
        """Las dos ranuras del secreto del operador en ESTE tenant; None si no hay fila.

        Devuelve la FILA entera (secreto en claro heredado, ciphertexts y caducidad de
        la ranura anterior): descifrar y decidir si la anterior sigue vigente es del
        servicio, que es quien tiene la clave y el reloj.
        """
        return await self._conn.fetchrow(
            "SELECT webhook_secret, secreto_cifrado, secreto_nonce, anterior_cifrado, "
            "anterior_nonce, anterior_caduca_at FROM postal_operator_credential "
            "WHERE operador = $1",
            operador,
        )

    async def lock_operator_credential(self, operador: str) -> asyncpg.Record | None:
        """La fila del operador con `FOR UPDATE`: serializa dos rotaciones simultáneas."""
        return await self._conn.fetchrow(
            "SELECT webhook_secret, secreto_cifrado, secreto_nonce FROM "
            "postal_operator_credential WHERE operador = $1 FOR UPDATE",
            operador,
        )

    async def upsert_operator_credential(
        self,
        operador: str,
        *,
        cifrado: bytes,
        nonce: bytes,
        anterior_cifrado: bytes | None,
        anterior_nonce: bytes | None,
        anterior_caduca_at: datetime | None,
        rotado_por: uuid.UUID | None,
    ) -> datetime:
        """Fija la ranura vigente y (si hay) la anterior. Vacía el secreto en claro."""
        return await self._conn.fetchval(
            "INSERT INTO postal_operator_credential "
            "(operador, webhook_secret, secreto_cifrado, secreto_nonce, anterior_cifrado, "
            " anterior_nonce, anterior_caduca_at, rotado_por) "
            "VALUES ($1, NULL, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (operador) DO UPDATE SET webhook_secret = NULL, "
            " secreto_cifrado = EXCLUDED.secreto_cifrado, secreto_nonce = EXCLUDED.secreto_nonce, "
            " anterior_cifrado = EXCLUDED.anterior_cifrado, "
            " anterior_nonce = EXCLUDED.anterior_nonce, "
            " anterior_caduca_at = EXCLUDED.anterior_caduca_at, "
            " rotado_por = EXCLUDED.rotado_por, updated_at = now() "
            "RETURNING updated_at",
            operador,
            cifrado,
            nonce,
            anterior_cifrado,
            anterior_nonce,
            anterior_caduca_at,
            rotado_por,
        )

    async def find_by_guia(self, operador: str, guia: str) -> asyncpg.Record | None:
        """Localiza el envío por (operador, guia). Sin filtro de clearance: el
        callback es un acto de SISTEMA (autenticado por HMAC), no una lectura
        humana — actualiza el estado de custodia aunque el radicado sea
        clasificado (completitud de la traza ≠ control de acceso de lectura)."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM postal_shipment WHERE operador = $1 AND guia = $2",
            operador,
            guia,
        )

    async def try_insert_delivery(
        self,
        *,
        operador: str,
        event_id: str,
        guia: str,
        estado_operador: str,
        estado_interno: str | None,
        shipment_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Inserta la fila de dedup; None si (operador, event_id) ya existe.

        Es la COMPUERTA de idempotencia: ON CONFLICT DO NOTHING serializa dos
        callbacks concurrentes del mismo evento (sólo uno obtiene el id)."""
        return await self._conn.fetchval(
            "INSERT INTO postal_webhook_delivery "
            "(operador, event_id, guia, estado_operador, estado_interno, shipment_id) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (operador, event_id) DO NOTHING RETURNING id",
            operador,
            event_id,
            guia,
            estado_operador,
            estado_interno,
            shipment_id,
        )

    async def mark_delivery_applied(self, delivery_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE postal_webhook_delivery SET aplicada = TRUE WHERE id = $1",
            delivery_id,
        )

    async def add_event(
        self,
        shipment_id: uuid.UUID,
        estado: str,
        descripcion: str | None,
        ts: datetime | None = None,
        ocurrido_operador_at: datetime | None = None,
    ) -> None:
        """Registra un evento append-only SIN cambiar el estado del envío (usado
        cuando la notificación del operador no implica una transición válida)."""
        await self._conn.execute(
            "INSERT INTO postal_shipment_event "
            "(shipment_id, estado, descripcion, ts, ocurrido_operador_at) "
            "VALUES ($1, $2, $3, COALESCE($4, now()), $5)",
            shipment_id,
            estado,
            descripcion,
            ts,
            ocurrido_operador_at,
        )

    # ------------------------------------------------------------------
    # B10 (ADR-035 §7) — cola de callbacks recibidos con la entidad suspendida
    # ------------------------------------------------------------------
    async def insert_diferido(
        self,
        *,
        operador: str,
        event_id: str,
        guia: str,
        cuerpo: bytes,
        cuerpo_sha256: str,
        firma_hmac: str,
        estado_interno: str,
    ) -> uuid.UUID | None:
        """Encola el cuerpo firmado; None si `(operador, event_id)` ya estaba en cola."""
        return await self._conn.fetchval(
            "INSERT INTO postal_callback_diferido "
            "(operador, event_id, guia, cuerpo, cuerpo_sha256, firma_hmac, estado_interno) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (operador, event_id) DO NOTHING RETURNING id",
            operador,
            event_id,
            guia,
            cuerpo,
            cuerpo_sha256,
            firma_hmac,
            estado_interno,
        )

    async def contar_diferidos_pendientes(self) -> int:
        return int(
            await self._conn.fetchval(
                "SELECT count(*) FROM postal_callback_diferido WHERE estado = 'pendiente'"
            )
        )

    async def siguiente_diferido(self) -> asyncpg.Record | None:
        """El pendiente más antiguo, bloqueado. El ORDEN importa (una máquina de estados)."""
        return await self._conn.fetchrow(
            "SELECT id, operador, event_id, guia, cuerpo, cuerpo_sha256, recibido_at "
            "FROM postal_callback_diferido "
            "WHERE estado = 'pendiente' ORDER BY recibido_at, id LIMIT 1 FOR UPDATE SKIP LOCKED"
        )

    async def cerrar_diferido(self, diferido_id: uuid.UUID, *, estado: str, resultado: str) -> None:
        await self._conn.execute(
            "UPDATE postal_callback_diferido SET estado = $2, resultado = $3, "
            "procesado_at = now(), ultimo_error = NULL WHERE id = $1",
            diferido_id,
            estado,
            resultado,
        )

    async def fallo_diferido(self, diferido_id: uuid.UUID, error: str) -> int:
        """Anota un fallo no determinista y devuelve los intentos acumulados."""
        return int(
            await self._conn.fetchval(
                "UPDATE postal_callback_diferido SET intentos = intentos + 1, ultimo_error = $2 "
                "WHERE id = $1 RETURNING intentos",
                diferido_id,
                error[:500],
            )
        )
