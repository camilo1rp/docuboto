# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.clients import document_client
from app.clients.document_client import DocumentClientError, DocumentPurgaError
from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.disposition import CT_DISPOSITION_CODES
from app.core.http_errors import motivo_de
from app.core.huella_documento import huella_de_documento
from app.core.trd_disposition import (
    assert_disposicion_final_autorizada,
    disposicion_final_autorizada,
)
from app.models.archive import Expediente, ExpedienteRadicado, TrdSerie
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.audit_read import AuditReadRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.metadata_repository import MetadataRepository
from app.schemas.archive import (
    ActaEliminacionResponse,
    BatchLinkResult,
    EjecucionFallo,
    EjecucionLoteResponse,
    ExpedienteCreate,
    ExpedienteDisposicionDecisionResponse,
    ExpedienteDisposicionResult,
    ExpedienteDisposicionVencidaResponse,
    ExpedienteEventoResponse,
    ExpedienteIndice,
    ExpedienteRadicadoResponse,
    ExpedienteResponse,
    ExpedienteRevisionPendienteResponse,
    ExpedienteUpdate,
    HistorialAccesoEntry,
    IndiceItem,
    LapidaResponse,
    LegalHoldRequest,
    LegalHoldResponse,
    LoteEliminacionItem,
    LoteEliminacionItemPublico,
    LoteEliminacionPublico,
    LoteEliminacionRechazo,
    LoteEliminacionResponse,
    RadicadoLink,
    RetentionSchedule,
    RolAutorizado,
    TrdSerieAprobar,
    TrdSerieConvalidar,
    TrdSerieCreate,
    TrdSerieDevolver,
    TrdSerieRegistrarRusd,
    TrdSerieResponse,
    TrdSerieUpdate,
    TrdSerieVersionCreate,
    TvdAgrupacionCreate,
    TvdAgrupacionUpdate,
    TvdAgrupacionVersionCreate,
)
from app.services.backoff import next_backoff_seconds
from app.services.eliminacion_acta import construir_acta_xml
from app.services.metadata_service import validate_metadata

if TYPE_CHECKING:
    from app.clients.storage_client import ExportZipStream

logger = logging.getLogger(__name__)

# Conservación PERMANENTE del acta de eliminación, materializada como el máximo
# que admite el contrato de storage-service (`retencion_anios <= 100`):
# Object-Lock exige una FECHA, no un infinito. No es una retención archivística
# —el acta no se elimina jamás, es lo único que sobrevive a lo eliminado—, es la
# forma de decirle al almacén que no deje borrarlo; y es renovable por la misma
# vía que las series de Conservación Total.
_ACTA_ELIMINACION_RETENCION_ANIOS = 100

_SERVICE = "archive-service"

# Canales que representan una consulta real de un usuario (gateway/mcp-server,
# ADR-019/D-03) — 'system' (jobs/regeneraciones internas, o llamadas internas
# que reutilizan `get_expediente` tras una mutación ya auditada aparte) queda
# excluido: no ensucia la pista de acceso ni la duplica (E15 perfil v4, R3-a).
# Espejo de `index_service._CANALES_AUDITABLES`.
_CANALES_AUDITABLES = frozenset({"api", "ui", "mcp"})


def _csv_safe(value: object) -> object:
    """Neutraliza la inyección de fórmulas CSV (Excel/Sheets) en los campos de
    texto libre del manifiesto de exportación (dependencia productora, causal de
    exclusión): un valor que empieza por `= + - @`/control se lee como fórmula."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value

# El LEEME del paquete de consulta lo lee una persona, no una máquina: los tres
# estados del expediente van en la lengua de trabajo. Reserva honesta: un estado
# nuevo se imprime crudo antes que dejar el renglón en blanco.
_ESTADO_EXPEDIENTE_ES: dict[str, str] = {
    "open": "abierto",
    "closed": "cerrado",
    "transferred": "transferido",
}

# Valid state transitions — re-opening a closed expediente is not permitted.
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"closed"},
    "closed": {"transferred"},
    "transferred": set(),
}

# Traza AGREGADA de consulta (RF-BUS-10/E09; Ley 1712/2014, Decreto 1081/2015):
# `object_type` deliberadamente FUERA de `AuditReadRepository._OBJECT_TYPES`
# (`app/repositories/audit_read.py`, hoy `("expediente", "expediente_indice")`)
# — un listado/búsqueda no es una lectura DE un expediente concreto, así que
# no debe colarse en `GET /{id}/historial-acceso` ni en el `<pistaAuditoria>`
# del índice FIRMADO de ninguno de los expedientes devueltos (Ac. AGN
# 001/2024 art. 4.3.2.3): esos se anclan al expediente puntual, esto habla de
# un CONJUNTO. `object_ref` es una constante por el mismo motivo: una
# consulta agregada no tiene un único expediente al que atribuirse.
_SEARCH_OBJECT_TYPE = "expediente_busqueda"
_SEARCH_OBJECT_REF = "listado"


def _expediente_not_found(expediente_id: uuid.UUID) -> HTTPException:
    """Cuerpo 404 NEUTRO, reutilizado por `get_expediente`/`get_aip` — misma
    forma exacta para "no existe", "existe pero por encima del clearance del
    llamante" y (en `get_aip`) "es visible pero no tiene AIP empaquetado
    todavía": ningún camino de denegación/ausencia es distinguible de otro
    (no-read-up, RF-SEG-08)."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "expediente_not_found",
            "detail": "No existe el expediente solicitado",
            "status": 404,
        },
    )


