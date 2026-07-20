# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cableado del AIP OAIS al cierre del expediente (E10 debt "AIP sin-wiring").
Diseño ratificado por `orfeo-architect` (`.claude/agent-memory/orfeo-architect/
project_aip-wiring-e10-diseno.md`) — espejo de `IndexService.
proteger_worm_indice` (índice)/`TransferenciaService.proteger_worm_acta`
(acta), con dos divergencias deliberadas:

1. **Reconciliación-only**: descargar TODOS los bytes de los anexos, armar el
   ZIP y subirlo al bucket WORM es CARO — nunca se intenta síncronamente en
   el path de la petición de cierre del expediente (a diferencia del índice,
   que sí tiene un intento síncrono barato). Solo `run_once_aip`
   (`app/jobs/indice_reconciliation.py`, 6ª fase) dispara esto, siempre con
   `SYSTEM_RECONCILER_ID`.
2. **Resolución de `file_id` CROSS-SERVICIO**: archive-service no conoce los
   `file_id` de los anexos de un radicado (viven en document-service) — un
   endpoint interno nuevo (`app/clients/document_client.py`) resuelve el
   inventario TOTAL en un solo round-trip batch.

Precondición dura: expediente `closed`/`transferred` + índice VIGENTE
`estado='firmado'` + `worm_protegido_at IS NOT NULL` (el AIP embebe el índice
YA firmado y protegido bajo WORM como `indice_file_id`) — filtrada en SQL por
`AipRepository.list_pending`, pero también re-verificada aquí (defensa en
profundidad, fail-closed) por si este método se invoca desde otro lugar en
el futuro.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import asyncpg
from orpycamcp_common import audit

from app.clients import document_client, storage_client
from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.repositories.aip_repository import AipRepository
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.services.backoff import next_backoff_seconds
from app.services.retention_math import total_trd_years

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"

# Sanea el `nombre` de cada documento dentro del AIP: `AipDocInput._validate_nombre`
# (storage-service) rechaza '/', '\\' y caracteres de control/nueva línea (zip-slip /
# inyección de línea en el manifiesto). Se reemplazan por '_' en vez de rechazar el
# empaquetado completo por un nombre de archivo "sucio" que el usuario ya subió
# legítimamente en su momento.
_UNSAFE_NOMBRE_RE = re.compile(r"[/\\\x00-\x1f]")
_MAX_NOMBRE_LEN = 255


def _safe_nombre(radicado_id: uuid.UUID, idx: int, filename: str) -> str:
    """`{radicado_id}__{idx}__{filename-saneado}` — único GLOBALMENTE dentro
    del AIP sin depender de un `anexo.id` que el contrato del endpoint
    interno de document-service no expone (solo `file_id`/`filename`, ver
    `AnexoInternalItem`): el prefijo `radicado_id` ya distingue anexos de
    radicados distintos; `idx` (posición dentro de la lista de ESE radicado)
    distingue dos anexos del MISMO radicado con el mismo nombre de archivo.
    `AipCreate` exige `nombre` único en TODO el payload — ver su
    `_check_unique_nombres`."""
    safe_filename = _UNSAFE_NOMBRE_RE.sub("_", filename).strip() or "anexo"
    nombre = f"{radicado_id}__{idx}__{safe_filename}"
    return nombre[:_MAX_NOMBRE_LEN]


