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
from app.core.constants import canal_del_acto
from app.core.minio_client import (
    ensure_object_lock_bucket,
    get_minio,
    verificar_legal_hold_aplicado,
    verificar_retencion_aplicada,
)
from app.models.file_record import FileRecord
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import AipCreate, AipReempaquetarRequest, AipResponse
from app.services.aip_builder import (
    AipDocMaterial,
    BagAnteriorCorruptoError,
    ReempaquetadoInfo,
    aip_sha256,
    build_bag,
    read_bag_anterior,
)
from app.services.aip_replica_service import ReplicaResult, replicate_aip
from app.services.pdfa import (
    PDFA_RESULTADO_MAP,
    PdfaResult,
    get_pdfa_validator,
    run_pdfa_validation,
)
from app.services.pdfa.subprocess_verapdf import _VALID_FLAVOURS
from app.services.pdfa_ingest_service import looks_like_pdf
from app.services.preservation_worm_service import (
    FileNotFoundForPreservationError,
    FixityMismatchError,
    PreservationDisabledError,
    RetencionDiferidaSinHoldError,
    _iso,
    _worm_lock_key,
    legal_hold_compartido,
    preservation_bucket_name,
)
from app.services.retention_policy import InvalidRetentionError, validate_retain_until

logger = logging.getLogger(__name__)

_SERVICE = "storage-service"
_AIP_TIPO = "aip"



class AipNoEncontradoError(Exception):
    """El AIP anterior no existe (o no es de este expediente) -- `404 aip_not_found`."""


class AipYaSustituidoError(Exception):
    """El AIP anterior ya tiene un sucesor con OTRO índice -- `409 aip_ya_sustituido`."""


class AipYaExisteError(Exception):
    """Ya hay un AIP para el expediente y el contenido es distinto: el AIP se VERSIONA (ADR-042),
    no se re-empaqueta como si fuera nuevo -- `409 aip_ya_existe`. `aip_id` = el AIP vigente."""

    def __init__(self, message: str, *, aip_id: uuid.UUID | None = None) -> None:
        super().__init__(message)
        self.aip_id = aip_id


class AipAnteriorFixityMismatchError(Exception):
    """El bag del AIP anterior no verifica contra sus manifiestos -- `409`."""


class IndiceNoEsRenovacionError(Exception):
    """El índice nuevo no desciende (cadena `renueva_a`) del índice embebido en el AIP anterior,
    o no es un `firma_lta_renovada` del mismo expediente -- `422`."""


# E10 C2b follow-up: estados TERMINALES de `files.pdfa_status` (migración
# 013) -- distinto de "pendiente" (encolado, worker asíncrono de ingesta
# todavía no corrió) o NULL (nunca evaluado). Solo un veredicto TERMINAL para
# el MISMO perfil se reusa aquí; ver `_reused_pdfa_result`.
_TERMINAL_PDFA_STATUS = frozenset({"conformant", "non_conformant", "not_evaluated"})
_REUSED_INGEST_AGENT = "reused/ingest-verdict"


# Firma de archivo PDF: por CONTENIDO, nunca por `files.mime_type` (lo declara el
# cliente). Misma función que la ingesta, con la tolerancia de 1 KiB de los lectores
# (AREA-08-02): dos copias de la regla divergen.
_looks_like_pdf = looks_like_pdf


