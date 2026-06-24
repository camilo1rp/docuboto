# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Batch service for bulk expediente creation."""

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from app.schemas.batch import BatchExpedienteCreate, BatchExpedientesRequest


class BatchArchiveService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self.conn = conn
        self.tenant_slug = tenant_slug

    async def create_batch_expedientes(
        self, request: BatchExpedientesRequest, user_id: UUID
    ) -> UUID:
        """Queue a batch expediente job. Returns job_id."""
        async with self.conn.transaction():
            job_id: UUID = await self.conn.fetchval(
                """
                INSERT INTO batch_jobs (job_type, status, created_by, total_items)
                VALUES ('expedientes', 'pending', $1, $2)
                RETURNING id
                """,
                user_id,
                len(request.expedientes),
            )
            for idx, exp in enumerate(request.expedientes):
                await self.conn.execute(
                    """
                    INSERT INTO batch_job_items (job_id, item_index, payload, status)
                    VALUES ($1, $2, $3, 'pending')
                    """,
                    job_id,
                    idx,
                    json.dumps(
                        {
                            **exp.model_dump(mode="json"),
                            "radicado_ids": [str(r) for r in exp.radicado_ids],
                            "trd_serie_id": str(exp.trd_serie_id) if exp.trd_serie_id else None,
                        }
                    ),
                )
        return job_id

    async def process_batch_expedientes(self, job_id: UUID) -> None:
        """Process queued expediente items."""
        await self.conn.execute(
            "UPDATE batch_jobs SET status='processing', started_at=now() WHERE id=$1",
            job_id,
        )
        items = await self.conn.fetch(
            "SELECT id, item_index, payload FROM batch_job_items WHERE job_id=$1 AND status='pending' ORDER BY item_index",
            job_id,
        )
        year = datetime.now(tz=UTC).year

        for item in items:
            try:
                data = json.loads(item["payload"])
                exp = BatchExpedienteCreate(**data)

                # Atomic sequence for expediente code
                seq: int = await self.conn.fetchval(
                    """
                    INSERT INTO expediente_sequences (year, last_seq)
                    VALUES ($1, 1)
                    ON CONFLICT (year)
                    DO UPDATE SET last_seq = expediente_sequences.last_seq + 1
                    RETURNING last_seq
                    """,
                    year,
                )
                code = f"EXP-{year}-{seq:04d}"

                trd_id = UUID(data["trd_serie_id"]) if data.get("trd_serie_id") else None

                exp_id: UUID = await self.conn.fetchval(
                    """
                    INSERT INTO expedientes (code, name, description, trd_serie_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    code,
                    exp.name,
                    exp.description,
                    trd_id,
                )

                # Link radicados if provided
                for i, (rid, tn) in enumerate(
                    zip(exp.radicado_ids, exp.tracking_numbers or [], strict=False)
                ):
                    tn_val = tn if i < len(exp.tracking_numbers) else str(rid)
                    await self.conn.execute(
                        """
                        INSERT INTO expediente_radicados
                            (expediente_id, radicado_id, tracking_number)
                        VALUES ($1, $2, $3)
                        ON CONFLICT DO NOTHING
                        """,
                        exp_id,
                        rid,
                        tn_val,
                    )

                await self.conn.execute(
                    """
                    UPDATE batch_job_items
                    SET status='success', result_id=$1, processed_at=now()
                    WHERE id=$2
                    """,
                    exp_id,
                    item["id"],
                )
                await self.conn.execute(
                    "UPDATE batch_jobs SET processed_items=processed_items+1 WHERE id=$1",
                    job_id,
                )
            except Exception as exc:
                await self.conn.execute(
                    """
                    UPDATE batch_job_items
                    SET status='failed', error_message=$1, processed_at=now()
                    WHERE id=$2
                    """,
                    str(exc),
                    item["id"],
                )
                await self.conn.execute(
                    "UPDATE batch_jobs SET failed_items=failed_items+1 WHERE id=$1",
                    job_id,
                )

        row = await self.conn.fetchrow("SELECT failed_items FROM batch_jobs WHERE id=$1", job_id)
        final_status = "completed" if row["failed_items"] == 0 else "failed"
        await self.conn.execute(
            "UPDATE batch_jobs SET status=$1, completed_at=now() WHERE id=$2",
            final_status,
            job_id,
        )

    async def get_job_status(self, job_id: UUID) -> dict:
        """Return job + items status dict."""
        job = await self.conn.fetchrow(
            """
            SELECT id, job_type, status, total_items, processed_items, failed_items,
                   created_at, started_at, completed_at, error_message
            FROM batch_jobs WHERE id=$1
            """,
            job_id,
        )
        if not job:
            return {}

        items = await self.conn.fetch(
            "SELECT item_index, status, result_id, error_message FROM batch_job_items WHERE job_id=$1 ORDER BY item_index",
            job_id,
        )
        return {
            "job_id": job["id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "total_items": job["total_items"],
            "processed_items": job["processed_items"],
            "failed_items": job["failed_items"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "error_message": job["error_message"],
            "items": [dict(i) for i in items],
        }
