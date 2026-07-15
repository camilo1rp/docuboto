# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de transferencias documentales (E12). search_path al tenant."""

from __future__ import annotations

import uuid

import asyncpg

from app.core.database import tenant_schema

_COLS = (
    "id, tipo, estado, expediente_id, ubicacion_origen_id, ubicacion_destino_id, "
    "creada_por, observacion, created_at, enviada_at, enviada_por, recibida_at, decidida_por, "
    # H-K/H-J (migración 021): motivo propio del rechazo (nunca pisa
    # `observacion`) + sello temporal propio del acto.
    "motivo_rechazo, rechazada_at"
)

# Migración 022 (E12 H-I): acta de entrega congelada, 1:1 con la transferencia
# RECIBIDA. Columnas propias -- deliberadamente NO se añaden a `_COLS` (el
# XML no debe arrastrarse por el hot path de `insert`/`get`/`list`/
# `update_estado`, ver docstring de la migración).
_ACTA_COLS = (
    "transferencia_id, acta_fuid_xml, acta_fuid_sha256, elaborado_por, entregado_por, "
    "recibido_por, elaborado_at, entregado_at, recibido_at, indice_version, congelada_at"
)

# Columnas que `update_estado` NUNCA acepta en `assignments`: `estado` tiene su
# propio parámetro (para que la guarda `expected_estado` quede visible en la
# firma, no enterrada en un dict libre) y `id` identifica la fila, no un valor
# a escribir. Un solo escritor de la columna de estado — mismo principio que
# retiró `status` de `ArchiveRepository.update_expediente` (ver su docstring).
_FORBIDDEN_ASSIGNMENT_COLS = frozenset({"estado", "id"})


class TransferenciaRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _sp(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def insert(self, data: dict) -> asyncpg.Record:
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        row = await self._conn.fetchrow(
            f"INSERT INTO transferencias ({', '.join(cols)}) VALUES ({ph}) " f"RETURNING {_COLS}",
            *data.values(),
        )
        assert row is not None
        return row

    async def get(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM transferencias WHERE id = $1", transferencia_id
        )

    async def list(self, *, estado: str | None, tipo: str | None) -> list[asyncpg.Record]:
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if estado is not None:
            params.append(estado)
            conds.append(f"estado = ${len(params)}")
        if tipo is not None:
            params.append(tipo)
            conds.append(f"tipo = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM transferencias {where} " "ORDER BY created_at DESC",
                *params,
            )
        )

    async def update_estado(
        self,
        transferencia_id: uuid.UUID,
        *,
        expected_estado: str,
        estado: str,
        assignments: dict | None = None,
    ) -> asyncpg.Record | None:
        """Transiciona `estado` con guarda TOCTOU (H-A) — gemela de
        `ArchiveRepository.transition_expediente` (ver su docstring, H3/cf83324).

        Antes este método emitía `UPDATE ... WHERE id = $N` SIN comprobar el
        estado de origen: `TransferenciaService._require` validaba la
        transición ANTES de llamar aquí, pero esa validación quedaba FUERA de
        la sentencia — dos llamadas concurrentes (p. ej. `rechazar()` y
        `recibir()` sobre la MISMA transferencia 'enviada') podían leer ambas
        el mismo estado de origen, pasar `_require`, y comprometer AMBOS
        `UPDATE`. El desenlace letal: `rechazada` + expediente ya congelado
        `transferred` por el `recibir()` concurrente -- un estado jurídicamente
        imposible e irreversible en un `audit_log` INDELEBLE (Ley 594/2000 art.
        16; Acuerdo AGN 001/2024 Tít. 4.4).

        `expected_estado` es OBLIGATORIO y solo por *keyword*, sin default: con
        un default "permisivo" un call-site futuro podría omitirlo y el TOCTOU
        volvería en silencio; sin default, olvidarlo es un `TypeError` en la
        primera llamada, no un bug que sobrevive a los tests. `estado` sale del
        dict libre para que la guarda sea visible en la firma, no un valor más
        entre `assignments` (que además la RECHAZA explícitamente, junto con
        `id`, ver `_FORBIDDEN_ASSIGNMENT_COLS`).

        `None` (perdedor de la carrera) es responsabilidad del LLAMANTE:
        `_require` sigue siendo la validación temprana/UX (404/409 con mensaje
        de negocio); este `WHERE` es quien serializa de verdad bajo
        concurrencia real y quien decide el caso alcanzable.
        """
        assignments = assignments or {}
        forbidden = _FORBIDDEN_ASSIGNMENT_COLS & assignments.keys()
        if forbidden:
            raise ValueError(
                f"update_estado: assignments no puede incluir {sorted(forbidden)} "
                "-- usa los parámetros `estado`/`transferencia_id` dedicados."
            )
        await self._sp()
        cols = list(assignments.keys())
        set_parts = ["estado = $1"]
        params: list[object] = [estado]
        idx = 2
        for c in cols:
            set_parts.append(f"{c} = ${idx}")
            params.append(assignments[c])
            idx += 1
        id_idx = idx
        params.append(transferencia_id)
        idx += 1
        expected_idx = idx
        params.append(expected_estado)
        return await self._conn.fetchrow(
            f"UPDATE transferencias SET {', '.join(set_parts)} "
            f"WHERE id = ${id_idx} AND estado = ${expected_idx} "
            f"RETURNING {_COLS}",
            *params,
        )

    async def insert_acta(self, data: dict) -> None:
        """Congela el acta de entrega (E12 H-I): escritura ÚNICA dentro de la
        MISMA `conn.transaction()` de `TransferenciaService.recibir` -- no hay
        ruta de `UPDATE` sobre esta tabla en el repositorio (la `PRIMARY KEY`
        de la migración 022 impide además un segundo `INSERT`), así que el
        acta es append-only de facto. Sin `RETURNING`: el llamante ya tiene
        todos los valores que insertó, no necesita releerlos."""
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await self._conn.execute(
            f"INSERT INTO transferencia_acta ({', '.join(cols)}) VALUES ({ph})",
            *data.values(),
        )

    async def get_acta(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        """Devuelve el acta YA CONGELADA -- `None` si la transferencia aún no
        se recibió (o fue rechazada, que nunca produce acta, ver
        `TransferenciaService.recibir`)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_COLS} FROM transferencia_acta WHERE transferencia_id = $1",
            transferencia_id,
        )