def _resolve_pdfa_profile(payload_profile: str | None) -> str:
    """Resuelve el flavour PDF/A a validar (E10 debt "perfil PDF/A por
    serie", RF-FIR-15) -- fail-closed HONESTO, `invalid != default`:

    - **Ausente/null** (`AipCreate.pdfa_profile is None`, la serie TRD no
      tiene política propia) -> default global `settings.pdfa_profile`.
      Ruta retrocompat: ningún AIP existente cambia de comportamiento.
    - **Presente pero fuera de `_VALID_FLAVOURS`** -> se conserva TAL CUAL
      (NUNCA se sustituye por el default) para que
      `PdfaValidator.validate()` lo degrade honestamente a
      `not_evaluated` (ver `SubprocessVeraPdfValidator.validate`, que ya
      hace exactamente esta comprobación) -- jamás se reporta conformidad
      contra un perfil que no corrió.

    Re-validación defense-in-depth: `AipCreate.pdfa_profile` cruza la
    frontera de confianza archive-service -> storage-service (payload de un
    endpoint interno, D-02) -- este sitio no asume que el llamador ya
    validó el campo contra la whitelist."""
    if payload_profile is None:
        return settings.pdfa_profile
    if payload_profile not in _VALID_FLAVOURS:
        logger.warning(
            "AipCreate.pdfa_profile=%r fuera de whitelist %s -- se conserva "
            "sin sustituir por el default (RF-FIR-15: el validador lo "
            "degradará a not_evaluated, nunca conformidad no evaluada)",
            payload_profile,
            sorted(_VALID_FLAVOURS),
        )
    return payload_profile


