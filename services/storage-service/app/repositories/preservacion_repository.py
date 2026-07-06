# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de preservación digital (E10).

Plan versionado append-only (vigente = MAX(version)) y eventos de preservación
append-only (PREMIS-style). search_path al tenant en cada método.
"""

from __future__ import annotations

import json

import asyncpg

_PLAN_COLS = (
    "id, version, formatos_destino, num_copias, periodicidad_fixity_dias, "
    "politica_migracion, contingencia, creado_por, created_at"
)
_EVT_COLS = "id, object_ref, tipo, resultado, hash_before, hash_after, detalle, agente, ts"
# E10 Increment B: `bagit` (columna legacy del stub) queda fuera — ya no se
# lee ni se escribe; el AIP real vive como ZIP en el bucket WORM (bucket +
# object_key), no como texto en esta tabla.
_AIP_COLS = (
    "id, expediente_id, premis_xml, aip_sha256, indice_sha256, num_documentos, "
    "estado, created_at, bucket, object_key, aip_bytes, formato, retain_until, "
    "retention_mode, legal_hold"
)


class PreservacionRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._schema = f"tenant_{tenant_slug.replace('-', '_')}"

    async def _sp(self) -> None:
        await self._conn.execute(f'SET search_path = "{self._schema}", public')

    # -- plan ----------------------------------------------------------

    async def current_plan(self) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_PLAN_COLS} FROM preservacion_plan ORDER BY version DESC LIMIT 1"
        )

    async def list_plan_versions(self) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_PLAN_COLS} FROM preservacion_plan ORDER BY version DESC"
            )
        )

    async def insert_plan(self, data: dict, *, creado_por: str | None) -> asyncpg.Record:
        await self._sp()
        current = await self._conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM preservacion_plan"
        )
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_plan
                (version, formatos_destino, num_copias, periodicidad_fixity_dias,
                 politica_migracion, contingencia, creado_por)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING {_PLAN_COLS}
            """,
            current + 1,
            data["formatos_destino"],
            data["num_copias"],
            data["periodicidad_fixity_dias"],
            data.get("politica_migracion"),
            data.get("contingencia"),
            creado_por,
        )
        assert row is not None
        return row

    # -- eventos -------------------------------------------------------

    async def insert_evento(self, data: dict, *, agente: str | None) -> asyncpg.Record:
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_evento
                (object_ref, tipo, resultado, hash_before, hash_after, detalle, agente)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            RETURNING {_EVT_COLS}
            """,
            data["object_ref"],
            data["tipo"],
            data["resultado"],
            data.get("hash_before"),
            data.get("hash_after"),
            json.dumps(data.get("detalle") or {}),
            agente,
        )
        assert row is not None
        return row

    async def insert_aip(self, data: dict) -> asyncpg.Record:
        """Inserta el AIP real (E10 Increment B).

        `id` se genera en la capa de servicio (no se deja el `DEFAULT
        gen_random_uuid()` de la tabla) porque `object_key` del ZIP subido a
        MinIO se deriva de ese mismo id — necesita conocerse ANTES del INSERT.

        `bagit` (columna legacy del stub del Increment A, ahora NULLABLE tras
        la migración 007) deliberadamente NO se escribe: el Bag real es un ZIP
        binario que vive en el bucket WORM, no un texto en esta tabla.
        """
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO aip (id, expediente_id, premis_xml, aip_sha256,
                             indice_sha256, num_documentos, bucket, object_key,
                             aip_bytes, formato, retain_until, retention_mode,
                             legal_hold)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING {_AIP_COLS}
            """,
            data["id"],
            data["expediente_id"],
            data["premis_xml"],
            data["aip_sha256"],
            data.get("indice_sha256"),
            data["num_documentos"],
            data["bucket"],
            data["object_key"],
            data["aip_bytes"],
            data["formato"],
            data["retain_until"],
            data["retention_mode"],
            data["legal_hold"],
        )
        assert row is not None
        return row

    async def latest_aip(self, expediente_id: str) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_AIP_COLS} FROM aip "
            "WHERE expediente_id = $1 ORDER BY created_at DESC LIMIT 1",
            expediente_id,
        )

    async def list_eventos(self, object_ref: str | None) -> list[asyncpg.Record]:
        await self._sp()
        if object_ref is not None:
            return list(
                await self._conn.fetch(
                    f"SELECT {_EVT_COLS} FROM preservacion_evento "
                    "WHERE object_ref = $1 ORDER BY ts ASC",
                    object_ref,
                )
            )
        return list(
            await self._conn.fetch(
                f"SELECT {_EVT_COLS} FROM preservacion_evento ORDER BY ts DESC LIMIT 200"
            )
        )
