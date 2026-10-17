# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de cadena de firma (firma_solicitud + firma_cadena).

La conexión llega con search_path ya fijado al tenant (via get_tenant_conn).
NO se vuelve a fijar search_path aquí (a diferencia de VistoBuenoRepository,
que usa get_db_conn sin tenant). Todos los placeholders usan $N de asyncpg.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import asyncpg

from app.core.constants import CADENA_NIVEL_AUTORITATIVO, RADICADO_ESTADO_ANULADO

_SOL_COLS = (
    "id, objeto_tipo, objeto_id, titulo, payload_sha256, estado, creado_por, "
    "nivel_seguridad, created_at, updated_at"
)

#: Las mismas columnas, calificadas para cuando `firma_solicitud` va con alias.
_SOL_COLS_S = ", ".join(f"s.{c.strip()}" for c in _SOL_COLS.split(","))

_CAD_COLS = (
    "id, solicitud_id, orden, firmante_id, rol, estado, firma_id, comentario, decidido_at"
)


class CadenaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # firma_solicitud
    # ------------------------------------------------------------------

    async def crear_solicitud(self, data: dict) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""INSERT INTO firma_solicitud
                    (objeto_tipo, objeto_id, titulo, payload_sha256, creado_por, nivel_seguridad)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {_SOL_COLS}""",
            data["objeto_tipo"],
            data.get("objeto_id"),
            data.get("titulo"),
            data.get("payload_sha256"),
            data.get("creado_por"),
            data.get("nivel_seguridad", 1),
        )
        assert row is not None
        return row

    async def resolver_nivel_por_objeto(self, objeto_id: str) -> int | None:
        """Nivel de seguridad REAL del objeto referenciado, o ``None`` si no resuelve.

        **No recibe `objeto_tipo` a propósito.** Ese campo lo elige el cliente en el
        body, y usarlo para decidir *si* se consulta la fuente autoritativa convertía
        una llave de autorización en algo que el llamante podía apagar: bastaba
        escribir `"Radicado"`, o declarar `"borrador"` sobre el UUID de un radicado
        CLASIFICADA, para que el gate no-read-up se evaluara contra un nivel inventado.
        Aquí se busca el identificador en las tablas dueñas y se devuelve el nivel que
        se encuentre — el tipo declarado no interviene.

        Devuelve el **máximo** cuando el identificador coincide en más de una tabla
        (improbable, pero si ocurre el lado seguro es el nivel más alto). ``None``
        significa "no se pudo establecer" —no existe, o la tabla no está en este
        schema—, nunca "es público": quien llama decide si eso es un rechazo
        (`CADENA_NIVEL_AUTORITATIVO`) o un objeto sin nivel propio.

        La comparación doble (identidad natural / UUID) tolera que el llamante pase el
        número de radicado o el código del expediente en vez del UUID.
        """
        # Nombres de tabla y columna salen de la constante del módulo, jamás del
        # request. Una sola consulta en el caso normal (ambas tablas presentes).
        selects = [
            f"SELECT nivel_seguridad FROM {table} WHERE "
            + " OR ".join(f"{col} = $1" for col in id_cols)
            for table, id_cols in CADENA_NIVEL_AUTORITATIVO.values()
        ]
        try:
            # Savepoint: invocada DENTRO de la transacción de firmar (B9-2), un
            # `UndefinedTableError` sin él deja la transacción abortada y el
            # reintento por tabla de abajo falla con `InFailedSQLTransaction`.
            async with self._conn.transaction():
                return await self._conn.fetchval(
                    f"SELECT MAX(nivel_seguridad) FROM ({' UNION ALL '.join(selects)}) AS n",
                    objeto_id,
                )
        except asyncpg.UndefinedTableError:
            # Alguna tabla fuente falta en este schema (tenant sin document- o
            # archive-service, o entorno de test aislado): se reintenta tabla a
            # tabla para no perder las que SÍ están.
            return await self._resolver_nivel_por_tabla(objeto_id)

    async def nivel_vigente_for_update(
        self, solicitud_id: uuid.UUID, objeto_id: str
    ) -> tuple[int, int] | None:
        """Nivel que RIGE el acto de firmar, leído bajo bloqueo (B9-2, SEG-B4).

        Devuelve ``(nivel_efectivo, copia_bloqueada)``: la copia es la que se leyó
        bajo ``FOR UPDATE``, y es de ella (no de la solicitud leída antes, sin
        bloqueo) de donde sale el ``nivel_anterior`` del asiento de la relectura
        (re-auditoría B9 H).

        ``max(copia de la cadena bajo FOR UPDATE, nivel autoritativo vivo del
        objeto)``. La copia (`firma_solicitud.nivel_seguridad`) se sincroniza por
        evento y puede ir atrasada (DLQ, o cadena declarada `borrador` sobre el UUID
        de un radicado, que el consumidor no alcanza): firmar con ella dejaba el
        acto sin 2FA, sin gate XAdES y con un actor de clearance menor. ``max`` es
        monótono: una desclasificación aún no sincronizada se trata con más rigor,
        nunca con menos.

        ``None`` si la solicitud ya no existe. No bloquea ``radicados``: una
        reclasificación que confirma después de esta lectura queda serializada como
        posterior a la firma, y eso es legítimo.
        """
        copia = await self._conn.fetchval(
            "SELECT nivel_seguridad FROM firma_solicitud WHERE id = $1 FOR UPDATE",
            solicitud_id,
        )
        if copia is None:
            return None
        vivo = await self.resolver_nivel_por_objeto(objeto_id)
        efectivo = max(int(copia), int(vivo)) if vivo is not None else int(copia)
        return efectivo, int(copia)

    async def objeto_anulado(self, objeto_id: str) -> bool | None:
        """¿El identificador apunta a un radicado ANULADO?

        ``None`` significa «no se pudo establecer» —no es un radicado, o la
        tabla no está en este schema—, nunca «no está anulado»: quien llama
        decide qué hacer con la duda.

        **No recibe `objeto_tipo`**, por el mismo motivo que
        ``resolver_nivel_por_objeto``: ese campo lo elige el cliente, y usarlo
        para decidir *si* se comprueba convertiría el control en algo que el
        llamante puede apagar declarando otro tipo sobre el mismo UUID.
        """
        tabla, id_cols = CADENA_NIVEL_AUTORITATIVO["radicado"]
        where = " OR ".join(f"{col} = $1" for col in id_cols)
        try:
            estado = await self._conn.fetchval(
                f"SELECT status FROM {tabla} WHERE {where} LIMIT 1", objeto_id
            )
        except asyncpg.UndefinedTableError:
            return None
        if estado is None:
            return None
        return str(estado) == RADICADO_ESTADO_ANULADO

    async def _resolver_nivel_por_tabla(self, objeto_id: str) -> int | None:
        niveles: list[int] = []
        for table, id_cols in CADENA_NIVEL_AUTORITATIVO.values():
            where = " OR ".join(f"{col} = $1" for col in id_cols)
            try:
                async with self._conn.transaction():  # savepoint, ver arriba
                    nivel = await self._conn.fetchval(
                        f"SELECT nivel_seguridad FROM {table} WHERE {where} LIMIT 1",
                        objeto_id,
                    )
            except asyncpg.UndefinedTableError:
                continue
            if nivel is not None:
                niveles.append(int(nivel))
        return max(niveles) if niveles else None

    async def get_solicitud(self, solicitud_id: uuid.UUID) -> asyncpg.Record | None:
        """La solicitud, con el estado del radicado al que apunta si lo hay.

        `radicado_status` viaja **con la fila** en vez de pedirse aparte, por dos
        motivos. Uno: es una lectura menos en el camino más caliente del
        servicio —firmar consulta la solicitud siempre—. Y dos, el que de verdad
        decide: quien lee la solicitud para decidir si se firma tiene delante, en
        el mismo objeto, el dato que lo impide; pedirlo aparte deja el control a
        merced de que alguien se acuerde de llamar, que es exactamente cómo
        nacen los gates que no guardan.

        `NULL` cuando el objeto no es un radicado —un expediente o un borrador lo
        son legítimamente— y también cuando `radicados` no existe en este schema.
        Las dos cosas significan «aquí no hay nada que impedir», no «está bien».
        """
        tabla, id_cols = CADENA_NIVEL_AUTORITATIVO["radicado"]
        where = " OR ".join(f"r.{col} = s.objeto_id" for col in id_cols)
        try:
            return await self._conn.fetchrow(
                f"SELECT {_SOL_COLS_S}, r.status AS radicado_status "
                f"FROM firma_solicitud s "
                f"LEFT JOIN LATERAL ("
                f"  SELECT status FROM {tabla} r WHERE {where} LIMIT 1"
                f") r ON TRUE "
                f"WHERE s.id = $1",
                solicitud_id,
            )
        except asyncpg.UndefinedTableError:
            # Schema sin `radicados` (tenant sin document-service, o entorno de
            # test aislado): la solicitud se sirve igual y el control de arriba
            # se queda sin dato, que es su caso `None` declarado.
            return await self._conn.fetchrow(
                f"SELECT {_SOL_COLS} FROM firma_solicitud WHERE id = $1",
                solicitud_id,
            )

    async def get_solicitud_for_update(self, solicitud_id: uuid.UUID) -> asyncpg.Record | None:
        """Same as get_solicitud but with a row lock (E06 Inc.2, RF-FIR-13).

        Used by CadenaFirmaService.emitir_challenge to hold the solicitud stable
        for the duration of the challenge-emission transaction (TOCTOU guard,
        same pattern as turno_activo_for_update).
        """
        return await self._conn.fetchrow(
            f"SELECT {_SOL_COLS} FROM firma_solicitud WHERE id = $1 FOR UPDATE",
            solicitud_id,
        )

    async def get_solicitud_por_objeto_id(
        self, objeto_id: str, solo_abierta: bool = True
    ) -> asyncpg.Record | None:
        """Busca la solicitud por el OBJETO, sin mirar el tipo que se declaró.

        El identificador es un UUID y por sí solo identifica el objeto; el tipo
        es una etiqueta del cliente. Los gates preguntan por aquí para que
        cambiar la etiqueta no haga desaparecer un circuito abierto.
        """
        if solo_abierta:
            return await self._conn.fetchrow(
                f"SELECT {_SOL_COLS} FROM firma_solicitud "
                "WHERE objeto_id = $1 AND estado = 'pendiente'",
                objeto_id,
            )
        return await self._conn.fetchrow(
            f"SELECT {_SOL_COLS} FROM firma_solicitud "
            "WHERE objeto_id = $1 ORDER BY created_at DESC LIMIT 1",
            objeto_id,
        )

    async def get_solicitud_por_objeto(
        self,
        objeto_tipo: str,
        objeto_id: str,
        solo_abierta: bool = True,
    ) -> asyncpg.Record | None:
        if solo_abierta:
            return await self._conn.fetchrow(
                f"SELECT {_SOL_COLS} FROM firma_solicitud "
                "WHERE objeto_tipo = $1 AND objeto_id = $2 AND estado = 'pendiente'",
                objeto_tipo,
                objeto_id,
            )
        return await self._conn.fetchrow(
            f"SELECT {_SOL_COLS} FROM firma_solicitud "
            "WHERE objeto_tipo = $1 AND objeto_id = $2 ORDER BY created_at DESC LIMIT 1",
            objeto_tipo,
            objeto_id,
        )

    async def existe_abierta(self, objeto_tipo: str, objeto_id: str) -> bool:
        val = await self._conn.fetchval(
            "SELECT 1 FROM firma_solicitud "
            "WHERE objeto_tipo = $1 AND objeto_id = $2 AND estado = 'pendiente' LIMIT 1",
            objeto_tipo,
            objeto_id,
        )
        return val is not None

    async def actualizar_estado_solicitud(
        self, solicitud_id: uuid.UUID, estado: str
    ) -> None:
        await self._conn.execute(
            "UPDATE firma_solicitud SET estado = $2, updated_at = now() WHERE id = $1",
            solicitud_id,
            estado,
        )

    async def invalidar_por_objeto(self, objeto_tipo: str, objeto_id: str, motivo: str) -> int:
        """Deja sin efecto los circuitos de un objeto cuyo texto cambió.

        La migración 018 creó el estado `invalidada` justamente para esto —«la
        cadena seguía "pendiente" sobre un documento que ya no existe, o peor,
        "firmado" sobre uno que cambió después»— y **ningún código lo escribía**.
        El esquema anticipó el trámite y la lógica no lo conectó.

        Alcanza a las solicitudes vivas (`pendiente`) y a las ya cerradas
        (`firmado`), porque el problema es el mismo en ambas: las primeras piden
        a alguien que firme un texto que ya no es, y las segundas atestiguan un
        texto que dejó de estar. Los turnos aún sin resolver caen con ellas,
        para que desaparezcan de la bandeja de quien los tenía; los ya resueltos
        se conservan intactos, porque son el registro de un acto que sí ocurrió.

        No bloquea el rehacer: el índice de unicidad filtra por `pendiente`, así
        que una solicitud invalidada no impide abrir la que la sustituye — la
        propia migración lo comprueba para que un cambio futuro no lo rompa.

        Devuelve cuántas solicitudes se invalidaron.
        """
        filas = await self._conn.fetch(
            """
            UPDATE firma_solicitud
               SET estado = 'invalidada',
                   invalidada_at = now(),
                   invalidada_motivo = $3,
                   updated_at = now()
             WHERE objeto_tipo = $1
               AND objeto_id = $2
               AND estado IN ('pendiente', 'firmado')
            RETURNING id
            """,
            objeto_tipo,
            objeto_id,
            motivo,
        )
        for fila in filas:
            await self._conn.execute(
                """
                UPDATE firma_cadena
                   SET estado = 'invalidada'
                 WHERE solicitud_id = $1
                   AND estado = 'pendiente'
                """,
                fila["id"],
            )
        return len(filas)

    # ------------------------------------------------------------------
    # firma_cadena
    # ------------------------------------------------------------------

    async def crear_turnos(
        self,
        solicitud_id: uuid.UUID,
        firmantes: list[uuid.UUID],
        roles: Sequence[str] | None = None,
    ) -> None:
        """Inserta los turnos de la cadena (orden 1..N).

        `roles` es paralelo a `firmantes`. Cuando falta, todos los turnos son
        de `firma`: es lo que eran todas las cadenas antes de E23, y un default
        distinto reescribiría su significado en silencio.
        """
        for indice, (orden, firmante_id) in enumerate(enumerate(firmantes, start=1)):
            rol = roles[indice] if roles else "firma"
            await self._conn.execute(
                "INSERT INTO firma_cadena (solicitud_id, orden, firmante_id, rol) "
                "VALUES ($1, $2, $3, $4)",
                solicitud_id,
                orden,
                firmante_id,
                rol,
            )

    async def list_turnos(self, solicitud_id: uuid.UUID) -> list[asyncpg.Record]:
        rows = await self._conn.fetch(
            f"SELECT {_CAD_COLS} FROM firma_cadena "
            "WHERE solicitud_id = $1 ORDER BY orden",
            solicitud_id,
        )
        return list(rows)

    async def turno_activo(self, solicitud_id: uuid.UUID) -> asyncpg.Record | None:
        """Returns the current active turn: lowest orden with estado='pendiente'."""
        return await self._conn.fetchrow(
            f"SELECT {_CAD_COLS} FROM firma_cadena "
            "WHERE solicitud_id = $1 AND estado = 'pendiente' ORDER BY orden LIMIT 1",
            solicitud_id,
        )

    async def turno_activo_for_update(
        self, solicitud_id: uuid.UUID
    ) -> asyncpg.Record | None:
        """
        Same as turno_activo but acquires a row-level lock (FOR UPDATE).

        Must be called inside a transaction. Prevents two concurrent requests
        from the same firmante signing the same turn (TOCTOU, RF-FIR-04):
        the second request blocks until the first transaction commits, at which
        point estado='firmado' and marcar_turno returns False (0 rows affected).
        """
        return await self._conn.fetchrow(
            f"SELECT {_CAD_COLS} FROM firma_cadena "
            "WHERE solicitud_id = $1 AND estado = 'pendiente' ORDER BY orden LIMIT 1 "
            "FOR UPDATE",
            solicitud_id,
        )

    async def es_firmante(
        self, solicitud_id: uuid.UUID, firmante_id: uuid.UUID
    ) -> bool:
        """Returns True if firmante_id is any member of this chain."""
        val = await self._conn.fetchval(
            "SELECT 1 FROM firma_cadena "
            "WHERE solicitud_id = $1 AND firmante_id = $2 LIMIT 1",
            solicitud_id,
            firmante_id,
        )
        return val is not None

    async def marcar_turno(
        self,
        turno_id: uuid.UUID,
        estado: str,
        firma_id: uuid.UUID | None = None,
        comentario: str | None = None,
    ) -> bool:
        """
        Marks a turn as resolved; returns True iff exactly 1 row was updated.

        The WHERE clause conditions on estado='pendiente' so that a concurrent
        transaction that already committed the same update yields 0 rows —
        the caller must treat False as a 409 (duplicate sign attempt).
        """
        tag: str = await self._conn.execute(
            "UPDATE firma_cadena "
            "SET estado = $2, firma_id = $3, comentario = $4, decidido_at = now() "
            "WHERE id = $1 AND estado = 'pendiente'",
            turno_id,
            estado,
            firma_id,
            comentario,
        )
        # asyncpg returns the command tag string, e.g. "UPDATE 1" or "UPDATE 0".
        return tag.split()[-1] == "1"

    async def actualizar_nivel_seguridad(
        self,
        objeto_tipo: str,
        objeto_ids: list[str],
        nivel: int,
        *,
        solo_subir: bool = False,
    ) -> int:
        """Syncs nivel_seguridad in firma_solicitud after a radicado reclassification.

        Updates all solicitudes matching objeto_tipo and any of the provided
        objeto_ids (typically [tracking_number, radicado_uuid_str]) whose stored
        nivel_seguridad differs from ``nivel``.  The ``nivel_seguridad <> $1``
        predicate makes this idempotent: redelivered events produce 0 updates.

        ``solo_subir=True`` (la corrección que hace la RELECTURA al firmar, B9-S3)
        cambia el predicado a ``nivel_seguridad < $1``: es monótona y NO baja las
        cadenas hermanas del mismo objeto que ya van por encima del nivel vivo. El
        evento `reclassified` (``solo_subir=False``) sí puede bajar, porque es la
        reclasificación autoritativa del objeto.

        Returns the number of rows actually updated (for audit/logging).

        Tolerates asyncpg.UndefinedTableError (tenant schema without firma_solicitud)
        by returning 0 — a legitimate no-op (ackeable): the tenant has no signature
        chains to sync, so the event is safely consumed without re-queuing.
        This mirrors resolver_nivel_radicado's pattern for the radicados table.
        """
        comparador = "<" if solo_subir else "<>"
        try:
            tag: str = await self._conn.execute(
                "UPDATE firma_solicitud "
                "SET nivel_seguridad = $1, updated_at = now() "
                "WHERE objeto_tipo = $2 AND objeto_id = ANY($3::text[]) "
                f"  AND nivel_seguridad {comparador} $1",
                nivel,
                objeto_tipo,
                objeto_ids,
            )
            # asyncpg returns a command tag like "UPDATE 2"; parse the row count.
            return int(tag.split()[-1])
        except asyncpg.UndefinedTableError:
            # firma_solicitud table absent in this tenant schema.
            # No signature chains to sync — treat as 0 rows updated (ackeable no-op).
            return 0

    async def pendientes_de_firmante(
        self,
        firmante_id: uuid.UUID,
        offset: int,
        limit: int,
        max_clearance: int = 1,
    ) -> tuple[list[asyncpg.Record], int]:
        """
        Returns (rows, total) for the inbox: solicitudes where the ACTIVE turn
        (lowest pending orden) belongs to firmante_id, filtered by max_clearance.

        The nivel_seguridad <= max_clearance predicate enforces no-read-up (RF-SEG-08):
        the window COUNT(*) OVER() applies the same filter so X-Total-Count does not
        reveal the number of hidden solicitudes above the actor's clearance.

        Each row contains: solicitud_id, objeto_tipo, objeto_id, titulo, orden, created_at.
        """
        args = (firmante_id, limit, offset, max_clearance)
        try:
            # Savepoint: un `UndefinedTableError` sin él aborta la transacción ambiente.
            async with self._conn.transaction():
                rows = await self._conn.fetch(
                    _sql_pendientes(tuple(CADENA_NIVEL_AUTORITATIVO.values())), *args
                )
        except asyncpg.UndefinedTableError:
            # Tenant sin alguna tabla fuente (sin document- o archive-service): se
            # consulta con las que SÍ existen. Sin ninguna, el nivel vivo no existe
            # y rige la copia (igual que `resolver_nivel_por_objeto`).
            presentes = []
            for tabla, cols in CADENA_NIVEL_AUTORITATIVO.values():
                if await self._conn.fetchval("SELECT to_regclass($1) IS NOT NULL", tabla):
                    presentes.append((tabla, cols))
            rows = await self._conn.fetch(_sql_pendientes(tuple(presentes)), *args)
        total = int(rows[0]["total_count"]) if rows else 0
        return list(rows), total


def _sql_pendientes(fuentes: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    """Consulta de la bandeja con el gate sobre ``GREATEST(copia, nivel vivo)``.

    La misma condición vale para las filas y para el ``COUNT(*) OVER()`` (comparten
    ``WHERE``), así ``X-Total-Count`` no revela cadenas por encima del clearance. El
    nivel vivo sale de las tablas dueñas (nombres de la constante del módulo, jamás
    del request); la copia sola iría atrasada si el evento `reclassified` no llegó.
    """
    if fuentes:
        selects = " UNION ALL ".join(
            f"SELECT t.nivel_seguridad AS n FROM {tabla} t WHERE "
            + " OR ".join(f"t.{col} = s.objeto_id" for col in cols)
            for tabla, cols in fuentes
        )
        vivo = f"COALESCE((SELECT MAX(v.n) FROM ({selects}) v), 0)"
    else:
        vivo = "0"
    return f"""
            SELECT
                s.id            AS solicitud_id,
                s.objeto_tipo,
                s.objeto_id,
                s.titulo,
                c.orden,
                -- Qué le piden a esta persona, no solo que le piden algo: la
                -- bandeja enseña «Visto bueno» o «Firma» ANTES de abrir el
                -- detalle. Dar el visto bueno y firmar comprometen distinto.
                c.rol,
                s.created_at,
                COUNT(*) OVER() AS total_count
            FROM firma_solicitud s
            JOIN firma_cadena c ON c.solicitud_id = s.id
            WHERE s.estado          = 'pendiente'
              AND c.firmante_id     = $1
              AND c.estado          = 'pendiente'
              AND GREATEST(s.nivel_seguridad, {vivo}) <= $4
              AND c.orden = (
                  SELECT MIN(c2.orden)
                  FROM firma_cadena c2
                  WHERE c2.solicitud_id = s.id AND c2.estado = 'pendiente'
              )
            ORDER BY s.created_at DESC
            LIMIT $2 OFFSET $3
            """
