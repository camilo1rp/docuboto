# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.models.archive import Expediente, ExpedienteRadicado, TrdSerie
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.audit_read import AuditReadRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.metadata_repository import MetadataRepository
from app.schemas.archive import (
    BatchLinkResult,
    ExpedienteCreate,
    ExpedienteDisposicionResult,
    ExpedienteEventoResponse,
    ExpedienteIndice,
    ExpedienteRadicadoResponse,
    ExpedienteResponse,
    ExpedienteUpdate,
    HistorialAccesoEntry,
    IndiceItem,
    RadicadoLink,
    RetentionSchedule,
    RolAutorizado,
    TrdSerieCreate,
    TrdSerieResponse,
    TrdSerieUpdate,
)
from app.services.metadata_service import validate_metadata

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"

# Canales que representan una consulta real de un usuario (gateway/mcp-server,
# ADR-019/D-03) — 'system' (jobs/regeneraciones internas, o llamadas internas
# que reutilizan `get_expediente` tras una mutación ya auditada aparte) queda
# excluido: no ensucia la pista de acceso ni la duplica (E15 perfil v4, R3-a).
# Espejo de `index_service._CANALES_AUDITABLES`.
_CANALES_AUDITABLES = frozenset({"api", "ui", "mcp"})

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
        # Metadatos (E03, ADR-007): si la serie TRD tiene plantilla activa, los
        # valores se validan contra su JSON Schema antes de abrir el expediente.
        metadata_template_id: uuid.UUID | None = None
        if payload.trd_serie_id is not None:
            template = await self._metadata_repo.get_active_template(payload.trd_serie_id)
            if template is not None:
                validate_metadata(payload.metadata, template["json_schema"])
                metadata_template_id = template["id"]

        year = datetime.now(tz=UTC).year
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
            payload={"id": str(expediente.id)},
        )
        return _to_expediente_response(expediente)

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
                    "detail": f"Expediente {expediente_id} not found",
                    "status": 404,
                },
            )
        await self._audit_read_access(expediente.code, user_id=user_id, canal=canal)
        return _to_expediente_response(expediente)

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
        # No-read-up (RF-SEG-08): el PATCH devuelve `ExpedienteResponse` completo
        # (mismo leak que `GET /{id}` si se usara el clearance "sin restricción")
        # — resuelve el clearance real del llamante, igual que `get_expediente`.
        # Mantiene semántica 404-no-403: un expediente por encima del clearance
        # no revela su existencia.
        max_clearance = await self._repo.caller_clearance(user_id)
        existing = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": f"Expediente {expediente_id} not found",
                    "status": 404,
                },
            )

        if payload.status is not None:
            allowed = _VALID_TRANSITIONS.get(existing.status, set())
            if payload.status not in allowed:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "invalid_status_transition",
                        "detail": (
                            f"Cannot transition from '{existing.status}' to '{payload.status}'"
                        ),
                        "status": 422,
                    },
                )
            # Este PATCH genérico es una ruta alterna a `transfer_expediente` para la
            # misma transición closed→transferred: aplica la MISMA guarda de índice
            # firmado (Acuerdo AGN 001/2024 art. 4.3.2.4) — de lo contrario un
            # llamador podría transferir un expediente sin índice sellado rodeando
            # el endpoint dedicado `POST /{id}/transfer`.
            if payload.status == "transferred":
                await self._assert_indice_firmado(expediente_id)

        updated = await self._repo.update_expediente(
            expediente_id,
            name=payload.name,
            description=payload.description,
            trd_serie_id=payload.trd_serie_id,
            status=payload.status,
        )
        return _to_expediente_response(updated)  # type: ignore[arg-type]

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
                    "detail": f"Expediente {expediente_id} not found",
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
                    "detail": f"Cannot transition from '{current}' to '{target}'",
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

        `firmante` es el valor mostrado en la hoja de ruta/auditoría (puede
        caer a X-Username si no hay X-User-Id). `actor_id` es estrictamente el
        X-User-Id (UUID de `auth_users.id`) que signature-service exige para
        atribuir el sello (RF-FIR-11/12): si falta o la firma falla, el índice
        queda 'pendiente_firma' (decisión del usuario, RT-16) — el cierre del
        expediente NUNCA se bloquea por esto.
        """
        from app.services.index_service import IndexService

        exp = await self._expediente_or_404(expediente_id)
        self._assert_transition(exp.status, "closed")
        disposition = None
        if exp.trd_serie_id is not None:
            schedule = await self.compute_retention(exp.trd_serie_id, datetime.now(tz=UTC).date())
            disposition = schedule.model_dump(mode="json")
        transitioned = await self._repo.transition_expediente(
            expediente_id, status="closed", disposition=disposition
        )
        # Índice electrónico: versión final + sello XAdES-B al cierre (RF-EXP-06).
        # El resultado (`signed`) se captura para dejar traza del estado del
        # índice en el audit_log de cierre — best-effort: un sellado fallido
        # NUNCA bloquea el cierre (RT-16), pero debe quedar auditado.
        # `assert_open=False`: en este punto el expediente YA transicionó a
        # 'closed' (arriba) — esta ES la regeneración final legítima que el
        # guard de `regenerate` rechazaría por defecto.
        index = IndexService(self._conn, self._tenant_slug)
        rebuild_result = await index.regenerate(
            expediente_id, generado_por=firmante or "system", assert_open=False
        )
        signed = await index.firmar_indice(
            expediente_id, tenant_slug=self._tenant_slug, actor_id=actor_id
        )
        indice_estado = "firmado" if signed is not None else "pendiente_firma"
        await self._repo.insert_expediente_evento(
            expediente_id,
            numero_expediente=exp.code,
            accion="cerrado",
            comentario=f"firmante={firmante}" if firmante else None,
        )
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="archive.expediente_closed",
            object_type="expediente",
            object_ref=exp.code,
            actor=firmante,
            payload={
                "indice_estado": indice_estado,
                "firma_id": str(signed.firma_id) if signed is not None else None,
                # Trazabilidad del salto v1->v2 (E15 Inc.3, RT-15): xsd_version
                # real de la versión que `regenerate` acaba de devolver (NO la
                # constante del módulo) — no afirma "v2" si `regenerate`
                # resultó `unchanged` sobre una versión persistida distinta.
                "xsd_version": rebuild_result.xsd_version,
            },
        )
        if signed is None:
            # Contraparte de 'firma.creada' (signature-service): deja traza
            # explícita de que el índice electrónico de este expediente cerró
            # SIN sellar (ADR-010, trazabilidad de no-conformidad).
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.indice_firma_pendiente",
                object_type="expediente_indice",
                object_ref=exp.code,
                actor=firmante,
                payload={
                    "expediente_id": str(expediente_id),
                    "motivo": "sellado_xades_no_obtenido_al_cierre",
                },
            )
        # No re-lee vía `get_expediente`: ese método resuelve el clearance del
        # llamante (`caller_clearance(user_id)`), y aquí no se propaga
        # `user_id` — hacerlo devolvería 404 espurio para un archivista con
        # permiso de disposición pero sin clearance de lectura sobre un
        # expediente RESERVADA/CLASIFICADA, pese a que el cierre YA se
        # comprometió (`get_db_conn` es autocommit, sin transacción de
        # request). Se construye la respuesta estrecha a partir del `exp` ya
        # cargado + el resultado de la propia transición (ortogonalidad
        # decidida, ver `ExpedienteDisposicionResult`).
        #
        # `transition_expediente` hace `UPDATE ... RETURNING *`; solo devuelve
        # None si el WHERE id=$1 no casa, algo inalcanzable aquí (la fila la
        # acaba de cargar `_expediente_or_404` en la misma conexión secuencial
        # y no hay DELETE físico de expedientes). Aun así, fail-closed: un
        # endpoint de disposición NUNCA debe reportar `status="open"` — si la
        # transición no devolvió fila, es una invariante rota, no un cierre.
        if transitioned is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "expediente_transition_failed",
                    "detail": "La transición de cierre no devolvió el expediente actualizado.",
                    "status": 409,
                },
            )
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
        self, expediente_id: uuid.UUID
    ) -> ExpedienteDisposicionResult:
        """Transfiere el expediente (closed → transferred).

        Bloqueante: exige índice electrónico 'firmado' — ver
        `_assert_indice_firmado` (Acuerdo AGN 001/2024 art. 4.3.2.4).
        """
        exp = await self._expediente_or_404(expediente_id)
        self._assert_transition(exp.status, "transferred")
        await self._assert_indice_firmado(expediente_id)
        transitioned = await self._repo.transition_expediente(
            expediente_id, status="transferred"
        )
        await self._repo.insert_expediente_evento(
            expediente_id, numero_expediente=exp.code, accion="transferido"
        )
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="archive.expediente_transferred",
            object_type="expediente",
            object_ref=exp.code,
        )
        # Misma razón que `close_expediente`: no re-lee vía `get_expediente`
        # (evita el 404 espurio no-read-up) — respuesta estrecha construida a
        # partir de la transición ya comprometida. Fail-closed idéntico: un
        # endpoint de disposición nunca reporta el estado pre-transición.
        if transitioned is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "expediente_transition_failed",
                    "detail": "La transición de transferencia no devolvió el expediente actualizado.",
                    "status": 409,
                },
            )
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
                    "detail": f"Expediente {expediente_id} not found",
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
                    "detail": f"Expediente {expediente_id} not found",
                    "status": 404,
                },
            )
        offset = (page - 1) * size
        total = await self._audit_read.count_for_expediente(self._tenant_slug, exp.code)
        rows = await self._audit_read.list_for_expediente(
            self._tenant_slug, exp.code, offset=offset, limit=size
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
                    "detail": f"Expediente {expediente_id} not found",
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

    async def link_radicado(
        self, expediente_id: uuid.UUID, payload: RadicadoLink
    ) -> ExpedienteRadicadoResponse:
        # No-read-up DIFERIDO aquí (decisión del arquitecto, ver
        # `core.clearance.CLEARANCE_SIN_RESTRICCION`) — brecha conocida y
        # explícita, no escondida en un default de firma. RF-SEG-08.
        existing = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": f"Expediente {expediente_id} not found",
                    "status": 404,
                },
            )
        self._assert_open(existing)

        duplicate = await self._repo.radicado_link_exists(expediente_id, payload.radicado_id)
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "radicado_already_linked",
                    "detail": (
                        f"Radicado {payload.radicado_id} is already linked to expediente {expediente_id}"
                    ),
                    "status": 409,
                },
            )

        link = await self._repo.link_radicado(
            expediente_id,
            payload.radicado_id,
            payload.tracking_number,
            content_hash=payload.content_hash.lower() if payload.content_hash else None,
            formato=payload.formato,
            tamano_bytes=payload.tamano_bytes,
            folios=payload.folios,
            dependencia_productora_codigo=payload.dependencia_productora_codigo,
            dependencia_productora_nombre=payload.dependencia_productora_nombre,
        )
        await self._regenerate_index(expediente_id)
        await self._repo.insert_expediente_evento(
            expediente_id,
            numero_expediente=existing.code,
            accion="radicado_vinculado",
            detalles={
                "radicado_id": str(payload.radicado_id),
                "tracking_number": payload.tracking_number,
            },
        )
        return _to_radicado_response(link)

    async def link_radicados_batch(
        self, expediente_id: uuid.UUID, items: list[RadicadoLink]
    ) -> BatchLinkResult:
        """Vincula múltiples radicados de una vez (idempotente) y regenera el índice
        una sola vez al final. Omite los ya vinculados (paridad legado: inclusión masiva).

        No-read-up DIFERIDO aquí (decisión del arquitecto, ver
        `core.clearance.CLEARANCE_SIN_RESTRICCION`) — brecha conocida y explícita,
        no escondida en un default de firma. RF-SEG-08.
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
        self._assert_open(existing)
        vinculados = omitidos = 0
        for it in items:
            if await self._repo.radicado_link_exists(expediente_id, it.radicado_id):
                omitidos += 1
                continue
            await self._repo.link_radicado(
                expediente_id,
                it.radicado_id,
                it.tracking_number,
                content_hash=it.content_hash.lower() if it.content_hash else None,
                formato=it.formato,
                tamano_bytes=it.tamano_bytes,
                folios=it.folios,
                dependencia_productora_codigo=it.dependencia_productora_codigo,
                dependencia_productora_nombre=it.dependencia_productora_nombre,
            )
            vinculados += 1
        if vinculados:
            await self._regenerate_index(expediente_id)
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
        (`expediente_eventos`) como en el `audit_log` inmutable (Canal A).

        No-read-up DIFERIDO aquí (decisión del arquitecto, ver
        `core.clearance.CLEARANCE_SIN_RESTRICCION`) — brecha conocida y explícita,
        no escondida en un default de firma. RF-SEG-08.
        """
        existing = await self._repo.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": f"Expediente {expediente_id} not found",
                    "status": 404,
                },
            )
        self._assert_open(existing)
        excluded = await self._repo.unlink_radicado(expediente_id, radicado_id, causal=causal)
        if not excluded:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "radicado_link_not_found",
                    "detail": (
                        f"Radicado {radicado_id} is not linked to expediente {expediente_id}"
                    ),
                    "status": 404,
                },
            )
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
        )

    # ------------------------------------------------------------------
    # TRD series
    # ------------------------------------------------------------------

    async def create_trd_serie(
        self, payload: TrdSerieCreate, actor: uuid.UUID | None = None
    ) -> TrdSerieResponse:
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
        )
        # Auditoría forense transversal (E08 §9, Canal A): la TRD gobierna
        # retención y disposición final (Ley 594/2000 art. 24) — su alta debe
        # quedar trazada igual que la de un expediente.
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="archive.trd_serie_created",
            object_type="trd_serie",
            object_ref=serie.code,
            actor=str(actor) if actor else None,
            payload={"id": str(serie.id)},
        )
        return _to_trd_response(serie)

    async def compute_retention(self, serie_id: uuid.UUID, closed_at: date) -> RetentionSchedule:
        """Calendario de retención/disposición de un expediente cerrado, derivado de
        su serie TRD (E04/ADR-015). Función pura sobre la fila de la serie."""
        serie = await self._repo.get_trd_serie_by_id(serie_id)
        if serie is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "trd_serie_not_found",
                    "detail": f"TRD serie {serie_id} not found",
                    "status": 404,
                },
            )
        agest = serie.archivo_gestion_years or 0
        acentral = serie.archivo_central_years or 0
        fin_gestion = _add_years(closed_at, agest)
        fin_central = _add_years(fin_gestion, acentral)
        code, label = _agn_disposition(serie.disposition)
        return RetentionSchedule(
            serie_id=serie.id,
            code=serie.code,
            closed_at=closed_at,
            archivo_gestion_years=agest,
            archivo_central_years=acentral,
            fin_archivo_gestion=fin_gestion,
            fin_archivo_central=fin_central,
            disposition_code=code,
            disposition_label=label,
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
                    "detail": f"TRD serie {serie_id} not found",
                    "status": 404,
                },
            )
        return _to_trd_response(serie)

    async def update_trd_serie(
        self,
        serie_id: uuid.UUID,
        payload: TrdSerieUpdate,
        actor: uuid.UUID | None = None,
    ) -> TrdSerieResponse:
        existing = await self._repo.get_trd_serie_by_id(serie_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "trd_serie_not_found",
                    "detail": f"TRD serie {serie_id} not found",
                    "status": 404,
                },
            )
        updated = await self._repo.update_trd_serie(
            serie_id,
            name=payload.name,
            description=payload.description,
            retention_years=payload.retention_years,
            total_retention=payload.total_retention,
            disposition=payload.disposition,
            is_active=payload.is_active,
        )
        # Auditoría forense transversal (E08 §9, Canal A): mutar la TRD es un
        # acto administrativo sobre el instrumento archivístico (Ley 594/2000
        # art. 24) — debe quedar quién cambió qué, igual que en expedientes.
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="archive.trd_serie_updated",
            object_type="trd_serie",
            object_ref=existing.code,
            actor=str(actor) if actor else None,
            payload={
                "id": str(serie_id),
                "changes": payload.model_dump(exclude_unset=True),
            },
        )
        return _to_trd_response(updated)  # type: ignore[arg-type]


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
        comentario=r["comentario"],
        detalles=detalles or {},
        ts=r["ts"],
    )


_AGN_DISPOSITION: dict[str, tuple[str, str]] = {
    "CT": ("CT", "Conservación Total"),
    "conserve": ("CT", "Conservación Total"),
    "E": ("E", "Eliminación"),
    "eliminate": ("E", "Eliminación"),
    "S": ("S", "Selección"),
    "select": ("S", "Selección"),
    "M": ("M", "Microfilmación/Digitalización"),
    "digitize": ("M", "Microfilmación/Digitalización"),
}


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
    )
