# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Document service — business logic for radicado lifecycle.

Tracking number assignment is atomic: the repository executes an
INSERT … ON CONFLICT DO UPDATE RETURNING on the tracking_sequences table,
which is safe under concurrent requests within the same tenant schema.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

import asyncpg
from orpycamcp_common import audit, events

from app.clients.tenant_client import TenantServiceClient
from app.core.clearance import resolve_caller_clearance
from app.models.document import Anexo, Radicado
from app.repositories.document_repository import (
    DocumentRepository,
    InvalidTenantSlugError,
)
from app.repositories.metadata_repository import MetadataRepository
from app.schemas.document import (
    AnexoResponse,
    DispositionUpdate,
    DocumentCreate,
    DocumentResponse,
    DocumentUpdate,
)
from app.schemas.respuesta import RespuestaItem, RespuestaRapidaCreate
from app.services.metadata_service import validate_metadata

logger = logging.getLogger(__name__)

_SERVICE = "document-service"
_STREAM = "orpycamcp.document.events"

# Canales que representan una consulta real de un usuario (gateway/mcp-server,
# ADR-019/D-03) — 'system' (llamadas internas, p. ej. `crear_respuesta`
# leyendo el antecedente vía el repositorio directo) queda excluido: no
# ensucia la pista de acceso con ruido interno. Espejo de
# `archive_service.ArchiveService._CANALES_AUDITABLES`.
_CANALES_AUDITABLES = frozenset({"api", "ui", "mcp"})

# Traza AGREGADA de listado/búsqueda (RF-BUS-10/E09; Ley 1712/2014, Decreto
# 1081/2015): `object_type`/`object_ref` son constantes DEDICADAS, distintas
# de `object_type="radicado"` que usa la lectura individual
# (`document.radicado_consultado`) — un listado/búsqueda no es una lectura DE
# un radicado concreto, es de un CONJUNTO. document-service no tiene hoy un
# equivalente a `AuditReadRepository`/`GET /historial-acceso`
# (archive-service sí), pero se reservan dedicadas para cuando exista, y
# para que nadie las confunda con `object_type="radicado"` al filtrar
# `audit_log`. Acción única para listado y búsqueda (a diferencia de
# archive-service, que las distingue con dos acciones) — spec de este
# incremento.
_SEARCH_OBJECT_TYPE = "radicado_busqueda"
_SEARCH_OBJECT_REF = "listado"
_SEARCH_ACTION = "document.radicado_busqueda"


async def audit_search_access(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str,
    filtros: dict[str, Any],
    total: int,
    page: int,
    size: int,
    user_id: uuid.UUID | None,
    canal: str,
) -> None:
    """Traza AGREGADA de listado/búsqueda de radicados — función de módulo
    (no método de `DocumentService`) porque `search_documents`
    (`app/routers/search.py`) ejecuta SQL crudo directo sobre la conexión sin
    pasar por la capa de servicio (a diferencia de `archive-service`, donde
    list/search sí van por `ArchiveService`); `DocumentService.list` reusa
    esta misma función para no duplicar la lógica de gating/best-effort.

    UNA sola entrada por consulta EXITOSA, nunca por fila del resultado —
    auditar por fila infla la cadena de hash de `public.audit_log`
    (encadenada por tenant, `orpycamcp_common.audit`) proporcionalmente a
    `size`. Gate y best-effort iguales a la lectura individual: sin actor
    real (`user_id is None`) o canal no auditable, no registra; un fallo de
    `audit.append` nunca degrada la consulta a 500.
    """
    if canal not in _CANALES_AUDITABLES or user_id is None:
        return
    try:
        await audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action=_SEARCH_ACTION,
            object_type=_SEARCH_OBJECT_TYPE,
            object_ref=_SEARCH_OBJECT_REF,
            actor=str(user_id),
            canal=canal,
            payload={**filtros, "total": total, "page": page, "size": size},
        )
    except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
        logger.warning("audit.append (%s) failed", _SEARCH_ACTION, exc_info=True)