def _safe_uuid(value: str | None) -> uuid.UUID | None:
    """Parseo defensivo para columnas UUID de identidad (`expediente_eventos.actor`)
    pobladas a partir de un parámetro `str | None` histórico (`firmante`/`actor`
    en `close_expediente`/`transfer_expediente`, H-E). Con `require_actor`
    (H-D) el router SIEMPRE entrega un UUID válido, pero ambos métodos siguen
    siendo públicos e invocables directo (tests de integración, jobs) con
    cualquier string -- `None` en vez de un `ValueError` no capturado a mitad
    de una transacción de negocio."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


class ArchiveService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._repo = ArchiveRepository(conn, tenant_slug)
        self._metadata_repo = MetadataRepository(conn, tenant_slug)
        self._index_repo = IndexRepository(conn)
        self._audit_read = AuditReadRepository(conn)
        self._conn = conn
        self._tenant_slug = tenant_slug

    # ------------------------------------------------------------------
    # Expedientes
    # ------------------------------------------------------------------

    async def create_expediente(
        self,
        payload: ExpedienteCreate,
        opened_by: uuid.UUID | None,
    ) -> ExpedienteResponse:
        # No-write-up (RF-SEG-08, M3): antes `nivel_seguridad` pasaba directo
        # al INSERT sin contrastarlo contra el clearance de quien abre el
        # expediente — un portador de USUA_PERM_EXPEDIENTE con clearance
        # PUBLICA podía crear un expediente CLASIFICADA y perderlo de vista de
        # inmediato (list/get/search lo ocultan por no-read-up). Clasificar/
        # reservar es un acto reglado de quien tiene competencia (Ley
        # 1712/2014 arts. 6, 18-20; Decreto 1081/2015): un funcionario no
        # puede originar contenido por encima de su propia habilitación.
        # Espejo exacto del no-write-up de reclasificación en
        # document-service (`document_service.py`, reclassify): 403
        # EXPLÍCITO, NO un clamp silencioso a PUBLICA — rebajar el nivel
        # solicitado sería una desclasificación de facto sin dejar rastro del
        # acto. `opened_by` es el X-User-Id ya validado por el router
        # (`_optional_user`, `routers/expedientes.py`), nunca un campo del
        # body. `caller_clearance(None)` resuelve a PUBLICA=1 (fail-closed);
        # en la práctica ese caso es inalcanzable aquí porque
        # `require_permission` ya exige `USUA_PERM_EXPEDIENTE` y
        # `check_permission` rechaza `user_id is None` con 403 antes de
        # llegar a este handler (D-08) — se mantiene el gate de todos modos
        # por si esa cadena de permisos cambiara.
        clearance = await self._repo.caller_clearance(opened_by)
        if payload.nivel_seguridad > clearance:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": (
                        "No puede crear un expediente con nivel_seguridad "
                        f"{payload.nivel_seguridad} por encima de su propio "
                        f"clearance ({clearance}): clasificar o reservar un "
                        "expediente es un acto reglado, reservado a quien "
                        "tiene la competencia para hacerlo (no-write-up, "
                        "Ley 1712/2014 arts. 6, 18-20; Decreto 1081/2015)."
                    ),
                    "status": 403,
                },
            )

        # ADR-026 (Increment 2, D3): valida ANTES de tocar la base que el
        # instrumento amarrado corresponda a `origen` -- la Capa 4
        # (`trg_expedientes_pin_tipo`, migración 041) lo rechazaría igual,
        # pero como error crudo de Postgres (500, sin `error`/`detail`
        # estructurados). Este chequeo produce el mismo 409 legible que ya
        # usa `create_tvd_agrupacion` para `instrumento_code_en_uso` --
        # el trigger queda como última línea de defensa, no como la única.
        if payload.trd_serie_id is not None:
            pinned = await self._repo.get_trd_serie_by_id(payload.trd_serie_id)
            if pinned is not None:
                esperado = "TRD" if payload.origen == "corriente" else "TVD"
                if pinned.tipo_instrumento != esperado:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "instrumento_origen_mismatch",
                            "detail": (
                                f"El expediente origen='{payload.origen}' no puede "
                                f"amarrarse a '{pinned.code}', que es "
                                f"{pinned.tipo_instrumento} -- espera "
                                f"{esperado} (ADR-026 D3)."
                            ),
                            "status": 409,
                        },
                    )
            # `pinned is None` (id inexistente) se deja pasar sin 409: el
            # `INSERT` reventará con el 404/FK habitual, mismo comportamiento
            # que antes de este ADR.

        # Metadatos (E03, ADR-007): si la serie TRD tiene plantilla activa, los
        # valores se validan contra su JSON Schema antes de abrir el expediente.
        metadata_template_id: uuid.UUID | None = None
        if payload.trd_serie_id is not None:
            template = await self._metadata_repo.get_active_template(payload.trd_serie_id)
            if template is not None:
                validate_metadata(payload.metadata, template["json_schema"])
                metadata_template_id = template["id"]

        year = datetime.now(tz=UTC).year
        # Atomicidad expediente<->auditoría: código+INSERT+índice v0+evento de
        # apertura+asiento forense son un único acto de negocio (Ley 594/2000
        # arts. 21-24) — si cualquiera falla, ninguno debe persistir. Todo el
        # tramo es 100% DB (sin HTTP/Redis a mitad, verificado en
        # `index_service.create_version_zero`/`insert_expediente_evento`/
        # `audit.append`), así que envolverlo es seguro. `audit.append` abre
        # su propia sub-transacción con `pg_advisory_xact_lock`; anidada aquí
        # degrada a SAVEPOINT y el lock se libera al commit de este bloque
        # (aceptable: el tramo es corto y puramente DB, no queda retenido
        # durante I/O externo). La validación previa (clearance/plantilla)
        # queda deliberadamente FUERA: no hay nada que revertir y no
        # conviene mantener la tx abierta durante ella.
        async with self._conn.transaction():
            code = await self._repo.next_expediente_code(year)
            expediente = await self._repo.create_expediente(
                code=code,
                name=payload.name,
                description=payload.description,
                trd_serie_id=payload.trd_serie_id,
                opened_by=opened_by,
                metadata=payload.metadata,
                metadata_template_id=metadata_template_id,
                nivel_seguridad=payload.nivel_seguridad,
                origen=payload.origen,
                fecha_extrema_inicial=payload.fecha_extrema_inicial,
                fecha_extrema_final=payload.fecha_extrema_final,
            )
            # Índice electrónico disponible desde la creación (E15, versión 0).
            from app.services.index_service import IndexService

            await IndexService(self._conn, self._tenant_slug).create_version_zero(expediente)
            # Hoja de ruta del expediente (E02 §9): apertura.
            await self._repo.insert_expediente_evento(
                expediente.id,
                numero_expediente=expediente.code,
                accion="abierto",
                actor=opened_by,
            )
            # Auditoría forense transversal (E08 §9, Canal A).
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.expediente_created",
                object_type="expediente",
                object_ref=expediente.code,
                actor=str(opened_by) if opened_by else None,
                # `nivel_seguridad` deja motivado el acto de clasificación desde
                # la apertura (barato de incluir, valioso cuando >=2/RESERVADA-
                # CLASIFICADA): ver el gate no-write-up más arriba.
                payload={"id": str(expediente.id), "nivel_seguridad": expediente.nivel_seguridad},
                canal=audit.CANAL_PETICION,
            )
        return _to_expediente_response(expediente)

    async def set_publicacion(
        self,
        expediente_id: uuid.UUID,
        publicar: bool,
        fundamento: str,
        max_clearance: int,
        *,
        actor: str | None,
        canal: str = "system",
    ) -> dict[str, Any]:
        """Ejecuta el acto de publicación de oficio del expediente (art. 12).

        La escritura y su asiento van en UNA transacción: un acto de difusión sin
        constancia de quién lo decidió y con qué fundamento no es un acto
        administrativo, es un cambio de estado. Si la auditoría falla, la
        publicación no ocurre.

        `404` neutro para «no existe», «te supera el clearance» y «no es de nivel
        público»: distinguirlos convertiría este endpoint en un oráculo sobre
        expedientes reservados.
        """
        async with self._conn.transaction():
            row = await self._repo.set_publicable(
                expediente_id, publicar, max_clearance, _safe_uuid(actor)
            )
            if row is None:
                raise _expediente_not_found(expediente_id)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action=(
                    "archive.expediente_publicacion_activada"
                    if publicar
                    else "archive.expediente_publicacion_retirada"
                ),
                object_type="expediente",
                # El código del expediente lo resuelve el `RETURNING`, nunca el
                # cliente: es el identificador con el que se auditará el acto.
                object_ref=row["code"],
                actor=actor,
                canal=canal,
                payload={"publicable": row["publicable"], "fundamento": fundamento},
            )
        return {
            "code": row["code"],
            "publicable": row["publicable"],
            "publicado_at": row["publicado_at"],
        }

    async def set_legal_hold(
        self,
        expediente_id: uuid.UUID,
        payload: LegalHoldRequest,
        max_clearance: int,
        *,
        actor: str,
        canal: str = "system",
    ) -> LegalHoldResponse:
        """Declara o levanta el legal hold del expediente (AREA-08-07).

        Único camino de usuario hacia el freeze de litigio de storage: el hold ya
        no es un campo del body de ninguna ruta de protección. Archive decide el
        permiso, comprueba que el expediente es VISIBLE para el llamante (clearance
        en el `WHERE` de la lectura: `404` neutro si no existe o lo supera) y
        orquesta el acto en storage con token interno + identidad firmada.

        Storage asienta el acto atómico con sus filas; archive deja además SU
        asiento (`archive.expediente_legal_hold_fijado/levantado`) con motivo y
        acta. Si storage falla no hay asiento de archive: el acto no ocurrió.
        """
        expediente = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if expediente is None:
            raise _expediente_not_found(expediente_id)

        from app.clients import storage_client  # perezoso, mismo patrón que arriba

        try:
            res = await storage_client.fijar_legal_hold_internal(
                tenant_slug=self._tenant_slug,
                actor_id=actor,
                expediente_id=str(expediente.id),
                activo=payload.activo,
                motivo=payload.motivo,
                acta_ref=payload.acta_ref,
            )
        except storage_client.StorageLegalHoldError as exc:
            if exc.status == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "sin_objetos_protegidos",
                        "detail": str(exc),
                        "status": 404,
                    },
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "storage_unavailable", "detail": str(exc), "status": 503},
            ) from exc

        async with self._conn.transaction():
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action=(
                    "archive.expediente_legal_hold_fijado"
                    if payload.activo
                    else "archive.expediente_legal_hold_levantado"
                ),
                object_type="expediente",
                object_ref=expediente.code,
                actor=actor,
                canal=canal,
                payload={
                    "motivo": payload.motivo,
                    "acta_ref": payload.acta_ref,
                    "objetos_afectados": res["objetos_afectados"],
                    "objetos_sin_cambio": res["objetos_sin_cambio"],
                },
            )
        return LegalHoldResponse(
            expediente_id=expediente.id,
            code=expediente.code,
            activo=payload.activo,
            objetos_afectados=int(res["objetos_afectados"]),
            objetos_sin_cambio=int(res["objetos_sin_cambio"]),
        )

    async def list_expedientes(
        self,
        *,
        status: str | None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[ExpedienteResponse], int]:
        # No-read-up (RF-SEG-08): mismo criterio que `get_expediente`/`search_expedientes`
        # — antes este listado no filtraba por `nivel_seguridad` en absoluto, así que
        # cualquier portador de USUA_PERM_EXPEDIENTE enumeraba expedientes RESERVADA/
        # CLASIFICADA (code/name/description/metadata/disposition/opened_by).
        offset = (page - 1) * size
        clearance = await self._repo.caller_clearance(user_id)
        expedientes, total = await self._repo.list_expedientes(
            status=status, offset=offset, limit=size, max_clearance=clearance
        )
        # Traza AGREGADA de consulta (RF-BUS-10): UNA entrada por listado
        # exitoso — ver `_audit_search_access`.
        await self._audit_search_access(
            action="archive.expediente_consultado_listado",
            filtros={"status": status},
            total=total,
            page=page,
            size=size,
            user_id=user_id,
            canal=canal,
        )
        return [_to_expediente_response(e) for e in expedientes], total

    async def search_expedientes(
        self,
        *,
        q: str | None,
        status: str | None,
        date_from: date | None,
        date_to: date | None,
        metadata_filter: dict | None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[asyncpg.Record], int]:
        """Full-text search de expedientes (E09/ADR-014).

        Envuelve `ArchiveRepository.search_expedientes` (antes invocado
        directamente desde el router, sin pasar por el servicio) para poder
        dejar la traza agregada de consulta (RF-BUS-10) con el mismo criterio
        que `list_expedientes` — actor/canal/total en un solo sitio.
        """
        rows, total = await self._repo.search_expedientes(
            q=q,
            status=status,
            date_from=date_from,
            date_to=date_to,
            metadata_filter=metadata_filter,
            page=page,
            size=size,
            user_id=user_id,
        )
        # `q` es texto libre tecleado por quien busca y puede contener datos
        # personales (nombres, números de documento, asuntos) — RF-BUS-10
        # exige dejar constancia de los CRITERIOS y el TOTAL para rendición de
        # cuentas, no necesariamente el texto crudo, que además duplicaría
        # PII dentro de una cadena de auditoría INMUTABLE y de retención
        # indefinida (Ley 1712/2014 principio de minimización). Se registra
        # solo presencia + longitud de `q`; `status`/`date_from`/`date_to`
        # son criterios estructurales (no datos personales del buscador) y sí
        # se registran tal cual; de `metadata_filter` solo las CLAVES
        # consultadas (los valores podrían igualmente ser PII).
        filtros = {
            "q_presente": q is not None,
            "q_len": len(q) if q else None,
            "status": status,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "metadata_filter_keys": sorted(metadata_filter) if metadata_filter else None,
        }
        await self._audit_search_access(
            action="archive.expediente_consultado_busqueda",
            filtros=filtros,
            total=total,
            page=page,
            size=size,
            user_id=user_id,
            canal=canal,
        )
        return rows, total

    async def _audit_search_access(
        self,
        *,
        action: str,
        filtros: dict,
        total: int,
        page: int,
        size: int,
        user_id: uuid.UUID | None,
        canal: str,
    ) -> None:
        """Traza AGREGADA de consulta (RF-BUS-10/E09; Ley 1712/2014, Decreto
        1081/2015) — gemelo de `_audit_read_access` pero UNA sola entrada por
        listado/búsqueda EXITOSO, nunca por fila del resultado: auditar por
        fila (i) infla la cadena de hash de `public.audit_log` (encadenada
        por tenant, `orpycamcp_common.audit`) proporcionalmente a `size`, y
        (ii) contaminaría `GET /{id}/historial-acceso` y el
        `<pistaAuditoria>` del índice FIRMADO de CADA expediente devuelto
        (Ac. AGN 001/2024 art. 4.3.2.3) con eventos que en realidad describen
        un CONJUNTO, no una lectura de ESE expediente — de ahí que
        `object_type`/`object_ref` sean las constantes de módulo
        `_SEARCH_OBJECT_TYPE`/`_SEARCH_OBJECT_REF`, deliberadamente fuera del
        `_OBJECT_TYPES` que lee `AuditReadRepository`.

        Best-effort: nunca degrada la consulta a 500 por un fallo de
        auditoría. Mismo gate que `_audit_read_access`: canal no auditable o
        `user_id is None` descartan el registro (sin actor real no hay a
        quién atribuir la consulta).
        """
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action=action,
                object_type=_SEARCH_OBJECT_TYPE,
                object_ref=_SEARCH_OBJECT_REF,
                actor=str(user_id),
                canal=canal,
                payload={**filtros, "total": total, "page": page, "size": size},
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (%s) failed", action, exc_info=True)

    async def get_expediente(
        self,
        expediente_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> ExpedienteResponse:
        max_clearance = await self._repo.caller_clearance(user_id)
        expediente = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if expediente is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        await self._audit_read_access(expediente.code, user_id=user_id, canal=canal)
        return _to_expediente_response(expediente)

    async def export_expediente_zip(
        self,
        expediente_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[ExportZipStream, str]:
        """Exporta el expediente como ZIP de USUARIO (E02/export): índice
        electrónico XML (si existe) + manifiesto CSV + los bytes de los anexos de
        sus radicados VINCULADOS. **No-read-up a nivel de expediente**: 404 si el
        expediente supera el clearance del llamante; si es visible, se exporta su
        contenido (el ensamblado de bytes lo hace storage-service, D-02). Devuelve
        `(stream, filename)`: el ZIP llega de storage ya completo y se reenvía por
        trozos sin bufferizarlo (B4-5); los fallos de storage se resuelven aquí,
        antes del primer byte.
        """
        import base64
        import csv
        import io

        from app.clients import document_client, storage_client

        max_clearance = await self._repo.caller_clearance(user_id)
        expediente = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if expediente is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        # Inventario CLEARANCE-AWARE (RF-SEG-08, cierre de la fuga read-up
        # por-radicado): document-service filtra por `nivel_seguridad <=
        # max_clearance` y devuelve una entrada por cada radicado VISIBLE (aunque
        # sin anexos), omitiendo los sobre-clearance. Se piden TODOS los radicados
        # (vinculados + excluidos) para conocer su visibilidad — el manifiesto
        # representa el hueco trazable del excluido (Ac. AGN 001/2024 art. 4.3.2.3)
        # SOLO si el radicado es visible; un radicado clasificado por encima del
        # clearance del llamante no se enumera siquiera. `expediente.radicados` ya
        # viene ordenado por `orden_documental` (orden original preservado).
        anexos_by_rad: dict[str, list[dict]] = {}
        all_ids = [r.radicado_id for r in expediente.radicados]
        if all_ids:
            try:
                anexos_by_rad = await document_client.fetch_anexos_internal(
                    tenant_slug=self._tenant_slug,
                    radicado_ids=all_ids,
                    max_clearance=max_clearance,
                )
            except document_client.DocumentClientError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "error": "inventario_anexos_no_disponible",
                        "detail": str(exc),
                        "status": 502,
                    },
                )
        visible_ids = set(anexos_by_rad.keys())

        # Metadata (asunto/tipo) de los radicados visibles para el manifiesto
        # (E02/export deuda) — best-effort: un fallo no impide la exportación,
        # solo deja esas columnas vacías. Mismo `max_clearance` (no-read-up).
        meta_by_rad: dict[str, dict] = {}
        if all_ids:
            try:
                meta_by_rad = await document_client.fetch_radicado_metadata_internal(
                    tenant_slug=self._tenant_slug,
                    radicado_ids=all_ids,
                    max_clearance=max_clearance,
                )
            except document_client.DocumentClientError:
                meta_by_rad = {}

        items: list[dict] = []
        manifest = io.StringIO()
        writer = csv.writer(manifest)
        writer.writerow(
            [
                "orden_documental",
                "numero_radicado",
                "tipo",
                "asunto",
                "estado",
                "folios",
                "formato",
                "dependencia_productora",
                "fecha_incorporacion",
                "causal_exclusion",
                "num_anexos",
            ]
        )
        num_documentos = 0  # documentos VISIBLES vinculados (para el asiento)
        for r in expediente.radicados:
            # No-read-up: un radicado sobre el clearance del llamante no aparece
            # en `visible_ids` → se omite del manifiesto (ni su existencia se filtra).
            if str(r.radicado_id) not in visible_ids:
                continue
            if r.estado == "vinculado":
                num_documentos += 1
                anexos = anexos_by_rad.get(str(r.radicado_id), [])
                for idx, a in enumerate(anexos):
                    # Saneo del filename (subido por el usuario) a un LEAF sin
                    # separadores: un '/' o '\' partiría el arcname en segmentos y
                    # un nombre hostil haría fallar `_safe_arcname` en storage (422
                    # → el export ENTERO del expediente caería). El prefijo `idx-`
                    # ya garantiza unicidad y neutraliza `.`/`..` exactos.
                    leaf = str(a["filename"]).replace("/", "_").replace("\\", "_") or "archivo"
                    items.append(
                        {
                            "file_id": str(a["file_id"]),
                            "arcname": f"documentos/{r.tracking_number}/{idx}-{leaf}",
                        }
                    )
                num_anexos = len(anexos)
                causal = ""
            else:
                # Excluido (soft-delete): SIN bytes, pero representado en el
                # manifiesto con su causal para preservar la trazabilidad.
                num_anexos = 0
                causal = r.excluido_causal or ""
            meta = meta_by_rad.get(str(r.radicado_id), {})
            writer.writerow(
                [
                    "" if r.orden_documental is None else r.orden_documental,
                    r.tracking_number,
                    meta.get("doc_type", ""),
                    _csv_safe(meta.get("subject", "")),
                    r.estado,
                    "" if r.folios is None else r.folios,
                    # Caída a la metadata del origen para los vínculos ANTERIORES
                    # a que estas dos columnas se resolvieran en el servidor: el
                    # dato ya viene en `meta_by_rad` (mismo viaje), y un
                    # manifiesto que deja la columna vacía teniendo el dato al
                    # lado es el patrón que este arreglo cierra. Lo GUARDADO
                    # manda: es la copia congelada del instante del vínculo.
                    _csv_safe(r.formato or meta.get("formato") or ""),
                    _csv_safe(
                        r.dependencia_productora_nombre or meta.get("origin_dept") or ""
                    ),
                    r.added_at.isoformat() if r.added_at else "",
                    _csv_safe(causal),
                    num_anexos,
                ]
            )

        # Índice electrónico XML — best-effort: un expediente sin índice firmado
        # (aún abierto) se exporta igual, solo con el manifiesto + los anexos.
        indice_incluido = False
        indice_firmado = False
        extra_files: list[dict] = []
        try:
            from app.services.index_service import IndexService

            _index = IndexService(self._conn, self._tenant_slug)
            xml = await _index.get_xml(
                expediente_id, version=None, user_id=user_id, canal=canal
            )
            # Si el índice está FIRMADO es un dato distinto de si su XML viaja en
            # el paquete, y hasta ahora el LEEME confundía los dos (ver abajo).
            # Best-effort como el propio XML: un fallo aquí no impide exportar,
            # solo hace que el paquete no afirme una firma que no puede comprobar.
            try:
                _indice = await _index.get_indice(
                    expediente_id, version=None, user_id=user_id, canal=canal
                )
                indice_firmado = _indice.estado == "firmado"
            except Exception:  # noqa: BLE001
                # `except HTTPException` NO bastaba, y lo enseñó un test que ya
                # existía: cualquier otro fallo aquí tumbaba el EXPORT ENTERO por
                # un dato accesorio del LEEME. El índice es best-effort en este
                # camino —el bloque de arriba lo trata igual—, así que ante la
                # duda el paquete se entrega sin afirmar una firma que no pudo
                # comprobar, que es la respuesta segura.
                indice_firmado = False
            extra_files.append(
                {
                    "arcname": "indice-electronico.xml",
                    "contenido_b64": base64.b64encode(xml.encode("utf-8")).decode(),
                }
            )
            indice_incluido = True
        except HTTPException:
            pass

        # LEEME de ALCANCE (RF-FIR-15): que el receptor no confunda esta copia de
        # consulta con un AIP acreditado (E10) ni una transferencia FUID (E12).
        leyenda = (
            "COPIA DE CONSULTA DE EXPEDIENTE - OrpycaMCP\n"
            "===========================================\n\n"
            f"Expediente: {expediente.code}\n"
            f"Estado del expediente al exportar: {_ESTADO_EXPEDIENTE_ES.get(expediente.status, expediente.status)}\n"
            # Dos hechos distintos, y antes se afirmaban como uno solo: la línea
            # decia «Indice electronico FIRMADO incluido: si» leyendo una variable
            # que solo significa «el XML se pudo incluir». Un expediente abierto
            # se exporta con su índice sin firmar —el comentario de arriba lo dice
            # expresamente— y el paquete de entrega afirmaba que llevaba un índice
            # firmado. Quien recibe esta copia lee esa línea como garantía de
            # autenticidad; que sea falsa es un defecto probatorio, no de estilo.
            f"Indice electronico incluido: {'si' if indice_incluido else 'no'}\n"
            f"Indice electronico firmado: "
            f"{'si' if indice_firmado else 'no (pendiente de firma)' if indice_incluido else 'no'}\n\n"
            "ALCANCE: este ZIP es una copia de CONSULTA/ENTREGA para un usuario. "
            "NO es un paquete de preservacion acreditado (AIP, E10) ni una "
            "transferencia documental formal con FUID (E12). Los radicados "
            "excluidos figuran en el manifiesto (sin sus bytes) para preservar la "
            "trazabilidad del expediente (Acuerdo AGN 001/2024 art. 4.3.2.3). "
            "Verifique la integridad de cada archivo con checksums.txt.\n"
        )
        extra_files.append(
            {
                "arcname": "LEEME.txt",
                "contenido_b64": base64.b64encode(leyenda.encode("utf-8")).decode(),
            }
        )
        extra_files.append(
            {
                "arcname": "manifiesto.csv",
                "contenido_b64": base64.b64encode(manifest.getvalue().encode("utf-8")).decode(),
            }
        )

        try:
            stream = await storage_client.export_zip_internal(
                tenant_slug=self._tenant_slug, items=items, extra_files=extra_files
            )
        except storage_client.StorageExportError as exc:
            if exc.status_code == 413:
                err, code = "export_demasiado_grande", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            elif exc.status_code == 507:
                err, code = "espacio_insuficiente", status.HTTP_507_INSUFFICIENT_STORAGE
            else:
                err, code = "export_no_disponible", status.HTTP_502_BAD_GATEWAY
            raise HTTPException(
                status_code=code, detail={"error": err, "detail": str(exc), "status": code}
            ) from exc
        try:
            await self._audit_export_access(
                expediente.code,
                user_id=user_id,
                canal=canal,
                num_documentos=num_documentos,
                num_anexos=len(items),
            )
        except BaseException:
            await stream.aclose()
            raise
        return stream, f"expediente-{expediente.code}.zip"

    async def _audit_export_access(
        self,
        code: str,
        *,
        user_id: uuid.UUID | None,
        canal: str,
        num_documentos: int,
        num_anexos: int,
    ) -> None:
        """Traza de la exportación (RF-BUS-10): una descarga masiva del expediente
        deja asiento con actor/total, best-effort (no degrada la descarga a 500)."""
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.expediente_exportado",
                object_type="expediente",
                object_ref=code,
                actor=str(user_id),
                canal=canal,
                payload={"num_documentos": num_documentos, "num_anexos": num_anexos},
            )
        except Exception:  # noqa: BLE001 — auditoría best-effort
            logger.warning("audit.append (expediente_exportado) failed", exc_info=True)

    async def get_aip(
        self,
        expediente_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> dict:
        """Lectura mediada del AIP de preservación digital (E10 Increment C,
        cierre del no-read-up en `GET /preservacion/aip/{id}` de
        storage-service, RF-SEG-08). storage-service no modela
        `nivel_seguridad` sobre `aip` (solo conoce `expediente_id` como
        string libre, ver `storage-service/migrations/tenant/004_aip.sql`) —
        archive-service SÍ conoce la clasificación VIGENTE del expediente, así
        que resuelve el clearance del llamante y confirma visibilidad ANTES de
        mediar la llamada a storage-service, mismo criterio no-read-up que
        `get_expediente` (nunca la clasificación cacheada/denormalizada — la
        VIGENTE, resuelta en cada lectura).

        `expediente_id` (el UUID de este servicio) se pasa a storage-service
        como `str(expediente.id)` — ver el docstring de
        `app.clients.storage_client.get_aip` para el contrato de ese string
        con un futuro empaquetador.

        404 NEUTRO (`_expediente_not_found`) en los dos caminos de
        denegación/ausencia: expediente inexistente o por encima del
        clearance del llamante, Y expediente visible pero SIN AIP empaquetado
        todavía — misma forma de respuesta en ambos, para que no exista un
        oráculo de "¿hay un AIP para este expediente clasificado?". Un fallo
        de storage-service (red, timeout, HTTP inesperado) es ortogonal a la
        autorización y se traduce a 503, no a un 404 que confundiría "no
        autorizado" con "servicio caído".
        """
        max_clearance = await self._repo.caller_clearance(user_id)
        expediente = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if expediente is None:
            raise _expediente_not_found(expediente_id)

        from app.clients import storage_client  # perezoso, mismo patrón que index_service.py

        try:
            aip = await storage_client.get_aip(
                tenant_slug=self._tenant_slug, expediente_id=str(expediente.id)
            )
        except storage_client.StorageAipError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "storage_unavailable",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc
        if aip is None:
            raise _expediente_not_found(expediente_id)
        # Pista de acceso a material clasificado (RF-BUS-10; Ley 594/2000 art. 22):
        # la lectura del AIP divulga el inventario documental completo del
        # expediente — se audita como acceso fuerte, gemelo de `_audit_read_access`
        # pero con acción propia para distinguirla de la lectura del metadato.
        await self._audit_aip_access(expediente.code, user_id=user_id, canal=canal)
        return aip

    async def reintentar_aip(
        self, expediente_id: uuid.UUID, *, actor: str, user_id: uuid.UUID | None
    ) -> dict:
        """Reintento MANUAL del empaquetado de un AIP `agotado` (AREA-07-14).

        El job solo lo pasa a `agotado` tras `indice_reconciliation_max_intentos`; sin
        esta ruta el AIP de un expediente quedaba sin empaquetar para siempre salvo
        tocar la base. UPDATE gateado en el WHERE por estado y por clearance
        (`AipRepository.reintentar_manual`) y asiento `archive.aip_reintento_manual` en
        la MISMA transacción. 404 neutro si no aplica por cualquier causa: expediente
        inexistente o por encima del clearance, AIP que no está `agotado`."""
        from app.repositories.aip_repository import AipRepository

        max_clearance = await self._repo.caller_clearance(user_id)
        async with self._conn.transaction():
            fila = await AipRepository(self._conn).reintentar_manual(
                expediente_id, max_clearance=max_clearance
            )
            if fila is None:
                raise _expediente_not_found(expediente_id)
            code = await self._conn.fetchval(
                "SELECT code FROM expedientes WHERE id = $1", expediente_id
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.aip_reintento_manual",
                object_type="expediente_aip",
                object_ref=code or str(expediente_id),
                actor=actor,
                payload={
                    "expediente_id": str(expediente_id),
                    "intentos_previos": fila["intentos_previos"],
                    "error_previo": fila["error_previo"],
                },
                canal=audit.CANAL_PETICION,
            )
        return {"expediente_id": fila["expediente_id"], "estado": fila["estado"]}

    async def _audit_aip_access(
        self, code: str, *, user_id: uuid.UUID | None, canal: str
    ) -> None:
        """Pista de acceso al AIP (RF-BUS-10) — gemelo de `_audit_read_access`
        con acción `archive.aip_consultado`. Best-effort y gated por canal/actor
        real; sin actor real (canal 'system' o `user_id is None`) no escribe."""
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.aip_consultado",
                object_type="expediente",
                object_ref=code,
                actor=str(user_id),
                canal=canal,
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (aip_consultado) failed", exc_info=True)

    async def get_eventos_preservacion(
        self,
        expediente_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> list[dict]:
        """Lectura mediada de los eventos PREMIS de preservación digital
        (continuación de E10 Increment C, cierre del no-read-up en
        `GET /preservacion/eventos` de storage-service, RF-SEG-08).
        storage-service no modela `nivel_seguridad` sobre `preservacion_evento`
        (solo conoce `object_ref` como string libre) — archive-service SÍ
        conoce la clasificación VIGENTE del expediente, así que resuelve el
        clearance del llamante y confirma visibilidad ANTES de mediar la
        llamada a storage-service, mismo criterio no-read-up que `get_aip`
        (nunca la clasificación cacheada/denormalizada — la VIGENTE, resuelta
        en cada lectura).

        `expediente_id` (el UUID de este servicio) se pasa a storage-service
        como `object_ref=str(expediente.id)` — ver el docstring de
        `app.clients.storage_client.list_eventos`: el filtro es exacto, los
        eventos keyed por `file_id`/`aip` id de ese expediente NO se incluyen
        en este MVP (refinamiento semántico diferido, no inventar un join a
        los file_ids).

        404 NEUTRO (`_expediente_not_found`) si el expediente no existe o
        está por encima del clearance del llamante — a diferencia de
        `get_aip`, NO hay un tercer caso "visible pero sin eventos": una
        lista vacía es una respuesta legítima (200), no un 404, porque
        "expediente sin actividad de preservación todavía" no es indistinguible
        de "no autorizado" del mismo modo que "sin AIP empaquetado" (el AIP es
        una operación explícita y rara; los eventos son una traza continua).
        Un fallo de storage-service (red, timeout, HTTP inesperado) es
        ortogonal a la autorización y se traduce a 503.
        """
        max_clearance = await self._repo.caller_clearance(user_id)
        expediente = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if expediente is None:
            raise _expediente_not_found(expediente_id)

        from app.clients import storage_client  # perezoso, mismo patrón que get_aip

        # AREA-07-15: los eventos de preservación NO cuelgan todos del expediente —
        # la validación PDF/A y las migraciones de formato van por `file_id`. Antes solo
        # se pedía `object_ref=expediente_id` y la pestaña Preservación los omitía.
        # El conjunto se resuelve AQUÍ, desde el inventario del sistema (nunca del
        # cliente) y con el clearance del llamante: los anexos de un radicado por encima
        # de él no se incluyen (sin fuga de existencia).
        refs = await self._object_refs_preservacion(expediente, max_clearance)

        try:
            eventos = await storage_client.list_eventos(
                tenant_slug=self._tenant_slug, object_refs=refs
            )
        except storage_client.StorageEventosError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "storage_unavailable",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc
        # Pista de acceso a material clasificado (RF-BUS-10; Ley 594/2000 art. 22):
        # la lectura de los eventos divulga actividad de preservación del
        # expediente — se audita como acceso, gemelo de `_audit_aip_access`
        # pero con acción propia.
        await self._audit_eventos_access(expediente.code, user_id=user_id, canal=canal)
        return eventos

    async def _object_refs_preservacion(
        self, expediente: Expediente, max_clearance: int
    ) -> list[str]:
        """`object_ref` de los eventos PREMIS que pertenecen al expediente: él mismo, su
        AIP (`aip_worm_object_id`) y los `file_id` de los anexos de sus radicados VIGENTES
        visibles para el llamante. Fail-closed ante document-service caído: una lista
        parcial presentada como completa sería peor que un 503."""
        from app.repositories.aip_repository import AipRepository

        refs: list[str] = [str(expediente.id)]
        aip_id = await AipRepository(self._conn).get_aip_object_id(expediente.id)
        if aip_id is not None:
            refs.append(str(aip_id))
        vigentes = [r.radicado_id for r in expediente.radicados if r.estado == "vinculado"]
        try:
            anexos = await document_client.fetch_anexos_internal(
                tenant_slug=self._tenant_slug,
                radicado_ids=vigentes,
                max_clearance=max_clearance,
            )
        except DocumentClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "document_service_unavailable",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc
        for radicado_id in vigentes:
            for anexo in anexos.get(str(radicado_id), []):
                ref = str(anexo["file_id"])
                if ref not in refs:
                    refs.append(ref)
        return refs

    async def _audit_eventos_access(
        self, code: str, *, user_id: uuid.UUID | None, canal: str
    ) -> None:
        """Pista de acceso a los eventos de preservación (RF-BUS-10) — gemelo
        de `_audit_aip_access` con acción `archive.preservacion_eventos_consultados`.
        Best-effort y gated por canal/actor real; sin actor real (canal
        'system' o `user_id is None`) no escribe."""
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.preservacion_eventos_consultados",
                object_type="expediente",
                object_ref=code,
                actor=str(user_id),
                canal=canal,
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning(
                "audit.append (preservacion_eventos_consultados) failed", exc_info=True
            )

    async def _audit_read_access(
        self, code: str, *, user_id: uuid.UUID | None, canal: str
    ) -> None:
        """Pista de acceso (E15 perfil v4, R3-a) — gemelo de
        `IndexService._audit_read_access`: una lectura exitosa de `GET
        /expedientes/{id}` deja traza en `audit_log` con el actor real y el
        canal de origen; 'system' (llamadas internas que reutilizan
        `get_expediente` como lectura auxiliar, no como respuesta de una
        mutación — `close_expediente`/`transfer_expediente` ya NO pasan por
        aquí, construyen su propia `ExpedienteDisposicionResult` sin re-leer)
        NO escribe pista. Best-effort: nunca degrada la lectura a 500 por un
        fallo de auditoría.

        `user_id is None` también descarta el registro (espejo de
        `IndexService._audit_read_access`): sin actor real no hay a quién
        atribuir la lectura (R3-a exige un actor real, no `actor=None`)."""
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.expediente_consultado",
                object_type="expediente",
                object_ref=code,
                actor=str(user_id) if user_id else None,
                canal=canal,
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (expediente_consultado) failed", exc_info=True)

    async def update_expediente(
        self,
        expediente_id: uuid.UUID,
        payload: ExpedienteUpdate,
        user_id: uuid.UUID | None = None,
    ) -> ExpedienteResponse:
        """Actualiza metadatos (name/description/trd_serie_id). NO transiciona el
        ciclo de vida (`status` ya no existe en `ExpedienteUpdate` — ver su
        docstring): eso es exclusivo de `POST /{id}/close` y `POST /{id}/transfer`.

        No-read-up (RF-SEG-08): el PATCH devuelve `ExpedienteResponse` completo
        (mismo leak que `GET /{id}` si se usara el clearance "sin restricción")
        — resuelve el clearance real del llamante, igual que `get_expediente`.
        Mantiene semántica 404-no-403: un expediente por encima del clearance
        no revela su existencia. Esta decisión de seguridad NO cambia aquí.

        Reclasificación TRD (Ley 594/2000 art. 24): cambiar `trd_serie_id`
        altera el tiempo de retención y la disposición final proyectada — es
        un acto con efecto jurídico, no una corrección cosmética. Por eso:
        (a) exige el expediente `open` (`_assert_open`, mismo criterio que
        link/unlink — sobre closed/transferred la disposición ya está
        materializada o el expediente ya no está bajo custodia); (b) exige un
        actor identificable (X-User-Id) — una reclasificación sin responsable
        no puede escribirse; (c) deja un asiento propio
        `archive.expediente_reclasificado` con la disposición PROYECTADA
        antes/después (RF-FIR-15: no afirma que `expedientes.disposition` ya
        cambió en disco — eso solo ocurre al cierre).

        Atomicidad mutación<->asiento (mismo patrón que H1/H2): el UPDATE, el
        evento de la hoja de ruta y `audit.append` son un único acto de
        negocio dentro de una sola `conn.transaction()`. El "before" auditado
        sale del propio `UPDATE ... RETURNING` (repo, `FOR UPDATE`), NO de la
        pre-lectura no-read-up de arriba (lección H3: hay una ventana TOCTOU
        entre ambas). Si no hay cambios efectivos, no se emite evento ni
        asiento (no se contamina la cadena con actos que no ocurrieron).

        Intentos denegados (H6): si el bloque falla (expediente no-open al
        reclasificar, actor ausente), se deja un asiento best-effort
        `archive.expediente_update_denied` FUERA de la tx ya revertida
        (autocommit) — nunca degrada el fallo real a 500, nunca enmascara la
        excepción original.
        """
        max_clearance = await self._repo.caller_clearance(user_id)
        existing = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )

        intenta_reclasificar = (
            payload.trd_serie_id is not None and payload.trd_serie_id != existing.trd_serie_id
        )
        actor = str(user_id) if user_id is not None else None

        try:
            if intenta_reclasificar:
                self._assert_open(existing)
                if actor is None:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "error": "actor_required",
                            "detail": (
                                "La reclasificación de TRD requiere un actor "
                                "identificable (X-User-Id)."
                            ),
                            "status": 403,
                        },
                    )

            async with self._conn.transaction():
                result = await self._repo.update_expediente(
                    expediente_id,
                    name=payload.name,
                    description=payload.description,
                    trd_serie_id=payload.trd_serie_id,
                )
                if result is None:
                    # Solo alcanzable si el expediente desapareció entre la
                    # pre-lectura y este UPDATE — no hay DELETE de expedientes
                    # hoy, así que es defensivo, no un caso normal.
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "expediente_update_failed",
                            "detail": "La actualización no devolvió el expediente.",
                            "status": 409,
                        },
                    )
                updated, old = result

                changed: dict[str, Any] = {}
                if payload.name is not None and payload.name != old["name"]:
                    changed["name"] = {"before": old["name"], "after": updated.name}
                if payload.description is not None and payload.description != old["description"]:
                    changed["description"] = {
                        "before": old["description"],
                        "after": updated.description,
                    }
                if payload.trd_serie_id is not None and payload.trd_serie_id != old["trd_serie_id"]:
                    # Anotado: la fila viene sin tipar y `trd_serie_id`
                    # sale como `object`, que ni siquiera tras comprobar
                    # `None` sirve como UUID para `compute_retention`.
                    old_serie_id: uuid.UUID | None = old["trd_serie_id"]
                    changed["trd_serie_id"] = {
                        "before": str(old_serie_id) if old_serie_id else None,
                        "after": str(updated.trd_serie_id),
                    }

                if changed:
                    reclasifica = "trd_serie_id" in changed
                    action = (
                        "archive.expediente_reclasificado"
                        if reclasifica
                        else "archive.expediente_updated"
                    )
                    audit_payload: dict[str, Any] = {"changed": changed}
                    if reclasifica:
                        # ADR-026 (Increment 2, D4): esta es una PROYECCIÓN
                        # informativa (el expediente sigue `open`, M2 RF-SEG-08 --
                        # la disposición real se recalcula, y esta vez sí se
                        # congela, en `close_expediente`) -- pero el mismo
                        # peligro de D4 aplica igual: para un expediente
                        # `fondo_acumulado`, proyectar desde `hoy` en vez de
                        # `fecha_extrema_final` mostraría un calendario que no
                        # es el que `close_expediente` va a congelar. `origen`
                        # no cambia entre el amarre viejo y el nuevo (no hay
                        # ruta para editarlo), así que ambas proyecciones usan
                        # la MISMA base.
                        if updated.origen == "fondo_acumulado" and updated.fecha_extrema_final:
                            base_date = updated.fecha_extrema_final
                            base_date_origen: Literal["closed_at", "fecha_extrema_final"] = (
                                "fecha_extrema_final"
                            )
                        else:
                            base_date = datetime.now(tz=UTC).date()
                            base_date_origen = "closed_at"
                        old_serie_id = old["trd_serie_id"]
                        new_serie_id = updated.trd_serie_id
                        serie_before = (
                            await self.compute_retention(
                                old_serie_id, base_date, base_date_origen=base_date_origen
                            )
                            if old_serie_id is not None
                            else None
                        )
                        serie_after = (
                            await self.compute_retention(
                                new_serie_id, base_date, base_date_origen=base_date_origen
                            )
                            if new_serie_id is not None
                            else None
                        )
                        audit_payload["serie_codigo"] = {
                            "before": serie_before.code if serie_before else None,
                            "after": serie_after.code if serie_after else None,
                        }
                        audit_payload["disposicion_proyectada"] = {
                            "before": serie_before.model_dump(mode="json")
                            if serie_before
                            else None,
                            "after": serie_after.model_dump(mode="json") if serie_after else None,
                        }
                        audit_payload["expediente_status"] = updated.status

                    await self._repo.insert_expediente_evento(
                        expediente_id,
                        numero_expediente=updated.code,
                        accion="reclasificado" if reclasifica else "actualizado",
                        actor=user_id,
                        detalles={"changed": changed},
                    )
                    # `audit.append` LO ÚLTIMO dentro de la tx (H1/H2/H4).
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service=_SERVICE,
                        action=action,
                        object_type="expediente",
                        object_ref=updated.code,
                        actor=actor,
                        payload=audit_payload,
                        canal=audit.CANAL_PETICION,
                    )
        except HTTPException as exc:
            # Best-effort (mismo patrón que `audit.append_denial` /
            # `expediente_close_failed`): nunca deja que un fallo de auditoría
            # oculte el error real, nunca lo convierte en un 500 por su cuenta.
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_update_denied",
                    object_type="expediente",
                    object_ref=existing.code,
                    actor=actor,
                    payload={
                        "status_actual": existing.status,
                        "motivo": motivo_de(exc),
                    },
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(expediente_update_denied) failed", exc_info=True
                )
            raise

        return _to_expediente_response(updated)

    # ------------------------------------------------------------------
    # Ciclo de vida del expediente (E02/ADR-015)
    # ------------------------------------------------------------------

    async def _expediente_or_404(self, expediente_id: uuid.UUID):
        # Usado SOLO por close/transfer: pre-condición interna de existencia/
        # estado, NO una lectura expuesta al llamante — ambos ya exigen
        # `USUA_PERM_EXPEDIENTE` crud>=3 en el router (D-08). No-read-up aquí
        # NO es deuda: la disposición (cierre/transferencia) es ortogonal al
        # clearance de lectura por diseño DECIDIDO (Opción B, validada por
        # archival-compliance-auditor — Ley 594/2000 arts. 22-24/27, Ley
        # 1712/2014 arts. 18-19; gatear disposición por clearance invertiría
        # el mínimo privilegio: un archivista de custodia mueve series al AGN
        # sin leer cada expediente reservado). El único constraint es que la
        # respuesta de close/transfer no exponga contenido clasificado — ver
        # `ExpedienteDisposicionResult`. Ver `core.clearance.
        # CLEARANCE_SIN_RESTRICCION` para el contraste con los usos SÍ
        # diferidos (link/unlink/create).
        exp = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        return exp

    def _assert_transition(self, current: str, target: str) -> None:
        if target not in _VALID_TRANSITIONS.get(current, set()):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "invalid_status_transition",
                    "detail": f"No se puede pasar de «{current}» a «{target}»",
                    "status": 409,
                },
            )

    async def close_expediente(
        self,
        expediente_id: uuid.UUID,
        firmante: str | None = None,
        actor_id: str | None = None,
    ) -> ExpedienteDisposicionResult:
        """Cierra el expediente: materializa la disposición TRD, genera la versión
        final del índice y sella XAdES-B el índice (E06 Inc.1, best-effort).

        `firmante` es el valor mostrado en la hoja de ruta/auditoría. `actor_id`
        es estrictamente el X-User-Id (UUID de `auth_users.id`) que
        signature-service exige para atribuir el sello (RF-FIR-11/12): si
        falta o la firma falla, el índice queda 'pendiente_firma' (decisión
        del usuario, RT-16) — el cierre del expediente NUNCA se bloquea por
        esto. Desde H-D (E12, `app.core.actor.require_actor`), el router
        pasa el MISMO valor (`Actor.audit_actor`, el UUID como string) en
        ambos parámetros — ya no hay fallback a X-Username: antes `firmante`
        podía caer a un username si faltaba X-User-Id, dejando una identidad
        inconsistente con la de `TransferenciaService`/`transfer_expediente`
        para el mismo funcionario.

        Atomicidad transición<->auditoría (mismo patrón que `link_radicado`):
        la transición de estado, el evento de la hoja de ruta y la
        regeneración final del índice (tramo 100% DB, sin HTTP a mitad) son
        un único acto de negocio dentro de una sola `conn.transaction()` —
        antes cada sentencia commiteaba por su cuenta (autocommit) y un fallo
        a mitad (p. ej. el índice ya 'firmado' por un `POST /indice/firmar`
        previo sobre el expediente aún open) dejaba el expediente 'closed'
        SIN evento ni asiento de auditoría, de forma permanente e
        irreproducible (el reintento choca con 409 `invalid_status_transition`).

        El sellado XAdES-B (`firmar_indice_close`) es HTTP y queda
        deliberadamente FUERA de la transacción, tras el commit, best-effort
        (RT-16): (a) un POST no se revierte con ROLLBACK — si el sello se
        emite y la tx aborta, signature-service ya lo tendría marcado sobre
        una fila revertida; (b) mantener la tx abierta durante la latencia
        HTTP (incluidos los reintentos síncronos acotados del control
        compensatorio, residual Ac. AGN 001/2024 art. 4.3.2.4) retendría el
        `pg_advisory_xact_lock` del tenant que `audit.append` toma
        internamente, serializando TODA la auditoría del tenant. Por eso el
        asiento 'archive.expediente_closed' (dentro de la tx, lo último) NO
        puede afirmar `indice_estado`/`firma_id` del sellado — ese resultado
        aún no existe en el instante del commit (RF-FIR-15, no
        sobre-declarar) — y el desenlace del sellado se audita aparte,
        después, en su propio asiento **dentro** de
        `IndexService.firmar_indice_close` (atómico con la marca de estado
        del índice — ver su docstring, H5).

        Traza de intentos denegados (H6): el Modelo de Requisitos SGDEA exige
        que la pista registre también los intentos DENEGADOS de alterar el
        ciclo de vida de un expediente (es lo que permite demostrar que el
        control existió y actuó). Ante un `HTTPException`, se deja fuera de la
        tx (ya revertida, en autocommit) un asiento
        'archive.expediente_close_failed' — mismo patrón best-effort que
        `audit.append_denial` (`core/clearance.py`): nunca degrada el fallo
        real a un 500 por un problema de auditoría, y jamás enmascara la
        excepción original (`raise` sin argumentos al final, siempre re-lanza
        LA MISMA excepción de negocio).

        El `try` abarca TAMBIÉN las validaciones previas (`_assert_transition`,
        `compute_retention`), no solo el bloque transaccional: el intento
        denegado más FRECUENTE es precisamente el que rebota en la validación
        —cerrar un expediente ya 'closed'/'transferred' (409), o con una serie
        TRD inexistente (404)—, y dejarlo fuera del `try` hacía que el caso
        común no dejara rastro mientras el docstring afirmaba lo contrario
        (RF-FIR-15). Las validaciones siguen FUERA de la `conn.transaction()`,
        que se abre después: son lecturas puras, no hay nada que revertir.
        """
        from app.services.index_service import IndexService

        exp = await self._expediente_or_404(expediente_id)
        index = IndexService(self._conn, self._tenant_slug)
        try:
            self._assert_transition(exp.status, "closed")
            disposition = None
            # ADR-025 (Increment 1) — congelación del amarre (D3/D4): mientras
            # el expediente está `open`, `exp.trd_serie_id` FLOTA y puede
            # apuntar a una versión ya derogada sin daño (ninguna consecuencia
            # jurídica se deriva de un expediente abierto). Al CERRAR se
            # re-resuelve por CÓDIGO la versión VIGENTE (convalidada/migrada)
            # EN ESTE INSTANTE — nunca la que el expediente traía arrastrada —
            # y esa es la que se congela: la ÚLTIMA escritura de `trd_serie_id`
            # en la vida del expediente (el trigger `trg_expedientes_trd_pin_
            # immutable` la rechaza después de este punto).
            pin_trd = False
            new_trd_serie_id: uuid.UUID | None = None
            new_trd_serie_code: str | None = None
            trd_revision_requerida = False
            trd_revision_motivo: str | None = None
            if exp.trd_serie_id is not None:
                pinned = await self._repo.get_trd_serie_by_id(exp.trd_serie_id)
                if pinned is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "error": "trd_serie_not_found",
                            "detail": "No existe la serie TRD indicada",
                            "status": 404,
                        },
                    )
                vigente = await self._repo.get_trd_serie_vigente_by_code(pinned.code)
                if vigente is None:
                    # D5 — gate de disposición: `borrador`/`aprobada`/`derogada`
                    # no gobiernan ningún cierre. Un expediente NUNCA queda
                    # gobernado por un instrumento que el sistema no puede
                    # afirmar como convalidado (RF-FIR-15).
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "trd_version_no_vigente",
                            "detail": (
                                f"La serie TRD '{pinned.code}' no tiene ninguna "
                                "versión convalidada ni migrada vigente: no puede "
                                "gobernar el cierre de un expediente bajo un "
                                "instrumento no convalidado (Ac. AGN 001/2024, que compila el 004/2019, "
                                "ADR-025)."
                            ),
                            "status": 409,
                        },
                    )
                # ADR-026 (Increment 2, D4) -- EL PUNTO PELIGROSO: la fecha
                # base del calendario depende de `exp.origen`, NUNCA de si el
                # llamador "se acuerda". `close_expediente` es el ÚNICO sitio
                # donde un expediente real elige entre las dos bases -- se
                # reutiliza este método íntegro (no una ruta de ingesta
                # paralela, ADR-026 D3) precisamente para que esta decisión
                # se tome una sola vez.
                if exp.origen == "fondo_acumulado":
                    if exp.fecha_extrema_final is None:
                        # No debería ocurrir -- `expedientes_fondo_acumulado_
                        # forma_check` (migración 041) lo exige NOT NULL en
                        # todo expediente `fondo_acumulado`. Defensivo, no
                        # teórico: mismo criterio que el 404/409 de arriba,
                        # nunca se asume la integridad de una fila ajena a
                        # este acto.
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "error": "fondo_acumulado_sin_fecha_extrema",
                                "detail": (
                                    "El expediente de fondo acumulado no tiene "
                                    "fecha_extrema_final: no hay base para calendarizar "
                                    "su retención (ADR-026 D4)."
                                ),
                                "status": 409,
                            },
                        )
                    base_date = exp.fecha_extrema_final
                    base_date_origen: Literal["closed_at", "fecha_extrema_final"] = (
                        "fecha_extrema_final"
                    )
                else:
                    base_date = datetime.now(tz=UTC).date()
                    base_date_origen = "closed_at"
                schedule = await self.compute_retention(
                    vigente.id, base_date, base_date_origen=base_date_origen
                )
                disposition = schedule.model_dump(mode="json")
                pin_trd = True
                new_trd_serie_id = vigente.id
                new_trd_serie_code = vigente.code
                # D6: `migrada` SÍ gobierna el cierre, pero DECLARÁNDOLO — marca
                # de revisión honesta (nunca bloqueante: el cierre no se detiene
                # a esperar la ratificación del Increment 2).
                if vigente.estado == "migrada":
                    trd_revision_requerida = True
                    trd_revision_motivo = "cerrado_bajo_serie_migrada"
            else:
                # RF-TRD-09 (2026-08-28) -- UN CIERRE SIN SERIE NO DEJABA NINGÚN
                # RASTRO. El expediente quedaba `closed` con `disposition = NULL`
                # y `trd_revision_requerida = false`: sin calendario de retención,
                # sin fecha de disposición y **sin aparecer en ninguna cola**. No
                # es un expediente conservado de más, es un expediente que sale
                # del ciclo de vida archivístico en silencio -- nadie volverá a
                # mirarlo, porque nada lo nombra.
                #
                # El motivo `cierre_sin_snapshot_de_retencion` EXISTE desde la
                # migración 037, que lo usó para marcar los cierres históricos...
                # y ningún camino de código lo escribió nunca después. El backfill
                # reparó el pasado y **nada protegía el futuro** -- el mismo hueco
                # que tenían las defensas de `audit_log` (cubrían modificar y
                # borrar, ninguna cubría añadir). Se comprobó sobre el tenant de
                # demostración: 1 expediente cerrado en ese estado, invisible.
                #
                # Se MARCA, no se bloquea: es el criterio que este mismo método ya
                # aplica a `migrada` (D6, «marca de revisión honesta, nunca
                # bloqueante»). Bloquear el cierre dejaría atrapados los
                # expedientes que ya están abiertos sin serie, y convertiría un
                # problema de clasificación en uno de disponibilidad. La cola que
                # lo recoge -- `GET /trd/revision-pendiente`, tercera pestaña de
                # `/admin/trd` -- YA distingue este motivo y ya dice que no tiene
                # resolución automática; solo que hasta hoy nunca recibía ninguno.
                trd_revision_requerida = True
                trd_revision_motivo = "cierre_sin_snapshot_de_retencion"

            async with self._conn.transaction():
                transitioned = await self._repo.transition_expediente(
                    expediente_id,
                    status="closed",
                    expected_status=exp.status,
                    disposition=disposition,
                    pin_trd=pin_trd,
                    new_trd_serie_id=new_trd_serie_id,
                    new_trd_serie_code=new_trd_serie_code,
                    trd_revision_requerida=trd_revision_requerida,
                    trd_revision_motivo=trd_revision_motivo,
                )
                # Fail-closed DENTRO de la tx: `transition_expediente` cierra el
                # `WHERE` con `AND status = $N` (H3) — un `None` aquí ya no es
                # teórico: significa que OTRA transición ganó la carrera entre
                # la lectura de `exp` (arriba) y este UPDATE (doble clic,
                # reintento del cliente, dos réplicas del gateway). Revierte en
                # vez de dejar un cierre a medias o reportar un resultado que
                # no es el que realmente se comprometió.
                if transitioned is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "expediente_transition_failed",
                            "detail": "La transición de cierre no devolvió el expediente actualizado.",
                            "status": 409,
                        },
                    )
                # H-E (E12, misma corrección aplicada a `TransferenciaService`):
                # antes `actor=` no se pasaba en absoluto -- la columna UUID de
                # `expediente_eventos` quedaba NULL y la única identidad vivía
                # en el texto libre `comentario="firmante=..."`. `actor_id` es
                # estrictamente X-User-Id (ver docstring) desde que el router
                # usa `require_actor` (fail-closed), pero este método sigue
                # siendo público y invocable directo (tests de integración,
                # jobs) con un `actor_id` que no sea un UUID -- parseo
                # defensivo (`None` si no castea) en vez de reventar con un
                # `ValueError` no capturado a mitad de la transacción.
                # H-E' — el `comentario="firmante=<uuid>"` se retira, por el
                # MISMO motivo por el que ya se retiró de `transfer_expediente`
                # y de `TransferenciaService.recibir`: es texto libre redundante
                # con la columna tipada `actor`, y el router pasa el mismo
                # `Actor.audit_actor` a los dos parámetros (ver docstring), así
                # que `firmante` y `actor_id` son el MISMO valor.
                #
                # Se vio en la hoja de ruta, que es donde importa: el asiento
                # «cerrado» mostraba `firmante=50b64803-568d-49f7-9299-…` con
                # «OPERADOR» —esa misma persona, ya resuelta— dos líneas más
                # abajo. El nombre estaba al lado del identificador.
                #
                # Tercer sitio de tres: la corrección se había aplicado a los
                # otros dos y aquí no, y el comentario de `transfer_expediente`
                # ya lo dejaba escrito.
                await self._repo.insert_expediente_evento(
                    expediente_id,
                    numero_expediente=exp.code,
                    accion="cerrado",
                    actor=_safe_uuid(actor_id),
                )
                # Índice electrónico: versión final del conjunto documental
                # (RF-EXP-06). Los dos guards de `regenerate` se levantan aquí, y
                # cada uno por su motivo:
                #
                #  - `assert_open=False`: el expediente YA transicionó arriba a
                #    'closed', así que su propio estado ya no pasaría el guard.
                #  - `indice_firmado_previo=True`: si el índice vigente se firmó
                #    con el expediente todavía abierto —un camino que la interfaz
                #    ofrece—, el guard de inmutabilidad lo rechazaba y la
                #    transacción revertía ÍNTEGRA. El expediente quedaba
                #    **imposible de cerrar**, y el único rastro era un asiento
                #    `archive.expediente_close_failed` con motivo
                #    `expediente_closed` sobre un expediente en estado 'open'.
                #    El comentario anterior decía que este parche cerraba ese
                #    hueco; describía la intención, no lo que el código hacía.
                #
                # Esta es la regeneración final legítima: append-only, la versión
                # firmada anterior se conserva, y si el conjunto documental no
                # cambió `regenerate` devuelve la vigente sin crear otra.
                rebuild_result = await index.regenerate(
                    expediente_id,
                    generado_por=firmante or "system",
                    assert_open=False,
                    indice_firmado_previo=True,
                )
                # `audit.append` LO ÚLTIMO dentro de la tx (además del que emite
                # `regenerate` internamente vía 'archive.indice_regenerated', que
                # ya toma el advisory lock del tenant: este re-toma el mismo lock,
                # ya poseído, sin espera adicional). El sellado XAdES-B todavía no
                # se ha intentado en este punto — el payload declara solo lo que
                # es cierto en el instante del commit: la versión final del
                # índice ya persistida, sin sellar, más el efecto jurídico del
                # cierre (Ley 594/2000 art. 24; Acuerdo AGN 001/2024, que compila el 004/2019) — la
                # disposición TRD materializada y el estado anterior (H4): antes
                # solo vivía en `expedientes.disposition`, una columna MUTABLE,
                # sin traza inmutable del acto.
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_closed",
                    object_type="expediente",
                    object_ref=exp.code,
                    actor=firmante,
                    payload={
                        "indice_version": rebuild_result.version,
                        "xml_sha256": rebuild_result.xml_sha256,
                        # Trazabilidad del salto v1->v2 (E15 Inc.3, RT-15): xsd_version
                        # real de la versión que `regenerate` acaba de devolver (NO la
                        # constante del módulo) — no afirma "v2" si `regenerate`
                        # resultó `unchanged` sobre una versión persistida distinta.
                        "xsd_version": rebuild_result.xsd_version,
                        "indice_estado": "no_firmado",
                        "status_anterior": exp.status,
                        "closed_at": transitioned.closed_at,
                        # ADR-025: el amarre CONGELADO (la version VIGENTE
                        # re-resuelta al cerrar), no el que el expediente traia
                        # arrastrado antes de este acto -- ver `new_trd_serie_id`
                        # más arriba. Puede diferir de `exp.trd_serie_id` si la
                        # serie tuvo versiones nuevas mientras el expediente
                        # estaba abierto.
                        "trd_serie_id_previo": str(exp.trd_serie_id) if exp.trd_serie_id else None,
                        "trd_serie_id": str(new_trd_serie_id) if new_trd_serie_id else None,
                        "trd_serie_code": new_trd_serie_code,
                        "trd_revision_requerida": trd_revision_requerida,
                        "disposition": disposition,
                    },
                    canal=audit.CANAL_PETICION,
                )
        except HTTPException as exc:
            # Best-effort (mismo patrón que `audit.append_denial`): nunca deja
            # que un fallo de auditoría oculte el 409/500 real, y nunca
            # convierte este catch en un 500 por su cuenta.
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_close_failed",
                    object_type="expediente",
                    object_ref=exp.code,
                    actor=firmante,
                    payload={
                        # Estado REAL tras el ROLLBACK: como el bloque completo se
                        # revirtió, el expediente sigue en `exp.status` (el que se
                        # observó al empezar el intento) — no es una suposición.
                        "status_actual": exp.status,
                        "motivo": motivo_de(exc),
                    },
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(expediente_close_failed) failed", exc_info=True
                )
            raise
        # --- fuera de la tx (ya comprometida), best-effort (RT-16): sellado
        # XAdES-B vía HTTP a signature-service. Un fallo aquí NUNCA bloquea
        # ni revierte el cierre, que ya es un hecho consumado — el desenlace
        # (firmado / pendiente_firma) se audita DENTRO de `firmar_indice_close`,
        # atómico con la marca de estado del índice (H5) — ya no aquí, dos
        # commits distintos para el mismo par efecto+asiento.
        # `firmar_indice_close` (residual Ac. AGN 001/2024 art. 4.3.2.4, no
        # `firmar_indice` a secas): control compensatorio con intento
        # síncrono ACOTADO + degradación diferenciada por motivo
        # (`seal_absent` no reintenta, `transient` reintenta hasta agotar
        # `settings.close_sign_max_sync_attempts`) — ver su docstring en
        # `IndexService`. `firmar_indice` (un único intento) sigue siendo el
        # usado por el reintento manual (`firmar_indice_retry` /
        # `POST /indice/firmar`).
        await index.firmar_indice_close(
            expediente_id, tenant_slug=self._tenant_slug, actor_id=actor_id
        )
        # No re-lee vía `get_expediente`: ese método resuelve el clearance del
        # llamante (`caller_clearance(user_id)`), y aquí no se propaga
        # `user_id` — hacerlo devolvería 404 espurio para un archivista con
        # permiso de disposición pero sin clearance de lectura sobre un
        # expediente RESERVADA/CLASIFICADA, pese a que el cierre YA se
        # comprometió. Se construye la respuesta estrecha a partir del
        # resultado de la propia transición (ortogonalidad decidida, ver
        # `ExpedienteDisposicionResult`), NO de una re-lectura.
        result = transitioned
        return ExpedienteDisposicionResult(
            id=result.id,
            code=result.code,
            status=result.status,
            disposition=result.disposition,
            closed_at=result.closed_at,
            transferred_at=None,
        )

    async def _assert_indice_firmado(self, expediente_id: uuid.UUID) -> None:
        """Exige índice electrónico 'firmado' antes de transferir.

        El índice firmado es el instrumento que da fe de integridad del
        expediente electrónico para la transferencia primaria/secundaria y su
        FUID (Acuerdo AGN 001/2024 art. 4.3.2.4). Un índice 'pendiente_firma'
        (sellado no obtenido al cierre, o cierre sin índice del todo) NO
        bloquea el cierre (RT-16, best-effort) pero SÍ bloquea la
        transferencia — es una condición distinta y posterior.
        """
        current = await self._index_repo.get_current(expediente_id)
        if current is None or current["estado"] != "firmado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "indice_no_firmado",
                    "detail": (
                        "El expediente no puede transferirse sin índice electrónico "
                        "firmado (Acuerdo AGN 001/2024 art. 4.3.2.4)"
                    ),
                    "status": 409,
                },
            )

    async def transfer_expediente(
        self, expediente_id: uuid.UUID, actor: str | None = None
    ) -> ExpedienteDisposicionResult:
        """Transfiere el expediente (closed → transferred).

        Bloqueante: exige índice electrónico 'firmado' — ver
        `_assert_indice_firmado` (Acuerdo AGN 001/2024 art. 4.3.2.4). No hay
        HTTP en ningún punto de este flujo (a diferencia de `close_expediente`),
        así que la transición, el evento de la hoja de ruta y el asiento de
        auditoría se comprometen atómicamente en una única `conn.transaction()`
        (mismo patrón que `close_expediente`/`link_radicado`): antes cada
        sentencia commiteaba por su cuenta y un fallo entre el UPDATE y el
        `audit.append` dejaba el expediente 'transferred' (estado que
        CONGELA el expediente — `_assert_open`) sin evento ni asiento,
        de forma irreversible y sin rastro.

        `_assert_indice_firmado` se evalúa FUERA de la tx, antes: el estado
        'firmado' del índice es absorbente (`mark_pendiente`/`regenerate`
        lo protegen con `WHERE estado <> 'firmado'` / guard 409), así que
        leerlo fuera solo puede volverse más permisivo entre la lectura y la
        transición — no hay TOCTOU explotable.

        `actor` (H2/H-D): identidad de quien transfiere — SIEMPRE
        `Actor.audit_actor` (X-User-Id, sin fallback a X-Username desde
        `app.core.actor.require_actor`; ver `close_expediente`). Antes el
        router ni siquiera declaraba estas cabeceras pese a que
        `require_permission(min_crud=3)` YA exige `X-User-Id` para autorizar
        la operación: la identidad estaba en la petición y se descartaba,
        dejando un cambio de custodia irreversible sin responsable
        identificado en la hoja de ruta ni en `audit_log`.
        """
        exp = await self._expediente_or_404(expediente_id)
        try:
            # Dentro del `try` (pero FUERA de la tx, que se abre después): el
            # rechazo de `_assert_indice_firmado` es justamente el control que
            # impone el Acuerdo AGN 001/2024 art. 4.3.2.4 — transferir sin
            # índice electrónico sellado — y es el evento que un auditor
            # necesita poder ver. Dejarlo fuera del `try` lo hacía invisible.
            self._assert_transition(exp.status, "transferred")
            await self._assert_indice_firmado(expediente_id)
            async with self._conn.transaction():
                transitioned = await self._repo.transition_expediente(
                    expediente_id, status="transferred", expected_status=exp.status
                )
                # Fail-closed DENTRO de la tx: `transition_expediente` cierra el
                # `WHERE` con `AND status = $N` (H3) — igual razonamiento que
                # `close_expediente`, ahora alcanzable bajo una carrera real (dos
                # transferencias concurrentes del mismo expediente ya 'closed').
                if transitioned is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "expediente_transition_failed",
                            "detail": (
                                "La transición de transferencia no devolvió el "
                                "expediente actualizado."
                            ),
                            "status": 409,
                        },
                    )
                # H-E (E12): antes `actor=` no se pasaba -- la columna UUID
                # quedaba NULL. Vocabulario (H-E', mismo criterio que
                # `TransferenciaService.recibir`): quien transfiere no "firma"
                # -- se retira `comentario=f"firmante={...}"` (texto libre
                # redundante con la columna tipada `actor`). `_safe_uuid`:
                # mismo parseo defensivo que `close_expediente` (ver su
                # comentario) -- este método sigue aceptando cualquier `str`.
                await self._repo.insert_expediente_evento(
                    expediente_id,
                    numero_expediente=exp.code,
                    accion="transferido",
                    actor=_safe_uuid(actor),
                )
                # `audit.append` LO ÚLTIMO dentro de la tx (advisory lock del
                # tenant retenido el mínimo tiempo posible hasta el COMMIT).
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_transferred",
                    object_type="expediente",
                    object_ref=exp.code,
                    actor=actor,
                    canal=audit.CANAL_PETICION,
                )
        except HTTPException as exc:
            # Gemelo de `expediente_close_failed`: la transferencia es un cambio
            # de custodia irreversible, y su DENEGACIÓN es tan auditable como su
            # consumación (Modelo de Requisitos SGDEA; Ley 594/2000 art. 22).
            # Best-effort: nunca degrada el fallo real a un 500 por auditoría.
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_transfer_failed",
                    object_type="expediente",
                    object_ref=exp.code,
                    actor=actor,
                    payload={
                        "status_actual": exp.status,
                        "motivo": motivo_de(exc),
                    },
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(expediente_transfer_failed) failed", exc_info=True
                )
            raise
        # Misma razón que `close_expediente`: no re-lee vía `get_expediente`
        # (evita el 404 espurio no-read-up) — respuesta estrecha construida a
        # partir de la transición ya comprometida.
        result = transitioned
        return ExpedienteDisposicionResult(
            id=result.id,
            code=result.code,
            status=result.status,
            disposition=result.disposition,
            closed_at=result.closed_at,
            transferred_at=None,
        )

    async def list_eventos(
        self, expediente_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> list[ExpedienteEventoResponse]:
        """Hoja de ruta del expediente (E02 §9 / Canal B): visible a quien pueda LEER el
        expediente, acotada por su clearance (no read-up, RF-SEG-08). No requiere admin.
        Un expediente por encima del clearance devuelve historial vacío."""
        max_clearance = await self._repo.caller_clearance(user_id)
        rows = await self._repo.list_expediente_eventos(expediente_id, max_clearance)
        return [_to_evento_response(r) for r in rows]

    async def get_indice(
        self, expediente_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> ExpedienteIndice:
        """Índice/foliado del expediente: radicados VIGENTES ordenados con folio
        secuencial. `exp.radicados` trae vinculados Y excluidos (soft-delete,
        E15 perfil v3) — un excluido no es documental vigente y no debe
        contar folio (filtra estado='vinculado', igual que la respuesta
        general del expediente en `_to_expediente_response`).

        No-read-up (RF-SEG-08): resuelve el clearance real del llamante (igual
        que `get_expediente`) — antes usaba `_expediente_or_404` con el
        default sin restricción, dejando el foliado como una ruta alterna
        para leer un expediente CLASIFICADA sin la clearance debida."""
        max_clearance = await self._repo.caller_clearance(user_id)
        exp = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        vigentes = [r for r in exp.radicados if r.estado == "vinculado"]
        items = [
            IndiceItem(
                folio=i,
                radicado_id=r.radicado_id,
                tracking_number=r.tracking_number,
                added_at=r.added_at,
            )
            for i, r in enumerate(vigentes, start=1)
        ]
        return ExpedienteIndice(
            expediente_id=exp.id, code=exp.code, total_folios=len(items), items=items
        )

    async def get_historial_acceso(
        self,
        expediente_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        *,
        page: int,
        size: int,
    ) -> tuple[list[HistorialAccesoEntry], int]:
        """Historial de acceso (lecturas) al expediente/índice (E15 perfil v4,
        R3-b) — consulta `public.audit_log` filtrada por `object_ref = code`.

        No-read-up (RF-SEG-08): mismo criterio 404-vs-403 que el resto de
        lecturas del expediente — un expediente por encima del clearance del
        llamante es 404 (no revela su existencia), ANTES de tocar audit_log."""
        max_clearance = await self._repo.caller_clearance(user_id)
        exp = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        offset = (page - 1) * size
        # Los números de radicado del expediente entran en la consulta para que
        # el historial incluya QUIÉN LEYÓ Y QUIÉN DESCARGÓ sus documentos, no
        # solo lo que se hizo sobre el expediente como contenedor. El asiento ya
        # existía en document-service; lo que faltaba era pedirlo.
        #
        # No abre ninguna fuga: esos mismos números ya se listan en la vista del
        # expediente para quien supera su gate, y de cada asiento solo se
        # devuelven actor, acción, instante y referencia — el `payload` (que
        # lleva el nivel de seguridad del radicado) NO se pinta.
        # `exp` ya viene con sus vínculos cargados: `get_expediente_by_id` los
        # resuelve. Pedirlos otra vez sería una consulta de más y, encima, por
        # un método privado del repositorio.
        trackings = [r.tracking_number for r in (exp.radicados or [])]
        total = await self._audit_read.count_for_expediente(
            self._tenant_slug, exp.code, trackings
        )
        rows = await self._audit_read.list_for_expediente(
            self._tenant_slug, exp.code, trackings, offset=offset, limit=size
        )
        return [_to_historial_entry(r) for r in rows], total

    async def get_roles_autorizados(
        self, expediente_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> list[RolAutorizado]:
        """Snapshot VIVO de qué grupos pueden leer este expediente AHORA (E15
        perfil v4, R4-b) — deliberadamente NO firmado (la matriz RBAC cambia
        con el tiempo). Mismo gate no-read-up que el resto de lecturas."""
        max_clearance = await self._repo.caller_clearance(user_id)
        exp = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        rows = await self._repo.list_roles_autorizados(exp.nivel_seguridad)
        return [
            RolAutorizado(grupo=r["grupo"], clearance_max=r["clearance_max"], crud=r["crud"])
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Radicado associations
    # ------------------------------------------------------------------

    async def _regenerate_index(self, expediente_id: uuid.UUID) -> None:
        """Regenera el índice electrónico tras un cambio del conjunto (E15, RF-EXP-04)."""
        from app.services.index_service import IndexService

        await IndexService(self._conn, self._tenant_slug).regenerate(
            expediente_id, generado_por="system"
        )

    def _assert_open(self, exp) -> None:
        if exp.status != "open":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "expediente_not_open",
                    "detail": f"Expediente en estado '{exp.status}': no admite cambios",
                    "status": 409,
                },
            )

    async def _huellas_por_radicado(
        self, radicado_ids: list[uuid.UUID]
    ) -> dict[str, str]:
        """La huella de cada radicado, resuelta EN EL SERVIDOR (E15 T-08).

        El índice electrónico debe llevar la huella de cada documento (RT-15
        art. 4.3.2.3) y el sistema siempre la ha tenido —storage-service la
        calcula al subir el anexo—; lo que faltaba era quien la trajera. La
        pantalla de vincular manda `radicado_id` y `tracking_number`, y el
        campo quedaba nulo para TODO lo vinculado desde la interfaz: el índice
        se autodeclaraba sin huellas teniendo los ficheros con SHA-256 al lado.

        **No se le pide al cliente**, por el mismo motivo que el departamento
        se deriva del municipio y no se acepta suelto: una huella que aporta
        quien vincula no prueba nada sobre lo que el archivo custodia. El campo
        `content_hash` del contrato sigue existiendo para el llamante que la
        conoce de primera mano (importación con fixity, E11 INT-05), y lo que
        llegue explícito MANDA sobre lo resuelto aquí.

        **Inventario sin recorte de clearance** (`max_clearance=None`): es un
        acto de sistema componiendo el índice, no una lectura de contenido por
        una persona. Recortarlo dejaría la completitud del índice dependiendo
        de quién vinculó — dos archivistas producirían índices distintos del
        mismo expediente, que es justo lo que un índice no puede permitirse. La
        huella no viaja en la respuesta al llamante; leer el índice se gatea
        aparte (`IndexService.verify`, no-read-up).

        **Best-effort**: cualquier fallo devuelve ``{}`` y la vinculación sigue
        sin huella. Vincular no puede quedar preso de que otros dos servicios
        estén en pie, y un documento sin huella el índice sabe declararlo.
        """
        if not radicado_ids:
            return {}
        from app.clients import storage_client

        try:
            anexos_por_rad = await document_client.fetch_anexos_internal(
                tenant_slug=self._tenant_slug,
                radicado_ids=radicado_ids,
                max_clearance=None,
            )
        except DocumentClientError:
            logger.warning(
                "No se pudo inventariar los anexos de %d radicados al vincular; "
                "quedan sin huella declarada en el índice",
                len(radicado_ids),
                exc_info=True,
            )
            return {}

        file_ids = [
            str(a["file_id"])
            for anexos in anexos_por_rad.values()
            for a in anexos
            if a.get("file_id")
        ]
        if not file_ids:
            return {}
        huellas = await storage_client.fetch_file_hashes_internal(
            tenant_slug=self._tenant_slug, file_ids=file_ids
        )
        if not huellas:
            return {}

        resultado: dict[str, str] = {}
        for rad_id, anexos in anexos_por_rad.items():
            propias = [
                huellas[str(a["file_id"])]
                for a in anexos
                if a.get("file_id") and str(a["file_id"]) in huellas
            ]
            huella = huella_de_documento(propias)
            if huella is not None:
                resultado[str(rad_id)] = huella
        return resultado

    @staticmethod
    def _productora(meta: dict | None) -> tuple[int | None, str | None]:
        """Código y nombre de la dependencia productora, o `(None, None)`.

        Los dos JUNTOS o NINGUNO: el índice electrónico solo emite
        `<dependenciaProductora>` cuando tiene ambos, y la BD lo exige con un
        CHECK de pareja (migración 019). Media pareja sería una fila que la
        base rechaza en el mejor caso y un elemento a medias en el peor."""
        if not meta:
            return None, None
        codigo = meta.get("origin_dept_code")
        nombre = meta.get("origin_dept")
        if codigo is None or not nombre:
            return None, None
        return int(codigo), str(nombre)

    async def _resolver_metadatos_vinculo(
        self,
        items: list[RadicadoLink],
        *,
        expediente_code: str,
        actor: uuid.UUID | None,
        clearance: int,
    ) -> dict[str, dict[str, Any]]:
        """Resuelve EN EL SERVIDOR lo que el índice electrónico afirma de cada
        radicado vinculado, y lo coteja con lo que trae el cuerpo (AREA-07-02 y
        AREA-06-08).

        Hasta aquí «lo explícito manda»: el número de radicado, la tipología, la
        dependencia productora, el formato, el tamaño y la huella que escribiera
        quien vincula se sellaban en un índice que luego se FIRMA con XAdES —y
        ni siquiera se comprobaba que el radicado existiera—. El cliente
        Keycloak del frontend es público (PKCE), así que el allowlist del BFF no
        protege el backend, y el asiento de auditoría registra quién lo hizo
        pero no impide el dato falso. Regla del sistema: *un dato probatorio lo
        resuelve el servidor*.

        Contrato:

        - **Fail-closed para existencia y número**: sin respuesta de
          document-service (o con una respuesta sin `tracking_number`, que es el
          document-service anterior a este cambio) → 503. No se puede sellar un
          número oficial que nadie pudo cotejar.
        - Radicado ausente en la respuesta (inexistente O por encima del
          clearance del llamante) → 404 `radicado_no_encontrado`.
        - Radicado sin `doc_class` → 422 `radicado_sin_tipificar`: la tipología
          es obligatoria en el índice firmado y no se admite declararla aquí.
        - Un valor del cuerpo que difiera del resuelto → 422
          `metadato_no_coincide` con asiento de denegación; el cuerpo del error
          nombra los CAMPOS, nunca el valor resuelto (no es un oráculo de
          números de radicado). Un valor declarado que el servidor no puede
          verificar (resuelto vacío) también es un desacuerdo: no hay ruta de
          «lo explícito manda» —la importación (E11 INT-05) no vincula por aquí—.
        - Lo que se persiste es SIEMPRE lo resuelto. `folios` es la excepción:
          es un conteo físico declarado que ningún servicio conoce.
        - La huella (`content_hash`) es best-effort como siempre (storage puede
          estar caído): sin huella resuelta se persiste vacía, pero una huella
          DECLARADA se rechaza si no se pudo verificar.

        **No-read-up (RF-SEG-08, hallazgo medio de T6)**: la resolución se pide
        con el clearance del LLAMANTE. Antes iba sin recorte y el 422
        `metadato_no_coincide` listaba los campos erróneos de un radicado
        reservado: con un UUID, un usuario de clearance 1 enumeraba la
        tipología, el MIME, la dependencia productora y confirmaba el número y
        la huella —el canal que T6 abrió al cotejar—. Un radicado por encima
        del clearance responde igual que uno inexistente (404 neutro, el mismo
        criterio de document-service): ni existencia ni cotejo. El contenido del
        índice no cambia por quién vincula: lo que se vincula se resuelve igual
        y solo se acota QUIÉN puede vincularlo.
        """
        ids = list(dict.fromkeys(it.radicado_id for it in items))
        try:
            meta = await document_client.fetch_radicado_metadata_internal(
                tenant_slug=self._tenant_slug,
                radicado_ids=ids,
                max_clearance=clearance,
            )
        except DocumentClientError:
            logger.warning(
                "No se pudo cotejar %d radicados con document-service al vincular",
                len(ids),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "document_service_no_disponible",
                    "detail": (
                        "No se pudo cotejar el radicado con el servicio de "
                        "documentos: no se vincula lo que no se pudo verificar. "
                        "Reintente en unos instantes."
                    ),
                    "status": 503,
                },
            )
        huellas = await self._huellas_por_radicado(ids)

        resueltos: dict[str, dict[str, Any]] = {}
        desacuerdos: dict[str, list[str]] = {}
        for it in items:
            clave = str(it.radicado_id)
            m = meta.get(clave)
            if m is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "radicado_no_encontrado",
                        "detail": "No existe un radicado visible con ese identificador",
                        "status": 404,
                        "radicado_id": clave,
                    },
                )
            tracking = m.get("tracking_number")
            if not tracking:
                # Respuesta sin número: un document-service que aún no lo
                # devuelve. Se trata como no disponible, no como «sin número».
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "document_service_no_disponible",
                        "detail": (
                            "El servicio de documentos no devolvió el número del "
                            "radicado: no se puede cotejar."
                        ),
                        "status": 503,
                    },
                )
            if not m.get("doc_class"):
                # `<tipologia>` es obligatorio en el XSD del índice (Ac. AGN
                # 001/2024 4.3.2.3): sellar el índice con la tipología vacía
                # —y sin forma de corregirla luego— deja un hueco permanente.
                # Se tipifica primero en document-service. El radicado ya es
                # visible para el llamante, así que nombrarlo no filtra nada.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "error": "radicado_sin_tipificar",
                        "detail": (
                            "El radicado no tiene tipología documental: "
                            "tipifíquelo antes de vincularlo a un expediente."
                        ),
                        "status": 422,
                        "radicado_id": clave,
                    },
                )
            dep_codigo, dep_nombre = self._productora(m)
            huella = huellas.get(clave)
            r = {
                "tracking_number": str(tracking),
                "tipologia": str(m["doc_class"]),
                "dep_codigo": dep_codigo,
                "dep_nombre": dep_nombre,
                "formato": m.get("formato"),
                "tamano_bytes": m.get("tamano_bytes"),
                "content_hash": huella,
            }
            campos: list[str] = []
            if it.tracking_number != r["tracking_number"]:
                campos.append("tracking_number")
            if it.tipologia is not None and it.tipologia != r["tipologia"]:
                campos.append("tipologia")
            if it.dependencia_productora_codigo is not None and (
                it.dependencia_productora_codigo != dep_codigo
                or it.dependencia_productora_nombre != dep_nombre
            ):
                campos.append("dependencia_productora")
            if it.formato is not None and it.formato != r["formato"]:
                campos.append("formato")
            if it.tamano_bytes is not None and it.tamano_bytes != r["tamano_bytes"]:
                campos.append("tamano_bytes")
            if it.content_hash is not None and it.content_hash.lower() != huella:
                campos.append("content_hash")
            if campos:
                desacuerdos[clave] = campos
            resueltos[clave] = r

        if desacuerdos:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.radicado_vinculo_denegado",
                    object_type="expediente",
                    object_ref=expediente_code,
                    actor=str(actor) if actor else None,
                    payload={"motivo": "metadato_no_coincide", "desacuerdos": desacuerdos},
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — la denegación no depende del asiento.
                logger.warning("audit.append(radicado_vinculo_denegado) failed", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "metadato_no_coincide",
                    "detail": (
                        "Lo declarado no coincide con lo que el sistema tiene del "
                        "radicado. Esos datos los resuelve el servidor: omítalos."
                    ),
                    "status": 422,
                    "desacuerdos": desacuerdos,
                },
            )
        return resueltos

    async def completar_origen_legado(self, limit: int) -> dict[str, int]:
        """Completa la productora/formato/tamaño de vínculos LEGADOS de expedientes ABIERTOS
        con lo que document-service sabe HOY (AREA-07-01).

        Antes del perfil v4 esos tres datos los aportaba (o no) quien vinculaba; los vínculos
        viejos quedaron en NULL y el índice los declara con `productoraPresente="false"`. Se
        completan SOLO las columnas NULL —nunca se pisa un valor ya declarado— y SOLO en
        expedientes abiertos: los cerrados son probatorios (su índice está firmado y WORM) y
        no se tocan. Cada vínculo se intenta UNA vez (`origen_completado_at`): un NULL puede
        ser legítimo (sin anexos no hay formato) y sin la marca se reelegiría para siempre.

        Es un acto de sistema: la resolución va con `CLEARANCE_SIN_RESTRICCION` (completa un
        dato del propio expediente, no lo expone a nadie) y cada vínculo que cambia deja su
        asiento `archive.vinculo_origen_completado` en la misma transacción. Si
        document-service no responde NO se marca nada (la siguiente barrida reintenta):
        marcar «intentado» sin haber podido preguntar sería perder el vínculo.

        El índice no se regenera aquí: lo recoge la próxima regeneración (vincular,
        desvincular o cerrar), con el XSD ya validado antes de sellar."""
        pendientes = await self._repo.list_vinculos_origen_pendiente(limit)
        resumen = {"vistos": 0, "completados": 0, "sin_cambios": 0}
        if not pendientes:
            return resumen
        ids = list(dict.fromkeys(r["radicado_id"] for r in pendientes))
        try:
            meta = await document_client.fetch_radicado_metadata_internal(
                tenant_slug=self._tenant_slug,
                radicado_ids=ids,
                max_clearance=CLEARANCE_SIN_RESTRICCION,
            )
        except DocumentClientError:
            logger.warning(
                "Backfill de origen: document-service no respondió (%d radicados); "
                "se reintenta en la próxima barrida",
                len(ids),
                exc_info=True,
            )
            return resumen
        for r in pendientes:
            resumen["vistos"] += 1
            m = meta.get(str(r["radicado_id"]))
            dep_codigo, dep_nombre = self._productora(m)
            tamano = (m or {}).get("tamano_bytes")
            async with self._conn.transaction():
                fila = await self._repo.completar_origen_vinculo(
                    r["expediente_id"],
                    r["radicado_id"],
                    dep_codigo=dep_codigo,
                    dep_nombre=dep_nombre,
                    formato=(m or {}).get("formato"),
                    tamano_bytes=int(tamano) if tamano is not None else None,
                )
                if fila is None:
                    continue  # ya no era elegible (cerrado/excluido/otra barrida)
                campos = [
                    nombre
                    for nombre, hecho in (
                        ("dependencia_productora", fila["productora_completada"]),
                        ("formato", fila["formato_completado"]),
                        ("tamano_bytes", fila["tamano_completado"]),
                    )
                    if hecho
                ]
                if not campos:
                    resumen["sin_cambios"] += 1
                    continue
                resumen["completados"] += 1
                valores: dict[str, Any] = {}
                if fila["productora_completada"]:
                    valores["dep_codigo"] = dep_codigo
                    valores["dep_nombre"] = dep_nombre
                if fila["formato_completado"]:
                    valores["formato"] = (m or {}).get("formato")
                if fila["tamano_completado"]:
                    valores["tamano_bytes"] = int(tamano) if tamano is not None else None
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.vinculo_origen_completado",
                    object_type="expediente",
                    object_ref=r["expediente_code"],
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "radicado_id": str(r["radicado_id"]),
                        "campos": campos,
                        # Los valores escritos y su fuente: sin ellos el asiento no permite
                        # reconstruir el dato que luego entra al índice firmado.
                        "valores": valores,
                        "fuente": "document-service",
                        "resuelto_at": datetime.now(tz=UTC).isoformat(),
                    },
                )
        return resumen

    async def link_radicado(
        self,
        expediente_id: uuid.UUID,
        payload: RadicadoLink,
        user_id: uuid.UUID | None = None,
    ) -> ExpedienteRadicadoResponse:
        """Vincula un radicado al expediente.

        M2 (RF-SEG-08, resuelto): la mutación es LEGÍTIMA sin clearance de
        lectura — componer un expediente (ordenar/vincular) es gestión
        archivística, ortogonal a leer su contenido (Ac. AGN 001/2024 arts.
        4.3.1.7/4.3.2.3; misma lógica que M1 en close/transfer). Por eso la
        existencia/estado del expediente se resuelve SIEMPRE con
        `CLEARANCE_SIN_RESTRICCION` y la vinculación SIEMPRE se ejecuta. Lo
        que se acota es la RESPUESTA cuando el llamante no puede LEER el
        expediente (`nivel_seguridad > su clearance`): se suprime el 409
        `radicado_already_linked` (no revela pertenencia previa) y se omite
        `added_at` real (idempotente-silencioso), para no dejar un oráculo de
        lectura disfrazado de escritura.

        M2 hallazgo #1 (oráculo residual de ciclo de vida, auditoría M2):
        `_assert_open` (409 `expediente_not_open`, con el estado LITERAL en
        el cuerpo) solo se evalúa para quien SÍ puede leer — `puede_leer` se
        resuelve ANTES, y a un no-lector el estado closed/transferred del
        expediente NUNCA le llega como 409: cae en la misma rama sin mutar
        de más abajo (`existing.status != "open"`), indistinguible del resto
        de acks acotados.
        """
        existing = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )

        clearance = await self._repo.caller_clearance(user_id)
        puede_leer = existing.nivel_seguridad <= clearance

        if puede_leer:
            self._assert_open(existing)

        duplicate = await self._repo.radicado_link_exists(expediente_id, payload.radicado_id)
        if duplicate:
            if puede_leer:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "radicado_already_linked",
                        "detail": (
                            "El radicado ya está vinculado a este expediente"
                        ),
                        "status": 409,
                    },
                )
            # Idempotente-silencioso (M2): no revela que ya estaba vinculado
            # ni el `added_at` real de esa vinculación previa a quien no
            # puede leer el expediente.
            return ExpedienteRadicadoResponse(
                radicado_id=payload.radicado_id,
                tracking_number=payload.tracking_number,
                added_at=None,
            )

        if existing.status != "open":
            # Solo alcanzable con `puede_leer=False`: si pudiera leer,
            # `_assert_open` ya habría lanzado el 409 informativo arriba.
            # Un no-lector no tiene nada que mutar (closed/transferred) y
            # recibe el MISMO ack acotado que el resto de ramas -- el cuerpo
            # no distingue "closed", "transferred" ni "ya vinculado" (M2
            # hallazgo #1).
            return ExpedienteRadicadoResponse(
                radicado_id=payload.radicado_id,
                tracking_number=payload.tracking_number,
                added_at=None,
            )

        # Los metadatos del vínculo (número de radicado, tipología, dependencia
        # productora, formato, tamaño y huella) los RESUELVE EL SERVIDOR y se
        # cotejan con lo que trae el cuerpo (AREA-07-02 / AREA-06-08): quien
        # vincula no escribe lo que el índice —que se firma con XAdES— afirma.
        # Se resuelve ANTES de abrir la transacción: el tramo transaccional es
        # 100% BD a propósito (ver el comentario de abajo), y una llamada HTTP a
        # mitad mantendría abierto el `pg_advisory_xact_lock` del tenant durante
        # el viaje de red.
        resueltos = await self._resolver_metadatos_vinculo(
            [payload], expediente_code=existing.code, actor=user_id, clearance=clearance
        )
        r = resueltos[str(payload.radicado_id)]
        tracking_number = r["tracking_number"]
        content_hash = r["content_hash"]
        dep_codigo, dep_nombre = r["dep_codigo"], r["dep_nombre"]
        formato = r["formato"]
        tamano_bytes = r["tamano_bytes"]
        tipologia = r["tipologia"]

        # Atomicidad vínculo<->auditoría (mismo patrón que `create_expediente`):
        # la mutación, la regeneración del índice, el evento de la hoja de
        # ruta y el asiento forense son un único acto de negocio — si algo
        # falla a mitad, ninguno debe persistir (antes: autocommit por
        # statement, un fallo entre el link y el `audit.append` dejaba el
        # vínculo hecho SIN su asiento de auditoría). Tramo 100% DB (sin
        # HTTP/Redis a mitad); `audit.append` va último para minimizar la
        # retención del `pg_advisory_xact_lock` del tenant que abre.
        async with self._conn.transaction():
            link = await self._repo.link_radicado(
                expediente_id,
                payload.radicado_id,
                tracking_number,
                content_hash=content_hash,
                formato=formato,
                tamano_bytes=tamano_bytes,
                folios=payload.folios,
                dependencia_productora_codigo=dep_codigo,
                dependencia_productora_nombre=dep_nombre,
                tipologia=tipologia,
            )
            await self._regenerate_index(expediente_id)
            await self._repo.insert_expediente_evento(
                expediente_id,
                numero_expediente=existing.code,
                accion="radicado_vinculado",
                detalles={
                    "radicado_id": str(payload.radicado_id),
                    "tracking_number": tracking_number,
                },
            )
            # Auditoría forense inmutable (E08 §9, Canal A): faltaba — la
            # vinculación de un radicado es una mutación del conjunto documental
            # del expediente, igual de auditable que `radicado_excluido`. SIEMPRE
            # se escribe, incluso cuando la respuesta se acota abajo (la
            # redacción es de la respuesta al llamante, no de la traza forense).
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.radicado_linked",
                object_type="expediente",
                object_ref=existing.code,
                actor=str(user_id) if user_id else None,
                payload={
                    "radicado_id": str(payload.radicado_id),
                    "tracking_number": tracking_number,
                },
                canal=audit.CANAL_PETICION,
            )
        response = _to_radicado_response(link)
        if not puede_leer:
            # Respuesta acotada (M2): no filtra el `added_at` real a quien no
            # puede leer el expediente. `radicado_id`/`tracking_number` son
            # eco del input que el propio llamante ya envió, no se redactan.
            return ExpedienteRadicadoResponse(
                radicado_id=response.radicado_id,
                tracking_number=response.tracking_number,
                added_at=None,
            )
        return response

    async def link_radicados_batch(
        self,
        expediente_id: uuid.UUID,
        items: list[RadicadoLink],
        user_id: uuid.UUID | None = None,
    ) -> BatchLinkResult:
        """Vincula múltiples radicados de una vez (idempotente) y regenera el índice
        una sola vez al final. Omite los ya vinculados (paridad legado: inclusión masiva).

        M2 (RF-SEG-08, resuelto) — mismo criterio que `link_radicado`: la
        mutación es LEGÍTIMA sin clearance de lectura y SIEMPRE se ejecuta;
        la RESPUESTA se acota (`vinculados`/`omitidos` -> None) cuando el
        llamante no puede LEER el expediente, para no filtrar el volumen del
        conjunto documental. `total` se conserva (eco del input).

        M2 hallazgo #1: para un no-lector, `_assert_open` (409 con el estado
        LITERAL) nunca se evalúa -- si el expediente no está open, se
        devuelve el mismo ack acotado SIN mutar, indistinguible del caso
        open acotado (ver rama `elif` más abajo).
        """
        existing = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
                },
            )

        clearance = await self._repo.caller_clearance(user_id)
        puede_leer = existing.nivel_seguridad <= clearance

        if puede_leer:
            self._assert_open(existing)
        elif existing.status != "open":
            # No-lector, expediente no-open: sin mutar (M2 hallazgo #1).
            return BatchLinkResult(vinculados=None, omitidos=None, total=len(items))

        # Un solo viaje para todo el lote, y fuera de la transacción (mismo
        # criterio que `link_radicado`): todo se resuelve en el servidor y se
        # coteja con lo declarado (AREA-07-02 / AREA-06-08). Un solo ítem que no
        # case aborta el lote entero ANTES de escribir nada.
        resueltos = await self._resolver_metadatos_vinculo(
            items, expediente_code=existing.code, actor=user_id, clearance=clearance
        )

        # Atomicidad lote<->auditoría (mismo patrón que `link_radicado`): UN
        # expediente, N vínculos es una unidad todo-o-nada — si un vínculo del
        # lote falla a mitad, revierte TODO el lote (nada de vínculos
        # parciales ni índice regenerado a medias sin su asiento forense). Si
        # el bucle no vincula nada (todos omitidos), la tx cierra sin writes
        # (inocuo). `audit.append` último write dentro del `if vinculados`.
        vinculados = omitidos = 0
        linked_ids: list[str] = []
        async with self._conn.transaction():
            for it in items:
                if await self._repo.radicado_link_exists(expediente_id, it.radicado_id):
                    omitidos += 1
                    continue
                r = resueltos[str(it.radicado_id)]
                await self._repo.link_radicado(
                    expediente_id,
                    it.radicado_id,
                    r["tracking_number"],
                    content_hash=r["content_hash"],
                    formato=r["formato"],
                    tamano_bytes=r["tamano_bytes"],
                    folios=it.folios,
                    dependencia_productora_codigo=r["dep_codigo"],
                    dependencia_productora_nombre=r["dep_nombre"],
                    tipologia=r["tipologia"],
                )
                vinculados += 1
                linked_ids.append(str(it.radicado_id))
            if vinculados:
                await self._regenerate_index(expediente_id)
                # Auditoría forense inmutable (E08 §9, Canal A): faltaba — la
                # inclusión masiva es la MISMA mutación de conjunto documental que
                # `link_radicado`, repetida N veces; queda SIEMPRE, incluso cuando
                # la respuesta se acota abajo.
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.radicado_batch_linked",
                    object_type="expediente",
                    object_ref=existing.code,
                    actor=str(user_id) if user_id else None,
                    payload={
                        "vinculados": vinculados,
                        "omitidos": omitidos,
                        "radicado_ids": linked_ids,
                    },
                    canal=audit.CANAL_PETICION,
                )
        if not puede_leer:
            return BatchLinkResult(vinculados=None, omitidos=None, total=len(items))
        return BatchLinkResult(vinculados=vinculados, omitidos=omitidos, total=len(items))

    async def unlink_radicado(
        self,
        expediente_id: uuid.UUID,
        radicado_id: uuid.UUID,
        causal: str | None = None,
        actor: uuid.UUID | None = None,
    ) -> None:
        """Excluye el radicado del expediente por SOFT-DELETE (E15 perfil v3,
        RT-15 art. 4.3.2.3): la fila permanece (estado='excluido') para que el
        índice electrónico muestre el hueco trazable en el orden documental —
        ya no hay DELETE físico. `causal` es opcional (motivo de la exclusión).

        `actor` es el X-User-Id de quien excluye: la exclusión es una
        intervención documental sobre el orden original (análoga a un acta de
        retiro) — la trazabilidad completa (Ley 594/2000; Ac. AGN 001/2024
        art. 4.3.2.3) exige identificar al agente responsable, no solo cuándo
        y por qué. Queda tanto en la hoja de ruta del expediente
        (`expediente_eventos`) como en el `audit_log` inmutable (Canal A). Es
        también el `user_id` con el que se resuelve el clearance del llamante
        para la respuesta acotada (M2, ver abajo).

        M2 (RF-SEG-08, resuelto): la exclusión es LEGÍTIMA sin clearance de
        lectura (mismo criterio que `link_radicado`/`link_radicados_batch`) y
        SIEMPRE se ejecuta. Cuando el llamante no puede LEER el expediente
        (`nivel_seguridad > su clearance`), se SUPRIME el 404
        `radicado_link_not_found`: responde 204 exista o no el vínculo, para
        no revelar pertenencia a quien no tiene clearance de lectura.

        M2 hallazgo #1: por el mismo motivo, `_assert_open` (409 con el
        estado LITERAL) solo se evalúa para quien SÍ puede leer -- un
        no-lector con un expediente no-open recibe el mismo 204 sin mutar,
        indistinguible del resto de casos acotados.
        """
        existing = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )

        clearance = await self._repo.caller_clearance(actor)
        puede_leer = existing.nivel_seguridad <= clearance

        if puede_leer:
            self._assert_open(existing)
        elif existing.status != "open":
            # No-lector, expediente no-open: 204 sin mutar (M2 hallazgo #1).
            return

        # Atomicidad exclusión<->auditoría (mismo patrón que `link_radicado`):
        # la mutación, la regeneración del índice, el evento de la hoja de
        # ruta y el asiento forense son un único acto de negocio. Si
        # `unlink_radicado` no afecta fila, no hay nada que revertir: el
        # `raise`/`return` dentro del bloque cierran la tx sin writes
        # (rollback vacío o commit vacío, ambos inocuos).
        async with self._conn.transaction():
            excluded = await self._repo.unlink_radicado(expediente_id, radicado_id, causal=causal)
            if not excluded:
                if puede_leer:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "error": "radicado_link_not_found",
                            "detail": (
                                "El radicado no está vinculado a este expediente"
                            ),
                            "status": 404,
                        },
                    )
                # Idempotente-silencioso (M2): nada que mutar/auditar — no revela
                # si el vínculo existía a quien no puede leer el expediente.
                return
            await self._regenerate_index(expediente_id)
            await self._repo.insert_expediente_evento(
                expediente_id,
                numero_expediente=existing.code,
                accion="radicado_excluido",
                actor=actor,
                detalles={"radicado_id": str(radicado_id), "causal": causal},
            )
            # Auditoría forense inmutable (E08 §9, Canal A): la exclusión es una
            # acción de disposición reforzada (crud>=3, ver el decorador del
            # router) — antes solo dejaba rastro en `expediente_eventos` (schema
            # del tenant) y el asiento genérico `archive.indice_regenerated`;
            # ahora tiene su propio asiento con actor y causal en `audit_log`.
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.radicado_excluido",
                object_type="expediente",
                object_ref=existing.code,
                actor=str(actor) if actor else None,
                payload={"radicado_id": str(radicado_id), "causal": causal},
                canal=audit.CANAL_PETICION,
            )

    # ------------------------------------------------------------------
    # TRD series
    # ------------------------------------------------------------------

    async def create_trd_serie(
        self, payload: TrdSerieCreate, actor: uuid.UUID | None = None
    ) -> TrdSerieResponse:
        # Atomicidad mutación<->asiento (mismo patrón que `update_expediente`/
        # `close_expediente`): el INSERT y `audit.append` son un único acto
        # dentro de una sola `conn.transaction()` — si el asiento falla, el
        # alta de la serie no queda persistida (fail-closed, ver [[auditoria-actos-custodia]]).
        async with self._conn.transaction():
            serie = await self._repo.create_trd_serie(
                code=payload.code,
                name=payload.name,
                description=payload.description,
                retention_years=payload.retention_years,
                total_retention=payload.total_retention,
                disposition=payload.disposition,
                parent_id=payload.parent_id,
                archivo_gestion_years=payload.archivo_gestion_years,
                archivo_central_years=payload.archivo_central_years,
                pdfa_profile=payload.pdfa_profile,
            )
            # Auditoría forense transversal (E08 §9, Canal A): la TRD gobierna
            # retención y disposición final (Ley 594/2000 art. 24) — su alta
            # debe quedar trazada igual que la de un expediente. Estado
            # INICIAL completo (no solo `id`): sin él la cadena de
            # reconstrucción before/after de `update_trd_serie` no tiene punto
            # de partida.
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.trd_serie_created",
                object_type="trd_serie",
                object_ref=serie.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(serie.id),
                    "code": serie.code,
                    "name": serie.name,
                    "description": serie.description,
                    "retention_years": serie.retention_years,
                    "total_retention": serie.total_retention,
                    "disposition": serie.disposition,
                    "parent_id": str(serie.parent_id) if serie.parent_id else None,
                    "archivo_gestion_years": serie.archivo_gestion_years,
                    "archivo_central_years": serie.archivo_central_years,
                    "pdfa_profile": serie.pdfa_profile,
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(serie)

    async def compute_retention(
        self,
        serie_id: uuid.UUID,
        base_date: date,
        *,
        base_date_origen: Literal["closed_at", "fecha_extrema_final"],
    ) -> RetentionSchedule:
        """Calendario de retención/disposición, derivado de la serie TRD/TVD
        (E04/ADR-015; ADR-026 D4). Función pura sobre la fila de la serie.

        ADR-026 (Increment 2, D4) -- EL PUNTO PELIGROSO: `base_date` (antes
        `closed_at`) es la fecha desde la que se cuenta el calendario, y
        alimenta `retain_until` en Object-Lock COMPLIANCE (irreversible)
        aguas abajo. Para una TRD la base correcta es la fecha de CIERRE del
        expediente; para una TVD (fondo acumulado) es la `fecha_extrema_final`
        del expediente y NUNCA `closed_at` -- un fondo de 1985 registrado hoy
        no puede calendarizarse desde 2026, o su retención quedaría
        imborrable dos décadas de más.

        `base_date_origen` es un keyword-only SIN DEFAULT, a propósito: hace
        imposible omitir la procedencia. Ningún llamador de este método
        puede "olvidarse" de declarar de dónde sale `base_date` -- Python no
        deja invocarlo sin decidir explícitamente uno de los dos valores del
        `Literal`, y ese valor viaja después en el snapshot congelado
        (`RetentionSchedule.base_date_origen`) como prueba oponible, igual
        que `serie_estado`. La otra mitad de la defensa vive en los
        *callers* (`close_expediente`), que son los que realmente pueden
        confundir una fecha con otra -- este método no puede adivinarlo por
        su cuenta porque no sabe si `base_date` vino de `exp.closed_at` o de
        `exp.fecha_extrema_final`, solo lo que se le declara.

        Defensa adicional, en el propio cálculo: cuando
        `base_date_origen == 'fecha_extrema_final'`, la fase de archivo de
        GESTIÓN se fuerza a 0 pase lo que pase en `serie.archivo_gestion_years`
        (ADR-026 D4.2 -- "la fase de archivo de gestión es 0 para TVD"). El
        CHECK `trd_series_tvd_forma_check` (migración 039) ya lo garantiza en
        el motor para toda fila TVD real, pero forzarlo aquí también evita que
        una fila TRD invocada por error con `base_date_origen='fecha_extrema_final'`
        (p. ej. desde `GET /tvd/{id}/retention` sobre un id que no fuera TVD)
        arrastre una fase de gestión que no le corresponde a ese cómputo.

        Calendario ya vencido (`fin_archivo_central < hoy`) es el caso
        NORMAL en fondo acumulado, no un error (D4.4): se devuelve tal cual,
        sin redondear hacia adelante. El clamp de `retain_until`
        (no fijar retención WORM si `retain_until <= now()`) es del
        Increment 3 (ruta AIP/WORM), NO de este método -- aquí solo se
        declara la fecha, no se cablea Object-Lock."""
        serie = await self._repo.get_trd_serie_by_id(serie_id)
        if serie is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "trd_serie_not_found",
                    "detail": "No existe la serie TRD indicada",
                    "status": 404,
                },
            )
        agest = 0 if base_date_origen == "fecha_extrema_final" else (serie.archivo_gestion_years or 0)
        acentral = serie.archivo_central_years or 0
        fin_gestion = _add_years(base_date, agest)
        fin_central = _add_years(fin_gestion, acentral)
        code, label = _agn_disposition(serie.disposition)
        return RetentionSchedule(
            serie_id=serie.id,
            code=serie.code,
            base_date=base_date,
            archivo_gestion_years=agest,
            archivo_central_years=acentral,
            fin_archivo_gestion=fin_gestion,
            fin_archivo_central=fin_central,
            disposition_code=code,
            disposition_label=label,
            # ADR-025 (Increment 1): procedencia del instrumento -- se
            # DECLARA tal cual está en la fila (nunca se afirma una
            # convalidación que no ocurrió, RF-FIR-15; ver D6).
            serie_version=serie.version,
            serie_estado=serie.estado,
            acto_administrativo=serie.acto_administrativo,
            fecha_convalidacion=serie.fecha_convalidacion,
            instancia_convalidante=serie.instancia_convalidante,
            rusd_radicado=serie.rusd_radicado,
            snapshot_at=datetime.now(tz=UTC),
            # ADR-026 (Increment 2, D4/D5): declaración explícita de la base
            # y del fundamento de una eventual eliminación de fondo
            # acumulado (D5) -- viaja SIEMPRE, para ambos instrumentos.
            base_date_origen=base_date_origen,
            tipo_instrumento=serie.tipo_instrumento,
            fondo_nombre=serie.fondo_nombre,
            justificacion_valoracion=serie.justificacion_valoracion,
        )

    async def list_trd_series(
        self,
        *,
        is_active: bool | None,
        page: int,
        size: int,
    ) -> tuple[list[TrdSerieResponse], int]:
        offset = (page - 1) * size
        series, total = await self._repo.list_trd_series(
            is_active=is_active, offset=offset, limit=size
        )
        return [_to_trd_response(s) for s in series], total

    async def get_trd_serie(self, serie_id: uuid.UUID) -> TrdSerieResponse:
        serie = await self._repo.get_trd_serie_by_id(serie_id)
        if serie is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "trd_serie_not_found",
                    "detail": "No existe la serie TRD indicada",
                    "status": 404,
                },
            )
        return _to_trd_response(serie)

    async def get_trd_serie_versiones(self, code: str) -> list[TrdSerieResponse]:
        """`GET /api/v1/trd/{code}/versiones` (ADR-026): gemelo que ADR-025
        declaró aditivo y nunca implementó -- la pantalla de TVD lo necesita
        desde el día uno y dejar la TRD sin él consolidaba la asimetría.
        Historia completa (cualquier estado), más reciente primero; lista
        vacía si el código nunca existió (no es un 404: "sin versiones" es
        una respuesta válida, no un error)."""
        series = await self._repo.list_trd_serie_versiones_by_code(code)
        return [_to_trd_response(s) for s in series]

    # Campos SUSTANTIVOS del contenido convalidado (ADR-025 D1): los mismos
    # que el trigger `trg_trd_series_append_only` rechaza fuera de `borrador`.
    # `pdfa_profile` NO está aquí a propósito -- es la única excepción
    # editable en sitio en cualquier estado.
    _TRD_SUBSTANTIVE_FIELDS = (
        "name",
        "description",
        "retention_years",
        "total_retention",
        "archivo_gestion_years",
        "archivo_central_years",
        "disposition",
    )

    async def update_trd_serie(
        self,
        serie_id: uuid.UUID,
        payload: TrdSerieUpdate,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`PATCH /trd/{serie_id}` (ADR-025, Increment 1): edición EN SITIO,
        solo legítima sobre `borrador` -- es el `UPDATE` retroactivo que
        causaba el hallazgo Crítico (ver ADR-025 §"Por qué es Crítico").
        Cualquier otro estado responde `409 trd_version_inmutable`, salvo que
        el ÚNICO campo sustantivo enviado sea `pdfa_profile` (D1, política de
        preservación técnica, no contenido convalidado)."""
        existing = await self._repo.get_trd_serie_by_id(serie_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "trd_serie_not_found",
                    "detail": "No existe la serie TRD indicada",
                    "status": 404,
                },
            )
        has_substantive_change = any(
            getattr(payload, field) is not None for field in self._TRD_SUBSTANTIVE_FIELDS
        )
        if existing.estado != "borrador" and has_substantive_change:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_version_inmutable",
                    "detail": (
                        f"La versión {existing.version} del código «{existing.code}» "
                        f"está en estado «{existing.estado}»: su contenido es "
                        "append-only (Ac. AGN 001/2024, que compila el 004/2019, ADR-025). Use "
                        f"POST /api/v1/trd/{existing.code}/versiones para crear "
                        "una versión nueva."
                    ),
                    "status": 409,
                },
            )
        # Atomicidad mutación<->asiento (mismo patrón que `update_expediente`):
        # el UPDATE y `audit.append` son un único acto dentro de una sola
        # `conn.transaction()` — si el asiento falla, el cambio de retención/
        # disposición no queda persistido (fail-closed).
        # AREA-06-07: los dos juegos de retención no pueden quedar en desacuerdo. Lo que
        # decide el calendario son las dos fases; un PATCH del juego legado SOLO (sin las
        # fases) se traduce a ellas como en el alta (gestión = retention_years, central =
        # total - gestión), y las columnas legadas se reescriben DERIVADAS de las fases
        # finales. Antes ese PATCH cambiaba números que ningún cálculo leía.
        gestion = payload.archivo_gestion_years
        central = payload.archivo_central_years
        legado = payload.retention_years is not None or payload.total_retention is not None
        if gestion is None and central is None and legado:
            base_gestion = (
                payload.retention_years
                if payload.retention_years is not None
                else existing.retention_years
            )
            base_total = (
                payload.total_retention
                if payload.total_retention is not None
                else existing.total_retention
            )
            gestion, central = base_gestion, max(base_total - base_gestion, 0)
        fases_cambian = gestion is not None or central is not None
        final_gestion = gestion if gestion is not None else (existing.archivo_gestion_years or 0)
        final_central = central if central is not None else (existing.archivo_central_years or 0)
        async with self._conn.transaction():
            updated = await self._repo.update_trd_serie(
                serie_id,
                name=payload.name,
                description=payload.description,
                retention_years=final_gestion if fases_cambian else None,
                total_retention=(final_gestion + final_central) if fases_cambian else None,
                archivo_gestion_years=gestion,
                archivo_central_years=central,
                disposition=payload.disposition,
                pdfa_profile=payload.pdfa_profile,
            )
            # Diff before/after POR CAMPO (E08 §9, Canal A / Ley 594/2000 art.
            # 24): `payload.model_dump(exclude_unset=True)` solo decía el
            # valor NUEVO -- si alguien bajaba `retention_years` de 20 a 2, el
            # asiento no permitía reconstruir el valor anterior. Compara
            # contra `existing` (ya leído antes del UPDATE, no hace falta
            # releer) y solo incluye los campos que REALMENTE cambiaron: un
            # campo enviado con el mismo valor que ya tenía no es un cambio
            # auditable.
            changes: dict[str, dict[str, Any]] = {}
            for field in (*self._TRD_SUBSTANTIVE_FIELDS, "pdfa_profile"):
                sent = getattr(payload, field)
                if sent is None:
                    continue
                before = getattr(existing, field)
                if sent != before:
                    changes[field] = {"before": before, "after": sent}
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.trd_serie_updated",
                object_type="trd_serie",
                object_ref=existing.code,
                actor=str(actor) if actor else None,
                payload={"id": str(serie_id), "changes": changes},
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)  # type: ignore[arg-type]

    async def create_trd_serie_version(
        self,
        code: str,
        payload: TrdSerieVersionCreate,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`POST /trd/{code}/versiones` (ADR-025, Increment 1, nuevo): clona la
        versión VIGENTE (convalidada/migrada) de `code` como una fila NUEVA
        `version+1`, estado `borrador` -- NUNCA un `UPDATE` de la fila vigente
        (D1). Los campos omitidos en `payload` se copian de la vigente; los
        enviados la sobre-escriben en la versión nueva.

        `ux_trd_series_en_tramite` (037) impide que dos versiones en trámite
        (`borrador`/`aprobada`) coexistan para el mismo código -- lo que aquí
        se traduce a un `409` legible en vez de dejar escapar la
        `UniqueViolationError` cruda de Postgres.

        Migración 038 (hallazgo #3 del dictamen): si `code` NO tiene versión
        vigente (todas sus versiones quedaron `derogada` -- p. ej. tras un
        `derogar` explícito sin sucesora) se cae a la ÚLTIMA versión por
        número, sea cual sea su estado, como base para clonar. Sin este
        respaldo, un código totalmente derogado quedaba sin ningún camino de
        vuelta: ni siquiera se podía abrir un `borrador` nuevo que
        eventualmente se convalidara. `404` se reserva para el caso real de
        "este código nunca existió"."""
        vigente = await self._repo.get_trd_serie_vigente_by_code(code)
        base = vigente
        if base is None:
            base = await self._repo.get_trd_serie_ultima_by_code(code)
        if base is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "trd_code_not_found",
                    "detail": f"No existe ninguna versión para el código de serie '{code}'.",
                    "status": 404,
                },
            )
        vigente = base
        # AREA-06-07: las dos fases deciden el calendario; el juego legado se DERIVA de
        # ellas (o se traduce a ellas si solo se envió el legado), nunca se copia de la
        # versión vigente en desacuerdo con las fases nuevas.
        base_gestion = (
            vigente.archivo_gestion_years
            if vigente.archivo_gestion_years is not None
            else vigente.retention_years
        )
        base_central = (
            vigente.archivo_central_years
            if vigente.archivo_central_years is not None
            else max(vigente.total_retention - vigente.retention_years, 0)
        )
        if payload.archivo_gestion_years is None and payload.archivo_central_years is None and (
            payload.retention_years is not None or payload.total_retention is not None
        ):
            ret = payload.retention_years if payload.retention_years is not None else base_gestion
            tot = (
                payload.total_retention
                if payload.total_retention is not None
                else vigente.total_retention
            )
            nueva_gestion, nueva_central = ret, max(tot - ret, 0)
        else:
            nueva_gestion = (
                payload.archivo_gestion_years
                if payload.archivo_gestion_years is not None
                else base_gestion
            )
            nueva_central = (
                payload.archivo_central_years
                if payload.archivo_central_years is not None
                else base_central
            )
        # Atomicidad mutación<->asiento (mismo patrón que `create_trd_serie`).
        async with self._conn.transaction():
            try:
                new_serie = await self._repo.create_trd_serie_version(
                    code=vigente.code,
                    version=vigente.version + 1,
                    supersede_a=vigente.id,
                    created_by=actor,
                    name=payload.name if payload.name is not None else vigente.name,
                    description=(
                        payload.description
                        if payload.description is not None
                        else vigente.description
                    ),
                    retention_years=nueva_gestion,
                    total_retention=nueva_gestion + nueva_central,
                    disposition=(
                        payload.disposition
                        if payload.disposition is not None
                        else vigente.disposition
                    ),
                    parent_id=(
                        payload.parent_id if payload.parent_id is not None else vigente.parent_id
                    ),
                    archivo_gestion_years=nueva_gestion,
                    archivo_central_years=nueva_central,
                    pdfa_profile=(
                        payload.pdfa_profile
                        if payload.pdfa_profile is not None
                        else vigente.pdfa_profile
                    ),
                )
            except asyncpg.UniqueViolationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "trd_version_en_tramite",
                        "detail": (
                            f"Ya existe una versión en trámite (borrador/aprobada) "
                            f"para el código '{vigente.code}'; conclúyala "
                            "(aprobar + convalidar) o devuélvala a borrador "
                            f"(POST /api/v1/trd/{vigente.code}/versiones/"
                            f"{{version}}/devolver) antes de abrir otra. "
                            "'derogar' NO es una salida válida desde borrador/"
                            "aprobada (ADR-025, Increment 2 / migración 038)."
                        ),
                        "status": 409,
                    },
                ) from exc
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.trd_version_creada",
                object_type="trd_serie",
                object_ref=new_serie.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(new_serie.id),
                    "code": new_serie.code,
                    "version": new_serie.version,
                    "supersede_a": str(vigente.id),
                    "supersede_a_version": vigente.version,
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(new_serie)

    # ------------------------------------------------------------------
    # Circuito de convalidación (ADR-025 D2/D6, Increment 2): aprobar /
    # convalidar / derogar. Máquina de estados AISLADA en el service layer
    # (no en el router), validada explícitamente ANTES de tocar BD
    # (fail-fast 409) -- el CHECK/índice de Postgres queda como defensa en
    # profundidad, nunca como el único guardián. Ver ADR-025 §"Máquina de
    # estados del instrumento".
    # ------------------------------------------------------------------

    _TRD_APROBAR_DESDE = frozenset({"borrador"})
    _TRD_CONVALIDAR_DESDE = frozenset({"aprobada", "migrada"})
    _TRD_DEROGAR_DESDE = frozenset({"convalidada", "migrada"})
    # Migración 038 (hallazgo #1): "devolver con observaciones" es la puerta
    # de salida honesta de `aprobada` -- antes la ÚNICA salida era
    # `convalidar`, que exige declarar un acto administrativo. Sin esta
    # transición, un administrador atascado en `aprobada` (sin convalidación
    # todavía) no tenía forma de editar el instrumento salvo mentir sobre la
    # convalidación.
    _TRD_DEVOLVER_DESDE = frozenset({"aprobada"})

    async def _trd_serie_by_code_version_or_404(self, code: str, version: int) -> TrdSerie:
        serie = await self._repo.get_trd_serie_by_code_version(code, version)
        if serie is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "trd_version_not_found",
                    "detail": f"No existe la versión {version} del código de serie '{code}'.",
                    "status": 404,
                },
            )
        return serie

    def _assert_trd_action_from(self, serie: TrdSerie, action: str, valid_from: frozenset[str]) -> None:
        if serie.estado not in valid_from:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_transicion_invalida",
                    "detail": (
                        f"La versión {serie.version} del código '{serie.code}' esta en "
                        f"estado '{serie.estado}': '{action}' solo es válido desde "
                        f"{sorted(valid_from)} (ADR-025, máquina de estados del instrumento)."
                    ),
                    "status": 409,
                },
            )

    def _trd_race_conflict(self, action: str) -> HTTPException:
        """El `WHERE ... AND estado = <origen esperado>` del repositorio (H3,
        mismo patrón que `transition_expediente`) no devolvió fila: otra
        petición concurrente ganó la carrera entre la lectura de `existing` y
        este `UPDATE`. Nunca se aplica la transición sobre un estado que el
        llamante ya no observó."""
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "trd_transicion_invalida",
                "detail": (
                    f"'{action}' no se aplicó: el estado de la versión cambió "
                    "concurrentemente (ADR-025)."
                ),
                "status": 409,
            },
        )

    async def aprobar_trd_serie_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieAprobar,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`borrador -> aprobada` (ADR-025 D2, Increment 2): aprobación del
        Comité Institucional de Gestión y Desempeño (Ac. AGN 001/2024, que
        compila el 004/2019) -- condición necesaria y NO suficiente para
        gobernar un cierre (D5); sin convalidación del Consejo, el
        instrumento no es oponible.

        Migración 038 (hallazgo #5 del dictamen): ahora exige y escribe el
        PRIMER acto del circuito (`acta_comite`/`fecha_aprobacion_comite`) --
        antes el endpoint no recibía body y el sistema solo podía registrar
        el SEGUNDO acto (la convalidación), dejando una fila `convalidada`
        indistinguible por contenido de una `migrada` ratificada."""
        existing = await self._trd_serie_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "aprobar", self._TRD_APROBAR_DESDE)
        # Atomicidad mutación<->asiento (mismo patrón que `create_trd_serie_version`).
        async with self._conn.transaction():
            updated = await self._repo.aprobar_trd_serie(
                existing.id,
                acta_comite=payload.acta_comite,
                fecha_aprobacion_comite=payload.fecha_aprobacion_comite,
            )
            if updated is None:
                raise self._trd_race_conflict("aprobar")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.trd_version_aprobada",
                object_type="trd_serie",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "acta_comite": updated.acta_comite,
                    "fecha_aprobacion_comite": (
                        updated.fecha_aprobacion_comite.isoformat()
                        if updated.fecha_aprobacion_comite
                        else None
                    ),
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    async def devolver_trd_serie_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieDevolver,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`POST /trd/{code}/versiones/{version}/devolver` (ADR-025,
        migración 038 -- hallazgo #1): `aprobada -> borrador`, con motivo
        obligatorio. Es la vía honesta de "devolución con observaciones" que
        contempla Ac. AGN 001/2024 -- un paso ORDINARIO del procedimiento,
        no una excepción; sin ella, la única transición disponible desde
        `aprobada` era `convalidar`, que exige declarar un acto
        administrativo que tal vez todavía no existe.

        Limpia `acta_comite`/`fecha_aprobacion_comite` (repositorio): la
        aprobación queda VOID -- el contenido se va a editar de nuevo, así
        que exige una aprobación NUEVA antes de poder convalidar otra vez."""
        existing = await self._trd_serie_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "devolver", self._TRD_DEVOLVER_DESDE)
        async with self._conn.transaction():
            updated = await self._repo.devolver_trd_serie_a_borrador(existing.id)
            if updated is None:
                raise self._trd_race_conflict("devolver")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.trd_version_devuelta",
                object_type="trd_serie",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "estado_anterior": "aprobada",
                    "motivo": payload.motivo,
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    async def convalidar_trd_serie_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieConvalidar,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`POST /trd/{code}/versiones/{version}/convalidar` (ADR-025 D2/D6,
        Increment 2). Dos orígenes válidos, la MISMA acción los cubre:

        - Desde `aprobada` (circuito normal): `aprobada -> convalidada`, y en
          la MISMA transacción deroga la versión previamente vigente del
          mismo código (si existía) -- exactamente el patrón que
          `create_trd_serie_version` ya usaba al ABRIR la versión nueva, solo
          que la derogación real ocurre aquí, al CONVALIDARLA (ver máquina de
          estados del ADR). También re-apunta (`UPDATE`, no clona) las FK
          huérfanas de `tipos_documentales`/`expediente_metadata_templates`
          (aviso #3 del ADR) de la versión derogada a la nueva.
        - Desde `migrada` (ratificación D6): `migrada -> convalidada` SIN
          crear versión nueva ni derogar nada -- es la MISMA fila, se le
          añaden los campos del acto -- y limpia
          `trd_revision_requerida`/`trd_revision_motivo` de los expedientes
          `closed`/`transferred` amarrados a esta fila cuyo motivo sea
          EXACTAMENTE `cerrado_bajo_serie_migrada` (otros motivos no se
          resuelven con esta ratificación).

        Derogar la vigente previa ANTES de convalidar la nueva (no al
        revés): el índice único parcial `ux_trd_series_vigente` solo admite
        una fila vigente por código -- promover la nueva primero violaría
        transitoriamente esa unicidad.

        Migración 038 (hallazgos del dictamen):

        - **#5**: desde `aprobada` exige que el PRIMER acto ya conste
          (`acta_comite`/`fecha_aprobacion_comite`, escritos por `aprobar`) --
          sin este chequeo, una fila `convalidada` era indistinguible por
          contenido de una `migrada` ratificada (ninguna de las dos, hasta
          ahora, dejaba ver si el Comité aprobó antes del Consejo).
        - **Seguridad (huérfanos en carrera)**: si `vigente_previa` se leyó
          pero OTRA transacción la derogó primero (`derogada is None` tras
          `derogar_vigente_por_code`), esto ya NO se trata como "no había
          nada que derogar" -- se aborta fail-closed, porque las FK de
          `tipos_documentales`/`expediente_metadata_templates` quedarían
          colgando de una fila derogada por otro actor sin re-apuntar.
        - **Seguridad (colisión de FK)**: el re-apunte ahora es
          colisión-segura (`NOT EXISTS`, ver `repoint_*`) y audita también
          los `omitidos`; `UniqueViolationError` residual se traduce a 409."""
        existing = await self._trd_serie_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "convalidar", self._TRD_CONVALIDAR_DESDE)

        if existing.estado == "aprobada" and (
            existing.acta_comite is None or existing.fecha_aprobacion_comite is None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_aprobacion_comite_requerida",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        "está 'aprobada' pero no tiene registrado el acto del Comité "
                        "(acta_comite/fecha_aprobacion_comite): la convalidación del "
                        "Consejo no puede afirmar un primer acto que el sistema no "
                        "puede verificar (Ac. AGN 001/2024, ADR-025 D2, migración 038)."
                    ),
                    "status": 409,
                },
            )

        # Atomicidad mutación<->asiento (mismo patrón que `create_trd_serie`).
        async with self._conn.transaction():
            if existing.estado == "aprobada":
                vigente_previa = await self._repo.get_trd_serie_vigente_by_code(existing.code)
                derogada: TrdSerie | None = None
                if vigente_previa is not None:
                    derogada = await self._repo.derogar_vigente_por_code(
                        existing.code, exclude_id=existing.id
                    )
                    if derogada is None:
                        # Otra transaccion concurrente ya la derogo/movio
                        # entre la lectura de `vigente_previa` y este UPDATE
                        # -- abortar en vez de convalidar dejando FK
                        # huerfanas de una fila que este acto ya no controla.
                        raise self._trd_race_conflict("convalidar")

                updated = await self._repo.convalidar_trd_serie_desde_aprobada(
                    existing.id,
                    acto_administrativo=payload.acto_administrativo,
                    fecha_convalidacion=payload.fecha_convalidacion,
                    instancia_convalidante=payload.instancia_convalidante,
                    rusd_radicado=payload.rusd_radicado,
                    fecha_publicacion=payload.fecha_publicacion,
                )
                if updated is None:
                    raise self._trd_race_conflict("convalidar")

                n_tipos = n_tipos_omitidos = n_templates = n_templates_omitidos = 0
                if derogada is not None:
                    # Aviso #3 (ADR-025 §"Impacto en lo existente"): estos
                    # hijos apuntan a la FILA, no al código -- sin
                    # re-apuntarlos, la convalidación de la N+1 los dejaría
                    # huérfanos de la versión que acaba de derogarse.
                    # Colisión-segura (038): una clave que YA existe en la
                    # versión nueva se OMITE, nunca revienta la tx.
                    try:
                        n_tipos, n_tipos_omitidos = await self._repo.repoint_tipos_documentales(
                            old_serie_id=derogada.id, new_serie_id=updated.id
                        )
                        (
                            n_templates,
                            n_templates_omitidos,
                        ) = await self._repo.repoint_expediente_metadata_templates(
                            old_serie_id=derogada.id, new_serie_id=updated.id
                        )
                    except asyncpg.UniqueViolationError as exc:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "error": "trd_repoint_colision",
                                "detail": (
                                    "No se pudo re-apuntar tipos_documentales/"
                                    "expediente_metadata_templates de la versión "
                                    f"derogada ({derogada.code} v{derogada.version}) a la "
                                    f"nueva vigente: colisión de clave no cubierta por "
                                    "el filtro de exclusión (ADR-025, migración 038)."
                                ),
                                "status": 409,
                            },
                        ) from exc

                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.trd_version_convalidada",
                    object_type="trd_serie",
                    object_ref=updated.code,
                    actor=str(actor) if actor else None,
                    payload={
                        "id": str(updated.id),
                        "code": updated.code,
                        "version": updated.version,
                        "acto_administrativo": updated.acto_administrativo,
                        "fecha_convalidacion": (
                            updated.fecha_convalidacion.isoformat()
                            if updated.fecha_convalidacion
                            else None
                        ),
                        "instancia_convalidante": updated.instancia_convalidante,
                        "rusd_radicado": updated.rusd_radicado,
                        "fecha_publicacion": (
                            updated.fecha_publicacion.isoformat()
                            if updated.fecha_publicacion
                            else None
                        ),
                        "ratificacion": False,
                        "estado_anterior": "aprobada",
                        "derogada_id": str(derogada.id) if derogada else None,
                        "derogada_version": derogada.version if derogada else None,
                        "tipos_documentales_repointed": n_tipos,
                        "tipos_documentales_omitidos": n_tipos_omitidos,
                        "expediente_metadata_templates_repointed": n_templates,
                        "expediente_metadata_templates_omitidos": n_templates_omitidos,
                    },
                    canal=audit.CANAL_PETICION,
                )
                # `vigente_previa is not None` es REDUNDANTE con
                # `derogada is not None` (solo se deroga si había una vigente),
                # pero la correlación entre las dos variables no es visible
                # aquí y `vigente_previa.estado` va dentro. Explícito y con el
                # mismo comportamiento.
                if derogada is not None and vigente_previa is not None:
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service=_SERVICE,
                        action="archive.trd_version_derogada",
                        object_type="trd_serie",
                        object_ref=derogada.code,
                        actor=str(actor) if actor else None,
                        payload={
                            "id": str(derogada.id),
                            "code": derogada.code,
                            "version": derogada.version,
                            # `derogada` YA trae `estado='derogada'` (RETURNING
                            # del propio UPDATE) -- el estado ANTERIOR es el
                            # que se observó en `vigente_previa` antes de este acto.
                            "estado_anterior": vigente_previa.estado,
                            "motivo": "supersedida_por_convalidacion",
                            "supersedida_por": str(updated.id),
                            "supersedida_por_version": updated.version,
                        },
                        canal=audit.CANAL_PETICION,
                    )
            else:  # existing.estado == "migrada" -- ratificación D6.
                updated = await self._repo.convalidar_trd_serie_desde_migrada(
                    existing.id,
                    acto_administrativo=payload.acto_administrativo,
                    fecha_convalidacion=payload.fecha_convalidacion,
                    instancia_convalidante=payload.instancia_convalidante,
                    rusd_radicado=payload.rusd_radicado,
                    fecha_publicacion=payload.fecha_publicacion,
                )
                if updated is None:
                    raise self._trd_race_conflict("convalidar")

                n_ratificados = await self._repo.clear_revision_pendiente_por_serie(
                    updated.id, motivo="cerrado_bajo_serie_migrada"
                )

                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.trd_version_convalidada",
                    object_type="trd_serie",
                    object_ref=updated.code,
                    actor=str(actor) if actor else None,
                    payload={
                        "id": str(updated.id),
                        "code": updated.code,
                        "version": updated.version,
                        "acto_administrativo": updated.acto_administrativo,
                        "fecha_convalidacion": (
                            updated.fecha_convalidacion.isoformat()
                            if updated.fecha_convalidacion
                            else None
                        ),
                        "instancia_convalidante": updated.instancia_convalidante,
                        "rusd_radicado": updated.rusd_radicado,
                        "fecha_publicacion": (
                            updated.fecha_publicacion.isoformat()
                            if updated.fecha_publicacion
                            else None
                        ),
                        "ratificacion": True,
                        "estado_anterior": "migrada",
                        "expedientes_ratificados": n_ratificados,
                    },
                    canal=audit.CANAL_PETICION,
                )
        return _to_trd_response(updated)

    async def derogar_trd_serie_version(
        self, code: str, version: int, actor: uuid.UUID | None = None
    ) -> TrdSerieResponse:
        """`convalidada`/`migrada` -> `derogada` (baja explícita del
        instrumento, sin sucesora, ADR-025 Increment 2 / migración 038 --
        hallazgo #3 del dictamen). `valid_to`/`is_active` los fija el
        repositorio; ninguna consecuencia retroactiva -- los expedientes ya
        cerrados bajo esta versión conservan su snapshot congelado (D3/D4).

        Decisión (#3, ver ADR-025 addendum): se exige que NINGÚN expediente
        `open` esté clasificado bajo `code` -- si los hay, `close_expediente`
        re-resolvería por código al cerrarlos y no encontraría ninguna
        versión vigente (`409 trd_version_no_vigente` para siempre, salvo
        que se convalide una sucesora después). Se descartó exigir una
        sucesora YA convalidada como precondición de `derogar` porque este
        endpoint es también el camino LEGÍTIMO para retirar una serie que ya
        no produce documentos (dependencia eliminada, competencia trasladada)
        -- exigir sucesora bloquearía exactamente ese caso de uso. El código
        nunca queda sin camino de vuelta: `create_trd_serie_version` (038)
        cae a la última versión por número cuando no hay vigente, así que
        siempre se puede abrir un `borrador` nuevo y convalidarlo."""
        existing = await self._trd_serie_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "derogar", self._TRD_DEROGAR_DESDE)

        n_open = await self._repo.count_expedientes_open_por_trd_serie_code(existing.code)
        if n_open:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_derogacion_bloqueada_expedientes_abiertos",
                    "detail": (
                        f"Hay {n_open} expediente(s) 'open' clasificados bajo el "
                        f"código '{existing.code}': derogar esta versión sin una "
                        "sucesora los dejaría sin ningún instrumento vigente que "
                        "gobierne su cierre. Convalide una versión sucesora, o "
                        "reclasifique esos expedientes, antes de derogar "
                        "(ADR-025, migración 038)."
                    ),
                    "status": 409,
                },
            )

        async with self._conn.transaction():
            updated = await self._repo.derogar_trd_serie(existing.id)
            if updated is None:
                raise self._trd_race_conflict("derogar")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.trd_version_derogada",
                object_type="trd_serie",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "estado_anterior": existing.estado,
                    "motivo": "derogacion_explicita",
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    async def registrar_rusd_trd_serie_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieRegistrarRusd,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`POST /trd/{code}/versiones/{version}/registrar-rusd` (ADR-025,
        migración 038 -- hallazgo #4 del dictamen). El RUSD y la publicación
        (Ac. AGN 001/2024, que compila el 004/2019) tienen hasta 30 días
        hábiles DESPUÉS de la convalidación para completarse -- este
        endpoint es el único momento en que el sistema los acepta fuera de
        `convalidar`. Solo aplica sobre `convalidada`; cada campo solo puede
        rellenarse UNA vez (de NULL a un valor) -- el trigger de la 038 lo
        blinda a nivel de motor, pero el chequeo aquí da un 409 legible en
        vez de la excepción cruda de Postgres."""
        existing = await self._trd_serie_by_code_version_or_404(code, version)
        if existing.estado != "convalidada":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_version_no_convalidada",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        f"está en estado '{existing.estado}': el RUSD y la "
                        "publicación solo se registran sobre una versión "
                        "'convalidada' (ADR-025, migración 038)."
                    ),
                    "status": 409,
                },
            )
        if payload.rusd_radicado is not None and existing.rusd_radicado is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_rusd_ya_registrado",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        "ya tiene un rusd_radicado registrado; es inmutable una "
                        "vez puesto (ADR-025, migración 038)."
                    ),
                    "status": 409,
                },
            )
        if payload.fecha_publicacion is not None and existing.fecha_publicacion is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_fecha_publicacion_ya_registrada",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        "ya tiene fecha_publicacion registrada; es inmutable una "
                        "vez puesta (ADR-025, migración 038)."
                    ),
                    "status": 409,
                },
            )
        async with self._conn.transaction():
            updated = await self._repo.registrar_rusd_trd_serie(
                existing.id,
                rusd_radicado=payload.rusd_radicado,
                fecha_publicacion=payload.fecha_publicacion,
            )
            if updated is None:
                raise self._trd_race_conflict("registrar-rusd")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.trd_rusd_registrado",
                object_type="trd_serie",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "rusd_radicado": updated.rusd_radicado,
                    "fecha_publicacion": (
                        updated.fecha_publicacion.isoformat()
                        if updated.fecha_publicacion
                        else None
                    ),
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    # ------------------------------------------------------------------
    # TVD agrupaciones (ADR-026, Increment 1): la TVD es el MISMO instrumento
    # con otro contenido (discriminador `tipo_instrumento` sobre
    # `trd_series`, D1) -- el circuito de convalidación (aprobar/devolver/
    # convalidar/registrar-rusd/derogar) es TYPE-AGNOSTIC a nivel de
    # repositorio (opera por `id`/`estado`, nunca filtra por tipo) y se
    # REUTILIZA íntegro; solo cambian aquí: (a) las lecturas por código, que
    # pasan por `v_tvd_agrupaciones` (Capa 2) para que un id/código de TRD no
    # sea alcanzable desde esta superficie, y (b) los nombres de asiento de
    # auditoría (`archive.tvd_*`, D2: en `audit_log` la decisión es la
    # contraria a la del modelo de datos, a propósito).
    #
    # `tipo_instrumento` NUNCA se acepta del cliente (ADR-026 §"Superficie de
    # API"): estos métodos lo fijan ellos mismos al llamar al repositorio.
    # ------------------------------------------------------------------

    _TVD_APROBAR_DESDE = _TRD_APROBAR_DESDE
    # Una TVD nunca alcanza `migrada` (nace en `borrador`, D6 no aplica) --
    # el origen de `convalidar` para TVD es únicamente `aprobada`.
    _TVD_CONVALIDAR_DESDE = frozenset({"aprobada"})
    _TVD_DEROGAR_DESDE = frozenset({"convalidada"})
    _TVD_DEVOLVER_DESDE = _TRD_DEVOLVER_DESDE

    async def _tvd_code_conflict(self, code: str) -> HTTPException:
        """ADR-026 Capa 1: el espacio de `code` es único y COMPARTIDO entre
        TRD y TVD -- lo traduce a un `409 instrumento_code_en_uso` legible,
        indicando de qué tipo es el ocupante, en vez de dejar escapar la
        `UniqueViolationError` cruda de `trd_series_code_version_key`."""
        ocupante_tipo = await self._repo.get_ocupante_tipo_por_code(code)
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "instrumento_code_en_uso",
                "detail": (
                    f"El código '{code}' ya esta en uso por un instrumento "
                    f"de tipo '{ocupante_tipo or '(desconocido)'}'. TRD y TVD "
                    "comparten el mismo espacio de códigos (ADR-026 Capa 1): "
                    "una entidad no puede reutilizar el mismo código entre "
                    "instrumentos."
                ),
                "status": 409,
                "ocupante_tipo": ocupante_tipo,
            },
        )

    async def create_tvd_agrupacion(
        self, payload: TvdAgrupacionCreate, actor: uuid.UUID | None = None
    ) -> TrdSerieResponse:
        """`POST /api/v1/tvd` (ADR-026, Increment 1). Crea la agrupación
        version 1 en `borrador`. Mismo patrón de atomicidad mutación<->asiento
        que `create_trd_serie`."""
        async with self._conn.transaction():
            try:
                agrupacion = await self._repo.create_tvd_agrupacion(
                    code=payload.code,
                    name=payload.name,
                    description=payload.description,
                    total_retention=payload.total_retention,
                    disposition=payload.disposition,
                    parent_id=payload.parent_id,
                    fondo_nombre=payload.fondo_nombre,
                    fecha_extrema_inicial=payload.fecha_extrema_inicial,
                    fecha_extrema_final=payload.fecha_extrema_final,
                    justificacion_valoracion=payload.justificacion_valoracion,
                    archivo_central_years=payload.archivo_central_years,
                    pdfa_profile=payload.pdfa_profile,
                )
            except asyncpg.UniqueViolationError as exc:
                raise await self._tvd_code_conflict(payload.code) from exc
            # Auditoría forense transversal (E08 §9, Canal A) -- gemela de
            # `archive.trd_serie_created`, nombre distinto a propósito (D2).
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_agrupacion_created",
                object_type="tvd_agrupacion",
                object_ref=agrupacion.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(agrupacion.id),
                    "code": agrupacion.code,
                    "name": agrupacion.name,
                    "description": agrupacion.description,
                    "total_retention": agrupacion.total_retention,
                    "disposition": agrupacion.disposition,
                    "parent_id": str(agrupacion.parent_id) if agrupacion.parent_id else None,
                    "archivo_central_years": agrupacion.archivo_central_years,
                    "pdfa_profile": agrupacion.pdfa_profile,
                    "fondo_nombre": agrupacion.fondo_nombre,
                    "fecha_extrema_inicial": agrupacion.fecha_extrema_inicial.isoformat()
                    if agrupacion.fecha_extrema_inicial
                    else None,
                    "fecha_extrema_final": agrupacion.fecha_extrema_final.isoformat()
                    if agrupacion.fecha_extrema_final
                    else None,
                    "justificacion_valoracion": agrupacion.justificacion_valoracion,
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(agrupacion)

    async def list_tvd_agrupaciones(
        self, *, is_active: bool | None, page: int, size: int
    ) -> tuple[list[TrdSerieResponse], int]:
        offset = (page - 1) * size
        agrupaciones, total = await self._repo.list_tvd_agrupaciones(
            is_active=is_active, offset=offset, limit=size
        )
        return [_to_trd_response(s) for s in agrupaciones], total

    async def get_tvd_agrupacion(self, agrupacion_id: uuid.UUID) -> TrdSerieResponse:
        agrupacion = await self._repo.get_tvd_agrupacion_by_id(agrupacion_id)
        if agrupacion is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tvd_agrupacion_not_found",
                    "detail": "No existe la agrupación TVD indicada",
                    "status": 404,
                },
            )
        return _to_trd_response(agrupacion)

    async def get_tvd_agrupacion_versiones(self, code: str) -> list[TrdSerieResponse]:
        """Gemela de `get_trd_serie_versiones` sobre `v_tvd_agrupaciones`."""
        agrupaciones = await self._repo.list_tvd_agrupacion_versiones_by_code(code)
        return [_to_trd_response(s) for s in agrupaciones]

    async def update_tvd_agrupacion(
        self,
        agrupacion_id: uuid.UUID,
        payload: TvdAgrupacionUpdate,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`PATCH /api/v1/tvd/{id}` (ADR-026): gemela de `update_trd_serie`."""
        existing = await self._repo.get_tvd_agrupacion_by_id(agrupacion_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tvd_agrupacion_not_found",
                    "detail": "No existe la agrupación TVD indicada",
                    "status": 404,
                },
            )
        substantive_fields = (
            "name",
            "description",
            "total_retention",
            "disposition",
            "fondo_nombre",
            "fecha_extrema_inicial",
            "fecha_extrema_final",
            "justificacion_valoracion",
        )
        has_substantive_change = any(
            getattr(payload, field) is not None for field in substantive_fields
        )
        if existing.estado != "borrador" and has_substantive_change:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_version_inmutable",
                    "detail": (
                        f"La versión {existing.version} del código «{existing.code}» "
                        f"está en estado «{existing.estado}»: su contenido es "
                        "append-only (Ac. AGN 001/2024, que compila el 004/2019, ADR-026). Use "
                        f"POST /api/v1/tvd/{existing.code}/versiones para crear "
                        "una versión nueva."
                    ),
                    "status": 409,
                },
            )
        async with self._conn.transaction():
            updated = await self._repo.update_tvd_agrupacion(
                agrupacion_id,
                name=payload.name,
                description=payload.description,
                total_retention=payload.total_retention,
                disposition=payload.disposition,
                pdfa_profile=payload.pdfa_profile,
                fondo_nombre=payload.fondo_nombre,
                fecha_extrema_inicial=payload.fecha_extrema_inicial,
                fecha_extrema_final=payload.fecha_extrema_final,
                justificacion_valoracion=payload.justificacion_valoracion,
            )
            changes: dict[str, dict[str, Any]] = {}
            for field in (*substantive_fields, "pdfa_profile"):
                sent = getattr(payload, field)
                if sent is None:
                    continue
                before = getattr(existing, field)
                if sent != before:
                    changes[field] = {
                        "before": before.isoformat() if hasattr(before, "isoformat") else before,
                        "after": sent.isoformat() if hasattr(sent, "isoformat") else sent,
                    }
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_agrupacion_updated",
                object_type="tvd_agrupacion",
                object_ref=existing.code,
                actor=str(actor) if actor else None,
                payload={"id": str(agrupacion_id), "changes": changes},
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)  # type: ignore[arg-type]

    async def create_tvd_agrupacion_version(
        self,
        code: str,
        payload: TvdAgrupacionVersionCreate,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`POST /api/v1/tvd/{code}/versiones` (ADR-026): gemela de
        `create_trd_serie_version` sobre la vista/tabla TVD."""
        vigente = await self._repo.get_tvd_agrupacion_vigente_por_code(code)
        base = vigente
        if base is None:
            base = await self._repo.get_tvd_agrupacion_ultima_por_code(code)
        if base is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tvd_code_not_found",
                    "detail": f"No existe ninguna versión para el código de agrupación '{code}'.",
                    "status": 404,
                },
            )
        vigente = base
        # Los cuatro campos propios de la TVD (fondo, fechas extremas y
        # justificación) son NOT NULL por `trd_series_tvd_forma_check`
        # (migración 039), así que una versión vigente los tiene siempre. Pero
        # el modelo los declara opcionales —correctamente: una TRD los lleva a
        # NULL como negación declarada— y aquí se fusionan con el payload. Sin
        # esta comprobación, el caso imposible llegaba a la base y salía como
        # una violación de restricción opaca (500) en vez de un error legible.
        fondo_nombre = (
            payload.fondo_nombre if payload.fondo_nombre is not None else vigente.fondo_nombre
        )
        fecha_extrema_inicial = (
            payload.fecha_extrema_inicial
            if payload.fecha_extrema_inicial is not None
            else vigente.fecha_extrema_inicial
        )
        fecha_extrema_final = (
            payload.fecha_extrema_final
            if payload.fecha_extrema_final is not None
            else vigente.fecha_extrema_final
        )
        justificacion_valoracion = (
            payload.justificacion_valoracion
            if payload.justificacion_valoracion is not None
            else vigente.justificacion_valoracion
        )
        if (
            fondo_nombre is None
            or fecha_extrema_inicial is None
            or fecha_extrema_final is None
            or justificacion_valoracion is None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "tvd_forma_incompleta",
                    "detail": (
                        "Una versión de TVD exige fondo_nombre, fecha_extrema_inicial, "
                        "fecha_extrema_final y justificacion_valoracion (Ac. AGN 001/2024, "
                        "ADR-026)."
                    ),
                    "status": 422,
                },
            )
        async with self._conn.transaction():
            try:
                new_agrupacion = await self._repo.create_tvd_agrupacion_version(
                    code=vigente.code,
                    version=vigente.version + 1,
                    supersede_a=vigente.id,
                    created_by=actor,
                    name=payload.name if payload.name is not None else vigente.name,
                    description=(
                        payload.description
                        if payload.description is not None
                        else vigente.description
                    ),
                    total_retention=(
                        payload.total_retention
                        if payload.total_retention is not None
                        else vigente.total_retention
                    ),
                    disposition=(
                        payload.disposition
                        if payload.disposition is not None
                        else vigente.disposition
                    ),
                    parent_id=(
                        payload.parent_id if payload.parent_id is not None else vigente.parent_id
                    ),
                    fondo_nombre=fondo_nombre,
                    fecha_extrema_inicial=fecha_extrema_inicial,
                    fecha_extrema_final=fecha_extrema_final,
                    justificacion_valoracion=justificacion_valoracion,
                    archivo_central_years=(
                        payload.archivo_central_years
                        if payload.archivo_central_years is not None
                        else vigente.archivo_central_years
                    ),
                    pdfa_profile=(
                        payload.pdfa_profile
                        if payload.pdfa_profile is not None
                        else vigente.pdfa_profile
                    ),
                )
            except asyncpg.UniqueViolationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "trd_version_en_tramite",
                        "detail": (
                            f"Ya existe una versión en trámite (borrador/aprobada) "
                            f"para el código '{vigente.code}'; conclúyala "
                            "(aprobar + convalidar) o devuélvala a borrador "
                            f"(POST /api/v1/tvd/{vigente.code}/versiones/"
                            f"{{version}}/devolver) antes de abrir otra "
                            "(ADR-026)."
                        ),
                        "status": 409,
                    },
                ) from exc
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_version_creada",
                object_type="tvd_agrupacion",
                object_ref=new_agrupacion.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(new_agrupacion.id),
                    "code": new_agrupacion.code,
                    "version": new_agrupacion.version,
                    "supersede_a": str(vigente.id),
                    "supersede_a_version": vigente.version,
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(new_agrupacion)

    async def _tvd_agrupacion_by_code_version_or_404(self, code: str, version: int) -> TrdSerie:
        agrupacion = await self._repo.get_tvd_agrupacion_by_code_version(code, version)
        if agrupacion is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tvd_version_not_found",
                    "detail": f"No existe la versión {version} del código de agrupación '{code}'.",
                    "status": 404,
                },
            )
        return agrupacion

    async def aprobar_tvd_agrupacion_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieAprobar,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`borrador -> aprobada` (ADR-026). El circuito lo sostiene el mismo
        repositorio type-agnostic que TRD (`ArchiveRepository.aprobar_trd_serie`)."""
        existing = await self._tvd_agrupacion_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "aprobar", self._TVD_APROBAR_DESDE)
        async with self._conn.transaction():
            updated = await self._repo.aprobar_trd_serie(
                existing.id,
                acta_comite=payload.acta_comite,
                fecha_aprobacion_comite=payload.fecha_aprobacion_comite,
            )
            if updated is None:
                raise self._trd_race_conflict("aprobar")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_version_aprobada",
                object_type="tvd_agrupacion",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "acta_comite": updated.acta_comite,
                    "fecha_aprobacion_comite": (
                        updated.fecha_aprobacion_comite.isoformat()
                        if updated.fecha_aprobacion_comite
                        else None
                    ),
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    async def devolver_tvd_agrupacion_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieDevolver,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`aprobada -> borrador` (ADR-026, gemela de `devolver_trd_serie_version`)."""
        existing = await self._tvd_agrupacion_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "devolver", self._TVD_DEVOLVER_DESDE)
        async with self._conn.transaction():
            updated = await self._repo.devolver_trd_serie_a_borrador(existing.id)
            if updated is None:
                raise self._trd_race_conflict("devolver")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_version_devuelta",
                object_type="tvd_agrupacion",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "estado_anterior": "aprobada",
                    "motivo": payload.motivo,
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    async def convalidar_tvd_agrupacion_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieConvalidar,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`aprobada -> convalidada` (ADR-026). Único origen: una TVD nunca
        llega a `migrada` (nace en `borrador`, D6 no aplica -- §"Máquina de
        estados"). Deroga la vigente previa del mismo código en la misma
        transacción, mismo patrón que `convalidar_trd_serie_version`; el
        re-apunte de `tipos_documentales`/`expediente_metadata_templates`
        tiene el mismo sentido archivístico en un fondo acumulado."""
        existing = await self._tvd_agrupacion_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "convalidar", self._TVD_CONVALIDAR_DESDE)

        if existing.acta_comite is None or existing.fecha_aprobacion_comite is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_aprobacion_comite_requerida",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        "está 'aprobada' pero no tiene registrado el acto del Comité "
                        "(acta_comite/fecha_aprobacion_comite): la convalidación del "
                        "Consejo no puede afirmar un primer acto que el sistema no "
                        "puede verificar (Ac. AGN 001/2024, ADR-026)."
                    ),
                    "status": 409,
                },
            )

        async with self._conn.transaction():
            vigente_previa = await self._repo.get_tvd_agrupacion_vigente_por_code(existing.code)
            derogada: TrdSerie | None = None
            if vigente_previa is not None:
                derogada = await self._repo.derogar_vigente_por_code(
                    existing.code, exclude_id=existing.id
                )
                if derogada is None:
                    raise self._trd_race_conflict("convalidar")

            updated = await self._repo.convalidar_trd_serie_desde_aprobada(
                existing.id,
                acto_administrativo=payload.acto_administrativo,
                fecha_convalidacion=payload.fecha_convalidacion,
                instancia_convalidante=payload.instancia_convalidante,
                rusd_radicado=payload.rusd_radicado,
                fecha_publicacion=payload.fecha_publicacion,
            )
            if updated is None:
                raise self._trd_race_conflict("convalidar")

            n_tipos = n_tipos_omitidos = n_templates = n_templates_omitidos = 0
            if derogada is not None:
                try:
                    n_tipos, n_tipos_omitidos = await self._repo.repoint_tipos_documentales(
                        old_serie_id=derogada.id, new_serie_id=updated.id
                    )
                    (
                        n_templates,
                        n_templates_omitidos,
                    ) = await self._repo.repoint_expediente_metadata_templates(
                        old_serie_id=derogada.id, new_serie_id=updated.id
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "trd_repoint_colision",
                            "detail": (
                                "No se pudo re-apuntar tipos_documentales/"
                                "expediente_metadata_templates de la versión "
                                f"derogada ({derogada.code} v{derogada.version}) a la "
                                f"nueva vigente: colisión de clave no cubierta por "
                                "el filtro de exclusión (ADR-026)."
                            ),
                            "status": 409,
                        },
                    ) from exc

            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_version_convalidada",
                object_type="tvd_agrupacion",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "acto_administrativo": updated.acto_administrativo,
                    "fecha_convalidacion": (
                        updated.fecha_convalidacion.isoformat()
                        if updated.fecha_convalidacion
                        else None
                    ),
                    "instancia_convalidante": updated.instancia_convalidante,
                    "rusd_radicado": updated.rusd_radicado,
                    "fecha_publicacion": (
                        updated.fecha_publicacion.isoformat() if updated.fecha_publicacion else None
                    ),
                    "fondo_nombre": updated.fondo_nombre,
                    "justificacion_valoracion": updated.justificacion_valoracion,
                    "derogada_id": str(derogada.id) if derogada else None,
                    "derogada_version": derogada.version if derogada else None,
                    "tipos_documentales_repointed": n_tipos,
                    "tipos_documentales_omitidos": n_tipos_omitidos,
                    "expediente_metadata_templates_repointed": n_templates,
                    "expediente_metadata_templates_omitidos": n_templates_omitidos,
                },
                canal=audit.CANAL_PETICION,
            )
            # `vigente_previa is not None` es REDUNDANTE con
            # `derogada is not None` (solo se deroga si había una vigente),
            # pero la correlación entre las dos variables no es visible
            # aquí y `vigente_previa.estado` va dentro. Explícito y con el
            # mismo comportamiento.
            if derogada is not None and vigente_previa is not None:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.tvd_version_derogada",
                    object_type="tvd_agrupacion",
                    object_ref=derogada.code,
                    actor=str(actor) if actor else None,
                    payload={
                        "id": str(derogada.id),
                        "code": derogada.code,
                        "version": derogada.version,
                        "estado_anterior": vigente_previa.estado,
                        "motivo": "supersedida_por_convalidacion",
                        "supersedida_por": str(updated.id),
                        "supersedida_por_version": updated.version,
                    },
                    canal=audit.CANAL_PETICION,
                )
        return _to_trd_response(updated)

    async def derogar_tvd_agrupacion_version(
        self, code: str, version: int, actor: uuid.UUID | None = None
    ) -> TrdSerieResponse:
        """`convalidada -> derogada` (ADR-026). No se exige "sin expedientes
        open bajo el codigo" como precondicion adicional a la de TRD: la
        misma guarda (`count_expedientes_open_por_trd_serie_code`) aplica
        igual -- en fondo acumulado casi nunca hay expedientes `open` (se
        registran ya cerrados, ver ADR-026 §"Maquina de estados"), asi que en
        la practica casi nunca bloquea, pero se conserva por si alguna
        entidad SI abre expedientes bajo una TVD entre el Increment 1 y 2."""
        existing = await self._tvd_agrupacion_by_code_version_or_404(code, version)
        self._assert_trd_action_from(existing, "derogar", self._TVD_DEROGAR_DESDE)

        n_open = await self._repo.count_expedientes_open_por_trd_serie_code(existing.code)
        if n_open:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_derogacion_bloqueada_expedientes_abiertos",
                    "detail": (
                        f"Hay {n_open} expediente(s) 'open' clasificados bajo el "
                        f"código '{existing.code}': derogar esta versión sin una "
                        "sucesora los dejaría sin ningún instrumento vigente que "
                        "gobierne su cierre (ADR-026)."
                    ),
                    "status": 409,
                },
            )

        async with self._conn.transaction():
            updated = await self._repo.derogar_trd_serie(existing.id)
            if updated is None:
                raise self._trd_race_conflict("derogar")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_version_derogada",
                object_type="tvd_agrupacion",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "estado_anterior": existing.estado,
                    "motivo": "derogacion_explicita",
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    async def registrar_rusd_tvd_agrupacion_version(
        self,
        code: str,
        version: int,
        payload: TrdSerieRegistrarRusd,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        """`registrar-rusd` (ADR-026, gemela de `registrar_rusd_trd_serie_version`)."""
        existing = await self._tvd_agrupacion_by_code_version_or_404(code, version)
        if existing.estado != "convalidada":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_version_no_convalidada",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        f"está en estado '{existing.estado}': el RUSD y la "
                        "publicación solo se registran sobre una versión "
                        "'convalidada' (ADR-026)."
                    ),
                    "status": 409,
                },
            )
        if payload.rusd_radicado is not None and existing.rusd_radicado is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_rusd_ya_registrado",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        "ya tiene un rusd_radicado registrado; es inmutable una "
                        "vez puesto (ADR-026)."
                    ),
                    "status": 409,
                },
            )
        if payload.fecha_publicacion is not None and existing.fecha_publicacion is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "trd_fecha_publicacion_ya_registrada",
                    "detail": (
                        f"La versión {existing.version} del código '{existing.code}' "
                        "ya tiene fecha_publicacion registrada; es inmutable una "
                        "vez puesta (ADR-026)."
                    ),
                    "status": 409,
                },
            )
        async with self._conn.transaction():
            updated = await self._repo.registrar_rusd_trd_serie(
                existing.id,
                rusd_radicado=payload.rusd_radicado,
                fecha_publicacion=payload.fecha_publicacion,
            )
            if updated is None:
                raise self._trd_race_conflict("registrar-rusd")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_rusd_registrado",
                object_type="tvd_agrupacion",
                object_ref=updated.code,
                actor=str(actor) if actor else None,
                payload={
                    "id": str(updated.id),
                    "code": updated.code,
                    "version": updated.version,
                    "rusd_radicado": updated.rusd_radicado,
                    "fecha_publicacion": (
                        updated.fecha_publicacion.isoformat()
                        if updated.fecha_publicacion
                        else None
                    ),
                },
                canal=audit.CANAL_PETICION,
            )
        return _to_trd_response(updated)

    async def list_expedientes_disposicion_vencida(
        self,
        *,
        page: int,
        size: int,
        fase: str,
        disposition_code: str | None = None,
        incluir_aplazados: bool = False,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[ExpedienteDisposicionVencidaResponse], int]:
        """`GET /trd/disposicion-vencida` (RF-TRD-08): la cola de expedientes
        cuyo calendario de retención ya venció.

        Es la parte de RF-TRD-08 **que no destruye nada**: el sistema deja de
        limitarse a guardar el calendario y pasa a decir en voz alta qué venció,
        cuándo, bajo qué acto administrativo y qué toca hacer con ello. El brazo
        que ejecuta (borrado seguro con acta, RF-TRD-07) es el incremento
        siguiente y tendrá su propio procedimiento -- aval del Comité,
        publicidad previa del inventario, acta firmada.

        No-read-up con el mismo criterio que `list_expedientes`, **incluido el
        recuento**, y traza agregada de consulta (RF-BUS-10) gemela de la del
        informe de revisión pendiente: enumerar los expedientes que están a un
        paso de eliminarse es exactamente la clase de consulta que debe dejar
        rastro de quién la hizo."""
        offset = (page - 1) * size
        clearance = await self._repo.caller_clearance(user_id)
        rows, total = await self._repo.list_expedientes_disposicion_vencida(
            offset=offset,
            limit=size,
            max_clearance=clearance,
            fase=fase,
            disposition_code=disposition_code,
            incluir_aplazados=incluir_aplazados,
        )
        await self._audit_search_access(
            action="archive.expediente_disposicion_vencida_listado",
            filtros={
                "fase": fase,
                "disposition_code": disposition_code,
                "incluir_aplazados": incluir_aplazados,
            },
            total=total,
            page=page,
            size=size,
            user_id=user_id,
            canal=canal,
        )
        items = [self._fila_disposicion_vencida(r, fase=fase) for r in rows]
        return items, total

    async def proponer_lote_eliminacion(
        self,
        *,
        codigo: str,
        expediente_ids: list[uuid.UUID],
        observaciones: str | None,
        actor: uuid.UUID,
    ) -> LoteEliminacionResponse:
        """`POST /eliminacion/lotes` (RF-TRD-07, primera de las cinco fases).

        Se elimina **por lotes** porque el acta y el aval del Comité son del
        lote. Esta fase **no elimina nada**: lo que hace es fijar QUÉ se
        propone eliminar y congelarlo. La destrucción es la quinta fase.

        **Cinco condiciones de admisión, y ninguna se salta.** Un expediente
        entra solo si:

        1. Su vencimiento del archivo central está **confirmado** por un
           usuario autorizado (RF-TRD-06). Sin decisión no hay propuesta: sería
           saltarse la única barrera que impide la destrucción automática.
        2. Su disposición **termina en destrucción** (`E`, `S`, `M`).
           Conservación Total **jamás** entra, y si llega aquí el error está
           aguas arriba.
        3. El **instrumento congelado la gobierna** (ADR-025 D5), el mismo
           predicado del gate.
        4. Su **candado WORM ya expiró**. Es el máximo de los tres que puede
           tener (índice, acta, AIP): mientras uno siga vigente el borrado es
           físicamente imposible en modo COMPLIANCE, y un lote que se acepta y
           luego no se puede ejecutar es la peor de las dos respuestas.
        5. **No está ya en otro lote** vivo.

        **Lo rechazado se informa, no se descarta.** Quien propone 40 y recibe
        31 tiene que ver cuáles nueve faltan y qué le pasa a cada uno; si no, la
        única salida es probar de uno en uno. Un expediente **fuera del
        clearance** del proponente no aparece siquiera entre los candidatos —y
        se informa como «no encontrado», sin distinguirlo de uno inexistente,
        para no convertir el endpoint en un oráculo."""
        clearance = await self._repo.caller_clearance(actor)
        candidatos = await self._repo.list_candidatos_eliminacion(
            expediente_ids, max_clearance=clearance
        )
        por_id = {c["id"]: c for c in candidatos}

        admitidos: list[dict] = []
        rechazados: list[LoteEliminacionRechazo] = []
        ahora = datetime.now(tz=UTC)

        for exp_id in expediente_ids:
            fila = por_id.get(exp_id)
            if fila is None:
                rechazados.append(
                    LoteEliminacionRechazo(
                        expediente_id=exp_id,
                        expediente_code=None,
                        motivo="no_encontrado",
                        detalle=(
                            "No existe un expediente con ese identificador, o no "
                            "está a su alcance."
                        ),
                    )
                )
                continue
            snapshot = _leer_snapshot(fila["disposition"])
            code = snapshot.get("disposition_code")
            rechazo = _motivo_de_rechazo(fila, snapshot, ahora=ahora)
            if rechazo is not None:
                motivo, detalle = rechazo
                rechazados.append(
                    LoteEliminacionRechazo(
                        expediente_id=exp_id,
                        expediente_code=fila["code"],
                        motivo=motivo,
                        detalle=detalle,
                    )
                )
                continue
            worm = fila["worm_retain_until"]
            admitidos.append(
                {
                    "expediente_id": exp_id,
                    "expediente_code": fila["code"],
                    "expediente_nombre": fila["name"],
                    "trd_serie_code": fila["trd_serie_code"],
                    "disposition_code": code,
                    "fecha_vencimiento": fila["fecha_vencimiento"],
                    "fecha_extrema_inicial": fila["fecha_extrema_inicial"],
                    "fecha_extrema_final": fila["fecha_extrema_final"],
                    "nivel_seguridad": fila["nivel_seguridad"],
                    "radicados_incluidos": fila["radicados_incluidos"],
                    "worm_retain_until": None if _sin_worm(worm) else worm,
                }
            )

        if not admitidos:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "lote_sin_expedientes_admisibles",
                    "detail": (
                        "Ninguno de los expedientes indicados puede entrar en un "
                        "lote de eliminación. Revise el detalle de cada rechazo."
                    ),
                    "status": 409,
                    "rechazados": [r.model_dump(mode="json") for r in rechazados],
                },
            )

        async with self._conn.transaction():
            try:
                lote = await self._repo.crear_lote_eliminacion(
                    codigo=codigo, observaciones=observaciones, creado_por=actor
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "lote_codigo_duplicado",
                        "detail": f"Ya existe un lote de eliminación con el código '{codigo}'",
                        "status": 409,
                    },
                )
            for item in admitidos:
                await self._repo.add_item_lote_eliminacion(lote["id"], item)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="archive-service",
                action="archive.eliminacion_lote_propuesto",
                object_type="eliminacion_lote",
                object_ref=str(lote["id"]),
                actor=str(actor),
                payload={
                    "codigo": codigo,
                    "admitidos": len(admitidos),
                    "rechazados": len(rechazados),
                },
                canal=audit.CANAL_PETICION,
            )

        return LoteEliminacionResponse(
            id=lote["id"],
            codigo=lote["codigo"],
            estado=lote["estado"],
            observaciones=lote["observaciones"],
            creado_por=lote["creado_por"],
            creado_at=lote["creado_at"],
            total_items=len(admitidos),
            items=[LoteEliminacionItem(**a) for a in admitidos],
            rechazados=rechazados,
        )

    async def publicar_lote_eliminacion(
        self, lote_id: uuid.UUID, *, publicado_hasta: date, actor: uuid.UUID
    ) -> LoteEliminacionResponse:
        """`POST /eliminacion/lotes/{id}/publicar` (RF-TRD-07, fase 2 de 5).

        La **publicidad previa**: el inventario de lo que se va a eliminar se
        pone a la vista para que quien tenga algo que oponer pueda hacerlo
        ANTES, no después. Publicar **congela el inventario** —el trigger de la
        migración 047 deja de admitir que se quite una fila—, y con razón: lo
        publicado es sobre lo que un tercero decidió no oponerse, y cambiarlo
        después vaciaría de sentido el trámite.

        **El canal de oposición no se inventa aquí**: es la ventanilla única
        pública que la entidad ya tiene. Montar un formulario de oposición
        aparte crearía una segunda bandeja que nadie mira, y una oposición es
        exactamente lo que una ventanilla existe para recibir. La pantalla
        pública lo dice y enlaza.

        Sigue **sin eliminarse nada**."""
        # Transición y asiento son UN acto (AREA-06-01-B): `get_db_conn` no abre
        # transacción, así que sin este bloque el UPDATE se confirma solo y un
        # fallo del asiento deja un lote publicado sin traza. Los 409 se lanzan
        # FUERA: cuando no hubo fila no hay nada que revertir.
        async with self._conn.transaction():
            lote = await self._repo.publicar_lote_eliminacion(
                lote_id, publicado_hasta=publicado_hasta
            )
            if lote is not None:
                total = await self._repo.contar_items_lote(lote_id)
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service="archive-service",
                    action="archive.eliminacion_lote_publicado",
                    object_type="eliminacion_lote",
                    object_ref=str(lote_id),
                    actor=str(actor),
                    payload={
                        "codigo": lote["codigo"],
                        "publicado_hasta": str(publicado_hasta),
                        "total_items": total,
                    },
                    canal=audit.CANAL_PETICION,
                )
        if lote is None:
            # No se distingue «no existe» de «ya no está en borrador»: el
            # llamante que tiene el identificador ya sabe que existe, y lo que
            # necesita saber es que su acto NO se aplicó.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "lote_no_publicable",
                    "detail": (
                        "El lote no existe o ya no está en borrador: solo se "
                        "publica un lote que todavía no se publicó."
                    ),
                    "status": 409,
                },
            )
        return self._lote_a_respuesta(lote, total_items=total)

    async def avalar_lote_eliminacion(
        self,
        lote_id: uuid.UUID,
        *,
        acta_comite: str,
        fecha_aval: date,
        acto_administrativo: str,
        actor: uuid.UUID,
    ) -> LoteEliminacionResponse:
        """`POST /eliminacion/lotes/{id}/avalar` (RF-TRD-07, fase 3 de 5).

        El **aval del Comité Institucional de Gestión y Desempeño**: la
        decisión archivística que autoriza destruir (Ac. AGN 001/2024 art.
        4.3.2.5). El sistema no decide nada — registra que un Comité decidió,
        con qué acta y con qué acto administrativo, y sin las tres constancias
        no admite el aval.

        **No se avala con el plazo de publicidad todavía abierto.** Es la regla
        de fondo de esta fase: autorizar la destrucción antes de que venza el
        tiempo que la propia entidad dio para oponerse convierte la publicidad
        previa en un adorno. La guarda vive en el `WHERE` de la transición
        (`ArchiveRepository.avalar_lote_eliminacion`); aquí solo se COMPONE la
        explicación, con una lectura que no decide nada.

        Sigue **sin eliminarse nada**: avalar autoriza; la destrucción es la
        quinta fase (`ejecutar_lote_eliminacion`).

        **Separación de funciones**: quien propuso el lote no lo avala (409
        `separacion_de_funciones`). La guarda vive en el `WHERE` de la
        transición, no aquí."""
        async with self._conn.transaction():
            lote = await self._repo.avalar_lote_eliminacion(
                lote_id,
                acta_comite=acta_comite,
                fecha_aval=fecha_aval,
                acto_administrativo=acto_administrativo,
                avalado_por=actor,
            )
            if lote is not None:
                total = await self._repo.contar_items_lote(lote_id)
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service="archive-service",
                    action="archive.eliminacion_lote_avalado",
                    object_type="eliminacion_lote",
                    object_ref=str(lote_id),
                    actor=str(actor),
                    payload={
                        "codigo": lote["codigo"],
                        "acta_comite": acta_comite,
                        "fecha_aval": str(fecha_aval),
                        "acto_administrativo": acto_administrativo,
                        "total_items": total,
                    },
                    canal=audit.CANAL_PETICION,
                )
        if lote is None:
            raise await self._conflicto_de_lote(
                lote_id,
                accion="avalar",
                estado_exigido="publicado",
                error="lote_no_avalable",
                actor=actor,
            )
        return self._lote_a_respuesta(lote, total_items=total)

    async def anular_lote_eliminacion(
        self, lote_id: uuid.UUID, *, motivo: str, actor: uuid.UUID
    ) -> LoteEliminacionResponse:
        """`POST /eliminacion/lotes/{id}/anular` (RF-TRD-07, fase 3 de 5).

        Retira el lote del procedimiento. Se puede hasta el último momento
        —incluso ya avalado— pero **nunca después de ejecutarlo**: lo destruido
        no se des-destruye, y marcar «anulado» un lote ejecutado afirmaría lo
        contrario de lo que pasó.

        Si el lote llegó a publicarse **sigue siendo público tras anularse**,
        con su motivo: quien leyó que esos expedientes iban a eliminarse tiene
        que poder ver en qué quedó."""
        async with self._conn.transaction():
            lote = await self._repo.anular_lote_eliminacion(
                lote_id, motivo=motivo, anulado_por=actor
            )
            if lote is not None:
                total = await self._repo.contar_items_lote(lote_id)
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service="archive-service",
                    action="archive.eliminacion_lote_anulado",
                    object_type="eliminacion_lote",
                    object_ref=str(lote_id),
                    actor=str(actor),
                    payload={"codigo": lote["codigo"], "motivo": motivo, "total_items": total},
                    canal=audit.CANAL_PETICION,
                )
        if lote is None:
            raise await self._conflicto_de_lote(
                lote_id,
                accion="anular",
                estado_exigido=None,
                error="lote_no_anulable",
            )
        return self._lote_a_respuesta(lote, total_items=total)

    @staticmethod
    def _error_separacion(detalle: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "separacion_de_funciones",
                "detail": detalle,
                "status": 409,
            },
        )

    async def _denegar_separacion(
        self, lote_id: uuid.UUID, *, actor: uuid.UUID, accion: str
    ) -> None:
        """Deja constancia del intento de que una misma persona encadene dos
        actos de la destrucción. **Best-effort**: la denegación no puede
        degradar a 500 por un fallo del asiento."""
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="archive-service",
                action="archive.eliminacion_separacion_denegada",
                object_type="eliminacion_lote",
                object_ref=str(lote_id),
                actor=str(actor),
                payload={"accion": accion},
                canal=audit.CANAL_PETICION,
            )
        except Exception:  # noqa: BLE001 — la denegación no depende del asiento.
            logger.warning("audit.append(eliminacion_separacion_denegada) failed", exc_info=True)

    async def _conflicto_de_lote(
        self,
        lote_id: uuid.UUID,
        *,
        accion: str,
        estado_exigido: str | None,
        error: str,
        actor: uuid.UUID | None = None,
    ) -> HTTPException:
        """Compone el 409 de una transición que no se aplicó.

        La lectura es **de diagnóstico**, no una guarda: la transición ya se
        intentó y ya falló: lo único que se decide aquí es qué frase lee la
        persona. Y merece decidirse — «no se pudo avalar» manda a buscar un
        permiso cuando lo que pasa es que faltan cuatro días de plazo, que es
        el mismo defecto que ya se pagó aquí con un 403 que acusaba de faltar
        un permiso que el usuario tenía."""
        fila = await self._repo.get_lote_estado(lote_id)
        if fila is None:
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "lote_no_encontrado",
                    "detail": "No existe un lote de eliminación con ese identificador",
                    "status": 404,
                },
            )
        if accion == "avalar" and fila["publicidad_abierta"]:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "publicidad_en_curso",
                    "detail": (
                        "El plazo para oponerse sigue abierto hasta el "
                        f"{fila['publicado_hasta']}: el aval del Comité no se "
                        "registra antes de que venza."
                    ),
                    "status": 409,
                },
            )
        if accion == "avalar" and fila["estado"] == "publicado":
            # ADR-035: el plazo original venció pero la entidad estuvo no
            # operativa dentro de él (la ventanilla, canal de oposicion, daba
            # 503): el plazo efectivo se extiende por esos dias.
            plazo = await self._repo.plazo_oposicion_efectivo(lote_id)
            efectivo = plazo["plazo_efectivo_hasta"] if plazo is not None else None
            if plazo is not None and efectivo is not None and efectivo >= date.today():
                dias = (efectivo - plazo["publicado_hasta"]).days
                return HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "plazo_oposicion_interrumpido",
                        "detail": (
                            f"La entidad no estuvo operativa {dias} dia(s) dentro "
                            "del plazo para oponerse y la ventanilla estuvo cerrada: "
                            f"el plazo efectivo vence el {efectivo} y el aval no se "
                            "registra antes de que termine."
                        ),
                        "dias_suspendidos": dias,
                        "plazo_efectivo_hasta": str(efectivo),
                        "status": 409,
                    },
                )
        if (
            accion == "avalar"
            and actor is not None
            and fila["estado"] == "publicado"
            and fila["creado_por"] == actor
        ):
            # Plazo vencido y estado correcto: lo único que queda en el WHERE es
            # `creado_por <> avalador`. Se explica con su propio código (no con
            # «lote_no_avalable») para que quien lo recibe sepa que el remedio es
            # OTRA persona, no esperar ni cambiar el estado.
            await self._denegar_separacion(lote_id, actor=actor, accion="avalar")
            return self._error_separacion(
                "Quien propuso el lote no puede avalarlo: el aval del Comité lo "
                "registra otra persona."
            )
        exigido = (
            f" Se exige que esté en '{estado_exigido}'." if estado_exigido else ""
        )
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": error,
                "detail": (
                    f"No se puede {accion} un lote en estado '{fila['estado']}'.{exigido}"
                ),
                "status": 409,
            },
        )

    # ------------------------------------------------------------------
    # El ACTA de eliminación (RF-TRD-07, fase 4 de 5)
    # ------------------------------------------------------------------

    async def generar_acta_eliminacion(
        self,
        lote_id: uuid.UUID,
        *,
        metodo_borrado: str,
        observaciones: str | None,
        actor: uuid.UUID,
    ) -> ActaEliminacionResponse:
        """`POST /eliminacion/lotes/{id}/acta` (RF-TRD-07, fase 4 de 5).

        **El acta es lo que sobrevive a lo eliminado.** Cuando la fase 5 borre
        los bytes, el expediente dejará de existir y esta acta será —junto con
        la lápida y el `audit_log`— la única constancia de qué se destruyó,
        bajo qué acto y quién lo autorizó. Es de **conservación permanente**:
        no tiene calendario ni disposición final.

        Va **antes** del borrado y no después, y no es un detalle de orden: el
        inventario que el acta describe sale de un expediente que va a dejar de
        existir. Un acta redactada después no tendría de dónde leerlo.

        Se exige el lote **avalado**: sin la decisión del Comité no hay nada
        que documentar. Y **una sola vez** —`ON CONFLICT DO NOTHING`—: un
        segundo intento produciría otro documento, con otro instante y otro
        SHA, para una eliminación que ya tenía el suyo.

        `metodo_borrado` lo declara quien genera el acta y **no lo inventa el
        sistema** (Ac. AGN 001/2024 art. 4.3.2.5 exige decir cómo se destruye):
        una frase por defecto sería una afirmación que nadie hizo, sobre un
        acto irreversible.

        El sellado XAdES corre **después del commit** y es best-effort, igual
        que el del acta de transferencia: el acta ya quedó íntegra y su SHA
        anclado en `audit_log`, así que un signature-service caído retrasa la
        firma pero no pierde el documento."""
        lote = await self._repo.get_lote_estado(lote_id)
        if lote is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "lote_no_encontrado",
                    "detail": "No existe un lote de eliminación con ese identificador",
                    "status": 404,
                },
            )
        if lote["estado"] != "avalado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "lote_sin_aval",
                    "detail": (
                        f"El lote está en estado '{lote['estado']}': el acta de "
                        "eliminación documenta una decisión del Comité, y sin el "
                        "aval no hay nada que documentar."
                    ),
                    "status": 409,
                },
            )

        fila = await self._repo.get_lote_row(lote_id)
        items = await self._repo.list_items_lote_completo(lote_id)
        generado_at = datetime.now(UTC)
        # ADR-035: si el plazo de oposicion se extendio por una no-operatividad
        # de la entidad, el acta declara el plazo efectivo (no solo el original).
        plazo = await self._repo.plazo_oposicion_efectivo(lote_id)
        hasta_efectivo = plazo["plazo_efectivo_hasta"] if plazo is not None else None
        dias_interrumpido = 0
        if plazo is not None and hasta_efectivo is not None:
            dias_interrumpido = max(0, (hasta_efectivo - plazo["publicado_hasta"]).days)
        acta_xml = construir_acta_xml(
            plazo_efectivo_hasta=hasta_efectivo if dias_interrumpido else None,
            dias_interrumpido=dias_interrumpido,
            lote=fila,
            items=items,
            tenant_slug=self._tenant_slug,
            metodo_borrado=metodo_borrado,
            observaciones=observaciones,
            generado_por=str(actor),
            generado_ts=generado_at,
        )
        acta_sha = hashlib.sha256(acta_xml.encode("utf-8")).hexdigest()

        async with self._conn.transaction():
            creada = await self._repo.insert_acta_eliminacion(
                lote_id,
                acta_xml=acta_xml,
                acta_sha256=acta_sha,
                metodo_borrado=metodo_borrado,
                observaciones=observaciones,
                generado_por=actor,
                generado_at=generado_at,
            )
            if creada is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "acta_ya_existe",
                        "detail": (
                            "Este lote ya tiene su acta de eliminación. El acta es "
                            "única y de conservación permanente: no se regenera."
                        ),
                        "status": 409,
                    },
                )
            # En la MISMA tx: toda acta nace con su estado de firma, y ninguna
            # queda fuera del alcance de la reconciliación por escribirse a
            # medias.
            await self._repo.insert_acta_eliminacion_firma_pendiente(lote_id)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="archive-service",
                action="archive.eliminacion_acta_generada",
                object_type="eliminacion_acta",
                object_ref=str(lote_id),
                actor=str(actor),
                payload={
                    "codigo": fila["codigo"],
                    # El SHA va al asiento INDELEBLE: `eliminacion_acta` es
                    # append-only por trigger, pero un acta alterada saltándose
                    # el trigger seguiría delatándose contra este número.
                    "acta_sha256": acta_sha,
                    "total_items": len(items),
                    "metodo_borrado": metodo_borrado,
                },
                canal=audit.CANAL_PETICION,
            )

        await self._sellar_acta_eliminacion(lote_id, acta_xml=acta_xml, actor=actor)
        return await self.get_acta_eliminacion(lote_id, actor=actor)

    async def get_acta_eliminacion(
        self, lote_id: uuid.UUID, *, actor: uuid.UUID
    ) -> ActaEliminacionResponse:
        """`GET /eliminacion/lotes/{id}/acta`.

        **El acta se sirve entera o no se sirve.** Es un documento firmado y
        monolítico: recortarle filas rompería la firma y produciría algo que ya
        no es el acta. Así que lo que se acota es quién la lee, y el umbral se
        **deriva del contenido** —el nivel más alto del inventario—, nunca de
        un campo declarado.

        El 403 (y no 404) es deliberado aquí: quien pregunta ya podía ver el
        lote, así que no se le confirma nada que no supiera; lo que necesita es
        entender **por qué** no puede leerla, no creer que no existe."""
        acta = await self._repo.get_acta_eliminacion(lote_id)
        if acta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "acta_no_encontrada",
                    "detail": "Este lote todavía no tiene acta de eliminación",
                    "status": 404,
                },
            )
        clearance = await self._repo.caller_clearance(actor)
        nivel_acta = await self._repo.max_nivel_items_lote(lote_id)
        if nivel_acta > clearance:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "acta_sobre_su_clearance",
                    "detail": (
                        "El acta incluye expedientes con un nivel de clasificación "
                        "superior a su nivel de acceso, y no se puede servir "
                        "recortada sin romper su firma."
                    ),
                    "status": 403,
                },
            )
        return ActaEliminacionResponse(
            lote_id=lote_id,
            acta_xml=acta["acta_xml"],
            acta_sha256=acta["acta_sha256"],
            metodo_borrado=acta["metodo_borrado"],
            observaciones=acta["observaciones"],
            generado_por=acta["generado_por"],
            generado_at=acta["generado_at"],
            firma_estado=acta["firma_estado"] or "pendiente_firma",
            firma_id=acta["firma_id"],
            xades_level=acta["xades_level"],
            acreditado=bool(acta["acreditado"]),
            firmado_at=acta["firmado_at"],
            last_error=acta["last_error"],
            worm_protegido_at=acta["worm_protegido_at"],
            worm_retain_until=acta["worm_retain_until"],
        )

    async def firmar_acta_eliminacion_retry(
        self, lote_id: uuid.UUID, *, actor: uuid.UUID
    ) -> ActaEliminacionResponse:
        """`POST /eliminacion/lotes/{id}/acta/firmar` — reintento manual.

        No regenera nada: sella **el mismo XML** ya congelado. Existe porque un
        acta pendiente de firma con signature-service caído no puede quedarse
        esperando a que un job pase: alguien tiene que poder empujarla, y ver
        el motivo del último fallo mientras tanto."""
        acta = await self._repo.get_acta_eliminacion(lote_id)
        if acta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "acta_no_encontrada",
                    "detail": "Este lote todavía no tiene acta de eliminación",
                    "status": 404,
                },
            )
        if acta["firma_estado"] == "firmado":
            # No es un error: es el estado que se quería. Devolverla firmada
            # evita que un doble clic parezca un fallo. **Pero si le falta el
            # candado WORM, el reintento lo completa** (AREA-06-02): firmada y
            # sin proteger es justo el estado que bloquea `ejecutar`, y sin
            # esto el único desbloqueo manual era inalcanzable.
            if acta["worm_protegido_at"] is None:
                await self._proteger_worm_acta_eliminacion(lote_id, actor=actor)
            return await self.get_acta_eliminacion(lote_id, actor=actor)
        await self._sellar_acta_eliminacion(lote_id, acta_xml=acta["acta_xml"], actor=actor)
        return await self.get_acta_eliminacion(lote_id, actor=actor)

    async def _sellar_acta_eliminacion(
        self, lote_id: uuid.UUID, *, acta_xml: str, actor: uuid.UUID
    ) -> None:
        """Intento SÍNCRONO ACOTADO del sellado XAdES — gemelo de
        `TransferenciaService._sellar_acta_post_commit`, con sus mismos tres
        desenlaces:

        - éxito → `firmado` + asiento `archive.eliminacion_acta_firmada`;
        - `seal_absent` → degrada **sin reintentar**, porque firmar es
          estructuralmente imposible ahora mismo y repetir solo gasta tiempo;
        - `transient` agotado → degrada y queda para el reintento manual.

        **Siempre fuera de una transacción**: el sellado es HTTP y una llamada
        remota dentro de una tx la mantiene abierta a merced de un tercero.

        El XML **nunca se altera aquí**: el sello cubre exactamente la cadena
        ya anclada, y el XML firmado es un artefacto derivado en MinIO."""
        from app.clients import signature_client

        max_attempts = max(1, settings.acta_sign_max_sync_attempts)
        for intento in range(1, max_attempts + 1):
            outcome = await signature_client.sign_xml_outcome(
                tenant_slug=self._tenant_slug,
                actor_id=str(actor),
                payload_xml=acta_xml,
                objeto_tipo="acta_eliminacion",
                objeto_id=str(lote_id),
                indice_id=None,
                version=1,
            )
            if outcome.signed is not None:
                if not outcome.signed.minio_key_firmado:
                    # Firma sin artefacto: el CHECK de la migración 048 exige
                    # `minio_key_firmado` para admitir 'firmado', y con razón —
                    # un acta «firmada» cuyo XML firmado no está en ninguna
                    # parte no se puede verificar nunca más. Se trata como
                    # fallo del sellado, con su motivo, en vez de dejar que la
                    # base lo rechace con un error opaco. Lo destapó mypy: el
                    # tipo decía `str | None` y el código lo daba por presente.
                    await self._degradar_acta_eliminacion(
                        lote_id, actor=actor, motivo="sellado_sin_artefacto"
                    )
                    return
                async with self._conn.transaction():
                    afectadas = await self._repo.mark_acta_eliminacion_firmada(
                        lote_id,
                        firma_id=outcome.signed.firma_id,
                        minio_key=outcome.signed.minio_key_firmado,
                        xades_level=outcome.signed.xades_level,
                        acreditado=outcome.signed.acreditado,
                    )
                    # Cero filas = otro proceso ya la marcó firmada entre medias;
                    # el sello recién obtenido queda huérfano y NO se escribe el
                    # asiento fantasma.
                    if afectadas:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant_slug,
                            service="archive-service",
                            action="archive.eliminacion_acta_firmada",
                            object_type="eliminacion_acta",
                            object_ref=str(lote_id),
                            actor=str(actor),
                            payload={
                                "firma_id": str(outcome.signed.firma_id),
                                "xades_level": outcome.signed.xades_level,
                                "acreditado": outcome.signed.acreditado,
                            },
                            canal=audit.CANAL_PETICION,
                        )
                # Cableado WORM del acta recién firmada, FUERA de la tx que
                # acaba de comprometerse (mismo motivo que el sellado corre
                # fuera de la tx que congeló el acta). Si `afectadas` es 0
                # —carrera perdida— no se dispara: quien ganó ya lo hará.
                if afectadas:
                    await self._proteger_worm_acta_eliminacion(lote_id, actor=actor)
                return
            if outcome.seal_absent:
                await self._degradar_acta_eliminacion(
                    lote_id, actor=actor, motivo="sello_ausente"
                )
                return
            if intento < max_attempts:
                await asyncio.sleep(settings.acta_sign_sync_delay_seconds)

        await self._degradar_acta_eliminacion(
            lote_id, actor=actor, motivo="sellado_xades_no_obtenido"
        )

    async def _proteger_worm_acta_eliminacion(
        self, lote_id: uuid.UUID, *, actor: uuid.UUID
    ) -> Literal["protegido", "pendiente", "agotado", "omitido"]:
        """Object-Lock del acta de eliminación ya firmada (E10 + RF-TRD-07).

        Cierra la deuda que la fase 4 dejó declarada: el acta tenía sus tres
        garantías locales —trigger append-only, SHA en `audit_log` y firma
        XAdES— y no tenía candado. La razón era de MODELO: el almacén WORM
        asumía que todo artefacto protegido pertenece a un expediente, y un
        acta de eliminación cubre N expedientes que además van a dejar de
        existir. Ahora el almacén admite un propietario de tipo `lote`.

        **Conservación permanente se materializa como 100 años**, que es el
        máximo que el contrato admite: Object-Lock exige una fecha, no un
        infinito. No es una retención archivística —el acta no se elimina
        jamás—, es la forma de decirle al almacén «no dejes borrar esto»; y es
        renovable por la misma vía que ya usan las series de Conservación
        Total.

        **Best-effort y NUNCA lanza**, igual que su gemelo del acta de
        transferencia: la firma ya es un hecho consumado y este cableado no
        puede revertirla. Un fallo queda anotado con su motivo y **programa su
        reintento** (AREA-06-02): backoff exponencial y, agotado el umbral,
        `worm_reconcile_agotado` con su asiento. Lo consume
        `run_once_actas_eliminacion_worm` (`app/jobs/indice_reconciliation.py`),
        y `ejecutar_lote_eliminacion` exige el candado antes de destruir.

        Devuelve el desenlace para el resumen del job; el disparo síncrono lo
        ignora."""
        from app.clients import storage_client

        acta = None
        try:
            acta = await self._repo.get_acta_eliminacion(lote_id)
            if acta is None or acta["firma_estado"] != "firmado":
                return "omitido"
            if acta["worm_protegido_at"] is not None:
                return "omitido"
            if not acta["minio_key_firmado"]:
                return "omitido"
            # `file_id` se DERIVA de la clave del artefacto firmado
            # (`"{file_id}/{filename}"`), nunca de una entrada externa — misma
            # frontera de confianza que el acta de transferencia.
            file_id = acta["minio_key_firmado"].split("/", 1)[0]

            outcome = await storage_client.proteger_artefacto_worm(
                tenant_slug=self._tenant_slug,
                actor_id=str(actor),
                file_id=file_id,
                expediente_id=str(lote_id),
                tipo="acta_eliminacion",
                retencion_anios=_ACTA_ELIMINACION_RETENCION_ANIOS,
                fecha_inicio=date.today(),
                objeto_tipo="lote",
            )
            if not outcome.ok:
                return await self._bump_worm_backoff_acta_eliminacion(
                    lote_id,
                    intentos_previos=int(acta["worm_reconcile_intentos"] or 0),
                    motivo="worm_no_obtenido",
                )

            async with self._conn.transaction():
                afectadas = await self._repo.mark_acta_eliminacion_worm(
                    lote_id,
                    worm_object_id=outcome.protected_object_id,
                    worm_retain_until=outcome.retain_until,
                )
                if afectadas:
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service="archive-service",
                        action="archive.eliminacion_acta_worm_protegida",
                        object_type="eliminacion_acta",
                        object_ref=str(lote_id),
                        actor=str(actor),
                        payload={
                            "worm_object_id": str(outcome.protected_object_id),
                            "retain_until": (
                                outcome.retain_until.isoformat()
                                if outcome.retain_until
                                else None
                            ),
                        },
                        canal=audit.CANAL_PETICION,
                    )
            return "protegido"
        except Exception:  # noqa: BLE001 — best-effort, ver el docstring.
            logger.warning(
                "No se pudo cablear el WORM del acta de eliminación del lote %s",
                lote_id,
                exc_info=True,
            )
            if acta is None:
                return "pendiente"
            try:
                return await self._bump_worm_backoff_acta_eliminacion(
                    lote_id,
                    intentos_previos=int(acta["worm_reconcile_intentos"] or 0),
                    motivo="worm_excepcion",
                )
            except Exception:  # noqa: BLE001 — nunca degrada el resultado.
                logger.warning(
                    "No se pudo programar el reintento WORM del acta del lote %s",
                    lote_id,
                    exc_info=True,
                )
                return "pendiente"

    async def _bump_worm_backoff_acta_eliminacion(
        self, lote_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        """Registra un intento fallido del WORM del acta de eliminación con
        backoff exponencial -- gemelo de
        `TransferenciaService._bump_worm_backoff_acta` (mismo umbral y misma
        fórmula, que viven en `settings` y `app/services/backoff.py`)."""
        intentos_tras_este = intentos_previos + 1
        agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
        proximo_at = (
            None
            if agotado
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos_tras_este,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        await self._repo.mark_acta_eliminacion_worm_fallido(
            lote_id, motivo=motivo, proximo_at=proximo_at, agotado=agotado
        )
        if not agotado:
            return "pendiente"
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.eliminacion_acta_worm_reconcile_agotado",
                object_type="eliminacion_acta",
                object_ref=str(lote_id),
                actor=str(SYSTEM_RECONCILER_ID),
                canal="system",
                payload={"reconcile_intentos": intentos_tras_este, "motivo": motivo},
            )
        except Exception:  # noqa: BLE001 — la visibilidad no degrada el resultado.
            logger.warning(
                "audit.append(archive.eliminacion_acta_worm_reconcile_agotado) failed",
                exc_info=True,
            )
        return "agotado"

    async def _degradar_acta_eliminacion(
        self, lote_id: uuid.UUID, *, actor: uuid.UUID, motivo: str
    ) -> None:
        """Marca el fallo y su asiento como UN acto: si el asiento se escribiera
        aparte, un fallo entre ambos dejaría el estado sin explicación."""
        async with self._conn.transaction():
            await self._repo.mark_acta_eliminacion_pendiente(
                lote_id, last_error=motivo, proximo_at=None
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="archive-service",
                action="archive.eliminacion_acta_firma_pendiente",
                object_type="eliminacion_acta",
                object_ref=str(lote_id),
                actor=str(actor),
                payload={"motivo": motivo},
                canal=audit.CANAL_PETICION,
            )

    # ------------------------------------------------------------------
    # La EJECUCIÓN (RF-TRD-07, fase 5 de 5) -- aquí SÍ se destruye
    # ------------------------------------------------------------------

    async def ejecutar_lote_eliminacion(
        self, lote_id: uuid.UUID, *, actor: uuid.UUID
    ) -> EjecucionLoteResponse:
        """`POST /eliminacion/lotes/{id}/ejecutar` (RF-TRD-07, fase 5 de 5).

        **Esto destruye.** Es el único camino del sistema que borra documentos
        de archivo, y llega al final de un procedimiento de cinco actos:
        propuesta con cinco condiciones de admisión, publicidad previa con plazo
        para oponerse, aval del Comité con su acto administrativo, y acta
        firmada de conservación permanente.

        **Seis precondiciones, y ninguna es un formalismo**: lote `avalado`,
        **ejecutor distinto del avalador** (409 `separacion_de_funciones`:
        quien autoriza no destruye), acta existente, acta **firmada**, acta
        **bajo WORM** (409 `acta_sin_worm`), y el inventario congelado como
        única fuente de qué se destruye. Todas se comprueban ANTES de destruir
        el primer expediente: una guarda tras el bucle llegaría tarde. Exigir la firma antes de destruir puede
        bloquear la ejecución si signature-service está caído — y es
        deliberado: el último acto irreversible es exactamente el momento de no
        seguir adelante con una constancia a medias. Hay reintento manual para
        desbloquearlo.

        **Expediente por expediente, y todo-o-nada dentro de cada uno.** Si la
        purga de un expediente falla, ese expediente NO recibe lápida y NO se
        borra; los demás siguen. Un fallo parcial silencioso sería lo peor
        posible: una lápida sobre bytes vivos afirma para siempre una
        destrucción que no ocurrió.

        **La lápida se escribe ANTES de borrar el expediente**, en la misma
        transacción. Al revés, un fallo entre ambos dejaría el expediente
        destruido y sin lápida: la única respuesta a «¿qué pasó con
        2015-DEM-000123?» sería el silencio, indistinguible de «nunca existió».
        """
        estado = await self._repo.get_lote_estado(lote_id)
        if estado is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "lote_no_encontrado",
                    "detail": "No existe un lote de eliminación con ese identificador",
                    "status": 404,
                },
            )
        if estado["estado"] != "avalado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "lote_no_ejecutable",
                    "detail": (
                        f"El lote está en estado '{estado['estado']}': solo se ejecuta "
                        "un lote avalado por el Comité."
                    ),
                    "status": 409,
                },
            )
        if estado["avalado_por"] is not None and estado["avalado_por"] == actor:
            # La destrucción la ejecuta alguien distinto de quien la autorizó.
            # Va ANTES de tocar nada: lo destruido no se des-destruye.
            await self._denegar_separacion(lote_id, actor=actor, accion="ejecutar")
            raise self._error_separacion(
                "Quien avaló el lote no puede ejecutarlo: la destrucción la "
                "ejecuta otra persona."
            )
        acta = await self._repo.get_acta_eliminacion(lote_id)
        if acta is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "acta_ausente",
                    "detail": (
                        "No se destruye sin acta: el acta es lo único que quedará "
                        "diciendo qué se eliminó y bajo qué acto."
                    ),
                    "status": 409,
                },
            )
        if acta["firma_estado"] != "firmado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "acta_sin_firmar",
                    "detail": (
                        "El acta todavía no está firmada"
                        + (f" ({acta['last_error']})" if acta["last_error"] else "")
                        + ". Reintente el sellado antes de ejecutar: el último acto "
                        "irreversible no se hace con una constancia a medias."
                    ),
                    "status": 409,
                },
            )

        if acta["worm_protegido_at"] is None:
            # El acta es lo único que sobrevive a lo eliminado: sin candado, el
            # objeto firmado podría borrarse del almacén justo después de que
            # se destruya aquello que describe. El cableado WORM es best-effort
            # y puede haber fallado; la reconciliación o el reintento manual
            # (`/acta/firmar`) lo completan.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "acta_sin_worm",
                    "detail": (
                        "El acta está firmada pero todavía no está protegida bajo "
                        "retención (WORM)"
                        + (
                            f" ({acta['worm_last_error']})"
                            if acta["worm_last_error"]
                            else ""
                        )
                        + ". Reintente el sellado antes de ejecutar: no se destruye "
                        "lo que el acta describe mientras el acta puede borrarse."
                    ),
                    "status": 409,
                },
            )

        lote = await self._repo.get_lote_row(lote_id)
        items = await self._repo.list_items_lote_completo(lote_id)
        eliminados: list[uuid.UUID] = []
        fallidos: list[EjecucionFallo] = []
        total_radicados = 0
        total_anexos = 0

        for item in items:
            expediente_id = item["expediente_id"]
            try:
                recuento = await self._eliminar_expediente(
                    expediente_id, item=item, lote=lote, acta=acta, actor=actor
                )
            except (DocumentPurgaError, DocumentClientError) as exc:
                # El expediente queda INTACTO y sin lápida. Se informa con su
                # código, no solo con su UUID: quien tiene que reintentar
                # necesita saber cuál es.
                fallidos.append(
                    EjecucionFallo(
                        expediente_id=expediente_id,
                        expediente_code=item["expediente_code"],
                        detalle=str(exc),
                    )
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service="archive-service",
                    action="archive.eliminacion_expediente_fallida",
                    object_type="expediente",
                    # El CÓDIGO, no el identificador. Aquí importa el doble: tras el
                    # borrado la fila del expediente ya no existe, así que su UUID no
                    # lo puede resolver nadie nunca más — y el asiento de una
                    # destrucción es justo el que hay que poder encontrar.
                    object_ref=item["expediente_code"],
                    actor=str(actor),
                    payload={
                        "lote": lote["codigo"],
                        "expediente_code": item["expediente_code"],
                        "motivo": str(exc)[:500],
                    },
                    canal=audit.CANAL_PETICION,
                )
                continue
            eliminados.append(expediente_id)
            total_radicados += recuento["radicados_eliminados"]
            total_anexos += recuento["anexos_eliminados"]

        # El lote solo pasa a `ejecutado` si NO quedó ninguno pendiente: un lote
        # marcado ejecutado con expedientes vivos dentro mentiría sobre lo único
        # que este estado significa.
        ejecutado = False
        if not fallidos:
            # Transición y asiento son UN acto (AREA-06-01-B).
            async with self._conn.transaction():
                fila = await self._repo.marcar_lote_ejecutado(lote_id, ejecutado_por=actor)
                ejecutado = fila is not None
                if ejecutado:
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service="archive-service",
                        action="archive.eliminacion_lote_ejecutado",
                        object_type="eliminacion_lote",
                        object_ref=str(lote_id),
                        actor=str(actor),
                        payload={
                            "codigo": lote["codigo"],
                            "expedientes_eliminados": len(eliminados),
                            "radicados_eliminados": total_radicados,
                            "anexos_eliminados": total_anexos,
                            "acta_sha256": acta["acta_sha256"],
                        },
                        canal=audit.CANAL_PETICION,
                    )

        return EjecucionLoteResponse(
            lote_id=lote_id,
            estado="ejecutado" if ejecutado else lote["estado"],
            expedientes_eliminados=len(eliminados),
            radicados_eliminados=total_radicados,
            anexos_eliminados=total_anexos,
            fallidos=fallidos,
        )

    async def _eliminar_expediente(
        self,
        expediente_id: uuid.UUID,
        *,
        item: Any,
        lote: Any,
        acta: Any,
        actor: uuid.UUID,
    ) -> dict[str, int]:
        """Destruye UN expediente: primero sus documentos, después él mismo.

        El orden no es negociable. Si se borrara el expediente primero, sus
        vínculos a radicados caerían en cascada y **ya no habría forma de saber
        qué documentos había que destruir**: quedarían vivos para siempre y sin
        nadie que supiera que debían morir. Es el mismo razonamiento que dentro
        de document-service pone los bytes antes que las filas.
        """
        radicados = await self._repo.list_radicados_de_expediente(expediente_id)
        motivo = f"Eliminación TRD, lote {lote['codigo']}, {lote['acto_administrativo']}"
        recuento = await document_client.purgar_radicados_internal(
            tenant_slug=self._tenant_slug,
            radicado_ids=radicados,
            expediente_id=expediente_id,
            motivo=motivo,
            actor=actor,
        )

        async with self._conn.transaction():
            # La lápida ANTES del borrado y en la misma transacción: al revés,
            # un fallo entre ambos dejaría el expediente destruido y sin
            # lápida, y el silencio es indistinguible de «nunca existió».
            await self._repo.insert_lapida(
                {
                    "expediente_id": expediente_id,
                    "expediente_code": item["expediente_code"],
                    "trd_serie_code": item["trd_serie_code"],
                    "fecha_extrema_inicial": item["fecha_extrema_inicial"],
                    "fecha_extrema_final": item["fecha_extrema_final"],
                    "radicados_eliminados": recuento["radicados_eliminados"],
                    "anexos_eliminados": recuento["anexos_eliminados"],
                    "lote_id": lote["id"],
                    "lote_codigo": lote["codigo"],
                    "acta_comite": lote["acta_comite"],
                    "acto_administrativo": lote["acto_administrativo"],
                    "acta_sha256": acta["acta_sha256"],
                    "eliminado_por": actor,
                }
            )
            await self._repo.delete_expediente(expediente_id)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="archive-service",
                action="archive.expediente_eliminado_trd",
                object_type="expediente",
                # El CÓDIGO, no el identificador. Aquí importa el doble: tras el
                # borrado la fila del expediente ya no existe, así que su UUID no
                # lo puede resolver nadie nunca más — y el asiento de una
                # destrucción es justo el que hay que poder encontrar.
                object_ref=item["expediente_code"],
                actor=str(actor),
                payload={
                    "expediente_code": item["expediente_code"],
                    "lote": lote["codigo"],
                    "acto_administrativo": lote["acto_administrativo"],
                    "acta_sha256": acta["acta_sha256"],
                    **recuento,
                },
                canal=audit.CANAL_PETICION,
            )
        return recuento

    async def list_lapidas_de_lote(self, lote_id: uuid.UUID) -> list[LapidaResponse]:
        """Las lápidas de un lote ejecutado. **Sin recortar por clearance**: una
        lápida es content-free por construcción —código, serie, fechas extremas,
        volumen y el acto que lo autorizó— y no hay nada que proteger en ella.
        Recortarla haría desaparecer la constancia justo de los expedientes cuya
        destrucción más importa poder acreditar."""
        return [
            LapidaResponse(**dict(r)) for r in await self._repo.list_lapidas_de_lote(lote_id)
        ]

    async def list_lotes_publicos(self) -> list[LoteEliminacionPublico]:
        """Inventarios publicados, para cualquiera y sin autenticar."""
        rows = await self._repo.list_lotes_publicos()
        return [
            LoteEliminacionPublico(
                id=r["id"],
                codigo=r["codigo"],
                estado=r["estado"],
                publicado_at=r["publicado_at"],
                publicado_hasta=r["publicado_hasta"],
                fecha_aval=r["fecha_aval"],
                acto_administrativo=r["acto_administrativo"],
                ejecutado_at=r["ejecutado_at"],
                total_items=r["total_items"],
            )
            for r in rows
        ]

    async def get_lote_publico(self, lote_id: uuid.UUID) -> LoteEliminacionPublico:
        """Un inventario publicado con su detalle. El recorte de la descripción
        de los clasificados va en el `SELECT`, no aquí — ver
        `ArchiveRepository.get_lote_publico`."""
        lote, items = await self._repo.get_lote_publico(lote_id)
        if lote is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "lote_no_encontrado",
                    "detail": "No hay ningún inventario publicado con ese identificador",
                    "status": 404,
                },
            )
        return LoteEliminacionPublico(
            id=lote["id"],
            codigo=lote["codigo"],
            estado=lote["estado"],
            publicado_at=lote["publicado_at"],
            publicado_hasta=lote["publicado_hasta"],
            fecha_aval=lote["fecha_aval"],
            acto_administrativo=lote["acto_administrativo"],
            ejecutado_at=lote["ejecutado_at"],
            observaciones=lote["observaciones"],
            anulado_motivo=lote["anulado_motivo"],
            total_items=len(items),
            items=[LoteEliminacionItemPublico(**dict(i)) for i in items],
        )

    async def list_lotes_eliminacion(
        self, *, estado: str | None, page: int, size: int
    ) -> tuple[list[LoteEliminacionResponse], int]:
        """Listado de lotes, sin inventario. No se recorta por clearance: un
        lote **no es un expediente** — su código, su estado y su acta no dicen
        nada del archivo. Lo que se recorta es su inventario, al abrirlo."""
        offset = (page - 1) * size
        rows, total = await self._repo.list_lotes_eliminacion(
            estado=estado, offset=offset, limit=size
        )
        return [self._lote_a_respuesta(r, total_items=r["total_items"]) for r in rows], total

    async def get_lote_eliminacion(
        self, lote_id: uuid.UUID, *, actor: uuid.UUID
    ) -> LoteEliminacionResponse:
        """El lote con su inventario, **recortado por el clearance de quien
        mira** (RF-SEG-08).

        `total_items` sale de la BD y NO de contar lo devuelto: si se
        recalculara, la resta entre lo que dice el lote y lo que se ve
        delataría cuántos expedientes clasificados contiene. Es el mismo
        cuidado que el recuento de la cola de anulaciones, aquí en su
        variante de detalle."""
        clearance = await self._repo.caller_clearance(actor)
        lote, items = await self._repo.get_lote_eliminacion(lote_id, max_clearance=clearance)
        if lote is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "lote_no_encontrado",
                    "detail": "No existe un lote de eliminación con ese identificador",
                    "status": 404,
                },
            )
        total = await self._repo.contar_items_lote(lote_id)
        return self._lote_a_respuesta(
            lote,
            total_items=total,
            items=[
                LoteEliminacionItem(
                    expediente_id=i["expediente_id"],
                    expediente_code=i["expediente_code"],
                    expediente_nombre=i["expediente_nombre"],
                    trd_serie_code=i["trd_serie_code"],
                    disposition_code=i["disposition_code"],
                    fecha_vencimiento=i["fecha_vencimiento"],
                    fecha_extrema_inicial=i["fecha_extrema_inicial"],
                    fecha_extrema_final=i["fecha_extrema_final"],
                    nivel_seguridad=i["nivel_seguridad"],
                    radicados_incluidos=i["radicados_incluidos"],
                    worm_retain_until=i["worm_retain_until"],
                )
                for i in items
            ],
        )

    @staticmethod
    def _lote_a_respuesta(
        row: Any, *, total_items: int, items: list[LoteEliminacionItem] | None = None
    ) -> LoteEliminacionResponse:
        return LoteEliminacionResponse(
            id=row["id"],
            codigo=row["codigo"],
            estado=row["estado"],
            observaciones=row["observaciones"],
            creado_por=row["creado_por"],
            creado_at=row["creado_at"],
            publicado_at=row["publicado_at"],
            publicado_hasta=row["publicado_hasta"],
            acta_comite=row["acta_comite"],
            fecha_aval=row["fecha_aval"],
            acto_administrativo=row["acto_administrativo"],
            ejecutado_at=row["ejecutado_at"],
            anulado_motivo=row["anulado_motivo"],
            avalado_por=row["avalado_por"] if "avalado_por" in row.keys() else None,
            total_items=total_items,
            items=items or [],
        )

    async def decidir_disposicion(
        self,
        expediente_id: uuid.UUID,
        *,
        fase: str,
        decision: str,
        motivo: str | None,
        revisar_de_nuevo_el: date | None,
        actor: uuid.UUID,
    ) -> ExpedienteDisposicionDecisionResponse:
        """`POST /trd/disposicion-vencida/{id}/decidir` (RF-TRD-06/08).

        **«Ninguna destrucción es automática sin confirmación de usuario
        autorizado»** (RT 04, regla 4). Esta es esa confirmación — y sigue sin
        destruir nada: el brazo ejecutor (RF-TRD-07: borrado seguro con acta,
        aval del Comité y publicidad previa del inventario) no existe todavía.
        Confirmar es su **precondición**, no su disparo, y la respuesta lo dice
        para que nadie crea que ya ocurrió.

        Tres cosas que no se toman del cliente, y por el mismo motivo:

        * La **fecha del vencimiento** y el **código de disposición** salen del
          snapshot congelado en la base. Si viajaran en el cuerpo, confirmar
          una eliminación sería cuestión de mandar la fecha adecuada.
        * El **clearance** recorta el expediente antes de nada (RF-SEG-08), y
          un expediente fuera de alcance responde `404` como si no existiera:
          un `403` confirmaría que existe.
        * Si la acción de la TRD termina en destrucción, el **snapshot tiene
          que gobernarla**: se llama a `assert_disposicion_final_autorizada`
          (ADR-025 D5) — que hasta hoy estaba escrito y **sin ningún
          llamante**. Confirmar una eliminación que el gate va a rechazar
          después sería dejar que el funcionario descubra el límite al final
          del procedimiento, cuando ya movilizó al Comité.

        Sobre `CT` el gate **no** aplica: conservación total no termina en
        destrucción, y exigir un instrumento convalidado para confirmar que
        algo se conserva sería un requisito inventado."""
        clearance = await self._repo.caller_clearance(actor)
        fila = await self._repo.get_expediente_vencimiento(
            expediente_id, fase=fase, max_clearance=clearance
        )
        if fila is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "vencimiento_no_encontrado",
                    "detail": (
                        "No hay un expediente con ese identificador y un "
                        "vencimiento de esa fase a su alcance."
                    ),
                    "status": 404,
                },
            )
        snapshot = _leer_snapshot(fila["disposition"])
        code = snapshot.get("disposition_code")

        if decision == "confirmada" and code in _DISPOSICIONES_QUE_DESTRUYEN:
            assert_disposicion_final_autorizada(
                serie_estado=str(snapshot.get("serie_estado") or ""),
                acto_administrativo=snapshot.get("acto_administrativo"),
                code=snapshot.get("code"),
                serie_version=snapshot.get("serie_version"),
            )

        async with self._conn.transaction():
            row = await self._repo.registrar_decision_disposicion(
                expediente_id=expediente_id,
                fase=fase,
                decision=decision,
                fecha_vencimiento=fila["fecha_vencimiento"],
                disposition_code=code,
                motivo=motivo,
                revisar_de_nuevo_el=revisar_de_nuevo_el,
                decidido_por=actor,
            )
            # El asiento es EL REGISTRO DEL ACTO, no un acompañamiento: si la
            # cadena inmutable no puede recibirlo, la decisión no se toma
            # (mismo criterio que el descarte de un correo y que la anulación).
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="archive-service",
                action="archive.disposicion_decidida",
                object_type="expediente",
                # El CÓDIGO, no el identificador (ver `IndexService._ref_de_expediente`).
                object_ref=fila["code"],
                actor=str(actor),
                payload={
                    "fase": fase,
                    "decision": decision,
                    "fecha_vencimiento": str(fila["fecha_vencimiento"]),
                    "disposition_code": code,
                    "motivo": motivo,
                    "revisar_de_nuevo_el": (
                        str(revisar_de_nuevo_el) if revisar_de_nuevo_el else None
                    ),
                },
                canal=audit.CANAL_PETICION,
            )
        return ExpedienteDisposicionDecisionResponse(
            id=row["id"],
            expediente_id=expediente_id,
            fase=fase,
            decision=decision,
            fecha_vencimiento=row["fecha_vencimiento"],
            disposition_code=code,
            motivo=motivo,
            revisar_de_nuevo_el=revisar_de_nuevo_el,
            decidido_por=actor,
            decidido_at=row["decidido_at"],
            ejecutada=False,
            nota=(
                "Decisión registrada. El sistema NO ha eliminado ni transferido "
                "nada: la ejecución de la disposición final exige acta, aval del "
                "Comité y publicidad previa del inventario, y todavía no está "
                "construida."
            ),
        )

    def _fila_disposicion_vencida(
        self, row: asyncpg.Record, *, fase: str
    ) -> ExpedienteDisposicionVencidaResponse:
        """Compone una fila de la cola desde el snapshot congelado."""
        # `disposition` llega como texto JSON desde asyncpg (la columna es
        # jsonb y este servicio no registra codec): se decodifica aquí, en un
        # solo sitio, con el mismo cuidado que el resto del servicio -- un
        # snapshot ilegible NO tumba el informe entero, deja la fila con lo que
        # se pudo leer y sin afirmar nada sobre la eliminación.
        snapshot = _leer_snapshot(row["disposition"])

        serie_estado = snapshot.get("serie_estado")
        acto = snapshot.get("acto_administrativo")
        code = snapshot.get("disposition_code")
        gobierna = bool(serie_estado) and disposicion_final_autorizada(
            serie_estado=str(serie_estado), acto_administrativo=acto
        )
        return ExpedienteDisposicionVencidaResponse(
            id=row["id"],
            code=row["code"],
            status=row["status"],
            nivel_seguridad=row["nivel_seguridad"],
            trd_serie_code=row["trd_serie_code"],
            closed_at=row["closed_at"],
            fase=fase,
            fecha_vencimiento=row["fecha_vencimiento"],
            dias_vencido=row["dias_vencido"],
            disposition_code=code,
            disposition_label=snapshot.get("disposition_label"),
            serie_estado=serie_estado,
            acto_administrativo=acto,
            gobierna_eliminacion=gobierna,
            accion=_accion_de_disposicion(code, fase=fase),
            decision=row["decision"],
            decision_motivo=row["decision_motivo"],
            revisar_de_nuevo_el=row["revisar_de_nuevo_el"],
            decidido_por=row["decidido_por"],
            decidido_at=row["decidido_at"],
        )

    async def list_expedientes_revision_pendiente(
        self,
        *,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
        tipo: str | None = None,
    ) -> tuple[list[ExpedienteRevisionPendienteResponse], int]:
        """`GET /trd/revision-pendiente` (ADR-025, Increment 2): informe de
        expedientes marcados `trd_revision_requerida` (D4/D6) -- paginado,
        mismo patrón que `list_trd_series`.

        Hallazgo de seguridad [Alta, BLOQUEANTE], migración 038: este informe
        NO filtraba por clearance -- `USUA_PERM_TRD` es ORTOGONAL al
        clearance (potestad sobre el instrumento no es nivel para leer los
        expedientes que gobierna), así que cualquier portador enumeraba
        RESERVADA/CLASIFICADA, agravado porque el backfill de la 037 marcó
        masivamente todo el archivo cerrado histórico. No-read-up con el
        MISMO criterio que `list_expedientes` -- ver esa docstring y la de
        `ArchiveRepository.list_expedientes_revision_pendiente`. También deja
        traza AGREGADA de consulta (RF-BUS-10), gemela de `list_expedientes`:
        antes este listado de expedientes clasificados no dejaba rastro de
        quién lo consultó."""
        offset = (page - 1) * size
        clearance = await self._repo.caller_clearance(user_id)
        rows, total = await self._repo.list_expedientes_revision_pendiente(
            offset=offset, limit=size, max_clearance=clearance, tipo=tipo
        )
        await self._audit_search_access(
            action="archive.expediente_revision_pendiente_listado",
            filtros={},
            total=total,
            page=page,
            size=size,
            user_id=user_id,
            canal=canal,
        )
        items = [
            ExpedienteRevisionPendienteResponse(
                id=r["id"],
                code=r["code"],
                status=r["status"],
                trd_serie_id=r["trd_serie_id"],
                trd_serie_code=r["trd_serie_code"],
                trd_revision_motivo=r["trd_revision_motivo"],
                closed_at=r["closed_at"],
                tipo_instrumento=r["tipo_instrumento"],
            )
            for r in rows
        ]
        return items, total


