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
from datetime import datetime
from typing import Any

import asyncpg


class ImportRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

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
