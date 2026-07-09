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
from orpycamcp_common import audit

from app.schemas.batch import BatchExpedienteCreate, BatchExpedientesRequest

_SERVICE = "archive-service"


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
        # `created_by` es la identidad validada por el router (`_require_user`,
        # X-User-Id) al encolar el job — nunca un campo del body — y es el
        # actor que se deja en `audit_log` por cada expediente creado abajo
        # (espejo de `ArchiveService.create_expediente`, Ley 594/2000 arts. 21-24).
        created_by: UUID = await self.conn.fetchval(
            """
            UPDATE batch_jobs SET status='processing', started_at=now()
            WHERE id=$1
            RETURNING created_by
            """,
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

                exp_row = await self.conn.fetchrow(
                    """
                    INSERT INTO expedientes (code, name, description, trd_serie_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id, nivel_seguridad
                    """,
                    code,
                    exp.name,
                    exp.description,
                    trd_id,
                )
                exp_id: UUID = exp_row["id"]

                # Auditoría forense transversal (E08 §9, Canal A) — espejo de
                # `ArchiveService.create_expediente`. El camino normal deja
                # este asiento por expediente creado; el batch lo omitía por
                # completo (INSERT propio, fuera de `ArchiveService`), un
                # hueco de trazabilidad para instrumentos de creación masiva
                # (Ley 594/2000 arts. 21-24). Usa `self.conn` (la conexión
                # propia de la tarea detached, ver `routers/batch.py::
                # _run_batch_job` / `acquire_tenant`) — nunca la conexión
                # request-scoped, que ya fue devuelta al pool para cuando
                # este bucle corre.
                #
                # Va INMEDIATAMENTE tras el INSERT del expediente (que ya está
                # committed, autocommit), ANTES de vincular radicados y de
                # marcar el ítem 'success'/contadores: si el propio
                # `audit.append` fallara, el `except` marca el ítem 'failed'
                # sin haber contado un 'success' (sin doble conteo) y el
                # asiento refleja la creación real del expediente aunque el
                # resto del ítem no complete.
                await audit.append(
                    self.conn,
                    tenant_slug=self.tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_created",
                    object_type="expediente",
                    object_ref=code,
                    actor=str(created_by) if created_by else None,
                    payload={"id": str(exp_id), "nivel_seguridad": exp_row["nivel_seguridad"]},
                )

                # Link radicados if provided. Este INSERT es independiente del
                # de `ArchiveRepository.link_radicado` (creación por lote de
                # expedientes NUEVOS, no vínculo a uno existente) pero escribe
                # la MISMA tabla — debe asignar `orden_documental` ESTABLE
                # igual que el camino normal (E15 perfil v3, RT-15 art.
                # 4.3.2.3), si no el índice electrónico emitiría <orden>
                # vacío/None para estos vínculos. Como el bucle es secuencial
                # sobre una única conexión (sin transacción explícita
                # alrededor, autocommit por statement), el MAX+1 de cada
                # iteración ya ve los INSERT anteriores del mismo expediente.
                #
                # Decisión (hallazgo Baja, cierre RT-15): a diferencia de
                # `ArchiveRepository.link_radicado`, este MAX+1 NO usa
                # `pg_advisory_xact_lock` — se omite deliberadamente porque
                # `exp_id` es un expediente RECIÉN creado en esta misma
                # transacción/lote (único escritor secuencial, nadie más
                # puede referenciarlo todavía). El índice único parcial
                # `ux_exp_rad_orden` (migración 018) sigue siendo la red de
                # seguridad: si de todos modos hubiera colisión, el INSERT de
                # abajo fallaría y el ítem del lote quedaría 'failed' (no se
                # asignaría un orden duplicado). Si en el futuro se permite
                # vincular por lote a expedientes YA existentes (no creados en
                # este mismo job), este bloque deja de ser seguro y debe
                # adoptar el mismo lock, o delegar en
                # `ArchiveRepository.link_radicado`.
                for i, (rid, tn) in enumerate(
                    zip(exp.radicado_ids, exp.tracking_numbers or [], strict=False)
                ):
                    tn_val = tn if i < len(exp.tracking_numbers) else str(rid)
                    # E15 perfil v4 (R1): dependencia productora alineada por
                    # ÍNDICE con `radicado_ids` (listas paralelas opcionales,
                    # igual que `tracking_numbers`); fuera de rango o ausente
                    # -> None (no declarada para ese radicado).
                    dep_codigo = (
                        exp.dependencia_productora_codigos[i]
                        if i < len(exp.dependencia_productora_codigos)
                        else None
                    )
                    dep_nombre = (
                        exp.dependencia_productora_nombres[i]
                        if i < len(exp.dependencia_productora_nombres)
                        else None
                    )
                    await self.conn.execute(
                        """
                        INSERT INTO expediente_radicados
                            (expediente_id, radicado_id, tracking_number,
                             orden_documental, estado,
                             dependencia_productora_codigo, dependencia_productora_nombre)
                        VALUES (
                            $1, $2, $3,
                            (SELECT COALESCE(MAX(orden_documental), 0) + 1
                               FROM expediente_radicados WHERE expediente_id = $1),
                            'vinculado', $4, $5
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        exp_id,
                        rid,
                        tn_val,
                        dep_codigo,
                        dep_nombre,
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