# ------------------------------------------------------------------
# Response converters
# ------------------------------------------------------------------


def _to_expediente_response(exp: Expediente) -> ExpedienteResponse:
    return ExpedienteResponse(
        id=exp.id,
        code=exp.code,
        name=exp.name,
        description=exp.description,
        trd_serie_id=exp.trd_serie_id,
        status=exp.status,
        opened_by=exp.opened_by,
        opened_at=exp.opened_at,
        closed_at=exp.closed_at,
        updated_at=exp.updated_at,
        metadata=exp.metadata,
        disposition=exp.disposition,
        # "Documentos vigentes" del expediente: `exp.radicados` trae
        # vinculados Y excluidos (soft-delete, E15 perfil v3) porque el
        # índice electrónico necesita ver ambos — un excluido no debe
        # colarse en esta vista de conjunto ACTIVO (ver
        # `ArchiveRepository._get_radicados`).
        radicados=[_to_radicado_response(r) for r in exp.radicados if r.estado == "vinculado"],
        trd_serie_code=exp.trd_serie_code,
        trd_revision_requerida=exp.trd_revision_requerida,
        trd_revision_motivo=exp.trd_revision_motivo,
        origen=exp.origen,
        fecha_extrema_inicial=exp.fecha_extrema_inicial,
        fecha_extrema_final=exp.fecha_extrema_final,
        publicable=exp.publicable,
        publicado_at=exp.publicado_at,
    )


