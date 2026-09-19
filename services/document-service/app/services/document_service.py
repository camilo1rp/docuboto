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

# `builtins.list` en las anotaciones de este módulo NO es una manía de estilo:
# la clase define un método `list`, que dentro del cuerpo de la clase sombrea al
# `list` incorporado. Sin cualificar, `-> list[...]` resuelve al MÉTODO y la
# anotación queda sin significado (mypy: "Function ... is not valid as a type").
# Se cualifica en vez de renombrar el método porque `list` es su nombre correcto
# y renombrarlo obligaría a tocar routers y servicios de otros módulos.
import builtins
import logging
import uuid
from datetime import UTC, date, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
from orpycamcp_common import audit, events

from app.clients.tenant_client import TenantServiceClient
from app.core.clearance import resolve_caller_clearance
from app.core.config import settings
from app.models.document import Anexo, Radicado
from app.repositories.audit_read import AuditReadRepository
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
    HistorialAccesoEntry,
    IndiceClasificadoItem,
    IndiceClasificadosResponse,
)
from app.schemas.respuesta import RespuestaItem, RespuestaRapidaCreate
from app.services.metadata_service import validate_metadata
from app.services.tercero_service import TerceroService

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
# un radicado concreto, es de un CONJUNTO. Esa separación es la que permite que
# `GET /documents/{id}/historial-acceso` (`AuditReadRepository`) responda «quién
# ha visto ESTE radicado» sin contaminar la respuesta con cada vez que alguien
# abrió la bandeja. Hasta 2026-09 este comentario decía que ese endpoint no
# existía «pero se reservan dedicadas para cuando exista»: ya existe, y la
# previsión resultó buena. Acción única para listado y búsqueda (a diferencia de
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
    except Exception:
        logger.warning("audit.append (%s) failed", _SEARCH_ACTION, exc_info=True)


class DocumentNotFoundError(Exception):
    pass


class InvalidTenantError(Exception):
    pass


class InvalidDependenciaError(Exception):
    """El radicado referencia una dependencia inexistente o inactiva."""


def _zona_de_radicacion() -> tzinfo:
    """Zona horaria con la que se decide el año del radicado y la fecha base.

    Un nombre inválido no puede tumbar la radicación —es un dato de despliegue,
    no del documento—, así que se degrada a UTC dejando constancia. Lo que no se
    hace es fallar en silencio con una zona equivocada.
    """
    nombre = settings.tz_radicacion
    try:
        return ZoneInfo(nombre)
    except Exception:  # noqa: BLE001 — cualquier nombre inválido es el mismo caso
        logger.warning(
            "TZ_RADICACION=%r no es una zona horaria válida; se usa UTC. "
            "El año del número de radicado se decidirá en UTC.",
            nombre,
        )
        return UTC


class ReclassifyForbiddenError(Exception):
    """El llamante intenta asignar un nivel de seguridad superior a su propio clearance."""


class AnexoNoDisponibleError(Exception):
    """Se intenta adjuntar un fichero que ya cuelga de un radicado fuera de alcance."""


