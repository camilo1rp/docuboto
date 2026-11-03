# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos de la importación interoperable (E11, RF-INT-05). SQL crudo;
search_path fijado al schema del tenant por get_tenant_conn.

Inserta radicados **preservando el número original** como identidad inmutable (Ac.
AGN 060/2001) — a diferencia de la radicación normal, que genera el número de forma
atómica. La colisión de `tracking_number` (UNIQUE) se detecta y el ítem se OMITE (no
se sobreescribe: la identidad es inmutable)."""

import json
import uuid
from typing import Any

import asyncpg


class ImportRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_tenant_code(self, tenant_slug: str) -> str:
        """Código de entidad del tenant (el de `public.tenants`, o el slug en
        mayúsculas si no está registrado: mismo criterio que la radicación)."""
        code = await self._conn.fetchval(
            "SELECT code FROM public.tenants WHERE slug = $1", tenant_slug
        )
        return code if isinstance(code, str) else tenant_slug.upper()

    async def exists(self, tracking_number: str) -> bool:
        return await self._conn.fetchval(
            "SELECT 1 FROM radicados WHERE tracking_number = $1", tracking_number
        ) is not None

    async def insert_radicado(self, data: dict[str, Any]) -> uuid.UUID:
        """Inserta con el `tracking_number` (y year/sequence/tenant_code derivados de
        él) del paquete. Debe correr dentro de la transacción del import."""
        return await self._conn.fetchval(
            """
            INSERT INTO radicados
                (tracking_number, doc_type, year, sequence, subject, tenant_code,
                 nivel_seguridad, status, pages, origin_dept, dest_dept, sender_name,
                 sender_entity, observations, registered_at, metadata, disposition,
                 clasificacion_fundamento, clasificacion_causal, clasificacion_plazo_meses)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                    $16::jsonb,$17::jsonb,$18,$19,$20)
            RETURNING id
            """,
            data["tracking_number"],
            data["doc_type"],
            data["year"],
            data["sequence"],
            data["subject"],
            data["tenant_code"],
            data["nivel_seguridad"],
            data.get("status", "registered"),
            data.get("pages", 0),
            data.get("origin_dept"),
            data.get("dest_dept"),
            data.get("sender_name"),
            data.get("sender_entity"),
            data.get("observations"),
            data.get("registered_at"),
            json.dumps(data.get("metadata") or {}),
            json.dumps(data["disposition"]) if data.get("disposition") else None,
            data.get("clasificacion_fundamento"),
            data.get("clasificacion_causal"),
            data.get("clasificacion_plazo_meses"),
        )

    async def insert_anexo(
        self,
        *,
        radicado_id: uuid.UUID,
        file_id: uuid.UUID,
        filename: str,
        file_size: int | None,
        mime_type: str | None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO anexos (radicado_id, file_id, filename, file_size, mime_type) "
            "VALUES ($1,$2,$3,$4,$5)",
            radicado_id,
            file_id,
            filename,
            file_size,
            mime_type,
        )

    async def id_by_tracking(self, tracking_number: str) -> uuid.UUID | None:
        return await self._conn.fetchval(
            "SELECT id FROM radicados WHERE tracking_number = $1", tracking_number
        )

    async def set_responde_a(self, radicado_id: uuid.UUID, antecedente_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE radicados SET responde_a = $2 WHERE id = $1", radicado_id, antecedente_id
        )

    # --- cola de purga de binarios huérfanos (migración 038, AREA-09-14) ----------

    async def registrar_huerfanos(
        self,
        import_uuid: uuid.UUID,
        actor: uuid.UUID | None,
        filas: list[tuple[uuid.UUID, str | None]],
        subido_desde: Any,
    ) -> None:
        """Encola los binarios sin radicado. Idempotente por (import_uuid, file_id)."""
        for file_id, sha256 in {f: h for f, h in filas}.items():
            await self._conn.execute(
                "INSERT INTO import_binario_huerfano "
                "(import_uuid, file_id, sha256, actor, subido_desde) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (import_uuid, file_id) DO NOTHING",
                import_uuid,
                file_id,
                sha256,
                actor,
                subido_desde,
            )

    async def huerfanos_pendientes(
        self, import_uuid: uuid.UUID | None, limite: int
    ) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT import_uuid, file_id, sha256, actor, subido_desde, intentos "
                "FROM import_binario_huerfano WHERE estado = 'pendiente' "
                "AND ($1::uuid IS NULL OR import_uuid = $1) ORDER BY created_at LIMIT $2",
                import_uuid,
                limite,
            )
        )

    async def resolver_huerfanos(
        self, import_uuid: uuid.UUID, file_ids: list[uuid.UUID], estado: str
    ) -> list[uuid.UUID]:
        """Pasa a un estado terminal SOLO las filas aún 'pendiente' y devuelve cuáles
        transitaron (el asiento se escribe solo por ellas: dos barridos que coinciden
        no duplican la traza)."""
        rows = await self._conn.fetch(
            "UPDATE import_binario_huerfano SET estado = $3, resuelto_at = now(), "
            "ultimo_error = NULL WHERE import_uuid = $1 AND file_id = ANY($2::uuid[]) "
            "AND estado = 'pendiente' RETURNING file_id",
            import_uuid,
            file_ids,
            estado,
        )
        return [r["file_id"] for r in rows]

    async def fallar_huerfanos(
        self,
        import_uuid: uuid.UUID,
        file_ids: list[uuid.UUID],
        error: str,
        max_intentos: int,
    ) -> list[asyncpg.Record]:
        """Un intento fallido: suma el intento y, al agotar el máximo, pasa a 'agotado'.

        Devuelve SOLO las filas que acaban de pasar a 'agotado' (`file_id`, `intentos`,
        `ultimo_error`): es el acto que el llamante debe asentar, en la misma
        transacción. Una fila que sigue 'pendiente' no es un acto, es un reintento."""
        rows = await self._conn.fetch(
            "UPDATE import_binario_huerfano SET intentos = intentos + 1, ultimo_error = $3, "
            "estado = CASE WHEN intentos + 1 >= $4 THEN 'agotado' ELSE estado END, "
            "resuelto_at = CASE WHEN intentos + 1 >= $4 THEN now() ELSE resuelto_at END "
            "WHERE import_uuid = $1 AND file_id = ANY($2::uuid[]) AND estado = 'pendiente' "
            "RETURNING file_id, intentos, ultimo_error, estado",
            import_uuid,
            file_ids,
            error[:500],
            max_intentos,
        )
        return [r for r in rows if r["estado"] == "agotado"]
