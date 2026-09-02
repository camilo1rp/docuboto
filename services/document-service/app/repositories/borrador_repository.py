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

_PL = "id, nombre, tipo_documental, cuerpo, activo, created_at, es_global, creado_por"
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
            f"INSERT INTO plantilla (nombre, tipo_documental, cuerpo, activo, "
            f"                       es_global, creado_por) "
            f"VALUES ($1, $2, $3, $4, $5, $6) RETURNING {_PL}",
            data["nombre"],
            data.get("tipo_documental"),
            data["cuerpo"],
            data["activo"],
            data.get("es_global", True),
            data.get("creado_por"),
        )
        assert row is not None
        return row

    async def set_dependencias_plantilla(
        self, plantilla_id: uuid.UUID, depe_codigos: list[int]
    ) -> None:
        """Reemplaza la asignación de dependencias de una plantilla.

        Se borra y se reinserta en vez de calcular el diferencial: la lista es
        pequeña y el resultado es el mismo, pero el código que calcula
        diferencias tiene un modo de fallo —dejar filas que ya no tocaban— que
        aquí no compensa.
        """
        await self._conn.execute(
            "DELETE FROM plantilla_dependencia WHERE plantilla_id = $1", plantilla_id
        )
        for codigo in depe_codigos:
            await self._conn.execute(
                "INSERT INTO plantilla_dependencia (plantilla_id, depe_codigo) "
                "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                plantilla_id,
                codigo,
            )

    async def set_alcance_plantilla(self, plantilla_id: uuid.UUID, es_global: bool) -> None:
        await self._conn.execute(
            "UPDATE plantilla SET es_global = $2 WHERE id = $1", plantilla_id, es_global
        )

    async def dependencias_de_plantilla(self, plantilla_id: uuid.UUID) -> list[int]:
        rows = await self._conn.fetch(
            "SELECT depe_codigo FROM plantilla_dependencia "
            "WHERE plantilla_id = $1 ORDER BY depe_codigo",
            plantilla_id,
        )
        return [r["depe_codigo"] for r in rows]

    async def dependencias_del_usuario(self, user_id: uuid.UUID) -> list[int]:
        """Códigos de dependencia en los que el usuario tiene URD activa.

        Se lee `urd_usuaroldep` directamente, que vive en el MISMO schema del
        tenant: es la práctica ya establecida en este servicio para el clearance
        (`auth_users`, `auth_memberships`, `role_clearance`). Un cliente HTTP a
        auth-service para esto añadiría una llamada de red al listado más usado
        de la pantalla sin ganar aislamiento: el schema ya es compartido.
        """
        try:
            rows = await self._conn.fetch(
                "SELECT depe_id FROM urd_usuaroldep WHERE user_id = $1 AND is_active",
                user_id,
            )
        except asyncpg.UndefinedTableError:
            # Tablas de identidad del tenant ausentes (entorno aislado o no
            # provisionado del todo). Se devuelve la lista VACÍA —que recorta a
            # solo las globales—, nunca `None`, que significaría «sin
            # restricción»: es el mismo fail-closed que `resolve_caller_clearance`,
            # donde lo dudoso cae a Pública.
            #
            # Lo destapó el tier de integración, que provisiona solo las
            # migraciones de ESTE servicio: sin este `except`, el listado de
            # plantillas —la pantalla más usada de la producción documental—
            # respondía 500 en un tenant a medio provisionar.
            return []
        return [r["depe_id"] for r in rows]

    async def list_plantillas(
        self,
        activo: bool | None,
        *,
        depe_codigos: list[int] | None = None,
    ) -> list[asyncpg.Record]:
        """Plantillas visibles.

        `depe_codigos=None` significa **sin recorte** (administración: hay que
        poder gestionar lo que no se usa). Una lista, incluso vacía, recorta: y
        la vacía recorta a solo las globales, que es lo correcto para un usuario
        sin ninguna URD activa — no un atajo a «todas».

        El recorte va en el `WHERE` y no en una comprobación posterior, por la
        misma razón de siempre en este servicio: así el filtro y la lectura son
        el mismo acto y no hay ventana donde se pierda.
        """
        condiciones: list[str] = []
        params: list[object] = []

        if activo is not None:
            params.append(activo)
            condiciones.append(f"activo = ${len(params)}")

        if depe_codigos is not None:
            params.append(depe_codigos)
            condiciones.append(
                f"(es_global OR EXISTS (SELECT 1 FROM plantilla_dependencia pd "
                f"  WHERE pd.plantilla_id = plantilla.id "
                f"    AND pd.depe_codigo = ANY(${len(params)}::int[])))"
            )

        where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
        return list(
            await self._conn.fetch(f"SELECT {_PL} FROM plantilla{where} ORDER BY nombre", *params)
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