class DocumentAnuladoError(Exception):
    """Se intenta modificar un radicado ya anulado.

    La anulación pasa por un circuito de dos pasos con aprobación de supervisor;
    revertirla con un PATCH la vaciaría de contenido. El desanulado, si alguna vez
    hiciera falta, sería un acto propio con su causal y su aprobador, no un efecto
    lateral de editar un campo.
    """


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

    async def _rechazar_anexos_fuera_de_alcance(
        self, anexos: list, clearance: int
    ) -> None:
        """422 si algún `file_id` ya es anexo de un radicado fuera del clearance.

        El mensaje es NEUTRO a propósito, y es el mismo que correspondería a un
        fichero inexistente: distinguir «no existe» de «pertenece a algo que no
        puedes ver» convertiría este gate en un oráculo, y confirmar por fuerza
        bruta qué ficheros cuelgan de un radicado reservado es justo lo que se
        está evitando.
        """
        ids = [a.file_id for a in anexos if a.file_id is not None]
        if not ids:
            return
        fuera = await self._conn.fetchval(
            """
            SELECT 1
              FROM anexos a
              JOIN radicados r ON r.id = a.radicado_id
             WHERE a.file_id = ANY($1::uuid[])
               AND r.nivel_seguridad > $2
             LIMIT 1
            """,
            ids,
            clearance,
        )
        if fuera:
            raise AnexoNoDisponibleError(
                "Alguno de los archivos indicados no está disponible para adjuntar."
            )

    async def create(
        self,
        payload: DocumentCreate,
        registered_by: uuid.UUID | None,
    ) -> DocumentResponse:
        # El AÑO se decide en la zona horaria de radicación, no en UTC: ver
        # `Settings.tz_radicacion`. `now` sigue siendo consciente de zona, así
        # que lo que se guarda como instante no cambia; lo que cambia es qué día
        # y qué año se consideran, que es lo que ve quien recibe el acuse.
        now = datetime.now(tz=_zona_de_radicacion())
        year = now.year

        # No-write-up al radicar (RF-SEG-08, espejo del M3 de archive y de la
        # reclasificación): no se puede ORIGINAR un radicado por encima del propio
        # clearance — clasificar es acto reglado de quien tiene competencia (Ley
        # 1712/2014 arts. 6, 18-20). Un PUBLICA no puede radicar una CLASIFICADA y
        # perderla de vista (los reads no-read-up la ocultarían). Solo se resuelve
        # el clearance cuando hay clasificación (nivel>=2) — la radicación PUBLICA
        # (la mayoría) no paga la consulta.
        if payload.nivel_seguridad >= 2:
            clearance = await resolve_caller_clearance(self._conn, registered_by)
            if payload.nivel_seguridad > clearance:
                raise ReclassifyForbiddenError(
                    f"No se puede radicar en nivel {payload.nivel_seguridad}: "
                    f"el clearance del actor es {clearance}"
                )

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

        # El VÍNCULO anexo↔radicado no lo puede afirmar el cliente (RF-SEG-08).
        #
        # `AnexoInput.file_id` es un UUID que llega en el payload y se insertaba
        # tal cual. El gate de la descarga mediada comprueba tres cosas —que el
        # radicado exista, que esté dentro del clearance y que el `file_id` sea
        # anexo DE ESE radicado— y las tres eran ciertas: la tercera porque el
        # propio atacante acababa de escribirla. Adosando el `file_id` de un
        # anexo clasificado a un radicado propio de nivel PUBLICA, la descarga
        # devolvía sus bytes y quedaba auditada como lectura legítima.
        #
        # No se arregla con una unicidad sobre `file_id`: el almacén deduplica
        # por SHA-256, así que dos radicados que llevan el mismo documento
        # comparten fila de fichero de forma legítima, y esa restricción
        # rompería el caso normal para atajar el abusivo.
        #
        # La regla es más estrecha: no se adjunta un fichero que ya es anexo de
        # un radicado que el llamante NO puede leer. Adjuntar uno que sí puede
        # leer sigue permitido, que es el caso deduplicado.
        #
        # Va AQUÍ, antes de `next_sequence`, y no dentro de la transacción: el
        # rechazo no puede costar un consecutivo, porque un número gastado no se
        # devuelve nunca.
        if payload.anexos:
            clearance_anexos = await resolve_caller_clearance(self._conn, registered_by)
            await self._rechazar_anexos_fuera_de_alcance(payload.anexos, clearance_anexos)

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
            # E08: si se radica ya clasificado (nivel >= 2), persiste la metadata
            # de clasificación vigente (fundamento/causal/plazo) para el índice
            # Ley 1712 art. 20 — el schema ya exige el fundamento (422 si falta).
            audit_payload: dict[str, Any] = {
                "doc_type": radicado.doc_type,
                "id": str(radicado.id),
                "nivel_seguridad": radicado.nivel_seguridad,
            }
            if payload.nivel_seguridad >= 2:
                await self._repo.set_classification(
                    radicado.id,
                    payload.nivel_seguridad,
                    fundamento=payload.clasificacion_fundamento,
                    causal=payload.clasificacion_causal,
                    plazo_meses=payload.clasificacion_plazo_meses,
                )
                audit_payload["clasificacion_fundamento"] = payload.clasificacion_fundamento
                audit_payload["clasificacion_causal"] = payload.clasificacion_causal

            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.radicado_created",
                object_type="radicado",
                object_ref=tracking_number,
                actor=actor,
                payload=audit_payload,
            )

            # Actores (RF-RAD-05) DENTRO de la misma transacción: el radicado,
            # sus actores y los dos asientos son un solo acto. Vincular después,
            # en una segunda llamada, deja un radicado sin remitente si esa
            # llamada falla — y como nada lo comprobaba, `radicado_actor` estaba
            # vacía sobre 182 radicados mientras el catálogo tenía 40 terceros.
            #
            # Va DESPUÉS del asiento de creación para que la cadena cuente los
            # hechos en el orden en que ocurren: primero nace el radicado,
            # luego se le atan sus actores. `vincular_actores` escribe su propio
            # asiento —con cuántos contactos se corrigieron al vuelo—, así que
            # aquí no se duplica esa información.
            #
            # Se reutiliza `TerceroService.vincular_actores`, que es quien sabe
            # congelar el contacto y resolver la geografía DIVIPOLA; aquí no se
            # reimplementa nada. Un `tercero_id` inexistente aborta la
            # radicación entera (404 y rollback): mejor no radicar que radicar
            # sin saber de quién.
            if payload.actores:
                await TerceroService(self._conn, self._slug).vincular_actores(
                    radicado.id,
                    [a.model_dump() for a in payload.actores],
                    actor=registered_by,
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
        except Exception:
            logger.warning(
                "No se pudo publicar el evento de radicado %s",
                radicado.tracking_number,
                exc_info=True,
            )

    async def get_disposition(self, document_id: uuid.UUID, max_clearance: int) -> dict:
        """Lee los metadatos de disposición (RF-MET-08) respetando no-read-up.

        Un radicado inexistente y uno por encima del clearance del llamante elevan
        la MISMA excepción → mismo 404 neutro (RF-SEG-08, no-oráculo).
        """
        exists, disp = await self._repo.get_disposition(document_id, max_clearance)
        if not exists:
            raise DocumentNotFoundError(str(document_id))
        return disp

    async def set_disposition(
        self,
        document_id: uuid.UUID,
        payload: DispositionUpdate,
        max_clearance: int,
        *,
        actor: str | None = None,
        canal: str = "system",
    ) -> dict:
        """Fija/actualiza (merge) los metadatos de disposición (RF-MET-08).

        Ambas consultas —la lectura del estado actual y la escritura del merge—
        llevan el gate de clearance. Si el radicado se reclasifica POR ENCIMA del
        llamante entre una y otra, el UPDATE no encuentra fila y se eleva 404 en
        lugar de devolver un resultado vacío que aparentaría éxito.

        La escritura y su asiento son **un solo acto** (misma transacción): la
        disposición decide entre conservar y destruir (Ley 594/2000 art. 24), y
        un cambio sin asiento —o un asiento sin cambio— es justamente lo que la
        trazabilidad legal no admite. Aquí NO es best-effort, a diferencia de
        las trazas de lectura: si la auditoría falla, el cambio no ocurre.
        """
        exists, current = await self._repo.get_disposition(document_id, max_clearance)
        if not exists:
            raise DocumentNotFoundError(str(document_id))
        cambios = payload.model_dump(exclude_unset=True)
        merged = {**current, **cambios}

        async with self._conn.transaction():
            result = await self._repo.set_disposition(document_id, merged, max_clearance)
            if result is None:
                raise DocumentNotFoundError(str(document_id))
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.disposition_set",
                object_type="radicado",
                object_ref=str(document_id),
                actor=actor,
                canal=canal,
                # El estado ANTERIOR va en el asiento: sin él, la cadena dice
                # que alguien fijó «eliminar» pero no si eso cambió algo ni qué
                # había antes, que es la mitad de la pregunta cuando se audita
                # una eliminación años después.
                payload={"anterior": current, "cambios": cambios, "resultado": result},
            )
        return result

    async def set_publicacion(
        self,
        document_id: uuid.UUID,
        publicar: bool,
        fundamento: str,
        max_clearance: int,
        *,
        actor: str | None = None,
        canal: str = "system",
    ) -> dict:
        """Acto de publicación de oficio (11.1, Ley 1712/2014 art. 12).

        Mutación y asiento son **un solo acto**, como en la disposición: publicar
        hace visible un documento a cualquiera en internet, y despublicar lo
        retira; ambas decisiones tienen que poder responderse años después con
        quién, cuándo y por qué. Si la auditoría falla, la publicación no ocurre.

        El 404 es el mismo para «no existe», «te supera el clearance», «no es de
        nivel público» y «está anulado». No es pereza: distinguirlos convertiría
        este endpoint en un oráculo sobre la existencia y la clasificación de
        radicados reservados, que es justo lo que el resto del servicio evita.
        """
        actor_uuid: uuid.UUID | None = None
        if actor:
            try:
                actor_uuid = uuid.UUID(actor)
            except ValueError:
                actor_uuid = None

        async with self._conn.transaction():
            fila = await self._repo.set_publicable(
                document_id, publicar, max_clearance, actor_uuid
            )
            if fila is None:
                raise DocumentNotFoundError(str(document_id))
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.publicacion_activada" if publicar else "document.publicacion_retirada",
                object_type="radicado",
                object_ref=fila["tracking_number"],
                actor=actor,
                canal=canal,
                payload={"publicable": publicar, "fundamento": fundamento},
            )
        return {
            "tracking_number": fila["tracking_number"],
            "publicable": fila["publicable"],
            "publicado_at": fila["publicado_at"],
        }

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
        status: builtins.list[str] | None,
        max_clearance: int,
        metadata_filter: dict | None = None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
        due_from: date | None = None,
        due_to: date | None = None,
        order_by: str = "registered_at",
        order_dir: str = "desc",
    ) -> tuple[builtins.list[DocumentResponse], int]:
        radicados, total = await self._repo.find_all(
            doc_type=doc_type,
            status=status,
            metadata_filter=metadata_filter,
            page=page,
            size=size,
            max_clearance=max_clearance,
            due_from=due_from,
            due_to=due_to,
            order_by=order_by,
            order_dir=order_dir,
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
                # El asiento describe la consulta que se hizo, no una parte de
                # ella: un informe de vencimientos es «quién pidió ver qué
                # plazos», y con solo `doc_type`/`status` el registro afirmaría
                # de menos sobre lo que esa persona consultó.
                "due_from": due_from.isoformat() if due_from else None,
                "due_to": due_to.isoformat() if due_to else None,
                "order_by": order_by,
                "order_dir": order_dir,
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

    async def adjuntar_documento_producido(
        self,
        radicado_id: uuid.UUID,
        *,
        file_id: uuid.UUID,
        filename: str,
        file_size: int | None,
    ) -> None:
        """Anexa el PDF/A generado como soporte PRINCIPAL del radicado (E23).

        `es_principal=True` no es cosmética: distingue el documento que ES la
        comunicación de los adjuntos que la acompañan, y es lo que el índice
        electrónico y el envío postal necesitan para saber qué pieza despachar.

        No hay gate de clearance aquí y es correcto: el radicado se acaba de
        crear en esta misma llamada por el mismo actor, así que no hay
        descubrimiento posible de nada que el llamante no conociera ya. Es una
        continuación del acto de radicar, no un acceso nuevo.

        La huella SHA-256 no viaja aquí porque `anexos` no la guarda: la fixity
        de los bytes vive en storage-service, y la del documento producido queda
        en `borrador.documento_sha256`, que es donde la necesita el circuito de
        firma (RF-PRO-09).
        """
        await self._repo.crear_anexo_producido(
            radicado_id,
            file_id=file_id,
            filename=filename,
            file_size=file_size,
        )

    async def list_anexos(
        self, document_id: uuid.UUID, max_clearance: int
    ) -> builtins.list[Anexo]:
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
        except Exception:
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

    async def historial_acceso(
        self,
        radicado_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None,
        page: int,
        size: int,
        # `builtins.list` en el retorno y no `list`: la clase define un método
        # `list` que lo sombrea dentro de su cuerpo. Lo advierte la cabecera del
        # módulo, y aun así hay que acordarse.
    ) -> tuple[builtins.list[HistorialAccesoEntry], int]:
        """Quién leyó y quién descargó este radicado (RF-BUS-10).

        **No-read-up ANTES de tocar `audit_log`** (RF-SEG-08): un radicado por
        encima del clearance del llamante es 404, igual que en el resto de sus
        lecturas. Sin esa guarda, el historial sería un oráculo — quien no puede
        ver el radicado sabría igualmente que existe y cuánta gente lo ha
        mirado, que en un documento RESERVADO es justo lo que hay que ocultar.

        El filtro va por `tracking_number` y no por el UUID porque así se
        escribió la traza (`object_ref = <número de radicado>`): es la
        referencia estable y legible, la misma que aparece en el índice
        electrónico.
        """
        max_clearance = await resolve_caller_clearance(self._conn, user_id)
        radicado = await self._repo.get_by_id(radicado_id, max_clearance)
        if radicado is None:
            # Excepción de DOMINIO, no `HTTPException`: en este servicio la
            # traducción a HTTP vive en el router. Es también lo que hace que el
            # 404 del no-read-up sea indistinguible del de un radicado que no
            # existe — que es justo la propiedad que se busca.
            raise DocumentNotFoundError(str(radicado_id))
        lector = AuditReadRepository(self._conn)
        total = await lector.count_for_radicado(self._slug, radicado.tracking_number)
        filas = await lector.list_for_radicado(
            self._slug,
            radicado.tracking_number,
            offset=(page - 1) * size,
            limit=size,
        )
        return [HistorialAccesoEntry(**dict(f)) for f in filas], total

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
        except Exception:
            logger.warning("audit.append (radicado_consultado) failed", exc_info=True)

    async def crear_respuesta(
        self,
        antecedente_id: uuid.UUID,
        payload: RespuestaRapidaCreate,
        registered_by: uuid.UUID | None,
        max_clearance: int,
    ) -> DocumentResponse:
        """Respuesta rápida: crea una Salida vinculada a la Entrada antecedente.

        No-read-up (RF-SEG-08): el antecedente se lee CON el clearance del
        llamante. Es imprescindible aquí y no solo por simetría — el `subject` y
        el `tracking_number` de la Entrada se copian a la Salida que se crea
        (`RE: {subject}`, `metadata.antecedente`), así que sin gate responder a un
        radicado reservado EXFILTRABA su asunto a un documento legible.
        """
        entrada = await self._repo.get_by_id(antecedente_id, max_clearance)
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
    ) -> builtins.list[RespuestaItem]:
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

            # E08: además del nivel, persiste la metadata de clasificación vigente
            # (fundamento/causal/plazo) para el índice Ley 1712 art. 20; al
            # desclasificar (nivel 1) la limpia. El audit_log guarda el histórico.
            await self._repo.set_classification(
                document_id,
                nuevo_nivel,
                fundamento=fundamento_juridico,
                causal=causal_reserva,
                plazo_meses=plazo_reserva_meses,
            )
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
            except Exception:
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

    async def indice_clasificados(
        self, actor: uuid.UUID | None
    ) -> IndiceClasificadosResponse:
        """Genera el índice de información clasificada y reservada (Ley 1712 art. 20).

        Registro CONTENT-FREE y COMPLETO (todos los radicados nivel ≥ 2), ortogonal
        al clearance de lectura: es un acto de PUBLICACIÓN de metadata, no de lectura
        de contenido (por eso el repo no filtra por clearance ni selecciona `subject`).
        La generación se audita (RF-BUS-10) como una entrada agregada, best-effort.
        """
        # nivel → (etiqueta, excepción de la Ley 1712). Nivel 2 = RESERVADA (art. 19,
        # protege intereses públicos); nivel 3 = CLASIFICADA (art. 18, protege
        # derechos de personas / información privada).
        clasif = {
            2: ("reservada", "Ley 1712/2014 art. 19 (información reservada)"),
            3: ("clasificada", "Ley 1712/2014 art. 18 (información clasificada)"),
        }
        rows = await self._repo.indice_clasificados()
        items: builtins.list[IndiceClasificadoItem] = []
        for r in rows:
            etiqueta, excepcion = clasif.get(
                r["nivel_seguridad"], ("reservada", "Ley 1712/2014 art. 19")
            )
            items.append(
                IndiceClasificadoItem(
                    numero_radicado=r["tracking_number"],
                    tipo_radicado=r["doc_type"],
                    nivel_seguridad=r["nivel_seguridad"],
                    clasificacion=etiqueta,
                    excepcion_ley_1712=excepcion,
                    fundamento_juridico=r["clasificacion_fundamento"],
                    causal_reserva=r["clasificacion_causal"],
                    plazo_reserva_meses=r["clasificacion_plazo_meses"],
                    fecha_clasificacion=r["clasificado_at"],
                    fecha_radicacion=r["registered_at"],
                )
            )
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.indice_clasificados_generado",
                object_type="indice_clasificados",
                object_ref="registro",
                actor=str(actor) if actor else None,
                payload={"total": len(items)},
            )
        except Exception:
            logger.warning("audit.append (indice_clasificados) failed", exc_info=True)
        return IndiceClasificadosResponse(total=len(items), items=items)

    # `doc_class` y `metadata` entran aquí junto a los demás: retipificar decide la
    # serie TRD, y con ella cuánto se conserva el documento y si acaba destruido
    # (Ley 594/2000 art. 24). Un cambio con esa consecuencia que no dejara antes y
    # después sería la traza más pobre justo en el campo más caro de reconstruir.
    _CAMPOS_AUDITABLES = (
        "subject",
        "dest_dept",
        "observations",
        "status",
        "pages",
        "doc_class",
        "metadata",
    )

    async def update(
        self,
        document_id: uuid.UUID,
        payload: DocumentUpdate,
        max_clearance: int,
        actor: uuid.UUID | None = None,
    ) -> DocumentResponse:
        """Actualiza campos mutables respetando no-read-up (RF-SEG-08), con asiento.

        La respuesta es el `DocumentResponse` COMPLETO (asunto, remitente, anexos),
        así que sin el gate de clearance un PATCH era además una lectura total.

        **La mutación y su asiento son un solo acto.** Hasta ahora este método eran
        dos líneas sin ninguna traza: modificar el asunto, el destino, el estado o el
        número de folios de un documento oficial con número inmutable era invisible
        para la traza legal forense (Ley 594/2000, ADR-010). El contraste lo delataba:
        **leer** un radicado sí se auditaba. Aquí el asiento va DENTRO de la
        transacción —como en `AnulacionService`, no best-effort como las lecturas—:
        si el asiento falla, la modificación revierte. Preferimos que un cambio falle
        a que ocurra sin dejar rastro.

        Se registra el **antes y el después de cada campo tocado**, no solo el después:
        una traza que dice «se cambió el asunto» sin decir a qué no permite
        reconstruir nada. El estado previo se lee con el MISMO gate de clearance, así
        que el asiento nunca puede revelar un campo que el llamante no podía ver.

        **Retipificar revalida los metadatos** (E03/E04). Cambiar `doc_class` cambia
        la serie TRD que gobierna la retención y la disposición final del radicado, y
        los campos variables que le corresponden. Los metadatos efectivos —los que
        vengan en el cuerpo, o los que ya tuviera— se validan contra la plantilla
        activa del tipo NUEVO antes de escribir nada; si no encajan, el acto falla con
        422 en vez de dejar un radicado tipificado como X con los campos de Y. Es la
        misma comprobación que hace `create`, y a propósito: un radicado retipificado
        tiene que ser indistinguible de uno radicado así desde el principio.
        """
        async with self._conn.transaction():
            previo = await self._repo.get_by_id(document_id, max_clearance)
            if previo is None:
                raise DocumentNotFoundError(str(document_id))
            if previo.status == "anulado":
                # 409, no 404: aquí el radicado SÍ es visible para este llamante (el
                # `get_by_id` de arriba pasó su gate de clearance), así que decir
                # «está anulado» no revela nada que no pudiera leer. Un radicado por
                # encima de su clearance ya salió por el 404 neutro de arriba.
                raise DocumentAnuladoError(previo.tracking_number)

            # Revalidación de metadatos. Se dispara si se toca el tipo O los
            # metadatos: cambiar solo los metadatos también tiene que cumplir la
            # plantilla vigente, o el gate de la radicación sería puenteable con un
            # PATCH posterior.
            template_id: uuid.UUID | None = None
            escribir_template = payload.doc_class is not None or payload.metadata is not None
            if escribir_template:
                tipo_efectivo = payload.doc_class or previo.doc_class
                meta_efectiva = (
                    payload.metadata if payload.metadata is not None else (previo.metadata or {})
                )
                if tipo_efectivo:
                    template = await self._metadata_repo.get_active_template(tipo_efectivo)
                    if template is not None:
                        validate_metadata(meta_efectiva, template["json_schema"])
                        template_id = template["id"]

            radicado = await self._repo.update(
                document_id,
                payload,
                max_clearance,
                metadata_template_id=template_id,
                escribir_template=escribir_template,
            )
            if radicado is None:
                # Carrera: alguien lo anuló o lo reclasificó entre la lectura y el
                # UPDATE. El `WHERE` del UPDATE es el guard autoritativo.
                raise DocumentNotFoundError(str(document_id))

            cambios = {
                campo: {
                    "antes": getattr(previo, campo),
                    "despues": getattr(radicado, campo),
                }
                for campo in self._CAMPOS_AUDITABLES
                if getattr(payload, campo, None) is not None
                and getattr(previo, campo) != getattr(radicado, campo)
            }
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.radicado_actualizado",
                object_type="radicado",
                # El número resuelto server-side, nunca un campo del cuerpo.
                object_ref=radicado.tracking_number,
                actor=str(actor) if actor else None,
                payload={"cambios": cambios},
            )
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
        publicable=radicado.publicable,
        publicado_at=radicado.publicado_at,
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
