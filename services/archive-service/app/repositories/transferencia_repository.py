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
    "creada_por, observacion, created_at, enviada_at, recibida_at, decidida_por"
)


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
        self, transferencia_id: uuid.UUID, assignments: dict
    ) -> asyncpg.Record | None:
        await self._sp()
        cols = list(assignments.keys())
        set_clause = ", ".join(f"{c} = ${i}" for i, c in enumerate(cols, start=1))
        idx = len(cols) + 1
        return await self._conn.fetchrow(
            f"UPDATE transferencias SET {set_clause} WHERE id = ${idx} " f"RETURNING {_COLS}",
            *assignments.values(),
            transferencia_id,
        )
