# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
La bandeja compartida de correo (E19 Increment 2).

**El problema que resuelve.** Varias personas de correspondencia trabajan el
mismo buzón a la vez. Sin reserva, dos abren el mismo correo y lo radican dos
veces: dos números de la secuencia oficial —inmutable por Ley 594— consumidos
por una sola comunicación. El Orfeo legacy tenía el espejo del buzón pero no la
reserva: un correo radicado seguía en la lista con una «R» roja, y nada impedía
que otro lo abriera.

**Todas las transiciones son un solo `UPDATE` condicionado.** Reservar, radicar y
descartar comprueban y mutan en el mismo acto: entre un `SELECT` que dijera «está
libre» y un `UPDATE` que lo tomara cabe la petición de otra persona, y esa
ventana es exactamente el defecto. Cero filas actualizadas significa «alguien se
me adelantó», y se informa como tal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

# Columnas de la bandeja. `reservado_vigente` se calcula en SQL y no en Python:
# la caducidad se compara contra el reloj de la BASE, que es el mismo que usan
# las condiciones de las escrituras. Con el reloj del proceso, la lista podría
# decir «libre» sobre algo que el `UPDATE` va a rechazar por seguir reservado.
_COLUMNAS = """
    m.id, m.cuenta_id, m.message_id, m.carpeta, m.uidvalidity, m.uid,
    m.remitente_nombre, m.remitente_email, m.asunto, m.fecha_recibo,
    m.num_adjuntos, m.crudo_file_id, m.estado, m.tracking_number,
    m.radicado_id, m.radicado_por, m.radicado_at,
    m.reservado_por,
    (m.reservado_hasta IS NOT NULL AND m.reservado_hasta > now()) AS reservado_vigente,
    m.reservado_hasta
"""

# Quién puede tomar la reserva: nadie la tiene, la que había caducó, o ya es
# suya (reabrir el mismo correo no debe fallar).
_RESERVABLE = """
    m.estado = 'pendiente'
    AND (
        m.reservado_hasta IS NULL
        OR m.reservado_hasta <= now()
        OR m.reservado_por = {actor}
    )
"""


class EmailMensajeRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # -- sincronización ----------------------------------------------------

    async def registrar_si_es_nuevo(self, campos: dict[str, Any]) -> asyncpg.Record | None:
        """Inserta un correo recién visto en el buzón; `None` si ya estaba.

        `ON CONFLICT DO NOTHING` sobre `(cuenta_id, message_id)`: el
        sincronizador puede correr dos veces a la vez —dos operadores pulsando
        «actualizar»— y el segundo no debe duplicar la bandeja ni fallar.
        """
        nombres = list(campos.keys())
        marcadores = ", ".join(f"${i}" for i in range(1, len(nombres) + 1))
        return await self._conn.fetchrow(
            f"""
            INSERT INTO email_mensaje ({", ".join(nombres)})
            VALUES ({marcadores})
            ON CONFLICT (cuenta_id, message_id) DO NOTHING
            RETURNING {_COLUMNAS.replace("m.", "")}
            """,
            *campos.values(),
        )

    # -- bandeja -----------------------------------------------------------

    async def bandeja(
        self, cuenta_id: UUID, *, estado: str = "pendiente", limite: int = 100
    ) -> list[asyncpg.Record]:
        """Los correos de una cuenta, el más antiguo primero.

        FIFO como la cola de anulaciones: una ventanilla atiende por orden de
        llegada, y la fecha que ordena es la de RECIBO del correo, no la del
        sondeo que lo trajo.
        """
        return await self._conn.fetch(
            f"""
            SELECT {_COLUMNAS}
              FROM email_mensaje m
             WHERE m.cuenta_id = $1 AND m.estado = $2
             ORDER BY m.fecha_recibo ASC
             LIMIT $3
            """,
            cuenta_id, estado, limite,
        )

    async def contar(self, cuenta_id: UUID, *, estado: str = "pendiente") -> int:
        total = await self._conn.fetchval(
            "SELECT count(*) FROM email_mensaje WHERE cuenta_id = $1 AND estado = $2",
            cuenta_id, estado,
        )
        return int(total or 0)

    async def obtener(self, mensaje_id: UUID, cuenta_id: UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLUMNAS} FROM email_mensaje m WHERE m.id = $1 AND m.cuenta_id = $2",
            mensaje_id, cuenta_id,
        )

    # -- reserva -----------------------------------------------------------

    async def reservar(
        self, mensaje_id: UUID, *, actor: UUID, minutos: int
    ) -> asyncpg.Record | None:
        """Toma el correo para `actor`. `None` si otro lo tiene o ya no está pendiente.

        La condición y la mutación son el mismo `UPDATE`: dos peticiones
        simultáneas se serializan en la fila y solo una ve filas afectadas.
        """
        return await self._conn.fetchrow(
            f"""
            UPDATE email_mensaje m
               SET reservado_por = $2,
                   reservado_hasta = now() + make_interval(mins => $3)
             WHERE m.id = $1 AND {_RESERVABLE.format(actor="$2")}
            RETURNING {_COLUMNAS}
            """,
            mensaje_id, actor, minutos,
        )

    async def liberar(self, mensaje_id: UUID, *, actor: UUID) -> bool:
        """Suelta la reserva propia. No toca la de otro."""
        estado = await self._conn.execute(
            """
            UPDATE email_mensaje
               SET reservado_por = NULL, reservado_hasta = NULL
             WHERE id = $1 AND reservado_por = $2
            """,
            mensaje_id, actor,
        )
        return estado.endswith(" 1")

    # -- desenlaces --------------------------------------------------------

    async def marcar_radicado(
        self,
        mensaje_id: UUID,
        *,
        actor: UUID,
        radicado_id: UUID,
        tracking_number: str,
    ) -> asyncpg.Record | None:
        """Cierra el correo con su número. `None` si el actor perdió la reserva.

        **Exige la reserva vigente del propio actor**, no solo que el correo siga
        pendiente. Sin eso, dos personas que abrieran el mismo correo antes de que
        existiera la reserva podrían radicarlo las dos: la primera pasaría, y la
        segunda también, porque al comprobar solo el estado la carrera sigue
        abierta hasta el instante del `UPDATE`.
        """
        return await self._conn.fetchrow(
            f"""
            UPDATE email_mensaje m
               SET estado = 'radicado',
                   radicado_id = $3,
                   tracking_number = $4,
                   radicado_por = $2,
                   radicado_at = now(),
                   reservado_por = NULL,
                   reservado_hasta = NULL
             WHERE m.id = $1
               AND m.estado = 'pendiente'
               AND m.reservado_por = $2
               AND m.reservado_hasta > now()
            RETURNING {_COLUMNAS}
            """,
            mensaje_id, actor, radicado_id, tracking_number,
        )

    async def descartar(
        self, mensaje_id: UUID, *, actor: UUID, motivo: str
    ) -> asyncpg.Record | None:
        """Aparta un correo que llegó a la carpeta y no debía radicarse.

        Mismo requisito de reserva que radicar: descartar también es una decisión
        sobre una comunicación recibida, y dos personas no deben tomarla a la vez.
        """
        return await self._conn.fetchrow(
            f"""
            UPDATE email_mensaje m
               SET estado = 'descartado',
                   descartado_por = $2,
                   descartado_at = now(),
                   motivo_descarte = $3,
                   reservado_por = NULL,
                   reservado_hasta = NULL
             WHERE m.id = $1
               AND m.estado = 'pendiente'
               AND m.reservado_por = $2
               AND m.reservado_hasta > now()
            RETURNING {_COLUMNAS}
            """,
            mensaje_id, actor, motivo,
        )

    async def guardar_crudo(self, mensaje_id: UUID, *, file_id: UUID) -> None:
        """Enlaza el `.eml` conservado en storage (Ley 527/1999 art. 12)."""
        await self._conn.execute(
            "UPDATE email_mensaje SET crudo_file_id = $2 WHERE id = $1", mensaje_id, file_id
        )

    async def estado_bandeja(self, cuenta_id: UUID) -> dict[str, Any]:
        """Pendientes y marca de la última novedad. Lo consulta el sondeo.

        Es lo que permite avisar a las demás pantallas de que la bandeja cambió
        —alguien radicó, alguien reservó, entraron correos nuevos— sin traerse la
        lista entera cada pocos segundos. Con diez operadores mirando la
        ventanilla, la diferencia entre sondear una fila y sondear cien es la
        diferencia entre que la función exista y que estorbe.

        La marca combina las tres cosas que cambian lo que se ve: correos nuevos
        (`visto_at`), radicados y descartados. La **reserva también cuenta**: que
        un compañero tome un correo cambia lo que la pantalla debe mostrar, y sin
        ella el resto vería «libre» algo que ya no lo está.
        """
        fila = await self._conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE estado = 'pendiente') AS pendientes,
                   count(*) FILTER (
                       WHERE estado = 'pendiente'
                         AND reservado_hasta IS NOT NULL
                         AND reservado_hasta > now()
                   ) AS ocupados,
                   greatest(
                       max(visto_at),
                       max(radicado_at),
                       max(descartado_at),
                       max(reservado_hasta)
                   ) AS ultima_novedad
              FROM email_mensaje
             WHERE cuenta_id = $1
            """,
            cuenta_id,
        )
        return {
            "pendientes": int(fila["pendientes"] or 0),
            "ocupados": int(fila["ocupados"] or 0),
            "ultima_novedad": fila["ultima_novedad"],
        }

    async def ultima_sincronizacion(self, cuenta_id: UUID) -> datetime | None:
        return await self._conn.fetchval(
            "SELECT max(visto_at) FROM email_mensaje WHERE cuenta_id = $1", cuenta_id
        )
