# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Batch operations service for bulk document creation.

Auditoría (ADR-008/010): lo que se crea aquí son radicados — documentos
oficiales con número inmutable (Ley 594/2000, Acuerdo AGN 060/2001) — así que
cada uno creado por el lote deja su propio asiento en ``public.audit_log``,
espejo exacto de ``DocumentService.create`` (misma ``action``,
``object_type``/``object_ref``/``payload``) y del gemelo
``archive-service/app/services/batch_service.py::process_batch_expedientes``
(mismo patrón de granularidad y atomicidad por-ítem).

Granularidad — UN asiento por radicado creado, no uno por lote: un lote de
500 produce hasta 500 asientos encadenados por hash. Se descarta "uno por
lote" porque el índice electrónico y cualquier auditoría forense necesitan
poder señalar el asiento de UN tracking_number concreto (RT-15/E15); un
asiento agregado por lote perdería esa granularidad y sería inconsistente con
el camino síncrono (`DocumentService.create`), que ya audita por radicado.

Actor — ``created_by`` es el ``X-User-Id`` validado por el router al
ENCOLAR el job (``create_batch_documents``), nunca un campo del body ni algo
resuelto en el propio ``process_batch_documents`` (que corre en una tarea
detached, sin request). Se recupera con
``UPDATE batch_jobs ... RETURNING created_by`` al arrancar el procesamiento
— mismo patrón que ``BatchArchiveService.process_batch_expedientes``. Un
asiento con actor nulo registraría que algo pasó, pero no quién lo hizo.

Fallos parciales — cada ítem está envuelto en su propio
``async with self.conn.transaction():`` (secuencia + INSERT radicado +
UPDATE item 'success' + contador + ``audit.append``, todo o nada por ítem).
El manejo de fallo (`except`) queda FUERA de ese bloque: si algo del ítem
falla, su transacción revierte completa (incluida la auditoría) y el
`UPDATE ... status='failed'` corre en su propio statement autocommit — así
el resultado final (N asientos, M fallidos) refleja lo que REALMENTE entró,
no la intención del lote.

Reanudación y reproceso (AREA-09-15) — el lote ya no depende de que el proceso que
lo encoló siga vivo:

- Cada ítem es idempotente por construcción: su transacción reclama la fila
  (`FOR UPDATE`, solo si sigue 'pending'), crea el radicado, la marca 'success' con
  su `result_id` y asienta, todo o nada. Un ítem ya radicado conserva su número y
  jamás se vuelve a numerar; uno interrumpido no dejó nada (la tx revirtió).
- Un lote se procesa bajo un advisory lock de SESIÓN por (tenant, lote): dos réplicas
  (o el barrido y la petición) no lo procesan a la vez; si el proceso muere, la
  conexión cae y el lock se libera, y el barrido lo reanuda.
- Los ítems que fallan quedan 'failed' con su motivo (`error_message`, `error_tipo`) y
  `intentos`. El lote termina `completed` (sin fallos) o `completado_con_errores`.
  `failed` queda para el lote que reventó fuera de los ítems (`_marcar_job_fallido`).
- `reprocesar` devuelve a 'pending' SOLO los ítems fallidos (los faltantes ya lo
  están) y deja asiento; no toca los 'success'.