class AipWiringService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._aip = AipRepository(conn)
        self._archive = ArchiveRepository(conn, tenant_slug)
        self._index = IndexRepository(conn)

    async def _resolve_aip_retention(self, exp) -> tuple[int, date] | None:
        """`(retencion_anios_total, fecha_inicio=fecha_cierre)` — MISMA
        derivación que `IndexService._resolve_worm_retention` (TRD total,
        `fecha_inicio=closed_at`), replicada aquí (self-contained, no importa
        de `IndexService`) para no acoplar este servicio a los internos de
        otro. Fail-closed (``None``) en cualquier caso dudoso."""
        if exp.closed_at is None or exp.trd_serie_id is None:
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        total = total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)
        if total is None:
            return None
        return total, exp.closed_at.date()

    async def _bump_backoff(
        self, expediente_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
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
        await self._aip.bump_intento(
            expediente_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.aip_agotado",
                    object_type="expediente_aip",
                    object_ref=str(expediente_id),
                    actor=None,
                    canal="system",
                    payload={"intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning("audit.append(aip_agotado) failed", exc_info=True)
            return "agotado"
        return "pendiente"

    async def intentar_empaquetar(
        self, expediente_id: uuid.UUID
    ) -> Literal["empaquetado", "omitido", "pendiente", "agotado"]:
        """Best-effort, NUNCA lanza (mismo patrón que
        `IndexService.proteger_worm_indice`): cualquier excepción no prevista
        queda contenida aquí y degrada a `"pendiente"` (reintentable en la
        próxima barrida)."""
        try:
            row = await self._aip.get_or_create(expediente_id)
            if row["estado"] != "pendiente":
                # Idempotente: ya 'empaquetado'/'omitido'/'agotado' — no
                # re-dispara. `run_once_aip` no debería llamar aquí en ese
                # caso (SQL ya lo filtra), pero esta guarda es la defensa
                # real contra una doble llamada concurrente.
                return row["estado"]

            exp = await self._archive.get_expediente_by_id(
                expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
            )
            if exp is None or exp.status not in ("closed", "transferred") or exp.closed_at is None:
                return await self._bump_backoff(
                    expediente_id,
                    intentos_previos=int(row["intentos"]),
                    motivo="expediente_no_elegible",
                )

            # `get_current` NO proyecta las columnas WORM (`_WORM_COLS` solo
            # las trae `get_by_id`, ver `IndexRepository`) — dos lecturas en
            # vez de extender `get_current` (que sirven rutas de
            # lectura/verify/fixity ya probadas y no necesitan estas
            # columnas, ver el comentario de `_WORM_COLS` en ese repositorio).
            current = await self._index.get_current(expediente_id)
            indice_row = (
                await self._index.get_by_id(current["id"]) if current is not None else None
            )
            if (
                indice_row is None
                or indice_row["estado"] != "firmado"
                or indice_row["worm_protegido_at"] is None
                or not indice_row["minio_key_firmado"]
            ):
                return await self._bump_backoff(
                    expediente_id,
                    intentos_previos=int(row["intentos"]),
                    motivo="indice_no_worm_protegido",
                )
            indice_file_id = uuid.UUID(indice_row["minio_key_firmado"].split("/", 1)[0])

            # Solo radicados VIGENTES (`estado='vinculado'`) aportan
            # contenido al AIP — un radicado EXCLUIDO (soft-delete) ya quedó
            # trazado como hueco en el índice electrónico (que este mismo
            # AIP embebe vía `indice_file_id`); no hace falta duplicar sus
            # bytes en el paquete de contenido. Mismo criterio que
            # `count_vigentes`/`productora_presente` del lado del índice.
            vigente_ids = [r.radicado_id for r in exp.radicados if r.estado == "vinculado"]

            try:
                anexos_by_radicado = await document_client.fetch_anexos_internal(
                    tenant_slug=self._tenant_slug, radicado_ids=vigente_ids
                )
            except document_client.DocumentClientError as exc:
                logger.warning(
                    "Cableado AIP: document-service falló al resolver anexos "
                    "(expediente_id=%s): %s",
                    expediente_id,
                    exc,
                )
                return await self._bump_backoff(
                    expediente_id,
                    intentos_previos=int(row["intentos"]),
                    motivo="document_service_error",
                )

            documentos: list[dict] = []
            for radicado_id in vigente_ids:
                anexos = anexos_by_radicado.get(str(radicado_id), [])
                for idx, anexo in enumerate(anexos):
                    documentos.append(
                        {
                            "document_id": str(radicado_id),
                            "nombre": _safe_nombre(radicado_id, idx, anexo["filename"]),
                            "file_id": anexo["file_id"],
                        }
                    )

            if not documentos:
                # Expediente 100% físico (o sin anexos digitales en ningún
                # radicado vigente) — `AipCreate.documentos` exige
                # `min_length=1`, no es construible. Su preservación ya la
                # cubre el índice electrónico firmado+protegido bajo WORM
                # (E10 Inc.1). TERMINAL, no reintentable.
                async with self._conn.transaction():
                    affected = await self._aip.mark_omitido(expediente_id)
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant_slug,
                            service=_SERVICE,
                            action="archive.aip_omitido",
                            object_type="expediente_aip",
                            object_ref=str(expediente_id),
                            actor=str(SYSTEM_RECONCILER_ID),
                            canal="system",
                            payload={
                                "expediente_id": str(expediente_id),
                                "motivo": "sin_anexos_digitales",
                            },
                        )
                return "omitido"

            retention = await self._resolve_aip_retention(exp)
            if retention is None:
                return await self._bump_backoff(
                    expediente_id,
                    intentos_previos=int(row["intentos"]),
                    motivo="trd_sin_retencion",
                )
            retencion_anios, fecha_inicio = retention

            outcome = await storage_client.empaquetar_aip_internal(
                tenant_slug=self._tenant_slug,
                expediente_id=str(expediente_id),
                documentos=documentos,
                indice_file_id=indice_file_id,
                retencion_anios=retencion_anios,
                fecha_inicio=fecha_inicio,
            )

            if outcome.ok:
                assert outcome.aip_id is not None
                async with self._conn.transaction():
                    affected = await self._aip.mark_empaquetado(
                        expediente_id,
                        outcome.aip_id,
                        worm_retain_until=outcome.retain_until,
                    )
                    # Idempotencia real (mismo patrón S1 que `mark_worm_protegido`):
                    # `affected == 0` -> otra llamada concurrente ya marcó
                    # 'empaquetado' este expediente entre la lectura de `row`
                    # y este UPDATE. NO se escribe un asiento fantasma.
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant_slug,
                            service=_SERVICE,
                            action="archive.aip_empaquetado",
                            object_type="expediente_aip",
                            object_ref=str(expediente_id),
                            actor=str(SYSTEM_RECONCILER_ID),
                            canal="system",
                            payload={
                                "expediente_id": str(expediente_id),
                                "aip_id": str(outcome.aip_id),
                                "num_documentos": len(documentos),
                                "retencion_anios": retencion_anios,
                            },
                        )
                return "empaquetado" if affected else "omitido"

            motivo = (
                "fixity_mismatch"
                if outcome.fixity_mismatch
                else "worm_disabled"
                if outcome.disabled
                else "aip_no_confirmado"
            )
            return await self._bump_backoff(
                expediente_id, intentos_previos=int(row["intentos"]), motivo=motivo
            )
        except Exception:
            logger.warning(
                "Cableado AIP falló (best-effort), expediente_id=%s",
                expediente_id,
                exc_info=True,
            )
            return "pendiente"

    # -- E10 debt: renovación de retención WORM para series CT -------------
    # (extensión del AIP a la 5ª fase de reconciliación -- espejo EXACTO de
    # `IndexService._resolve_ct_retencion_anios`/`_bump_worm_renovacion_backoff`/
    # `renovar_worm_indice`, ver ese servicio para el diseño completo.)

    async def _resolve_ct_retencion_anios(self, expediente_id: uuid.UUID) -> int | None:
        """Total TRD (ambas fases) del expediente -- versión de RENOVACIÓN:
        la ventana rodante de renovación siempre usa `fecha_inicio=hoy` (no
        `closed_at`), así que este helper SOLO necesita el total y no exige
        `exp.closed_at` (aunque en la práctica siempre está poblado -- un AIP
        solo llega a `estado='empaquetado'` tras el cierre)."""
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if exp is None or exp.trd_serie_id is None:
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        return total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)

    async def _bump_worm_renovacion_backoff(
        self, expediente_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        """Registra un intento fallido de RENOVACIÓN de retención WORM del AIP
        (E10 debt) -- gemelo de `_bump_backoff` (backoff del EMPAQUETADO) para
        el eje de renovación: NO toca `estado`/`intentos` de `expediente_aip`
        (esos son del empaquetado, ya TERMINAL en `'empaquetado'`), solo las
        columnas `worm_renovacion_*` (migración 034). `agotado=True` aquí es
        una alarma operacional MÁS SERIA que en el eje de empaquetado: un
        artefacto de conservación PERMANENTE (serie CT) podría perder su lock
        COMPLIANCE al vencer `worm_retain_until` sin haberse podido extender."""
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
        await self._aip.bump_worm_renovacion_intento(
            expediente_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.aip_worm_renovacion_agotado",
                    object_type="expediente_aip",
                    object_ref=str(expediente_id),
                    actor=None,
                    canal="system",
                    payload={"renovacion_intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(aip_worm_renovacion_agotado) failed", exc_info=True
                )
            return "agotado"
        return "pendiente"

    async def renovar_worm_aip(
        self, expediente_id: uuid.UUID, *, intentos_previos: int
    ) -> Literal["renovado", "omitido", "pendiente", "agotado"]:
        """Extiende (E10 debt, AGN 001/2024 Art. 4.3.2.6) la retención WORM
        del AIP de un expediente de serie Conservación Total (CT) ANTES de
        que su `worm_retain_until` vigente venza -- 5ª fase de reconciliación
        (`run_once_worm_renovacion`, `app/jobs/indice_reconciliation.py`).

        Solo se llama sobre filas que YA pasaron el filtro CT en SQL
        (`AipRepository.list_worm_renovacion_pending`) -- este método NO
        vuelve a comprobar disposición, solo resuelve el total TRD vigente y
        llama a la renovación. Ventana RODANTE: `fecha_inicio=hoy`, nunca una
        fecha lejana/9999.

        Best-effort, NUNCA lanza -- mismo patrón que `intentar_empaquetar`."""
        try:
            retencion_anios = await self._resolve_ct_retencion_anios(expediente_id)
            if retencion_anios is None:
                return await self._bump_worm_renovacion_backoff(
                    expediente_id, intentos_previos=intentos_previos, motivo="trd_sin_retencion"
                )

            outcome = await storage_client.renovar_retencion_worm_internal(
                tenant_slug=self._tenant_slug,
                expediente_id=str(expediente_id),
                tipo="aip",
                retencion_anios=retencion_anios,
                fecha_inicio=datetime.now(tz=UTC).date(),
            )

            if outcome.ok:
                assert outcome.retain_until is not None
                async with self._conn.transaction():
                    await self._aip.mark_worm_renovado(
                        expediente_id, worm_retain_until=outcome.retain_until
                    )
                    if outcome.renovado:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant_slug,
                            service=_SERVICE,
                            action="archive.aip_worm_retencion_renovada",
                            object_type="expediente_aip",
                            object_ref=str(expediente_id),
                            actor=None,
                            canal="system",
                            payload={
                                "expediente_id": str(expediente_id),
                                "retencion_anios": retencion_anios,
                                "retain_until": outcome.retain_until.isoformat(),
                            },
                        )
                return "renovado" if outcome.renovado else "omitido"

            motivo = (
                "worm_disabled"
                if outcome.disabled
                else "worm_object_not_found"
                if outcome.not_found
                else "worm_no_confirmado"
            )
            return await self._bump_worm_renovacion_backoff(
                expediente_id, intentos_previos=intentos_previos, motivo=motivo
            )
        except Exception:
            logger.warning(
                "Renovación de retención WORM del AIP falló (best-effort), "
                "expediente_id=%s",
                expediente_id,
                exc_info=True,
            )
            return "pendiente"