class DocumentNotFoundError(Exception):
    pass


class InvalidTenantError(Exception):
    pass


class InvalidDependenciaError(Exception):
    """El radicado referencia una dependencia inexistente o inactiva."""


class ReclassifyForbiddenError(Exception):
    """El llamante intenta asignar un nivel de seguridad superior a su propio clearance."""


class DocumentService:
    def __init__(
        self,
        conn: asyncpg.Connection,
        tenant_slug: str,
        tenant_client: TenantServiceClient | None = None,
        redis: Any | None = None,
    ) -> None:
        try:
            self._repo = DocumentRepository(conn, tenant_slug)
        except InvalidTenantSlugError as exc:
            raise InvalidTenantError(str(exc)) from exc
        self._conn = conn
        self._slug = tenant_slug
        self._tenant_client = tenant_client
        self._redis = redis
        self._metadata_repo = MetadataRepository(conn)

    async def create(
        self,
        payload: DocumentCreate,
        registered_by: uuid.UUID | None,
    ) -> DocumentResponse:
        now = datetime.now(tz=UTC)
        year = now.year

        # Vínculo con el organigrama: si se indican códigos de dependencia, se
        # validan contra tenant-service y se denormaliza el nombre (snapshot
        # legal) en los campos de texto origin_dept/dest_dept.
        if payload.origin_dept_code is not None:
            payload.origin_dept = await self._resolve_dependencia(payload.origin_dept_code)
        if payload.dest_dept_code is not None:
            payload.dest_dept = await self._resolve_dependencia(payload.dest_dept_code)

        # RF-RAD-04: la fecha de vencimiento se calcula en días hábiles desde la
        # radicación. El calendario lo resuelve tenant-service (E14) vía HTTP;
        # sin response_days el radicado no tiene plazo.
        due_date: date | None = None
        if payload.response_days is not None:
            if self._tenant_client is None:
                raise RuntimeError("tenant_client requerido para calcular el plazo")
            due_date = await self._tenant_client.calculate_due_date(
                tenant_slug=self._slug,
                from_date=now.date(),
                business_days=payload.response_days,
            )

        # Metadatos (E03, ADR-007): si el tipo documental (doc_class) tiene una
        # plantilla activa, los valores se validan contra su JSON Schema antes de
        # radicar; se guarda la versión de plantilla usada. Sin plantilla, los
        # metadatos se almacenan tal cual (plantillas opt-in por tipo en F1).
        metadata_template_id: uuid.UUID | None = None
        if payload.doc_class:
            template = await self._metadata_repo.get_active_template(payload.doc_class)
            if template is not None:
                validate_metadata(payload.metadata, template["json_schema"])
                metadata_template_id = template["id"]

        tenant_code = await self._repo.get_tenant_code()
        sequence = await self._repo.next_sequence(year, payload.doc_type)
        tracking_number = f"{year}-{tenant_code}-{payload.doc_type}-{sequence:06d}"

        actor = str(registered_by) if registered_by else None

        # El radicado y su entrada de auditoría son atómicos: o se persisten
        # ambos o ninguno (la auditoría es la traza legal del radicado, ADR-008).
        async with self._conn.transaction():
            radicado = await self._repo.create(
                payload=payload,
                tracking_number=tracking_number,
                year=year,
                sequence=sequence,
                tenant_code=tenant_code,
                registered_by=registered_by,
                due_date=due_date,
                metadata_template_id=metadata_template_id,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.radicado_created",
                object_type="radicado",
                object_ref=tracking_number,
                actor=actor,
                payload={"doc_type": radicado.doc_type, "id": str(radicado.id)},
            )

        # El evento de dominio se publica tras confirmar la transacción. Es
        # best-effort: un fallo de Redis no revierte la radicación (la BD es la
        # fuente de verdad; el stream se puede reconstruir).
        await self._emit_created(radicado, actor)

        return _to_response(radicado)

    async def _emit_created(self, radicado: Radicado, actor: str | None) -> None:
        if self._redis is None:
            return
        try:
            await events.emit(
                self._redis,
                _STREAM,
                "document.radicado.created",
                tenant_slug=self._slug,
                actor=actor,
                dedup_key=radicado.tracking_number,
                payload={
                    "id": str(radicado.id),
                    "tracking_number": radicado.tracking_number,
                    "doc_type": radicado.doc_type,
                    "doc_class": radicado.doc_class,
                    "subject": radicado.subject,
                    "dest_dept": radicado.dest_dept,
                    "dest_dept_code": radicado.dest_dept_code,
                    "origin_dept": radicado.origin_dept,
                    "sender_name": radicado.sender_name,
                    "sender_entity": radicado.sender_entity,
                    "pages": radicado.pages,
                    "due_date": radicado.due_date.isoformat() if radicado.due_date else None,
                    # E21: propagación de ACL vía evento (fail-closed) — knowledge-service
                    # deriva el nivel del chunk de aquí; nunca hace fetch-back al radicado.
                    "nivel_seguridad": radicado.nivel_seguridad,
                },
            )
        except Exception:  # noqa: BLE001 — evento best-effort
            logger.warning(
                "No se pudo publicar el evento de radicado %s",
                radicado.tracking_number,
                exc_info=True,
            )

    async def get_disposition(self, document_id: uuid.UUID) -> dict:
        exists, disp = await self._repo.get_disposition(document_id)
        if not exists:
            raise DocumentNotFoundError(str(document_id))
        return disp

    async def set_disposition(self, document_id: uuid.UUID, payload: DispositionUpdate) -> dict:
        """Fija/actualiza (merge) los metadatos de disposición (RF-MET-08)."""
        exists, current = await self._repo.get_disposition(document_id)
        if not exists:
            raise DocumentNotFoundError(str(document_id))
        merged = {**current, **payload.model_dump(exclude_unset=True)}
        result = await self._repo.set_disposition(document_id, merged)
        return result if result is not None else {}

    async def _resolve_dependencia(self, codigo: int) -> str:
        """Valida la dependencia por código y devuelve su nombre (denormalizado)."""
        if self._tenant_client is None:
            raise RuntimeError("tenant_client requerido para validar dependencias")
        dep = await self._tenant_client.get_dependencia(tenant_slug=self._slug, codigo=codigo)
        if dep is None:
            raise InvalidDependenciaError(f"La dependencia {codigo} no existe")
        if not dep.get("activo", True):
            raise InvalidDependenciaError(f"La dependencia {codigo} está inactiva")
        nombre: str = dep["nombre"]
        return nombre

    async def list(
        self,
        *,
        doc_type: str | None,
        status: str | None,
        max_clearance: int,
        metadata_filter: dict | None = None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[DocumentResponse], int]:
        radicados, total = await self._repo.find_all(
            doc_type=doc_type,
            status=status,
            metadata_filter=metadata_filter,
            page=page,
            size=size,
            max_clearance=max_clearance,
        )
        # Filtros estructurales tal cual (no son datos personales de quien
        # consulta); de `metadata_filter` solo las CLAVES consultadas — los
        # valores podrían ser PII (mismo criterio que `search_documents`).
        await audit_search_access(
            self._conn,
            tenant_slug=self._slug,
            filtros={
                "doc_type": doc_type,
                "status": status,
                "metadata_filter_keys": sorted(metadata_filter) if metadata_filter else None,
            },
            total=total,
            page=page,
            size=size,
            user_id=user_id,
            canal=canal,
        )
        return [_to_response(r) for r in radicados], total

    async def get_by_id(
        self,
        document_id: uuid.UUID,
        max_clearance: int = 3,
        *,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> DocumentResponse:
        radicado = await self._repo.get_by_id(document_id, max_clearance)
        if radicado is None:
            raise DocumentNotFoundError(str(document_id))
        await self._audit_read_access(radicado.tracking_number, user_id=user_id, canal=canal)
        return _to_response(radicado)

    async def get_anexo_for_download(
        self,
        document_id: uuid.UUID,
        file_id: uuid.UUID,
        max_clearance: int,
        *,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> Anexo:
        """Resuelve el anexo a descargar respetando no-read-up (RF-SEG-08) y
        deja pista de acceso al CONTENIDO (RF-BUS-10).

        `DocumentNotFoundError` cubre los tres caminos de denegación: radicado
        inexistente, radicado por encima del clearance del llamante, y
        `file_id` que no pertenece a `document_id` — el router los traduce
        TODOS al mismo 404 neutro (ver `app/repositories/document_repository.py
        ::get_anexo_for_download`).
        """
        found = await self._repo.get_anexo_for_download(document_id, file_id, max_clearance)
        if found is None:
            raise DocumentNotFoundError(str(document_id))
        anexo, tracking_number, nivel_seguridad = found
        # Auditar la DESCARGA (acceso al contenido documental, más fuerte que la
        # lectura del metadato) — la autorización ya pasó; best-effort, gated.
        await self._audit_download_access(
            tracking_number,
            file_id=file_id,
            nivel_seguridad=nivel_seguridad,
            user_id=user_id,
            canal=canal,
        )
        return anexo

    async def list_anexos(
        self, document_id: uuid.UUID, max_clearance: int
    ) -> list[Anexo]:
        """Lista los anexos de un radicado respetando no-read-up (RF-SEG-08).

        `DocumentNotFoundError` (→ 404 neutro) si el radicado no existe o supera
        el clearance del llamante — indistinguible. Un radicado visible sin
        anexos devuelve `[]`. NO se audita por-consulta (es una composición de
        metadatos, no el acceso al contenido; la DESCARGA sí deja asiento).
        """
        anexos = await self._repo.list_anexos_for_document(document_id, max_clearance)
        if anexos is None:
            raise DocumentNotFoundError(str(document_id))
        return anexos

    async def _audit_download_access(
        self,
        tracking_number: str,
        *,
        file_id: uuid.UUID,
        nivel_seguridad: int,
        user_id: uuid.UUID | None,
        canal: str,
    ) -> None:
        """Pista de acceso al CONTENIDO de un anexo (RF-BUS-10; Ley 594/2000
        art. 22; Ley 1712/2014): una descarga AUTORIZADA (ya pasó no-read-up)
        deja traza en `audit_log` con actor/canal + `nivel_seguridad` del
        radicado — el evento probatorio relevante es la descarga del fichero,
        no solo la lectura del metadato. Best-effort (nunca degrada la descarga)
        y gated por canal/actor real, gemelo de `_audit_read_access`."""
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.anexo_descargado",
                object_type="radicado",
                object_ref=tracking_number,
                actor=str(user_id),
                canal=canal,
                payload={"file_id": str(file_id), "nivel_seguridad": nivel_seguridad},
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (anexo_descargado) failed", exc_info=True)

    async def get_by_tracking_number(
        self,
        tracking_number: str,
        max_clearance: int = 3,
        *,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> DocumentResponse:
        radicado = await self._repo.get_by_tracking_number(tracking_number, max_clearance)
        if radicado is None:
            raise DocumentNotFoundError(tracking_number)
        await self._audit_read_access(radicado.tracking_number, user_id=user_id, canal=canal)
        return _to_response(radicado)

    async def _audit_read_access(
        self, tracking_number: str, *, user_id: uuid.UUID | None, canal: str
    ) -> None:
        """Pista de acceso (RF-BUS-10/E09; Ley 1712/2014, Decreto 1081/2015) —
        gemelo de `ArchiveService._audit_read_access`: una lectura EXITOSA de
        `GET /documents/{id}` o `GET /documents/by-tracking/{tracking_number}`
        (el radicado ya pasó el filtro no-read-up de clearance) deja traza en
        `audit_log` con el actor real y el canal de origen; 'system' (llamadas
        internas, sin actor real) no escribe pista. Best-effort: nunca
        degrada la lectura a 500 por un fallo de auditoría.

        `user_id is None` también descarta el registro: sin actor real no hay
        a quién atribuir la lectura.
        """
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.radicado_consultado",
                object_type="radicado",
                object_ref=tracking_number,
                actor=str(user_id),
                canal=canal,
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (radicado_consultado) failed", exc_info=True)

    async def crear_respuesta(
        self,
        antecedente_id: uuid.UUID,
        payload: RespuestaRapidaCreate,
        registered_by: uuid.UUID | None,
    ) -> DocumentResponse:
        """Respuesta rápida: crea una Salida vinculada a la Entrada antecedente."""
        entrada = await self._repo.get_by_id(antecedente_id)
        if entrada is None:
            raise DocumentNotFoundError(str(antecedente_id))
        salida = DocumentCreate(
            doc_type="S",
            subject=(payload.subject or f"RE: {entrada.subject}")[:2000],
            dest_dept=payload.dest_dept or entrada.origin_dept,
            dest_dept_code=payload.dest_dept_code,
            observations=payload.observations,
            response_days=payload.response_days,
            metadata={
                **(payload.metadata or {}),
                "responde_a": str(antecedente_id),
                "antecedente": entrada.tracking_number,
            },
        )
        result = await self.create(salida, registered_by)
        await self._repo.set_responde_a(result.id, antecedente_id)
        return result

    async def listar_respuestas(
        self,
        antecedente_id: uuid.UUID,
        *,
        max_clearance: int = 3,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> list[RespuestaItem]:
        # No-read-up (RF-SEG-08): solo Salidas vinculadas a-nivel-o-por-debajo
        # del clearance del llamante. La lista devuelta es CONTENIDO de radicado.
        rows = await self._repo.list_respuestas(antecedente_id, max_clearance)
        # Traza AGREGADA de lectura (RF-BUS-10): una entrada por consulta, no por
        # fila. El `antecedente_id` es una referencia de radicado (no PII).
        await audit_search_access(
            self._conn,
            tenant_slug=self._slug,
            filtros={"antecedente_id": str(antecedente_id)},
            total=len(rows),
            page=1,
            size=len(rows),
            user_id=user_id,
            canal=canal,
        )
        return [RespuestaItem(**dict(r)) for r in rows]

    async def reclassify(
        self,
        document_id: uuid.UUID,
        *,
        nuevo_nivel: int,
        motivo: str,
        actor: uuid.UUID | None,
        causal_reserva: str | None = None,
        fundamento_juridico: str | None = None,
        plazo_reserva_meses: int | None = None,
    ) -> dict:
        """Reclassifies the security level of a radicado (E08, RF-SEG-08).

        Guards (inside a single transaction):
        - 404 if the radicado does not exist.
        - 404 (neutral) if the actor's clearance is below the document's current level
          (no-read-up: existence must not be revealed to under-clearance actors).
        - 403 if nuevo_nivel exceeds the actor's clearance (no-write-up: cannot
          classify above own level).
        - No-op (returns current state, no audit, no event) when nuevo_nivel equals
          the current level (idempotent for safe retry).

        The audit entry and the UPDATE are atomic. The Redis event is published
        best-effort after the transaction commits (BD is the source of truth).
        """
        actor_str = str(actor) if actor else None

        # Captured inside the transaction for post-commit use (Record stays valid).
        result_id: uuid.UUID | None = None
        result_tracking: str | None = None
        nivel_anterior: int | None = None

        async with self._conn.transaction():
            ctx = await self._repo.get_security_context(document_id)
            if ctx is None:
                raise DocumentNotFoundError(str(document_id))

            clearance = await resolve_caller_clearance(self._conn, actor)

            # No-read-up: actor below the document's level must not learn it exists
            # (RF-SEG-08, same neutral 404 used by firmar/rechazar in signature-service).
            if clearance < ctx["nivel_seguridad"]:
                raise DocumentNotFoundError(str(document_id))

            # No-write-up: cannot promote a document above own classification level.
            if nuevo_nivel > clearance:
                raise ReclassifyForbiddenError(
                    f"Cannot reclassify to level {nuevo_nivel}: actor clearance is {clearance}"
                )

            # Idempotent: no change needed — return current state without audit or event.
            if nuevo_nivel == ctx["nivel_seguridad"]:
                return {
                    "id": ctx["id"],
                    "tracking_number": ctx["tracking_number"],
                    "nivel_seguridad": ctx["nivel_seguridad"],
                }

            await self._repo.set_nivel_seguridad(document_id, nuevo_nivel)
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.radicado_reclassified",
                object_type="radicado",
                object_ref=ctx["tracking_number"],
                actor=actor_str,
                payload={
                    "nivel_anterior": ctx["nivel_seguridad"],
                    "nivel_nuevo": nuevo_nivel,
                    "motivo": motivo,
                    # Compliance #2 (Ley 1712/2014 art.18-19): fundamento jurídico
                    # de la reserva/clasificación — registro inmutable en audit_log.
                    "causal_reserva": causal_reserva,
                    "fundamento_juridico": fundamento_juridico,
                    "plazo_reserva_meses": plazo_reserva_meses,
                },
            )
            result_id = ctx["id"]
            result_tracking = ctx["tracking_number"]
            nivel_anterior = ctx["nivel_seguridad"]

        # Best-effort: a Redis failure does not roll back the already-committed UPDATE.
        if self._redis is not None:
            try:
                await events.emit(
                    self._redis,
                    _STREAM,
                    "document.radicado.reclassified",
                    tenant_slug=self._slug,
                    actor=actor_str,
                    dedup_key=f"{result_tracking}:{nuevo_nivel}",
                    payload={
                        "id": str(result_id),
                        "tracking_number": result_tracking,
                        "nivel_anterior": nivel_anterior,
                        "nivel_nuevo": nuevo_nivel,
                        "causal_reserva": causal_reserva,
                        "fundamento_juridico": fundamento_juridico,
                        "plazo_reserva_meses": plazo_reserva_meses,
                    },
                )
            except Exception:  # noqa: BLE001 — evento best-effort
                logger.warning(
                    "No se pudo publicar el evento de reclasificación %s",
                    result_tracking,
                    exc_info=True,
                )

        return {
            "id": result_id,
            "tracking_number": result_tracking,
            "nivel_seguridad": nuevo_nivel,
        }

    async def update(
        self,
        document_id: uuid.UUID,
        payload: DocumentUpdate,
    ) -> DocumentResponse:
        radicado = await self._repo.update(document_id, payload)
        if radicado is None:
            raise DocumentNotFoundError(str(document_id))
        return _to_response(radicado)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_response(radicado: Radicado) -> DocumentResponse:
    return DocumentResponse(
        id=radicado.id,
        tracking_number=radicado.tracking_number,
        doc_type=radicado.doc_type,
        year=radicado.year,
        sequence=radicado.sequence,
        subject=radicado.subject,
        doc_class=radicado.doc_class,
        origin_dept=radicado.origin_dept,
        dest_dept=radicado.dest_dept,
        origin_dept_code=radicado.origin_dept_code,
        dest_dept_code=radicado.dest_dept_code,
        sender_name=radicado.sender_name,
        sender_entity=radicado.sender_entity,
        status=radicado.status,
        pages=radicado.pages,
        nivel_seguridad=radicado.nivel_seguridad,
        observations=radicado.observations,
        registered_by=radicado.registered_by,
        registered_at=radicado.registered_at,
        updated_at=radicado.updated_at,
        due_date=radicado.due_date,
        metadata=radicado.metadata,
        anexos=[
            AnexoResponse(
                id=a.id,
                file_id=a.file_id,
                filename=a.filename,
                file_size=a.file_size,
                mime_type=a.mime_type,
                created_at=a.created_at,
                tipo_anexo=a.tipo_anexo,
                es_principal=a.es_principal,
                folios=a.folios,
            )
            for a in radicado.anexos
        ],
    )