"""

import json
from datetime import datetime
from uuid import UUID

import asyncpg
from orpycamcp_common import audit

from app.core.constants import SYSTEM_RECONCILER_ID
from app.repositories.metadata_repository import MetadataRepository
from app.schemas.batch import BatchDocumentCreate, BatchDocumentsRequest
from app.services.document_service import _zona_de_radicacion
from app.services.metadata_service import validate_metadata

_SERVICE = "document-service"

# Espacio de claves de los advisory locks de lote (el 2.º entero es el hash de
# schema+job). Distinto del de otros usos para que no se bloqueen entre sí.
_ESPACIO_LOCK_LOTE = 0x4C4F5445  # "LOTE"

ESTADOS_EN_CURSO = ("pending", "processing")
ESTADO_CON_ERRORES = "completado_con_errores"

# Tope del motivo guardado por ítem (el texto de una excepción puede ser largo).
_MAX_ERROR = 1000
# Tope del motivo que se copia al asiento `document.lote_reprocesado` por ítem.
_MAX_ERROR_ASIENTO = 500


# Quién puede ver o reprocesar un lote: su creador, o quien tiene PERM_RADI con
# min_crud=3 (`$3`). `$2` = el llamante. Va en el WHERE: para los demás el lote no
# existe (404 neutro `lote_no_encontrado`). Solo lotes de radicados: `batch_jobs` se
# comparte por nombre con archive-service (job_type 'expedientes').
_DEL_LLAMANTE = "job_type = 'documents' AND (created_by = $2 OR $3::boolean)"


class LoteError(Exception):
    """Error de negocio del lote, con el código que el router traduce a HTTP."""

    def __init__(self, codigo: str, detalle: str, status: int) -> None:
        self.codigo, self.detalle, self.status = codigo, detalle, status
        super().__init__(detalle)


class BatchService:
    """Service for batch document operations."""

    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self.conn = conn
        self.tenant_slug = tenant_slug

    async def create_batch_documents(self, request: BatchDocumentsRequest, user_id: UUID) -> UUID:
        """
        Create a batch job for multiple documents.

        Returns job_id for status tracking.
        """
        job_id = UUID(int=0)  # Will be generated by DB

        async with self.conn.transaction():
            # Create batch_jobs entry
            job_id = await self.conn.fetchval(
                """
                INSERT INTO batch_jobs
                (job_type, status, created_by, total_items)
                VALUES ('documents', 'pending', $1, $2)
                RETURNING id
                """,
                user_id,
                len(request.documents),
            )

            # Create batch_job_items for each document (pending)
            for idx, doc in enumerate(request.documents):
                await self.conn.execute(
                    """
                    INSERT INTO batch_job_items
                    (job_id, item_index, payload, status)
                    VALUES ($1, $2, $3, 'pending')
                    """,
                    job_id,
                    idx,
                    json.dumps(doc.model_dump()),
                )

        return job_id

    # -- exclusión mutua por lote ------------------------------------------------

    async def _tomar_lock(self, job_id: UUID) -> bool:
        """Advisory lock de SESIÓN por (schema del tenant, lote). No bloquea: `False` =
        otro proceso lo tiene. Se libera al soltarlo o al caer la conexión (si el
        proceso muere, el lote queda libre para el barrido)."""
        return bool(
            await self.conn.fetchval(
                "SELECT pg_try_advisory_lock($1::int, hashtext(current_schema() || ':' || $2::text))",
                _ESPACIO_LOCK_LOTE,
                str(job_id),
            )
        )

    async def _soltar_lock(self, job_id: UUID) -> None:
        await self.conn.fetchval(
            "SELECT pg_advisory_unlock($1::int, hashtext(current_schema() || ':' || $2::text))",
            _ESPACIO_LOCK_LOTE,
            str(job_id),
        )

    async def process_batch_documents(self, job_id: UUID, *, reanudado: bool = False) -> bool:
        """Procesa los ítems pendientes de un lote. `False` si otro proceso lo tiene
        (no se tocó nada). Seguro de reentrar: solo toma ítems 'pending', y cada uno
        es idempotente (ver docstring del módulo)."""
        if not await self._tomar_lock(job_id):
            return False
        try:
            await self._procesar(job_id, reanudado=reanudado)
        finally:
            await self._soltar_lock(job_id)
        return True

    async def _procesar(self, job_id: UUID, *, reanudado: bool) -> None:
        """Process pending documents in a batch job."""
        # Update job status to processing y recupera el actor del acto
        # (`create_batch_documents`, X-User-Id validado por el router; o quien pidió
        # el último reproceso) —es el único punto de esta tarea detached donde ese
        # actor está disponible, así que se guarda para atribuir cada asiento.
        created_by: UUID | None = await self.conn.fetchval(
            """
            UPDATE batch_jobs
            SET status = 'processing', started_at = COALESCE(started_at, now()),
                completed_at = NULL
            WHERE id = $1
            RETURNING COALESCE(reprocesado_por, created_by)
            """,
            job_id,
        )
        # Quién figura en los asientos. Una reanudación AUTOMÁTICA (el barrido) no es un
        # acto de la persona que encoló el lote: el asiento va a nombre del sistema y
        # `en_nombre_de` dice de quién es el lote. `registered_by` de los radicados sí
        # sigue siendo esa persona (el lote es suyo; de ella depende la tenencia después).
        responsable = str(created_by) if created_by else None
        actor = str(SYSTEM_RECONCILER_ID) if reanudado else responsable
        canal = "system" if reanudado else "api"
        en_nombre_de = {"en_nombre_de": responsable} if reanudado else {}

        # Fetch all pending items
        items = await self.conn.fetch(
            """
            SELECT id, item_index, payload
            FROM batch_job_items
            WHERE job_id = $1 AND status = 'pending'
            ORDER BY item_index
            """,
            job_id,
        )

        if reanudado:
            # Un lote interrumpido que el barrido retoma deja constancia: nadie lo pidió
            # en este momento, así que sin asiento nadie sabría por qué hay radicados
            # nuevos en una fecha posterior a la del lote.
            async with self.conn.transaction():
                await self.conn.execute(
                    "UPDATE batch_jobs SET reanudaciones = reanudaciones + 1 WHERE id = $1",
                    job_id,
                )
                await audit.append(
                    self.conn,
                    tenant_slug=self.tenant_slug,
                    service=_SERVICE,
                    action="document.lote_reanudado",
                    object_type="batch_job",
                    object_ref=str(job_id),
                    actor=actor,
                    canal=canal,
                    payload={
                        "job_id": str(job_id),
                        "items_pendientes": len(items),
                        **en_nombre_de,
                    },
                )

        # Process each item. Envuelto POR ÍTEM (nunca el bucle entero): así un
        # ítem que revierte no retiene el advisory lock de auditoría del
        # tenant (dentro de `audit.append`) durante el resto del job, y el
        # consecutivo de un ítem fallido no deja huecos (el
        # `INSERT ... tracking_sequences` también revierte).
        for item in items:
            try:
                payload = json.loads(item["payload"])
                doc_data = BatchDocumentCreate(**payload)

                async with self.conn.transaction():
                    # Reclamo del ítem: la fila se bloquea y solo se procesa si sigue
                    # 'pending'. Es lo que hace idempotente la reanudación y el
                    # reproceso: un ítem ya radicado ('success') conserva su número y
                    # nunca se vuelve a numerar, aunque dos procesos coincidieran.
                    if (
                        await self.conn.fetchval(
                            "SELECT status FROM batch_job_items WHERE id = $1 FOR UPDATE",
                            item["id"],
                        )
                        != "pending"
                    ):
                        continue

                    # Metadatos (E03, ADR-007): el lote no es una puerta lateral.
                    # Si el tipo documental tiene plantilla activa, se valida
                    # contra ella como en `DocumentService.create` y se guarda la
                    # versión usada. El esquema del lote no trae metadatos, así que
                    # una plantilla con campos obligatorios hace fallar el ítem
                    # (`failed` con el motivo): radicar por el lote y saltarse la
                    # plantilla dejaba radicados que la radicación normal rechaza.
                    metadata_template_id: UUID | None = None
                    if doc_data.doc_class:
                        template = await MetadataRepository(self.conn).get_active_template(
                            doc_data.doc_class
                        )
                        if template is not None:
                            validate_metadata({}, template["json_schema"])
                            metadata_template_id = template["id"]

                    # Dependencias por código (Ac. 060 §5.2): el nombre se
                    # denormaliza como en `DocumentService.create`. El lote corre
                    # sin request, así que se lee del MISMO schema del tenant en
                    # vez de saltar a tenant-service. Una inexistente o inactiva
                    # hace fallar el ítem ANTES de gastar el consecutivo.
                    dest_dept = doc_data.dest_dept
                    origin_dept: str | None = None
                    if doc_data.dest_dept_code is not None:
                        dest_dept = await self._nombre_dependencia(doc_data.dest_dept_code)
                    if doc_data.origin_dept_code is not None:
                        origin_dept = await self._nombre_dependencia(doc_data.origin_dept_code)

                    # Get tenant code and next sequence
                    tenant_code = await self.conn.fetchval(
                        "SELECT code FROM tenants WHERE slug = $1",
                        self.tenant_slug,
                    )
                    # El año se decide en la zona de radicación, como en
                    # `DocumentService.create`: en UTC, un lote entre las 19:00 y
                    # las 24:00 del 31-dic en Bogotá numeraba en el año siguiente.
                    year = datetime.now(tz=_zona_de_radicacion()).year
                    sequence = await self._get_next_sequence(year, doc_data.doc_type)

                    # Create radicado
                    tracking_number = (
                        f"{year}-{tenant_code}-{doc_data.doc_type}-{sequence:06d}"
                    )
                    radicado = await self.conn.fetchrow(
                        """
                        INSERT INTO radicados
                        (tracking_number, doc_type, subject, doc_class, dest_dept,
                         sender_name, sender_entity, pages, observations, status,
                         year, sequence, tenant_code, registered_by,
                         origin_dept, origin_dept_code, dest_dept_code,
                         metadata_template_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'registered',
                                $10, $11, $12, $13, $14, $15, $16, $17)
                        RETURNING id, nivel_seguridad
                        """,
                        tracking_number,
                        doc_data.doc_type,
                        doc_data.subject,
                        doc_data.doc_class,
                        dest_dept,
                        doc_data.sender_name,
                        doc_data.sender_entity,
                        # `radicados.pages` es NOT NULL DEFAULT 0, y el camino
                        # síncrono lo respeta porque `DocumentCreate` declara
                        # `pages: int = 0`. El esquema del lote lo declaró
                        # `int | None = None` —el formulario lo ofrece como
                        # «(opcional)»— y el INSERT mandaba ese `None` a la
                        # columna: TODO ítem sin páginas violaba la restricción
                        # y el lote entero fallaba. Se normaliza aquí, al borde
                        # de la escritura, para no romper el contrato con un
                        # cliente que ya manda `null`.
                        doc_data.pages if doc_data.pages is not None else 0,
                        doc_data.observations,
                        year,
                        sequence,
                        # `tenant_code` y `registered_by` son NOT NULL y el
                        # camino síncrono (`DocumentRepository.create`) los
                        # escribe siempre. Este INSERT los omitía: el gemelo
                        # divergía en las dos columnas que identifican de quién
                        # es el radicado y quién lo produjo, que en un documento
                        # oficial no son campos accesorios. `tenant_code` ya
                        # estaba leído arriba para componer el número.
                        tenant_code,
                        created_by,
                        origin_dept,
                        doc_data.origin_dept_code,
                        doc_data.dest_dept_code,
                        metadata_template_id,
                    )
                    radicado_id = radicado["id"]

                    # Update item with success
                    await self.conn.execute(
                        """
                        UPDATE batch_job_items
                        SET status = 'success', result_id = $1, processed_at = now(),
                            intentos = intentos + 1, error_message = NULL, error_tipo = NULL
                        WHERE id = $2
                        """,
                        radicado_id,
                        item["id"],
                    )
                    await self._increment_processed(job_id)

                    # Asiento forense por radicado creado — espejo exacto de
                    # `DocumentService.create` (misma action/object_type,
                    # Ley 594/2000 arts. 21-24). Al final de la tx del ítem
                    # (tras el UPDATE de éxito), como en
                    # `BatchArchiveService.process_batch_expedientes`: si algo
                    # del ítem falla, la tx revierte también este asiento —
                    # no queda traza de un radicado que no existe.
                    await audit.append(
                        self.conn,
                        tenant_slug=self.tenant_slug,
                        service=_SERVICE,
                        action="document.radicado_created",
                        object_type="radicado",
                        object_ref=tracking_number,
                        actor=actor,
                        canal=canal,
                        payload={
                            "doc_type": doc_data.doc_type,
                            "id": str(radicado_id),
                            "nivel_seguridad": radicado["nivel_seguridad"],
                            # De dónde salió: el lote y su ítem se borran a los 90 días
                            # (retención), el asiento no. Sin esto, un radicado de lote
                            # era indistinguible de uno radicado a mano.
                            "origen": "lote",
                            "job_id": str(job_id),
                            "item_index": item["item_index"],
                            **en_nombre_de,
                        },
                    )

            except Exception as exc:
                # El ítem queda MARCADO (estado + motivo + clase del error + intento)
                # para revisarlo o reprocesarlo; su tx revirtió completa.
                await self.conn.execute(
                    """
                    UPDATE batch_job_items
                    SET status = 'failed', error_message = $1, processed_at = now(),
                        intentos = intentos + 1, error_tipo = $3
                    WHERE id = $2
                    """,
                    str(exc)[:_MAX_ERROR],
                    item["id"],
                    type(exc).__name__,
                )
                await self._increment_failed(job_id)

        # Los contadores se RECUENTAN desde los ítems (autoridad única): con el
        # reproceso, un ítem fallido que luego sale bien no puede seguir sumando fallos.
        await self._recontar(job_id)
        job = await self.conn.fetchrow(
            """
            SELECT total_items, processed_items, failed_items
            FROM batch_jobs
            WHERE id = $1
            """,
            job_id,
        )

        # `failed` NO se usa aquí: es del lote que revienta fuera de los ítems
        # (`_marcar_job_fallido`). Con ítems fallidos el lote TERMINA, con errores.
        if job["failed_items"] > 0:
            status = ESTADO_CON_ERRORES
        elif job["processed_items"] >= job["total_items"]:
            status = "completed"
        else:
            status = "pending"  # quedaron ítems sin tomar: el barrido lo retoma
        await self.conn.execute(
            """
            UPDATE batch_jobs
            SET status = $1::varchar,
                completed_at = CASE WHEN $1::varchar = 'pending' THEN NULL ELSE now() END
            WHERE id = $2
            """,
            status,
            job_id,
        )

    # -- reproceso, barrido y retención --------------------------------------------

    async def reprocesar(self, job_id: UUID, actor: UUID, *, ve_ajenos: bool) -> int:
        """Devuelve a 'pending' los ítems FALLIDOS del lote para volver a intentarlos y
        deja el asiento `document.lote_reprocesado`. Los 'success' no se tocan (cada uno
        conserva su número). Devuelve cuántos ítems quedan por procesar (fallidos +
        faltantes). El procesamiento lo lanza el llamante (`process_batch_documents`).

        Solo el CREADOR del lote o quien tiene `PERM_RADI` con `min_crud=3`
        (`ve_ajenos`) puede reprocesarlo: el predicado va en el `WHERE`, y para
        cualquier otro el lote «no existe» (404 `lote_no_encontrado`, el mismo que un
        id inventado). La comprobación va ANTES de tomar el lock, para que un tercero no
        distinga «existe» por un 409 `lote_en_proceso`.

        409 `lote_en_proceso` si alguien lo está procesando; 409 `nada_que_reprocesar`
        si no hay fallidos ni faltantes. Mantiene el lock del lote durante el cambio
        para que ningún proceso lo recoja a medias."""
        if not await self.conn.fetchval(
            f"SELECT 1 FROM batch_jobs WHERE id = $1 AND {_DEL_LLAMANTE}", job_id, actor, ve_ajenos
        ):
            raise LoteError("lote_no_encontrado", "No existe el trabajo solicitado", 404)
        if not await self._tomar_lock(job_id):
            raise LoteError("lote_en_proceso", "El lote se está procesando ahora mismo", 409)
        try:
            async with self.conn.transaction():
                job = await self.conn.fetchrow(
                    f"SELECT status FROM batch_jobs WHERE id = $1 AND {_DEL_LLAMANTE} "
                    "FOR UPDATE",
                    job_id,
                    actor,
                    ve_ajenos,
                )
                if job is None:
                    raise LoteError("lote_no_encontrado", "No existe el trabajo solicitado", 404)
                fallidos = await self.conn.fetch(
                    "SELECT item_index, error_tipo, error_message, intentos FROM batch_job_items "
                    "WHERE job_id = $1 AND status = 'failed' ORDER BY item_index",
                    job_id,
                )
                faltantes = await self.conn.fetchval(
                    "SELECT count(*) FROM batch_job_items WHERE job_id = $1 AND status = 'pending'",
                    job_id,
                )
                total = len(fallidos) + faltantes
                if total == 0:
                    raise LoteError(
                        "nada_que_reprocesar", "El lote no tiene ítems fallidos ni faltantes", 409
                    )
                await self.conn.execute(
                    "UPDATE batch_job_items SET status = 'pending', error_message = NULL, "
                    "error_tipo = NULL WHERE job_id = $1 AND status = 'failed'",
                    job_id,
                )
                await self.conn.execute(
                    "UPDATE batch_jobs SET status = 'pending', completed_at = NULL, "
                    "error_message = NULL, reprocesado_por = $2, reprocesado_at = now() "
                    "WHERE id = $1",
                    job_id,
                    actor,
                )
                await self._recontar(job_id)
                await audit.append(
                    self.conn,
                    tenant_slug=self.tenant_slug,
                    service=_SERVICE,
                    action="document.lote_reprocesado",
                    object_type="batch_job",
                    object_ref=str(job_id),
                    actor=str(actor),
                    payload={
                        "job_id": str(job_id),
                        "estado_previo": job["status"],
                        # El motivo y los intentos se LIMPIAN en el UPDATE de arriba: el
                        # asiento es lo único que conserva por qué falló cada ítem.
                        "items_fallidos": [
                            {
                                "item_index": r["item_index"],
                                "error_tipo": r["error_tipo"],
                                "error_message": (r["error_message"] or "")[:_MAX_ERROR_ASIENTO],
                                "intentos": r["intentos"],
                            }
                            for r in fallidos
                        ],
                        "items_faltantes": faltantes,
                    },
                    canal=audit.CANAL_PETICION,
                )
        finally:
            await self._soltar_lock(job_id)
        return total

    async def reanudar_interrumpidos(self, despues_de_segundos: int, limite: int = 50) -> int:
        """Retoma los lotes `pending`/`processing` que nadie está procesando (el proceso
        que los llevaba murió o se reinició). Un lote muy reciente se salta: lo está
        arrancando su propia petición. El advisory lock decide quién lo procesa si dos
        réplicas coinciden. Devuelve cuántos lotes reanudó este proceso."""
        ids = await self.conn.fetch(
            """
            SELECT id FROM batch_jobs
            WHERE job_type = 'documents' AND status = ANY($1::text[])
              AND created_at < now() - make_interval(secs => $2)
            ORDER BY created_at LIMIT $3
            """,
            list(ESTADOS_EN_CURSO),
            float(despues_de_segundos),
            limite,
        )
        reanudados = 0
        for fila in ids:
            if await self.process_batch_documents(fila["id"], reanudado=True):
                reanudados += 1
        return reanudados

    async def purgar_lotes_antiguos(self, dias: int) -> int:
        """Retención: borra los lotes COMPLETADOS sin errores de hace más de `dias`
        (los ítems caen en cascada). Los `completado_con_errores`/`failed` se conservan:
        alguien tiene que revisarlos. Los radicados creados y sus asientos no se tocan.
        `dias <= 0` desactiva la retención."""
        if dias <= 0:
            return 0
        return int(
            await self.conn.fetchval(
                """
                WITH borrados AS (
                    DELETE FROM batch_jobs
                    WHERE job_type = 'documents' AND status = 'completed'
                      AND completed_at < now() - make_interval(days => $1)
                    RETURNING 1
                ) SELECT count(*) FROM borrados
                """,
                dias,
            )
            or 0
        )

    async def _recontar(self, job_id: UUID) -> None:
        await self.conn.execute(
            """
            UPDATE batch_jobs SET
                processed_items = (SELECT count(*) FROM batch_job_items
                                   WHERE job_id = $1 AND status = 'success'),
                failed_items = (SELECT count(*) FROM batch_job_items
                                WHERE job_id = $1 AND status = 'failed')
            WHERE id = $1
            """,
            job_id,
        )

    async def get_job_status(
        self, job_id: UUID, *, caller: UUID, clearance: int, ve_ajenos: bool
    ) -> dict:
        """Estado de un lote. `{}` = no existe O no es del llamante (404 neutro).

        Solo su creador, o quien tiene `PERM_RADI` con `min_crud=3` (`ve_ajenos`), lo
        ve: el predicado va en el `WHERE`. Y el `subject` de cada ítem lleva el gate de
        clearance (no-read-up, RF-SEG-08) en el SQL: el asunto de un radicado del lote
        que DESPUÉS se reclasificó por encima del nivel del llamante no se devuelve
        (`NULL`); el de un ítem sin radicado (aún no creado o fallido) sí, porque es lo
        que el propio llamante envió."""
        job = await self.conn.fetchrow(
            f"""
            SELECT id, job_type, status, total_items, processed_items, failed_items,
                   created_at, started_at, completed_at, error_message,
                   reanudaciones, reprocesado_at
            FROM batch_jobs
            WHERE id = $1 AND {_DEL_LLAMANTE}
            """,
            job_id,
            caller,
            ve_ajenos,
        )

        if not job:
            return {}

        items = await self.conn.fetch(
            """
            SELECT i.item_index, i.status, i.result_id, i.error_message, i.error_tipo,
                   i.intentos,
                   CASE WHEN r.id IS NULL OR r.nivel_seguridad <= $2
                        THEN i.payload->>'subject' END AS subject,
                   CASE WHEN r.nivel_seguridad <= $2 THEN r.nivel_seguridad END AS nivel_seguridad
            FROM batch_job_items i
            LEFT JOIN radicados r ON r.id = i.result_id
            WHERE i.job_id = $1
            ORDER BY i.item_index
            """,
            job_id,
            clearance,
        )
        detalle = [
            {
                "item_index": item["item_index"],
                "status": item["status"],
                "result_id": item["result_id"],
                "error_message": item["error_message"],
                "error_tipo": item["error_tipo"],
                "intentos": item["intentos"],
                "subject": item["subject"],
                "nivel_seguridad": item["nivel_seguridad"],
            }
            for item in items
        ]
        pendientes = sum(1 for i in detalle if i["status"] == "pending")
        fallidos = sum(1 for i in detalle if i["status"] == "failed")

        return {
            "job_id": job["id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "total_items": job["total_items"],
            "processed_items": job["processed_items"],
            "failed_items": job["failed_items"],
            "pending_items": pendientes,
            # «Procesar los faltantes»: hay algo que reintentar y el lote ya terminó
            # (los que siguen en curso los retoma el barrido solo).
            "reprocesable": (pendientes + fallidos) > 0
            and job["status"] in (ESTADO_CON_ERRORES, "failed"),
            "reanudaciones": job["reanudaciones"],
            "reprocesado_at": job["reprocesado_at"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "error_message": job["error_message"],
            "items": detalle,
            "errores": [i for i in detalle if i["status"] == "failed"],
        }

    async def _nombre_dependencia(self, codigo: int) -> str:
        """Nombre de una dependencia ACTIVA del tenant, o `ValueError` (el ítem falla)."""
        nombre = await self.conn.fetchval(
            "SELECT nombre FROM dependencias WHERE codigo = $1 AND activo", codigo
        )
        if nombre is None:
            raise ValueError(f"La dependencia {codigo} no existe o está inactiva")
        return str(nombre)

    async def _get_next_sequence(self, year: int, doc_type: str) -> int:
        """Get next sequence number for tracking number."""
        return await self.conn.fetchval(
            """
            INSERT INTO tracking_sequences (year, doc_type, last_seq)
            VALUES ($1, $2, 1)
            ON CONFLICT (year, doc_type)
            DO UPDATE SET last_seq = tracking_sequences.last_seq + 1
            RETURNING last_seq
            """,
            year,
            doc_type,
        )

    async def _increment_processed(self, job_id: UUID) -> None:
        """Increment processed items count."""
        await self.conn.execute(
            """
            UPDATE batch_jobs
            SET processed_items = processed_items + 1
            WHERE id = $1
            """,
            job_id,
        )

    async def _increment_failed(self, job_id: UUID) -> None:
        """Increment failed items count."""
        await self.conn.execute(
            """
            UPDATE batch_jobs
            SET failed_items = failed_items + 1
            WHERE id = $1
            """,
            job_id,
        )