def _to_radicado_response(r: ExpedienteRadicado) -> ExpedienteRadicadoResponse:
    return ExpedienteRadicadoResponse(
        radicado_id=r.radicado_id,
        tracking_number=r.tracking_number,
        added_at=r.added_at,
    )


def _to_historial_entry(r: asyncpg.Record) -> HistorialAccesoEntry:
    payload = r["payload"]
    if isinstance(payload, str):  # asyncpg devuelve JSONB como texto
        payload = json.loads(payload) if payload else {}
    return HistorialAccesoEntry(
        tenant_slug=r["tenant_slug"],
        ts=r["ts"],
        service=r["service"],
        actor=r["actor"],
        actor_nombre=r["actor_nombre"],
        canal=r["canal"],
        action=r["action"],
        object_type=r["object_type"],
        object_ref=r["object_ref"],
        payload=payload or {},
    )


def _to_evento_response(r) -> ExpedienteEventoResponse:
    detalles = r["detalles"]
    if isinstance(detalles, str):  # asyncpg devuelve JSONB como texto
        detalles = json.loads(detalles) if detalles else {}
    return ExpedienteEventoResponse(
        id=r["id"],
        expediente_id=r["expediente_id"],
        numero_expediente=r["numero_expediente"],
        accion=r["accion"],
        actor=r["actor"],
        actor_nombre=r["actor_nombre"],
        comentario=r["comentario"],
        detalles=detalles or {},
        ts=r["ts"],
    )


