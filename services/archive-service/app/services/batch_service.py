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
            # Atomicidad expediente<->auditoría por ÍTEM (espejo de
            # `ArchiveService.create_expediente`): secuencia + INSERT
            # expediente + asiento forense + vínculo de radicados + contador
            # de éxito son un único acto de negocio, todo o nada. Envuelto
            # POR ÍTEM (nunca el bucle entero): así un ítem que revierte no
            # retiene el advisory lock de auditoría del tenant durante el
            # resto del job, y el consecutivo de un ítem fallido no deja
            # huecos (el `INSERT ... sequences` también revierte). El
            # manejo de fallo (`except`) queda deliberadamente FUERA del
            # `async with`: la tx del ítem ya fue revertida por asyncpg en
            # ese punto, así que los UPDATE de 'failed'/`failed_items`
            # corren en su propio statement autocommit y SÍ persisten (si
            # estuvieran dentro, se revertirían con el resto y asyncpg
            # tampoco permite ejecutar más sentencias en una tx abortada).
            try:
                data = json.loads(item["payload"])
                exp = BatchExpedienteCreate(**data)

                async with self.conn.transaction():
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

                    # ADR-026 (Increment 2) -- NUEVO MODO DE FALLO, documentado
                    # aquí a propósito (ADR-026 §"Impacto en lo existente" #5):
                    # este INSERT no declara `origen`, así que TODO expediente
                    # de este import cae al `DEFAULT 'corriente'` de la
                    # migración 041. Si `trd_id` resuelve a una TVD, la Capa 4
                    # (`trg_expedientes_pin_tipo`, relajada por esa misma
                    # migración a `origen <-> tipo_instrumento`) lo RECHAZA --
                    # ya no con el mensaje ESTRICTO de la 039 ("el amarre de
                    # fondo acumulado llega en el Increment 2"), sino con el de
                    # la 041 ("un expediente origen=corriente no puede
                    # amarrarse a la TVD ..."), y el `except` de más abajo lo
                    # captura como fallo de ese ítem (mismo tratamiento que
                    # cualquier otra violación, `failed_items`/asiento
                    # 'archive.batch_item_failed' -- no revienta el lote
                    # entero). Es el comportamiento BUSCADO: un import masivo
                    # de fondo acumulado necesita declarar `origen` y las
                    # fechas extremas por ítem, que este método NO hace
                    # todavía -- soportarlo queda fuera del alcance de este
                    # incremento (ADR-026 no lo pide explícitamente para
                    # `batch_service`, solo exige documentar el modo de
                    # fallo). Antes de la 039/041, un `trd_id` de TVD ni
                    # siquiera podía existir; antes de la 041 en concreto,
                    # CUALQUIER TVD fallaba aquí (Capa 4 estricta) -- después
                    # de la 041 solo fallan las TVD, no las TRD, que es la
                    # combinación correcta para un import declarado
                    # `corriente` por defecto.
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

                    # Link radicados if provided. Este INSERT es independiente
                    # del de `ArchiveRepository.link_radicado` (creación por
                    # lote de expedientes NUEVOS, no vínculo a uno existente)
                    # pero escribe la MISMA tabla — debe asignar
                    # `orden_documental` ESTABLE igual que el camino normal
                    # (E15 perfil v3, RT-15 art. 4.3.2.3), si no el índice
                    # electrónico emitiría <orden> vacío/None para estos
                    # vínculos. El MAX+1 de cada iteración ya ve los INSERT
                    # anteriores del mismo expediente (misma tx/conexión).
                    #
                    # Decisión (hallazgo Baja, cierre RT-15): a diferencia de
                    # `ArchiveRepository.link_radicado`, este MAX+1 NO usa
                    # `pg_advisory_xact_lock` — se omite deliberadamente
                    # porque `exp_id` es un expediente RECIÉN creado en esta
                    # misma transacción/lote (único escritor secuencial,
                    # nadie más puede referenciarlo todavía). El índice único
                    # parcial `ux_exp_rad_orden` (migración 018) sigue siendo
                    # la red de seguridad: si de todos modos hubiera
                    # colisión, el INSERT de abajo fallaría y el ítem del
                    # lote quedaría 'failed' (no se asignaría un orden
                    # duplicado, y la tx del ítem revierte todo lo demás). Si
                    # en el futuro se permite vincular por lote a expedientes
                    # YA existentes (no creados en este mismo job), este
                    # bloque deja de ser seguro y debe adoptar el mismo lock,
                    # o delegar en `ArchiveRepository.link_radicado`.
                    for i, (rid, tn) in enumerate(
                        zip(exp.radicado_ids, exp.tracking_numbers or [], strict=False)
                    ):
                        tn_val = tn if i < len(exp.tracking_numbers) else str(rid)
                        # E15 perfil v4 (R1): dependencia productora alineada
                        # por ÍNDICE con `radicado_ids` (listas paralelas
                        # opcionales, igual que `tracking_numbers`); fuera de
                        # rango o ausente -> None (no declarada para ese
                        # radicado).
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

                    # Auditoría forense transversal (E08 §9, Canal A) — espejo
                    # de `ArchiveService.create_expediente`. El camino normal
                    # deja este asiento por expediente creado; el batch lo
                    # omitía por completo (INSERT propio, fuera de
                    # `ArchiveService`), un hueco de trazabilidad para
                    # instrumentos de creación masiva (Ley 594/2000 arts.
                    # 21-24). Usa `self.conn` (la conexión propia de la tarea
                    # detached, ver `routers/batch.py::_run_batch_job` /
                    # `acquire_tenant`) — nunca la conexión request-scoped.
                    #
                    # Va al FINAL de la tx del ítem (tras vincular radicados y
                    # marcar éxito), NO justo tras el INSERT: `audit.append`
                    # abre su propia sub-transacción con `pg_advisory_xact_lock`
                    # (por tenant), que anidada degrada a SAVEPOINT y retiene el
                    # lock hasta el commit del ítem. Ponerlo al final minimiza
                    # la ventana en que ese lock serializa la auditoría del
                    # tenant (no cubre el bucle de radicados). El atributo de
                    # atomicidad se conserva: si algo del ítem falla, la tx
                    # revierte también este asiento (no queda traza de un
                    # expediente que no existe) — por eso ya no hace falta
                    # auditar "temprano". Espejo exacto de `create_expediente`,
                    # donde el asiento es el último write del bloque atómico.
                    await audit.append(
                        self.conn,
                        tenant_slug=self.tenant_slug,
                        service=_SERVICE,
                        action="archive.expediente_created",
                        object_type="expediente",
                        object_ref=code,
                        actor=str(created_by) if created_by else None,
                        payload={
                            "id": str(exp_id),
                            "nivel_seguridad": exp_row["nivel_seguridad"],
                        },
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
