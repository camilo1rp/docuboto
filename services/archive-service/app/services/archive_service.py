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
import uuid
from datetime import UTC, date, datetime

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.models.archive import Expediente, ExpedienteRadicado, TrdSerie
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.metadata_repository import MetadataRepository
from app.schemas.archive import (
    BatchLinkResult,
    ExpedienteCreate,
    ExpedienteEventoResponse,
    ExpedienteIndice,
    ExpedienteRadicadoResponse,
    ExpedienteResponse,
    ExpedienteUpdate,
    IndiceItem,
    RadicadoLink,
    RetentionSchedule,
    TrdSerieCreate,
    TrdSerieResponse,
    TrdSerieUpdate,
)
from app.services.metadata_service import validate_metadata

_SERVICE = "archive-service"

# Valid state transitions — re-opening a closed expediente is not permitted.
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"closed"},
    "closed": {"transferred"},
    "transferred": set(),
}


class ArchiveService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._repo = ArchiveRepository(conn, tenant_slug)
        self._metadata_repo = MetadataRepository(conn, tenant_slug)
        self._index_repo = IndexRepository(conn)
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
    ) -> tuple[list[ExpedienteResponse], int]:
        offset = (page - 1) * size
        expedientes, total = await self._repo.list_expedientes(
            status=status, offset=offset, limit=size
        )
        return [_to_expediente_response(e) for e in expedientes], total

    async def get_expediente(
        self, expediente_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> ExpedienteResponse:
        max_clearance = await self._repo.caller_clearance(user_id)
        expediente = await self._repo.get_expediente_by_id(expediente_id, max_clearance)
        if expediente is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": f"Expediente {expediente_id} not found",
                    "status": 404,
                },
            )
        return _to_expediente_response(expediente)

    async def update_expediente(
        self, expediente_id: uuid.UUID, payload: ExpedienteUpdate
    ) -> ExpedienteResponse:
        existing = await self._repo.get_expediente_by_id(expediente_id)
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
        exp = await self._repo.get_expediente_by_id(expediente_id)
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
    ) -> ExpedienteResponse:
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
        await self._repo.transition_expediente(
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
        return await self.get_expediente(expediente_id)

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

    async def transfer_expediente(self, expediente_id: uuid.UUID) -> ExpedienteResponse:
        """Transfiere el expediente (closed → transferred).

        Bloqueante: exige índice electrónico 'firmado' — ver
        `_assert_indice_firmado` (Acuerdo AGN 001/2024 art. 4.3.2.4).
        """
        exp = await self._expediente_or_404(expediente_id)
        self._assert_transition(exp.status, "transferred")
        await self._assert_indice_firmado(expediente_id)
        await self._repo.transition_expediente(expediente_id, status="transferred")
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
        return await self.get_expediente(expediente_id)

    async def list_eventos(
        self, expediente_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> list[ExpedienteEventoResponse]:
        """Hoja de ruta del expediente (E02 §9 / Canal B): visible a quien pueda LEER el
        expediente, acotada por su clearance (no read-up, RF-SEG-08). No requiere admin.
        Un expediente por encima del clearance devuelve historial vacío."""
        max_clearance = await self._repo.caller_clearance(user_id)
        rows = await self._repo.list_expediente_eventos(expediente_id, max_clearance)
        return [_to_evento_response(r) for r in rows]

    async def get_indice(self, expediente_id: uuid.UUID) -> ExpedienteIndice:
        """Índice/foliado del expediente: radicados ordenados con folio secuencial."""
        exp = await self._expediente_or_404(expediente_id)
        items = [
            IndiceItem(
                folio=i,
                radicado_id=r.radicado_id,
                tracking_number=r.tracking_number,
                added_at=r.added_at,
            )
            for i, r in enumerate(exp.radicados, start=1)
        ]
        return ExpedienteIndice(
            expediente_id=exp.id, code=exp.code, total_folios=len(items), items=items
        )

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
        existing = await self._repo.get_expediente_by_id(expediente_id)
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
        una sola vez al final. Omite los ya vinculados (paridad legado: inclusión masiva)."""
        existing = await self._repo.get_expediente_by_id(expediente_id)
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
            )
            vinculados += 1
        if vinculados:
            await self._regenerate_index(expediente_id)
        return BatchLinkResult(vinculados=vinculados, omitidos=omitidos, total=len(items))

    async def unlink_radicado(self, expediente_id: uuid.UUID, radicado_id: uuid.UUID) -> None:
        existing = await self._repo.get_expediente_by_id(expediente_id)
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
        deleted = await self._repo.unlink_radicado(expediente_id, radicado_id)
        if not deleted:
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
            detalles={"radicado_id": str(radicado_id)},
        )

    # ------------------------------------------------------------------
    # TRD series
    # ------------------------------------------------------------------

    async def create_trd_serie(self, payload: TrdSerieCreate) -> TrdSerieResponse:
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
        self, serie_id: uuid.UUID, payload: TrdSerieUpdate
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
        radicados=[_to_radicado_response(r) for r in exp.radicados],
    )


def _to_radicado_response(r: ExpedienteRadicado) -> ExpedienteRadicadoResponse:
    return ExpedienteRadicadoResponse(
        radicado_id=r.radicado_id,
        tracking_number=r.tracking_number,
        added_at=r.added_at,
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