_AGN_DISPOSITION: dict[str, tuple[str, str]] = {
    # Derivado de `CT_DISPOSITION_CODES` (app/core/disposition.py) -- fuente
    # ÚNICA de qué códigos/alias equivalen a Conservación Total, compartida
    # con el filtro SQL de la renovación de retención WORM (E10 debt) para
    # que ambos no diverjan si se agrega un alias nuevo.
    **{code: ("CT", "Conservación Total") for code in CT_DISPOSITION_CODES},
    "E": ("E", "Eliminación"),
    "eliminate": ("E", "Eliminación"),
    "S": ("S", "Selección"),
    "select": ("S", "Selección"),
    "M": ("M", "Microfilmación/Digitalización"),
    "digitize": ("M", "Microfilmación/Digitalización"),
}


def _sin_worm(valor: object) -> bool:
    """`GREATEST(...)` sobre tres `COALESCE(..., '-infinity')` devuelve
    `-infinity` cuando el expediente no tiene NINGÚN candado. Se traduce a
    «sin candado» en un solo sitio: dejar el `-infinity` llegar a la fila del
    inventario lo pintaría como una fecha, y una fecha imposible en un
    inventario de eliminación es peor que un hueco."""
    return valor is None or (isinstance(valor, datetime) and valor.year <= 1)


