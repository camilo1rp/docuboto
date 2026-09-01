# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de plantillas y borradores. search_path fijado por la conexión."""

from __future__ import annotations

import json
import uuid

import asyncpg

_PL = "id, nombre, tipo_documental, cuerpo, activo, created_at"
_BO = (
    "id, doc_type, subject, dest_dept, cuerpo, plantilla_id, radicado_padre_id, "
    "estado, radicado_id, creado_por, created_at, updated_at, "
    "documento_file_id, documento_sha256, documento_formato, combinacion"
)


class BorradorRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # -- plantillas ----------------------------------------------------

    async def insert_plantilla(self, data: dict) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"INSERT INTO plantilla (nombre, tipo_documental, cuerpo, activo) "
            f"VALUES ($1, $2, $3, $4) RETURNING {_PL}",
            data["nombre"],
            data.get("tipo_documental"),
            data["cuerpo"],
            data["activo"],
        )
        assert row is not None
        return row

    async def list_plantillas(self, activo: bool | None) -> list[asyncpg.Record]:
        if activo is None:
            return list(await self._conn.fetch(f"SELECT {_PL} FROM plantilla ORDER BY nombre"))
        return list(
            await self._conn.fetch(
                f"SELECT {_PL} FROM plantilla WHERE activo = $1 ORDER BY nombre", activo
            )
        )

    async def get_plantilla(self, plantilla_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(f"SELECT {_PL} FROM plantilla WHERE id = $1", plantilla_id)

    async def delete_plantilla(self, plantilla_id: uuid.UUID) -> bool:
        res = await self._conn.execute("DELETE FROM plantilla WHERE id = $1", plantilla_id)
        return res.endswith("1")

    # -- borradores ----------------------------------------------------

    async def insert_borrador(self, data: dict) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""INSERT INTO borrador (doc_type, subject, dest_dept, cuerpo, plantilla_id,
                                     radicado_padre_id, creado_por)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING {_BO}""",
            data["doc_type"],
            data["subject"],
            data.get("dest_dept"),
            data.get("cuerpo"),
            data.get("plantilla_id"),
            data.get("radicado_padre_id"),
            data.get("creado_por"),
        )
        assert row is not None
        return row

    async def get_borrador(self, borrador_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(f"SELECT {_BO} FROM borrador WHERE id = $1", borrador_id)

    async def list_borradores(self, estado: str | None) -> list[asyncpg.Record]:
        if estado is None:
            return list(
                await self._conn.fetch(f"SELECT {_BO} FROM borrador ORDER BY created_at DESC")
            )
        return list(
            await self._conn.fetch(
                f"SELECT {_BO} FROM borrador WHERE estado = $1 ORDER BY created_at DESC", estado
            )
        )

    async def update_borrador(self, borrador_id: uuid.UUID, fields: dict) -> asyncpg.Record | None:
        if not fields:
            return await self.get_borrador(borrador_id)
        cols = list(fields.keys())
        sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(cols, start=1))
        idx = len(cols) + 1
        return await self._conn.fetchrow(
            f"UPDATE borrador SET {sets}, updated_at=now() WHERE id=${idx} RETURNING {_BO}",
            *fields.values(),
            borrador_id,
        )

    async def set_documento(
        self,
        borrador_id: uuid.UUID,
        *,
        file_id: uuid.UUID,
        sha256: str | None,
        formato: str,
        combinacion: dict,
    ) -> None:
        """Registra el documento producido y CONGELA el contexto usado.

        `combinacion` se serializa con `json.dumps` porque asyncpg no sabe
        convertir un dict de Python a jsonb: sin eso, `DataError`.

        El `::jsonb` es **redundante aquí y se deja por explícito**, no porque
        haga falta: en un `UPDATE ... SET columna=$n` Postgres ya conoce el tipo
        de destino y parsea el texto como JSON. Lo comprobé quitándolo contra
        Postgres real —el test de integración siguió en verde—, así que la
        justificación que este comentario decía antes era falsa. Donde sí es
        obligatorio es en posiciones sin tipo conocido, como `metadata @> $1`
        (ver `document_repository`).
        """
        await self._conn.execute(
            "UPDATE borrador SET documento_file_id=$2, documento_sha256=$3, "
            "documento_formato=$4, combinacion=$5::jsonb, updated_at=now() WHERE id=$1",
            borrador_id,
            file_id,
            sha256,
            formato,
            json.dumps(combinacion, default=str),
        )

    async def set_estado(
        self, borrador_id: uuid.UUID, estado: str, radicado_id: uuid.UUID | None = None
    ) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"UPDATE borrador SET estado=$2, radicado_id=COALESCE($3, radicado_id), "
            f"updated_at=now() WHERE id=$1 RETURNING {_BO}",
            borrador_id,
            estado,
            radicado_id,
        )