def _reused_pdfa_result(record: FileRecord, profile: str) -> PdfaResult | None:
    """E10 C2b follow-up (validación PDF/A en ingesta, E07): si `record`
    (resuelto en `_resolve_and_verify`, mismo objeto que ya se descargó/
    fijó) ya tiene un veredicto TERMINAL para el MISMO perfil que este AIP
    exige, lo devuelve envuelto en un `PdfaResult` para que el llamador NO
    invoque el validador (JVM/subprocess) de nuevo -- ahorra la parte cara
    de la validación, no la fijación de bytes (que ya ocurrió igual, se
    necesita para el payload del Bag).

    Devuelve `None` si no hay nada reusable: `pdfa_status` es NULL/
    "pendiente" (no terminal) o el perfil registrado difiere del exigido
    aquí -- en ese caso el llamador valida como si nunca se hubiese
    evaluado, exactamente igual que antes de este follow-up.
    """
    status = record.pdfa_status
    if status not in _TERMINAL_PDFA_STATUS:
        return None
    if record.pdfa_profile != profile:
        return None
    checked_at = record.pdfa_checked_at.isoformat() if record.pdfa_checked_at else None
    return PdfaResult(
        outcome=status,  # type: ignore[arg-type]  # ya validado contra _TERMINAL_PDFA_STATUS
        profile=profile,
        validator_agent=_REUSED_INGEST_AGENT,
        detail={
            "motivo": (
                "Veredicto reusado de la validación PDF/A en ingesta "
                "(E10 C2b follow-up) -- no se re-ejecutó el validador."
            ),
            "pdfa_checked_at": checked_at,
        },
    )


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
                canal=canal_del_acto(actor),
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

        retain_until = payload.resolve_retain_until_opcional()
        if retain_until is not None:
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
            # C-5/C7: «¿hay hold activo?» y el INSERT de la fila bajo el lock COMPARTIDO del hold.
            async with legal_hold_compartido(
                self._conn, self._tenant_slug, payload.expediente_id
            ):
                return await self._empaquetar_aip_locked(payload, actor, retain_until)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _empaquetar_aip_locked(
        self, payload: AipCreate, actor: str | None, retain_until: datetime | None
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
        # E10 C2b follow-up: `FileRecord` completo por documento (no solo el
        # `file_id`), capturado en ESTE MISMO loop de resolución para que la
        # comprobación de reuso de abajo (`_reused_pdfa_result`) no necesite
        # una segunda consulta a `files` -- el registro ya trae
        # `pdfa_status`/`pdfa_profile`/`pdfa_checked_at` (migración 013).
        records_by_document: dict[str, FileRecord] = {}
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
            records_by_document[doc_input.document_id] = record

        # Validación PDF/A (E10 Increment C2a, RF-PRE-02) — SIEMPRE fuera de
        # `conn.transaction()` (PIEZA 4 del contrato): un validador best-effort
        # (hoy el stub, mañana un subprocess veraPDF de Increment C2b) nunca
        # debe correr con la conexión de BD retenida dentro de una tx. Se
        # detecta el PDF por MAGIC BYTES sobre el contenido REAL descargado
        # (nunca por `files.mime_type`, que lo declara el cliente).
        pdfa_results: dict[str, PdfaResult] = {}
        if settings.preservation_pdfa_validation_enabled:
            validator = get_pdfa_validator(settings)
            profile = _resolve_pdfa_profile(payload.pdfa_profile)
            for material in docs_material:
                if not _looks_like_pdf(material.content):
                    continue
                # E10 C2b follow-up: reusa el veredicto de la validación en
                # INGESTA (E07, `app/services/pdfa_ingest_service.py`) si es
                # terminal para el MISMO perfil -- se salta el validador
                # caro (JVM/subprocess). El evento PREMIS `VALIDACION_PDFA`
                # se sigue emitiendo por documento más abajo (mismo dict
                # `pdfa_results`), solo cambia `validator_agent` a
                # "reused/ingest-verdict" para que quede honesto en el
                # rastro que NO se re-ejecutó el validador esta vez.
                reused = _reused_pdfa_result(records_by_document[material.document_id], profile)
                if reused is not None:
                    pdfa_results[material.document_id] = reused
                    continue
                pdfa_results[material.document_id] = await run_pdfa_validation(
                    validator, material.content, profile=profile
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

        # C-6: el AIP se versiona (ux_aip_expediente_version). Contenido DISTINTO sobre un
        # expediente que ya tiene AIP ya no es «un re-empaquetado legítimo» (migración 009): sería
        # una segunda versión 1. Se corta ANTES del `put_object` COMPLIANCE, que es irreversible.
        if await self._evt_repo.existe_aip(payload.expediente_id):
            # El 409 lleva el `aip_id` VIGENTE: quien llamó (archive-service) puede haber perdido
            # la respuesta de un empaquetado que sí se completó, y con el id reconcilia en vez de
            # tomar esto por un fallo de fixity (auditoría de cierre del lote C, S2).
            raise AipYaExisteError(
                f"el expediente {payload.expediente_id!r} ya tiene un AIP; una versión nueva "
                "solo nace del reempaquetado por renovación del sello de archivo",
                aip_id=await self._evt_repo.aip_vigente_id(payload.expediente_id),
            )

        # C-5/C7: el AIP nace con `legal_hold = existe un hold activo del expediente` (lo resuelve
        # el servidor), y una retención diferida exige el hold `valoracion_pendiente`.
        holds = await self._worm_repo.holds_activos(payload.expediente_id)
        hold_activo = bool(holds)
        if retain_until is None and "valoracion_pendiente" not in holds:
            raise RetencionDiferidaSinHoldError(
                f"expediente_id={payload.expediente_id}: retencion_diferida exige un hold "
                "activo `valoracion_pendiente`"
            )
        origen = "calendario" if retain_until is not None else "valoracion_pendiente"

        bucket = preservation_bucket_name(self._tenant_slug)
        await ensure_object_lock_bucket(minio, bucket)

        aip_id = uuid.uuid4()
        safe_expediente = payload.expediente_id.replace("/", "-")
        object_key = f"aip/{safe_expediente}/{now.date().isoformat()}-{aip_id}.zip"

        mode = settings.preservation_retention_mode if retain_until is not None else None
        retention = (
            Retention(mode, retain_until) if retain_until is not None and mode is not None else None
        )
        write_result = await minio.put_object(
            bucket,
            object_key,
            io.BytesIO(artifact.zip_bytes),
            length=len(artifact.zip_bytes),
            content_type="application/zip",
            retention=retention,
            legal_hold=hold_activo,
        )
        version_id = getattr(write_result, "version_id", None)

        # Mismo read-back fail-closed que en `preservation_worm_service`: el AIP es
        # el paquete de archivo, y darlo por protegido sobre la palabra del almacén
        # es exactamente lo que el acta no puede permitirse afirmar. Va aquí
        # también —y no solo en el otro servicio— porque son DOS caminos que
        # escriben retención, y verificar uno solo deja el gemelo abierto.
        #
        # Antes de la réplica a propósito: replicar un objeto cuya protección no se
        # pudo confirmar sería propagar la duda a la segunda copia.
        if retain_until is not None and mode is not None:
            await verificar_retencion_aplicada(
                minio,
                bucket,
                object_key,
                version_id=version_id,
                modo_esperado=mode,
                hasta_esperado=retain_until,
            )
        if hold_activo:
            await verificar_legal_hold_aplicado(minio, bucket, object_key, version_id=version_id)

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
            legal_hold=hold_activo,
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
                    "legal_hold": hold_activo,
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
                legal_hold=hold_activo,
                retencion_origen=origen,
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
                        "retain_until": _iso(retain_until),
                        "retention_mode": mode,
                        "legal_hold": hold_activo,
                        "retencion_origen": origen,
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
                        "resultado": PDFA_RESULTADO_MAP[result.outcome],
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
                    "retain_until": _iso(retain_until),
                    "retention_mode": mode,
                    "legal_hold": hold_activo,
                    "retencion_origen": origen,
                    "replica": replica_result.detalle if replica_result is not None else None,
                },
                canal=canal_del_acto(actor),
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
            version=row.get("version", 1),
            sustituye_a=row.get("sustituye_a"),
            motivo=row.get("motivo", "ingesta"),
        )


    # -- C-6 (ADR-042): reempaquetado por renovación del ArchiveTimeStamp --------------------

    async def reempaquetar_aip(
        self, payload: AipReempaquetarRequest, actor: str | None
    ) -> tuple[AipResponse, bool]:
        """Construye la versión n+1 de un AIP DESDE su bag anterior, con el índice renovado.

        Devuelve ``(AipResponse, creado)``: ``creado=False`` cuando el sucesor ya existía con el
        mismo ``indice_xml_sha256`` (reintento tras un corte entre storage y archive).

        El AIP anterior NO se toca (sigue bajo COMPLIANCE, con su fila `aip` y su fila WORM); el
        nuevo hereda el MAYOR `retain_until` de toda la cadena -- nunca menor -- y queda ligado
        al anterior por `sustituye_a` (tabla `aip`) y `renueva_a` (tabla WORM), de modo que la
        cascada de renovación de retención lo alcanza desde la raíz."""
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "El reempaquetado de AIP está desactivado (PRESERVATION_ENABLED=false)"
            )
        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id, _AIP_TIPO)
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            async with legal_hold_compartido(
                self._conn, self._tenant_slug, payload.expediente_id
            ):
                return await self._reempaquetar_aip_locked(payload, actor)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _reempaquetar_aip_locked(
        self, payload: AipReempaquetarRequest, actor: str | None
    ) -> tuple[AipResponse, bool]:
        anterior = await self._evt_repo.get_aip(payload.aip_anterior_id)
        if anterior is None or anterior["expediente_id"] != payload.expediente_id:
            raise AipNoEncontradoError(str(payload.aip_anterior_id))

        sucesor = await self._evt_repo.get_aip_sucesor(payload.aip_anterior_id)
        if sucesor is not None:
            if sucesor["indice_sha256"] == payload.indice_xml_sha256:
                return _aip_response(sucesor), False
            raise AipYaSustituidoError(
                f"el AIP {payload.aip_anterior_id} ya fue sustituido por {sucesor['id']} "
                "con otro índice"
            )
        if anterior["indice_sha256"] == payload.indice_xml_sha256:
            # Ya embebe ese índice: nada que reempaquetar (idempotente).
            return _aip_response(anterior), False

        # El índice nuevo tiene que SER una renovación del embebido: su fila WORM es
        # `firma_lta_renovada` y su cadena `renueva_a` sube por el índice embebido hasta el WORM
        # `indice` del mismo expediente. Sin esto un XML arbitrario podría colarse como «renovación».
        indice_record = await self._file_repo.get_by_id_any_state(payload.indice_file_id)
        if indice_record is None:
            raise FileNotFoundForPreservationError(str(payload.indice_file_id))
        if indice_record.sha256 != payload.indice_xml_sha256:
            raise IndiceNoEsRenovacionError(
                "el SHA-256 declarado del índice no coincide con el registrado"
            )
        fila_indice = await self._worm_repo.get_by_file_id(payload.indice_file_id)
        if fila_indice is None or fila_indice["tipo"] != "firma_lta_renovada":
            raise IndiceNoEsRenovacionError(
                "el índice nuevo no es un artefacto `firma_lta_renovada` protegido"
            )
        cadena = await self._worm_repo.cadena_ascendente(fila_indice["id"])
        raiz = cadena[-1] if cadena else None
        if (
            raiz is None
            or raiz["tipo"] != "indice"
            or raiz["expediente_id"] != payload.expediente_id
            or fila_indice["expediente_id"] != payload.expediente_id
            or anterior["indice_sha256"] not in {f["sha256"] for f in cadena[1:]}
        ):
            raise IndiceNoEsRenovacionError(
                "el índice nuevo no desciende del índice embebido en el AIP anterior "
                "(mismo expediente)"
            )

        fila_anterior = await self._worm_repo.get_aip_por_sha256(
            payload.expediente_id, anterior["aip_sha256"]
        )
        if fila_anterior is None:
            # Sin fila WORM no hay `version_id` ni retención que heredar: no se inventan.
            raise AipNoEncontradoError(f"{payload.aip_anterior_id} (sin objeto WORM)")

        minio = get_minio()
        bag_bytes = await self._descargar_aip_anterior(minio, fila_anterior)
        if aip_sha256(bag_bytes) != anterior["aip_sha256"]:
            await self._fixity_fallo(payload, anterior["aip_sha256"], aip_sha256(bag_bytes), actor)
            raise AipAnteriorFixityMismatchError(
                f"el ZIP del AIP {payload.aip_anterior_id} no coincide con su huella registrada"
            )
        try:
            previo = read_bag_anterior(bag_bytes, expediente_id=payload.expediente_id)
        except BagAnteriorCorruptoError as exc:
            await self._fixity_fallo(payload, anterior["aip_sha256"], None, actor, motivo=str(exc))
            raise AipAnteriorFixityMismatchError(str(exc)) from exc

        content = await _download(minio, indice_record.bucket, indice_record.object_key)
        if hashlib.sha256(content).hexdigest() != payload.indice_xml_sha256:
            await self._fixity_fallo(
                payload, payload.indice_xml_sha256, hashlib.sha256(content).hexdigest(), actor,
                motivo="indice_sha256_mismatch",
            )
            raise FixityMismatchError(
                f"SHA-256 del índice {payload.indice_file_id} no coincide con el declarado"
            )
        nuevo_indice = AipDocMaterial(
            document_id=previo.indice.document_id,
            nombre=previo.indice.nombre,
            content=content,
            sha256=payload.indice_xml_sha256,
            mime_type=previo.indice.mime_type or "application/xml",
        )

        now = datetime.now(tz=UTC)
        artifact = build_bag(
            expediente_id=payload.expediente_id,
            tenant_slug=self._tenant_slug,
            docs=previo.docs,
            indice=nuevo_indice,
            generado=now,
            actor=actor,
            pdfa_results=previo.pdfa_results,
            reempaquetado=ReempaquetadoInfo(
                aip_anterior_id=str(payload.aip_anterior_id),
                firma_id=str(payload.firma_id),
                ordinal=payload.ordinal,
            ),
        )
        zip_hash = aip_sha256(artifact.zip_bytes)

        # Retención: NUNCA menor. El mayor de toda la cadena de AIP y del anterior. C-5/C7: una
        # cadena sin ninguna fecha (retención diferida a la espera de la valoración) hereda ESO:
        # sin retención, solo hold.
        candidatos = [
            f
            for f in (
                fila_anterior["retain_until"],
                await self._worm_repo.retain_until_maximo_aip(payload.expediente_id),
            )
            if f is not None
        ]
        retain_until: datetime | None = max(candidatos) if candidatos else None
        if retain_until is not None and retain_until <= now:
            raise InvalidRetentionError(
                "la retención heredada de la cadena de AIP ya venció: no se reempaqueta"
            )
        mode = fila_anterior["retention_mode"] if retain_until is not None else None
        # El hold es «existe un hold activo del expediente» (lo resuelve el servidor, bajo el lock
        # compartido del hold), en unión con el de la fila anterior (perder un hold es el error caro).
        holds = await self._worm_repo.holds_activos(payload.expediente_id)
        legal_hold = bool(holds) or bool(fila_anterior["legal_hold"])
        origen = "calendario"
        if retain_until is None:
            origen = fila_anterior.get("retencion_origen", "valoracion_pendiente")
            if origen == "valoracion_pendiente" and "valoracion_pendiente" not in holds:
                raise RetencionDiferidaSinHoldError(
                    f"expediente_id={payload.expediente_id}: el AIP anterior espera valoración y "
                    "su hold `valoracion_pendiente` ya no está activo"
                )

        bucket = preservation_bucket_name(self._tenant_slug)
        await ensure_object_lock_bucket(minio, bucket)
        aip_id = uuid.uuid4()
        safe_expediente = payload.expediente_id.replace("/", "-")
        object_key = f"aip/{safe_expediente}/{now.date().isoformat()}-{aip_id}.zip"
        write_result = await minio.put_object(
            bucket,
            object_key,
            io.BytesIO(artifact.zip_bytes),
            length=len(artifact.zip_bytes),
            content_type="application/zip",
            retention=(
                Retention(mode, retain_until)
                if retain_until is not None and mode is not None
                else None
            ),
            legal_hold=legal_hold,
        )
        version_id = getattr(write_result, "version_id", None)
        if retain_until is not None and mode is not None:
            await verificar_retencion_aplicada(
                minio,
                bucket,
                object_key,
                version_id=version_id,
                modo_esperado=mode,
                hasta_esperado=retain_until,
            )
        if legal_hold:
            await verificar_legal_hold_aplicado(minio, bucket, object_key, version_id=version_id)
        num_copias = await self._evt_repo.current_num_copias()
        replica_result = await replicate_aip(
            primary_minio=minio,
            tenant_slug=self._tenant_slug,
            primary_bucket=bucket,
            object_key=object_key,
            zip_bytes=artifact.zip_bytes,
            retention_mode=mode,
            retain_until=retain_until,
            legal_hold=legal_hold,
            num_copias=num_copias if num_copias is not None else 1,
        )

        version = int(anterior["version"]) + 1
        dedup_key = f"aip:{payload.expediente_id}:{payload.firma_id}:{payload.ordinal}"
        async with self._conn.transaction():
            row = await self._evt_repo.insert_aip(
                {
                    "id": aip_id,
                    "expediente_id": payload.expediente_id,
                    "premis_xml": artifact.premis_xml,
                    "aip_sha256": zip_hash,
                    "indice_sha256": payload.indice_xml_sha256,
                    "num_documentos": len(previo.docs),
                    "bucket": bucket,
                    "object_key": object_key,
                    "aip_bytes": len(artifact.zip_bytes),
                    "formato": "application/zip",
                    "retain_until": retain_until,
                    "retention_mode": mode,
                    "legal_hold": legal_hold,
                    "version": version,
                    "sustituye_a": payload.aip_anterior_id,
                    "motivo": "renovacion_lta",
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
                legal_hold=legal_hold,
                objeto_tipo=fila_anterior["objeto_tipo"],
                renueva_a=fila_anterior["id"],
                retencion_origen=origen,
            )
            await self._evt_repo.insert_evento(
                {
                    "object_ref": payload.expediente_id,
                    "tipo": "FIJACION",
                    "resultado": "ok",
                    "hash_before": anterior["aip_sha256"],
                    "hash_after": zip_hash,
                    "detalle": {
                        "aip_anterior_id": str(payload.aip_anterior_id),
                        "indice_sha256": payload.indice_xml_sha256,
                        "payload_verificado_contra": "manifest-sha256 del AIP anterior",
                    },
                },
                agente=actor,
            )
            await self._evt_repo.insert_evento(
                {
                    "object_ref": payload.expediente_id,
                    "tipo": "RENOVACION",
                    "resultado": "ok",
                    "hash_before": anterior["aip_sha256"],
                    "hash_after": zip_hash,
                    "detalle": {
                        "subtipo": "aip_reempaquetado",
                        "aip_id": str(aip_id),
                        "aip_anterior_id": str(payload.aip_anterior_id),
                        "version": version,
                        "firma_id": str(payload.firma_id),
                        "ordinal": payload.ordinal,
                        "retain_until": _iso(retain_until),
                        "dedup_key": dedup_key,
                    },
                },
                agente=actor,
            )
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
                action="aip.reempaquetado",
                object_type="aip",
                object_ref=payload.expediente_id,
                actor=actor,
                payload={
                    "aip_id": str(aip_id),
                    "aip_anterior_id": str(payload.aip_anterior_id),
                    "version": version,
                    "firma_id": str(payload.firma_id),
                    "ordinal": payload.ordinal,
                    "aip_sha256": zip_hash,
                    "indice_sha256": payload.indice_xml_sha256,
                    "retain_until": _iso(retain_until),
                    "retention_mode": mode,
                    "legal_hold": legal_hold,
                    "retencion_origen": origen,
                },
                canal=canal_del_acto(actor),
            )
        response = _aip_response(row)
        if replica_result is not None:
            response.replicado = replica_result.ok
            response.replica_target = replica_result.target
            response.replica_copias_hechas = replica_result.copias_hechas
        return response, True

    async def _descargar_aip_anterior(self, minio, fila_worm) -> bytes:
        kwargs = {"version_id": fila_worm["version_id"]} if fila_worm["version_id"] else {}
        obj = await minio.get_object(fila_worm["bucket"], fila_worm["object_key"], **kwargs)
        try:
            return await obj.read()
        finally:
            close = getattr(obj, "close", None)
            if close is not None:
                close()

    async def _fixity_fallo(
        self,
        payload: AipReempaquetarRequest,
        esperado: str,
        obtenido: str | None,
        actor: str | None,
        *,
        motivo: str = "sha256_mismatch",
    ) -> None:
        """Rastro (evento FIJACION/fallo + asiento) ANTES de abortar, como `_resolve_and_verify`."""
        await self._evt_repo.insert_evento(
            {
                "object_ref": payload.expediente_id,
                "tipo": "FIJACION",
                "resultado": "fallo",
                "hash_before": esperado,
                "hash_after": obtenido,
                "detalle": {
                    "aip_anterior_id": str(payload.aip_anterior_id),
                    "motivo": motivo,
                    "contexto": "aip_reempaquetado",
                },
            },
            agente=actor,
        )
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="aip.reempaquetado_fixity_fallo",
            object_type="aip",
            object_ref=payload.expediente_id,
            actor=actor,
            payload={
                "aip_anterior_id": str(payload.aip_anterior_id),
                "hash_esperado": esperado,
                "hash_obtenido": obtenido,
                "motivo": motivo,
            },
            canal=canal_del_acto(actor),
        )


def _aip_response(row) -> AipResponse:
    """`AipResponse` desde una fila cruda de `aip` (con las columnas de versión)."""
    return AipResponse(**dict(row))