def _motivo_de_rechazo(
    fila: Any, snapshot: dict[str, Any], *, ahora: datetime
) -> tuple[str, str] | None:
    """Las cinco condiciones de admisión de un lote (RF-TRD-07), en orden y en
    un solo sitio. Devuelve `(motivo, detalle)` o `None` si el expediente
    entra.

    El ORDEN importa para el mensaje, no para la seguridad: se informa del
    impedimento más de fondo primero (no está confirmado) antes que del más
    circunstancial (su candado aún no expira), porque es el que la persona
    tiene que resolver."""
    code = snapshot.get("disposition_code")

    if fila["decision"] != "confirmada":
        return (
            "sin_confirmacion",
            "Su vencimiento no ha sido confirmado por un usuario autorizado. "
            "Ninguna destrucción es automática: primero hay que decidir.",
        )
    if code not in _DISPOSICIONES_QUE_DESTRUYEN:
        etiqueta = snapshot.get("disposition_label") or code or "sin declarar"
        return (
            "disposicion_no_destruye",
            f"Su disposición final es «{etiqueta}»: no termina en destrucción. "
            "Conservación Total se transfiere al archivo histórico y se conserva "
            "permanentemente.",
        )
    if not disposicion_final_autorizada(
        serie_estado=str(snapshot.get("serie_estado") or ""),
        acto_administrativo=snapshot.get("acto_administrativo"),
    ):
        return (
            "instrumento_no_gobierna",
            "El instrumento congelado no está convalidado: no puede gobernar "
            "una eliminación efectiva (ADR-025 D5).",
        )
    worm = fila["worm_retain_until"]
    if not _sin_worm(worm) and worm > ahora:
        return (
            "worm_vigente",
            f"Su retención WORM no expira hasta {worm.date().isoformat()}. "
            "En modo COMPLIANCE nadie puede borrar antes de esa fecha, ni el "
            "administrador: aceptarlo ahora daría un lote imposible de ejecutar.",
        )
    if fila["lote_existente"]:
        return (
            "ya_en_otro_lote",
            f"Ya forma parte del lote «{fila['lote_existente']}».",
        )
    return None


