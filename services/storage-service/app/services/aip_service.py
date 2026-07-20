# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Empaquetado de AIP real: BagIt + PREMIS v3 subido al bucket WORM (E10
Increment B, RF-PRE-05/07, ISO 14721 OAIS).

Orquesta, para cada documento declarado:
  1. resolver `file_id` -> fila real de `files` (bucket -documents);
  2. descargar sus BYTES de MinIO y RECALCULAR el SHA-256 — si no coincide con
     `files.sha256`, el empaquetado se ABORTA por completo (no se sube
     contenido cuya integridad está en duda);
  3. si `PRESERVATION_PDFA_VALIDATION_ENABLED=true`, validar PDF/A (E10
     Increment C2a, RF-PRE-02) cada documento detectado como PDF por MAGIC
     BYTES — FUERA de cualquier transacción de BD (`app.services.pdfa`). El
     validador por defecto (`StubPdfaValidator`) es HONESTO: siempre
     `outcome=not_evaluated` (RF-FIR-15) — jamás afirma conformidad ni
     no-conformidad cuando el validador real (veraPDF, E10 Increment C2b)
     no está disponible. Es ADVISORY: un PDF no-conforme NO bloquea el
     empaquetado, solo queda registrado (evento + PREMIS) para remediación
     archivística futura (migración de formato);
  4. construir el Bag (BagIt real) + PREMIS v3, validado con conformidad
     plena contra el XSD OFICIAL de PREMIS v3 de la Library of Congress
     (`schemas/premis-v3-loc.xsd`, E10 Increment C1a) — incluye, por cada
     documento validado, un evento `validation` PDF/A;
  5. subir el ZIP resultante al bucket WORM (Object Lock, misma fundación del
     Increment A) con la retención aportada por el llamador (archive-service,
     dueño de la TRD);
  6. persistir, en UNA transacción atómica, la fila `aip` (ubicación/formato
     reales), el registro en `preservacion_worm_objeto` (tipo="aip", para que
     el gate de disposición final también lo cubra), el rastro de eventos
     PREMIS (FIJACION/WORM/INGESTA resultado=ok, y VALIDACION_PDFA por
     documento PDF validado — `ok`/`fallo`/`no_evaluado`, keyado por
     `file_id`, no por `expediente_id`) y el `audit_log` inmutable — la
     subida a WORM ya ocurrió (irreversible, externa a la BD), pero su
     REGISTRO nunca debe quedar a medias.

storage-service NO conoce la TRD ni valida que `file_id` pertenezca al
`expediente_id` declarado — mismo contrato que `proteger_indice`
(`ProtegerIndiceRequest`): el llamador (archive-service) lo garantiza.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime

import asyncpg
from miniopy_async.retention import Retention
from orpycamcp_common import audit

from app.core.config import settings
from app.core.minio_client import ensure_object_lock_bucket, get_minio
from app.models.file_record import FileRecord
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import AipCreate, AipResponse
from app.services.aip_builder import AipDocMaterial, aip_sha256, build_bag
from app.services.aip_replica_service import ReplicaResult, replicate_aip
from app.services.pdfa import PdfaResult, get_pdfa_validator, run_pdfa_validation
from app.services.preservation_worm_service import (
    FileNotFoundForPreservationError,
    FixityMismatchError,
    PreservationDisabledError,
    _worm_lock_key,
    preservation_bucket_name,
)
from app.services.retention_policy import validate_retain_until

logger = logging.getLogger(__name__)

_SERVICE = "storage-service"
_AIP_TIPO = "aip"

# Firma de archivo PDF (magic bytes) — se detecta por CONTENIDO, nunca por
# `files.mime_type` (lo declara el cliente en el multipart original, no es
# fiable como base para decidir qué se valida — E10 Increment C2a, PIEZA 4).
_PDF_MAGIC = b"%PDF"

# `preservacion_evento.resultado` (migración 008) espejo del `PdfaResult.outcome`.
_PDFA_RESULTADO_MAP: dict[str, str] = {
    "conformant": "ok",
    "non_conformant": "fallo",
    "not_evaluated": "no_evaluado",
}


def _looks_like_pdf(content: bytes) -> bool:
    return content.startswith(_PDF_MAGIC)