def _leer_snapshot(raw: object) -> dict[str, Any]:
    """El snapshot congelado (`expedientes.disposition`) como dict.

    Llega como texto JSON desde asyncpg (columna jsonb, sin codec en este
    servicio). Un snapshot ilegible devuelve `{}` en vez de reventar: la fila
    sale sin afirmar nada sobre su eliminación —que es lo honesto— y el resto
    de la cola sigue siendo trabajo pendiente de alguien."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(raw, dict):
        return raw
    return {}


# Qué disposiciones terminan en destrucción, y por tanto exigen que el
# instrumento congelado la gobierne (ADR-025 D5) antes de confirmarlas.
# `S` (selección) elimina lo no seleccionado y `M` elimina el original tras
# reproducirlo: las tres destruyen. `CT` NO está aquí -- conservación total no
# termina en destrucción, y pedirle un instrumento convalidado para confirmar
# que algo se conserva sería un requisito inventado.
_DISPOSICIONES_QUE_DESTRUYEN: frozenset[str] = frozenset({"E", "S", "M"})


# Qué toca hacer cuando vence cada fase. Escrito por ADMISIÓN: un código que
# no esté aquí NO recibe una acción inventada, recibe la constancia de que el
# snapshot no la declara. **Un vencimiento no es una orden de destruir** -- en
# `CT` el vencimiento del archivo central significa conservar para siempre en
# el histórico, y presentar esa fila junto a las de `E` sin distinguirlas
# convertiría la cola en una «lista de lo que hay que borrar» que incluiría
# justo los expedientes que jamás se borran.
_ACCION_FASE_CENTRAL: dict[str, str] = {
    "CT": "Transferir al archivo histórico y conservar permanentemente (no se elimina)",
    "E": "Eliminar con acta, previo aval del Comité y publicidad del inventario",
    "S": "Seleccionar la muestra a conservar y eliminar el resto, con acta",
    "M": "Reproducir (microfilmar/digitalizar) y eliminar el original, con acta",
}


def _accion_de_disposicion(code: str | None, *, fase: str) -> str:
    if fase == "gestion":
        # La primera fase no depende del código: al vencer el archivo de
        # gestión todo expediente sube al central, se conserve o se elimine
        # después.
        return "Transferir al archivo central (transferencia primaria)"
    if code is None:
        return "Sin acción determinable: el snapshot congelado no declara código de disposición"
    return _ACCION_FASE_CENTRAL.get(
        code,
        f"Sin acción determinable: código de disposición '{code}' no reconocido",
    )


def _agn_disposition(value: str) -> tuple[str, str]:
    return _AGN_DISPOSITION.get(value, (value, value))


def _add_years(d: date, years: int) -> date:
    """Suma años a una fecha; 29-feb cae en 28-feb en años no bisiestos."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _to_trd_response(s: TrdSerie) -> TrdSerieResponse:
    return TrdSerieResponse(
        id=s.id,
        code=s.code,
        name=s.name,
        description=s.description,
        retention_years=s.retention_years,
        total_retention=s.total_retention,
        disposition=s.disposition,
        is_active=s.is_active,
        created_at=s.created_at,
        parent_id=s.parent_id,
        archivo_gestion_years=s.archivo_gestion_years,
        archivo_central_years=s.archivo_central_years,
        version=s.version,
        valid_from=s.valid_from,
        pdfa_profile=s.pdfa_profile,
        estado=s.estado,
        valid_to=s.valid_to,
        acto_administrativo=s.acto_administrativo,
        acta_comite=s.acta_comite,
        fecha_aprobacion_comite=s.fecha_aprobacion_comite,
        fecha_convalidacion=s.fecha_convalidacion,
        instancia_convalidante=s.instancia_convalidante,
        rusd_radicado=s.rusd_radicado,
        fecha_publicacion=s.fecha_publicacion,
        supersede_a=s.supersede_a,
        created_by=s.created_by,
        tipo_instrumento=s.tipo_instrumento,
        fondo_nombre=s.fondo_nombre,
        fecha_extrema_inicial=s.fecha_extrema_inicial,
        fecha_extrema_final=s.fecha_extrema_final,
        justificacion_valoracion=s.justificacion_valoracion,
    )