async def _download(minio, bucket: str, object_key: str) -> bytes:
    obj = await minio.get_object(bucket, object_key)
    try:
        return await obj.read()
    finally:
        close = getattr(obj, "close", None)
        if close is not None:
            close()


class AipService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._file_repo = FileRepository(conn, tenant_slug)
        self._worm_repo = PreservacionWormRepository(conn, tenant_slug)
        self._evt_repo = PreservacionRepository(conn, tenant_slug)

    async def _resolve_and_verify(
        self, minio, file_id: uuid.UUID, *, nombre: str, actor: str | None, expediente_id: str
    ) -> tuple[FileRecord, bytes]:
        """Resuelve `file_id` -> registro + bytes reales, y recalcula la fixity.

        Levanta `FileNotFoundForPreservationError` / `FixityMismatchError` si
        corresponde. En el segundo caso deja traza en `preservacion_evento`
        (FIJACION/fallo) y en el `audit_log` inmutable ANTES de propagar —
        mismo patrón que `PreservationWormService.proteger_indice`.
        """
        record = await self._file_repo.get_by_id_any_state(file_id)
        if record is None:
            raise FileNotFoundForPreservationError(str(file_id))

        content = await _download(minio, record.bucket, record.object_key)
        actual_sha256 = hashlib.sha256(content).hexdigest()

        if actual_sha256 != record.sha256:
            await self._evt_repo.insert_evento(
                {
                    "object_ref": expediente_id,
                    "tipo": "FIJACION",
                    "resultado": "fallo",
                    "hash_before": record.sha256,
                    "hash_after": actual_sha256,
                    "detalle": {"file_id": str(file_id), "nombre": nombre, "motivo": "sha256_mismatch"},
                },
                agente=actor,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="file.fixity_fallo",
                object_type="file",
                object_ref=str(file_id),
                actor=actor,
                payload={
                    "expediente_id": expediente_id,
                    "hash_esperado": record.sha256,
                    "hash_obtenido": actual_sha256,
                    "contexto": "aip",
                },
            )
            raise FixityMismatchError(
                f"SHA-256 no coincide para {nombre!r} (file_id={file_id}): "
                f"esperado {record.sha256}, obtenido {actual_sha256}"
            )

        return record, content

    async def empaquetar_aip(self, payload: AipCreate, actor: str | None) -> AipResponse:
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "El empaquetado de AIP está desactivado (PRESERVATION_ENABLED=false)"
            )

        retain_until = payload.resolve_retain_until()
        validate_retain_until(retain_until)

        # Advisory lock de SESIÓN por `(tenant, expediente_id, 'aip')`
        # (remediación de auditoría, Media): serializa el
        # check->put_object->insert de abajo, MISMO mecanismo
        # (`_worm_lock_key`) que `PreservationWormService.proteger_indice`
        # usa para el índice/acta -- reusado aquí con `tipo="aip"` para que
        # ambos namespaces de lock (índice vs AIP del mismo expediente) NO
        # compitan entre sí. Cubre AMBAS rutas HTTP (`/aip` pública y
        # `/aip-internal`, D-02) porque ambas llegan al MISMO método de
        # servicio. Ver la migración 012 (comentario corregido): el UNIQUE
        # por sí solo dedup por CONTENIDO exacto, no cierra la carrera
        # concurrente (dos builds del mismo expediente embeben timestamps
        # distintos -> `sha256` distinto) -- este lock sí, porque impide que
        # el `put_object` COMPLIANCE físico de dos llamadas al mismo
        # expediente corra en paralelo. Lock de SESIÓN (no `xact`): el
        # camino de fallo de fixity debe persistir su evento/asiento aunque
        # el resto de la operación no siga adelante. Se libera en el
        # `finally`, también ante excepción.
        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id, _AIP_TIPO)
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._empaquetar_aip_locked(payload, actor, retain_until)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _empaquetar_aip_locked(
        self, payload: AipCreate, actor: str | None, retain_until: datetime
    ) -> AipResponse:
        """Cuerpo de :meth:`empaquetar_aip` bajo el advisory lock de sesión
        por-(expediente, 'aip') -- todo el `check->put_object->insert` corre
        serializado contra otro empaquetado concurrente del MISMO
        expediente. `retain_until` ya viene validado."""
        minio = get_minio()

        docs_material: list[AipDocMaterial] = []
        # `document_id` -> `file_id` real: el rastro de `preservacion_evento`
        # (VALIDACION_PDFA) keyea por `file_id` (el objeto real validado),
        # mientras que el PREMIS keyea por `document_id` (identificador de
        # negocio del documento en el expediente) — espacios de nombres
        # deliberadamente distintos, ver PIEZA 6 del contrato.
        file_ids_by_document: dict[str, uuid.UUID] = {}
        for doc_input in payload.documentos:
            record, content = await self._resolve_and_verify(
                minio,
                doc_input.file_id,
                nombre=doc_input.nombre,
                actor=actor,
                expediente_id=payload.expediente_id,
            )
            docs_material.append(
                AipDocMaterial(
                    document_id=doc_input.document_id,
                    nombre=doc_input.nombre,
                    content=content,
                    sha256=record.sha256,
                    mime_type=record.mime_type,
                )
            )
            file_ids_by_document[doc_input.document_id] = doc_input.file_id

        # Validación PDF/A (E10 Increment C2a, RF-PRE-02) — SIEMPRE fuera de
        # `conn.transaction()` (PIEZA 4 del contrato): un validador best-effort
        # (hoy el stub, mañana un subprocess veraPDF de Increment C2b) nunca
        # debe correr con la conexión de BD retenida dentro de una tx. Se
        # detecta el PDF por MAGIC BYTES sobre el contenido REAL descargado
        # (nunca por `files.mime_type`, que lo declara el cliente).
        pdfa_results: dict[str, PdfaResult] = {}
        if settings.preservation_pdfa_validation_enabled:
            validator = get_pdfa_validator(settings)
            for material in docs_material:
                if not _looks_like_pdf(material.content):
                    continue
                pdfa_results[material.document_id] = await run_pdfa_validation(
                    validator, material.content, profile=settings.pdfa_profile
                )

        indice_material: AipDocMaterial | None = None
        indice_sha256: str | None = None
        if payload.indice_file_id is not None:
            record, content = await self._resolve_and_verify(
                minio,
                payload.indice_file_id,
                nombre="indice-firmado.xml",
                actor=actor,
                expediente_id=payload.expediente_id,
            )
            indice_sha256 = record.sha256
            indice_material = AipDocMaterial(
                document_id=f"indice:{payload.expediente_id}",
                nombre="indice-firmado.xml",
                content=content,
                sha256=record.sha256,
                mime_type=record.mime_type or "application/xml",
            )

        now = datetime.now(tz=UTC)
        # `build_bag` valida el PREMIS contra el XSD internamente y levanta
        # `PremisValidationError` si no conforma — no debería ocurrir (la
        # estructura la arma este mismo servicio), pero de ocurrir, es una
        # regresión: se propaga sin capturar y NADA se sube al bucket WORM.
        artifact = build_bag(
            expediente_id=payload.expediente_id,
            tenant_slug=self._tenant_slug,
            docs=docs_material,
            indice=indice_material,
            generado=now,
            actor=actor,
            pdfa_results=pdfa_results,
        )

        zip_hash = aip_sha256(artifact.zip_bytes)

        # Idempotencia PRE-`put_object`, bajo el advisory lock (remediación de
        # auditoría, Media): si ya existe un AIP con este `(expediente_id,
        # aip_sha256)` exacto -- mismo criterio que el índice único parcial
        # de la migración 012 -- corta-circuita SIN subir nada al bucket WORM
        # ni insertar una fila nueva. Decisión de semántica (documentada en
        # la migración 012): CONTENIDO IDÉNTICO = corte-circuito idempotente
        # (retorna el AIP YA existente); CONTENIDO DISTINTO = procede a
        # empaquetar una versión NUEVA (re-empaquetado legítimo, migración
        # 009 -- el AIP admite N versiones por expediente, `GET
        # /aip/{expediente_id}` siempre expone la más reciente). Bajo el
        # lock, dos llamadas concurrentes quedan serializadas: la 2ª ve aquí
        # lo que la 1ª ya comprometió, en vez de competir por el mismo
        # `put_object` COMPLIANCE físico.
        existing_aip = await self._evt_repo.get_aip_by_expediente_and_sha256(
            payload.expediente_id, zip_hash
        )
        if existing_aip is not None:
            logger.info(
                "Empaquetado AIP idempotente (contenido ya registrado) "
                "expediente_id=%s aip_sha256=%s aip_id=%s",
                payload.expediente_id,
                zip_hash,
                existing_aip["id"],
            )
            return AipResponse(**dict(existing_aip))

        bucket = preservation_bucket_name(self._tenant_slug)
        await ensure_object_lock_bucket(minio, bucket)

        aip_id = uuid.uuid4()
        safe_expediente = payload.expediente_id.replace("/", "-")
        object_key = f"aip/{safe_expediente}/{now.date().isoformat()}-{aip_id}.zip"

        mode = settings.preservation_retention_mode
        retention = Retention(mode, retain_until)
        write_result = await minio.put_object(
            bucket,
            object_key,
            io.BytesIO(artifact.zip_bytes),
            length=len(artifact.zip_bytes),
            content_type="application/zip",
            retention=retention,
            legal_hold=payload.legal_hold,
        )
        version_id = getattr(write_result, "version_id", None)

        # Réplica best-effort del AIP ya sellado (E10 Increment C1b). Se
        # dispara AQUÍ, ANTES de abrir la transacción crítica de abajo —
        # `replicate_aip` nunca propaga (best-effort no-fatal): un fallo de
        # I/O de la réplica NO debe poder tumbar el registro del AIP primario,
        # que ya es válido e inmutable en el bucket WORM primario.
        num_copias = await self._evt_repo.current_num_copias()
        if num_copias is None:
            num_copias = 1
        replica_result: ReplicaResult | None = await replicate_aip(
            primary_minio=minio,
            tenant_slug=self._tenant_slug,
            primary_bucket=bucket,
            object_key=object_key,
            zip_bytes=artifact.zip_bytes,
            retention_mode=mode,
            retain_until=retain_until,
            legal_hold=payload.legal_hold,
            num_copias=num_copias,
        )

        # La subida a WORM (arriba) es irreversible y externa a la BD — no hay
        # forma de hacerla atómica con el registro. Lo que SÍ debe ser atómico
        # es el registro mismo: si cualquier INSERT de aquí abajo fallara a
        # mitad de camino, un AIP ya inmutable en el bucket quedaría sin fila
        # `aip` y/o sin `preservacion_worm_objeto` — y el gate de disposición
        # final (que consulta esa última tabla) quedaría ciego a ese objeto
        # (hallazgo de auditoría Increment B).
        async with self._conn.transaction():
            row = await self._evt_repo.insert_aip(
                {
                    "id": aip_id,
                    "expediente_id": payload.expediente_id,
                    "premis_xml": artifact.premis_xml,
                    "aip_sha256": zip_hash,
                    "indice_sha256": indice_sha256,
                    "num_documentos": len(docs_material),
                    "bucket": bucket,
                    "object_key": object_key,
                    "aip_bytes": len(artifact.zip_bytes),
                    "formato": "application/zip",
                    "retain_until": retain_until,
                    "retention_mode": mode,
                    "legal_hold": payload.legal_hold,
                }
            )

            await self._worm_repo.insert(
                expediente_id=payload.expediente_id,
                tipo="aip",
                bucket=bucket,
                object_key=object_key,
                version_id=version_id,
                sha256=zip_hash,
                retain_until=retain_until,
                retention_mode=mode,
                legal_hold=payload.legal_hold,
            )

            # Rastro de eventos PREMIS consultable (`GET /eventos`, PIEZA 5):
            # la verificación de fijación EXITOSA y el sellado WORM también
            # quedan aquí — antes solo el camino de FALLO de fijación se
            # registraba en `preservacion_evento` (`_resolve_and_verify`),
            # dejando el rastro de éxito incompleto (hallazgo de auditoría
            # Increment B).
            await self._evt_repo.insert_evento(
                {
                    "object_ref": payload.expediente_id,
                    "tipo": "FIJACION",
                    "resultado": "ok",
                    "hash_after": zip_hash,
                    "detalle": {
                        "documentos": [
                            {"document_id": d.document_id, "sha256": d.sha256}
                            for d in docs_material
                        ],
                        "indice_sha256": indice_sha256,
                    },
                },
                agente=actor,
            )
            await self._evt_repo.insert_evento(
                {
                    "object_ref": payload.expediente_id,
                    "tipo": "WORM",
                    "resultado": "ok",
                    "hash_after": zip_hash,
                    "detalle": {
                        "bucket": bucket,
                        "object_key": object_key,
                        "version_id": version_id,
                        "retain_until": retain_until.isoformat(),
                        "retention_mode": mode,
                        "legal_hold": payload.legal_hold,
                    },
                },
                agente=actor,
            )
            await self._evt_repo.insert_evento(
                {
                    "object_ref": payload.expediente_id,
                    "tipo": "INGESTA",
                    "resultado": "ok",
                    "hash_after": zip_hash,
                    "detalle": {
                        "num_documentos": len(docs_material),
                        "bucket": bucket,
                        "object_key": object_key,
                        "aip_bytes": len(artifact.zip_bytes),
                    },
                },
                agente=actor,
            )

            # Validación PDF/A (E10 Increment C2a, RF-PRE-02): un
            # `preservacion_evento` tipo=VALIDACION_PDFA POR DOCUMENTO
            # validado (no por expediente, a diferencia de FIJACION/WORM/
            # INGESTA arriba) — `object_ref` es el `file_id` REAL del objeto
            # validado (PIEZA 6(a) del contrato), no `document_id` ni
            # `expediente_id`. El resultado ya se calculó FUERA de esta
            # transacción (ver arriba); aquí solo se persiste el rastro —
            # `resultado="no_evaluado"` (stub por defecto) NUNCA implica
            # conformidad, y el AIP se empaqueta igual (advisory, PIEZA 5).
            for document_id, result in pdfa_results.items():
                await self._evt_repo.insert_evento(
                    {
                        "object_ref": str(file_ids_by_document[document_id]),
                        "tipo": "VALIDACION_PDFA",
                        "resultado": _PDFA_RESULTADO_MAP[result.outcome],
                        "detalle": {
                            "document_id": document_id,
                            "profile": result.profile,
                            "outcome": result.outcome,
                            "validator_agent": result.validator_agent,
                            **result.detail,
                        },
                    },
                    agente=actor,
                )

            # Réplica (E10 Increment C1b, PIEZA 5): SOLO `preservacion_evento`
            # tipo=REPLICA — nunca `preservacion_worm_objeto` (exigiría migrar
            # su CHECK tipo IN(indice,aip); la inmutabilidad de la réplica la
            # garantiza el object-lock del bucket réplica, no una fila
            # nuestra) ni un evento PREMIS dentro del Bag (ya sellado). Se
            # inserta AQUÍ, dentro de la misma tx que el resto del AIP, para
            # que la traza quede consistente aunque la subida de la réplica
            # (I/O externo, ya resuelta arriba, best-effort no-fatal) haya
            # fallado.
            if replica_result is not None:
                await self._evt_repo.insert_evento(
                    {
                        "object_ref": payload.expediente_id,
                        "tipo": "REPLICA",
                        "resultado": "ok" if replica_result.ok else "fallo",
                        "hash_after": zip_hash if replica_result.ok else None,
                        "detalle": replica_result.detalle,
                    },
                    agente=actor,
                )

            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="aip.empaquetado",
                object_type="aip",
                object_ref=payload.expediente_id,
                actor=actor,
                payload={
                    "aip_id": str(aip_id),
                    "bucket": bucket,
                    "object_key": object_key,
                    "aip_sha256": zip_hash,
                    "num_documentos": len(docs_material),
                    "retain_until": retain_until.isoformat(),
                    "retention_mode": mode,
                    "legal_hold": payload.legal_hold,
                    "replica": replica_result.detalle if replica_result is not None else None,
                },
            )

        return AipResponse(
            id=row["id"],
            expediente_id=row["expediente_id"],
            aip_sha256=row["aip_sha256"],
            indice_sha256=row["indice_sha256"],
            num_documentos=row["num_documentos"],
            estado=row["estado"],
            created_at=row["created_at"],
            premis_xml=row["premis_xml"],
            bucket=row["bucket"],
            object_key=row["object_key"],
            aip_bytes=row["aip_bytes"],
            formato=row["formato"],
            retain_until=row["retain_until"],
            retention_mode=row["retention_mode"],
            legal_hold=row["legal_hold"],
            replicado=replica_result.ok if replica_result is not None else False,
            replica_target=replica_result.target if replica_result is not None else None,
            replica_copias_hechas=replica_result.copias_hechas if replica_result is not None else 1,
        )
