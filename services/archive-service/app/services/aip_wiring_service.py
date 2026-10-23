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

**E10 AIP wiring follow-up — AIP índice-only para expedientes 100% físicos**
(diseño ratificado por `orfeo-architect`, `.claude/agent-memory/orfeo-architect/
project_aip-indice-only-fisicos-e10.md`): un expediente sin NINGÚN anexo
digital (`documentos == []`) ya NO cae en `'omitido'` por defecto — el índice
electrónico firmado, que en ese punto SIEMPRE está disponible (la
precondición de arriba ya pasó), se empaqueta como el único objeto sustantivo
del AIP (el índice ES el payload del Bag, no un tag file — ver
`app.services.aip_builder._is_indice_only_mode` en storage-service). Kill-switch
`settings.aip_indice_only_enabled` (default `True`): si `False`, se preserva
el comportamiento previo (`'omitido'`, TERMINAL, sin AIP) como ventana de
transición/seguro operacional.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import asyncpg
from orpycamcp_common import audit

from app.clients import document_client, signature_client, storage_client
from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.repositories.aip_repository import AipRepository
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.services.backoff import next_backoff_seconds
from app.services.retention_math import RetencionDiferida, base_worm, total_trd_years

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


async def _huella_indice_original(tenant_slug: str, minio_key_firmado: str) -> str | None:
    """SHA-256 (hex) de los bytes del XML firmado ORIGINAL del índice, o ``None`` si no se pudo
    descargar. Es la identidad de la versión 1 del AIP cuando la firma no tiene renovaciones."""
    data = await storage_client.download_signed_bytes(
        tenant_slug=tenant_slug, minio_key_firmado=minio_key_firmado
    )
    return hashlib.sha256(data).hexdigest() if data else None


class AipWiringService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._aip = AipRepository(conn)
        self._archive = ArchiveRepository(conn, tenant_slug)
        self._index = IndexRepository(conn)

    async def _resolve_aip_retention(
        self, exp
    ) -> tuple[int, date] | RetencionDiferida | None:
        """`(retencion_anios_total, fecha_inicio=fecha_cierre)` — MISMA
        derivación que `IndexService._resolve_worm_retention` (TRD total,
        `fecha_inicio=closed_at`), replicada aquí (self-contained, no importa
        de `IndexService`) para no acoplar este servicio a los internos de
        otro. Fail-closed (``None``) en cualquier caso dudoso.

        ADR-026 (Increment 3, D4, AREA-06-04): para `exp.origen == 'fondo_acumulado'`
        la base es `fecha_extrema_final` (`retention_math.base_worm`), no `closed_at`; con
        el calendario ya vencido devuelve `RetencionDiferida` tras asegurar el hold
        `valoracion_pendiente` (ADR-043) y lanza `ValoracionHoldError` si no se pudo."""
        if exp.closed_at is None or exp.trd_serie_id is None:
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        total = total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)
        if total is None:
            return None
        inicio = base_worm(
            origen=exp.origen,
            closed_at=exp.closed_at,
            fecha_extrema_final=exp.fecha_extrema_final,
            total_years=total,
            margen_dias=settings.worm_piso_margen_dias,
        )
        if inicio is None:
            return None
        if isinstance(inicio, RetencionDiferida):
            from app.services.valoracion_service import ValoracionService

            return await ValoracionService(self._conn, self._tenant_slug).resolver_diferida(
                exp, serie
            )
        return total, inicio

    async def _resolve_aip_pdfa_profile(self, exp) -> str | None:
        """Perfil PDF/A (E10 debt "perfil por serie", RF-FIR-15) de la serie
        TRD del expediente -- gemelo de `_resolve_aip_retention`. `None` si
        el expediente no tiene serie, la serie no existe, o la serie no tiene
        política propia (`trd_series.pdfa_profile IS NULL`) -- en cualquiera
        de esos casos storage-service cae a su default global
        (`settings.pdfa_profile`), comportamiento IDÉNTICO al actual
        (retrocompat). NO reintenta ni bloquea el empaquetado: a diferencia
        de `_resolve_aip_retention` (cuyo `None` SÍ dispara backoff, la
        retención es obligatoria), un perfil ausente es una configuración
        válida, no un error transitorio."""
        if exp.trd_serie_id is None:
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        return serie.pdfa_profile

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
                    # Acto de sistema (reconciliación): atribuido al principal de
                    # sistema, no `None` (deuda E10: unificar con los demás
                    # asientos de renovación/reconciliación).
                    actor=str(SYSTEM_RECONCILER_ID),
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
            # B10 §8 / C-6: si el ArchiveTimeStamp del índice se renovó, el AIP embebe el XML
            # VIGENTE (que contiene todos los sellos), no el original. Tri-estado: un ERROR de
            # signature-service NO es «sin renovación» -- antes se empaquetaba el original en
            # silencio y el AIP nacía con una prueba que caduca (H2). Ahora se reintenta.
            indice_xml_sha256: str | None = None
            if indice_row.get("firma_id"):
                vigente = await signature_client.resolver_artefacto_vigente(
                    tenant_slug=self._tenant_slug, firma_id=str(indice_row["firma_id"])
                )
                if isinstance(vigente, signature_client.ErrorArtefacto):
                    return await self._bump_backoff(
                        expediente_id,
                        intentos_previos=int(row["intentos"]),
                        motivo="artefacto_vigente_no_resuelto",
                    )
                if isinstance(vigente, signature_client.ArtefactoVigente):
                    indice_file_id = vigente.file_id
                    indice_xml_sha256 = vigente.xml_sha256
            if indice_xml_sha256 is None:
                # Sin renovación: la huella del AIP v1 es la del XML firmado original.
                indice_xml_sha256 = await _huella_indice_original(
                    self._tenant_slug, indice_row["minio_key_firmado"]
                )
                if indice_xml_sha256 is None:
                    return await self._bump_backoff(
                        expediente_id,
                        intentos_previos=int(row["intentos"]),
                        motivo="indice_xml_no_resuelto",
                    )

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

            if not documentos and not settings.aip_indice_only_enabled:
                # Expediente 100% físico (o sin anexos digitales en ningún
                # radicado vigente) CON el kill-switch índice-only en OFF —
                # comportamiento PREVIO al follow-up: sin AIP, TERMINAL, no
                # reintentable. Su preservación ya la cubre el índice
                # electrónico firmado+protegido bajo WORM (E10 Inc.1).
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

            # `documentos` puede llegar vacía aquí (expediente 100% físico,
            # kill-switch índice-only en ON -- default): `indice_file_id`
            # SIEMPRE está disponible en este punto (la precondición
            # firmado+worm_protegido_at+minio_key_firmado ya pasó más arriba
            # -- si no, se salió por `_bump_backoff` antes de llegar aquí),
            # así que el AIP índice-only SIEMPRE es construible
            # (`AipCreate` acepta `documentos=[]` con `indice_file_id`
            # presente -- ver `aip_builder._is_indice_only_mode` en
            # storage-service). `omitido` NO se produce por esta vía nueva
            # -- solo lo produce el kill-switch OFF (arriba) o filas
            # históricas anteriores a este follow-up.
            from app.services.valoracion_service import ValoracionHoldError

            try:
                retention = await self._resolve_aip_retention(exp)
            except ValoracionHoldError:
                logger.warning(
                    "Cableado AIP: no se pudo fijar el hold de valoración (expediente_id=%s)",
                    expediente_id,
                    exc_info=True,
                )
                return await self._bump_backoff(
                    expediente_id,
                    intentos_previos=int(row["intentos"]),
                    motivo="hold_valoracion_no_fijado",
                )
            if retention is None:
                return await self._bump_backoff(
                    expediente_id,
                    intentos_previos=int(row["intentos"]),
                    motivo="trd_sin_retencion",
                )
            # Fondo vencido: sin retencion_anios/fecha_inicio, solo el hold (ADR-043).
            if isinstance(retention, RetencionDiferida):
                retencion_anios, fecha_inicio, diferida = None, None, retention.motivo
            else:
                retencion_anios, fecha_inicio = retention
                diferida = None
            pdfa_profile = await self._resolve_aip_pdfa_profile(exp)

            outcome = await storage_client.empaquetar_aip_internal(
                tenant_slug=self._tenant_slug,
                expediente_id=str(expediente_id),
                documentos=documentos,
                indice_file_id=indice_file_id,
                retencion_anios=retencion_anios,
                fecha_inicio=fecha_inicio,
                retencion_diferida=diferida,
                pdfa_profile=pdfa_profile,
            )

            reconciliado = False
            if outcome.ya_existe:
                # 409 `aip_ya_existe` NO es fixity: storage ya tiene el AIP (un empaquetado cuya
                # respuesta se perdió). Se reconcilia con el GET interno ANTES de registrar: solo
                # se da por empaquetado si lo vigente es el AIP que storage informó.
                outcome = await self._reconciliar_aip_existente(expediente_id, outcome)
                reconciliado = outcome.ok
            if outcome.ok:
                assert outcome.aip_id is not None
                async with self._conn.transaction():
                    affected = await self._aip.mark_empaquetado(
                        expediente_id,
                        outcome.aip_id,
                        indice_file_id=indice_file_id,
                        indice_xml_sha256=indice_xml_sha256,
                        firma_id=indice_row.get("firma_id"),
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
                                "retencion_diferida": diferida,
                                # E10 AIP wiring follow-up: marca honesta para
                                # los auditores -- distingue el AIP índice-only
                                # (sin anexos digitales, el índice ES el
                                # payload) del AIP con contenido real.
                                "indice_only": not documentos,
                                # El AIP ya existía en storage (409 `aip_ya_existe`): se
                                # registró lo que storage tiene, no un paquete recién armado.
                                "reconciliado_con_storage": reconciliado,
                            },
                        )
                return "empaquetado" if affected else "omitido"

            motivo = (
                "fixity_mismatch"
                if outcome.fixity_mismatch
                else "worm_disabled"
                if outcome.disabled
                else "aip_ya_existe_sin_reconciliar"
                if outcome.ya_existe
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

    async def _reconciliar_aip_existente(
        self, expediente_id: uuid.UUID, outcome: storage_client.AipPackagingOutcome
    ) -> storage_client.AipPackagingOutcome:
        """Resuelve un 409 `aip_ya_existe` contra `GET /preservacion/aip/{id}` (interno).

        `ok=True` solo si storage confirma un AIP vigente y, cuando el 409 informó su id, es ese
        mismo. Cualquier otra cosa (404, id distinto, storage caído) deja `ya_existe=True`: el
        llamador lo trata como reintentable, nunca como una mutación de anexo."""
        try:
            vigente = await storage_client.get_aip(
                tenant_slug=self._tenant_slug, expediente_id=str(expediente_id)
            )
        except storage_client.StorageAipError:
            return outcome
        if vigente is None or not vigente.get("id"):
            return outcome
        try:
            aip_id = uuid.UUID(str(vigente["id"]))
        except ValueError:
            return outcome
        if outcome.aip_id is not None and outcome.aip_id != aip_id:
            return outcome
        retain_until: datetime | None = None
        try:
            if vigente.get("retain_until"):
                retain_until = datetime.fromisoformat(str(vigente["retain_until"]))
        except (TypeError, ValueError):
            retain_until = None
        return storage_client.AipPackagingOutcome(ok=True, aip_id=aip_id, retain_until=retain_until)

    # -- E10 debt: renovación de retención WORM para series CT -------------
    # (extensión del AIP a la 5ª fase de reconciliación -- espejo EXACTO de
    # `IndexService._resolve_ct_retencion_anios`/`_bump_worm_renovacion_backoff`/
    # `renovar_worm_indice`, ver ese servicio para el diseño completo.)

    async def _resolve_ct_retencion_anios(self, expediente_id: uuid.UUID) -> int | None:
        """Total TRD (ambas fases) del expediente -- versión de RENOVACIÓN:
        la ventana rodante de renovación siempre usa `fecha_inicio=hoy` (no
        `closed_at`), así que este helper SOLO necesita el total y no exige
        `exp.closed_at` (aunque en la práctica siempre está poblado -- un AIP
        solo llega a `estado='empaquetado'` tras el cierre).

        ADR-026 (Increment 3, D4): sin guard para fondo acumulado (la ventana
        rodante parte de hoy, no de la fecha extrema)."""
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
                    actor=str(SYSTEM_RECONCILER_ID),
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
                            actor=str(SYSTEM_RECONCILER_ID),
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

    # -- C-6 (ADR-042): reempaquetado del AIP al renovar el sello de archivo ---------------
    # Fase `run_once_aip_reempaquetado`: BARRIDO contra `firma_artefacto_vigente` en vez de un
    # consumidor de `firma.lta_renovada` (el evento es best-effort y sin outbox: perderlo dejaba
    # el AIP desfasado para siempre). Conservación: corre también en entidades suspendidas.

    async def _bump_reempaque_backoff(
        self,
        expediente_id: uuid.UUID,
        *,
        intentos_previos: int,
        motivo: str,
        puede_agotar: bool = True,
    ) -> Literal["pendiente", "agotado"]:
        """Backoff PROPIO del reempaquetado (columnas `reempaque_*`); no toca `estado`. Un fallo
        de LECTURA de signature (`puede_agotar=False`) solo espacia el reintento: no es un
        defecto del expediente y agotarlo lo dejaría sordo a futuras renovaciones. Al agotar,
        el estado y su asiento son UN acto (misma transacción)."""
        intentos_tras_este = intentos_previos + 1
        agotado = puede_agotar and intentos_tras_este >= settings.indice_reconciliation_max_intentos
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
        async with self._conn.transaction():
            await self._aip.bump_reempaque_intento(
                expediente_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
            )
            if agotado:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.aip_reempaque_agotado",
                    object_type="expediente_aip",
                    object_ref=str(expediente_id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={"intentos": intentos_tras_este, "motivo": motivo},
                )
        return "agotado" if agotado else "pendiente"

    async def reempaquetar_por_renovacion(
        self, expediente_id: uuid.UUID, vigente: signature_client.ArtefactoVigente
    ) -> Literal["reempaquetado", "omitido", "pendiente", "agotado"]:
        """Crea la versión N+1 del AIP con el índice renovado. Best-effort, NUNCA lanza.

        Idempotente: bajo el advisory lock del expediente se RELEE la huella embebida; si ya
        coincide con la vigente no hay trabajo (otra pasada/réplica ganó). El contenido lo
        reconstruye storage-service desde el bag anterior (no se vuelve a pedir nada a
        document-service), de modo que NO depende de que la entidad esté activa."""
        got_lock = False
        try:
            got_lock = bool(
                await self._conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:aip_reemp'), "
                    "hashtext($1))",
                    str(expediente_id),
                )
            )
            if not got_lock:
                return "omitido"
            fila = await self._aip.get_para_reempaque(expediente_id)
            if fila is None or fila["estado"] != "empaquetado" or fila["reempaque_agotado"]:
                return "omitido"
            if fila["indice_xml_sha256"] == vigente.xml_sha256:
                return "omitido"
            anterior = fila["aip_vigente_id"] or fila["aip_worm_object_id"]
            if anterior is None:
                return await self._bump_reempaque_backoff(
                    expediente_id,
                    intentos_previos=int(fila["reempaque_intentos"]),
                    motivo="aip_anterior_desconocido",
                )
            outcome = await storage_client.reempaquetar_aip_internal(
                tenant_slug=self._tenant_slug,
                expediente_id=str(expediente_id),
                aip_anterior_id=anterior,
                indice_file_id=vigente.file_id,
                indice_xml_sha256=vigente.xml_sha256,
                firma_id=vigente.firma_id,
                ordinal=vigente.ordinal,
            )
            if outcome.ya_sustituido:
                # El AIP «vigente» que archive conocía ya tiene sucesor (un corte entre storage y
                # archive, o una renovación posterior): se resincroniza con la CABEZA real de
                # storage y se reintenta UNA vez sobre ella.
                cabeza = await self._cabeza_en_storage(expediente_id)
                if cabeza is not None and cabeza != anterior:
                    outcome = await storage_client.reempaquetar_aip_internal(
                        tenant_slug=self._tenant_slug,
                        expediente_id=str(expediente_id),
                        aip_anterior_id=cabeza,
                        indice_file_id=vigente.file_id,
                        indice_xml_sha256=vigente.xml_sha256,
                        firma_id=vigente.firma_id,
                        ordinal=vigente.ordinal,
                    )
            if outcome.ok:
                assert outcome.aip_id is not None and outcome.version is not None
                try:
                    async with self._conn.transaction():
                        await self._aip.insert_version(
                            expediente_id,
                            version=outcome.version,
                            aip_id=outcome.aip_id,
                            indice_file_id=vigente.file_id,
                            indice_xml_sha256=vigente.xml_sha256,
                            firma_id=uuid.UUID(vigente.firma_id),
                            firma_ordinal=vigente.ordinal,
                            motivo="renovacion_lta",
                            sustituye_a_aip_id=outcome.sustituye_a,
                        )
                        await self._aip.mark_reempaquetado(
                            expediente_id,
                            aip_vigente_id=outcome.aip_id,
                            indice_xml_sha256=vigente.xml_sha256,
                            proximo_at=datetime.now(tz=UTC)
                            + timedelta(seconds=settings.aip_reempaquetado_intervalo_s),
                            worm_retain_until=outcome.retain_until,
                        )
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant_slug,
                            service=_SERVICE,
                            action="archive.aip_reempaquetado",
                            object_type="expediente_aip",
                            object_ref=str(expediente_id),
                            actor=str(SYSTEM_RECONCILER_ID),
                            canal="system",
                            payload={
                                "expediente_id": str(expediente_id),
                                "aip_id": str(outcome.aip_id),
                                "sustituye_a": str(outcome.sustituye_a),
                                "version": outcome.version,
                                "firma_id": vigente.firma_id,
                                "ordinal": vigente.ordinal,
                                "indice_xml_sha256": vigente.xml_sha256,
                            },
                        )
                except asyncpg.UniqueViolationError:
                    # Otra pasada ya registró esta huella/versión: nada que hacer.
                    logger.info("Reempaquetado AIP: versión ya registrada (expediente_id=%s)", expediente_id)
                    return "omitido"
                return "reempaquetado"

            motivo = (
                "aip_ya_sustituido"
                if outcome.ya_sustituido
                else "aip_anterior_fixity_mismatch"
                if outcome.fixity_mismatch
                else "indice_no_es_renovacion_del_embebido"
                if outcome.indice_invalido
                else "aip_not_found"
                if outcome.not_found
                else "worm_disabled"
                if outcome.disabled
                else "reempaque_no_confirmado"
            )
            return await self._bump_reempaque_backoff(
                expediente_id, intentos_previos=int(fila["reempaque_intentos"]), motivo=motivo
            )
        except Exception:
            logger.warning(
                "Reempaquetado del AIP falló (best-effort), expediente_id=%s",
                expediente_id,
                exc_info=True,
            )
            return "pendiente"
        finally:
            if got_lock:
                try:
                    await self._conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:aip_reemp'), "
                        "hashtext($1))",
                        str(expediente_id),
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("No se pudo liberar el lock de reempaquetado", exc_info=True)

    async def _cabeza_en_storage(self, expediente_id: uuid.UUID) -> uuid.UUID | None:
        try:
            aip = await storage_client.get_aip(
                tenant_slug=self._tenant_slug, expediente_id=str(expediente_id)
            )
            return uuid.UUID(str(aip["id"])) if aip else None
        except (storage_client.StorageAipError, KeyError, ValueError):
            return None

    async def barrer_reempaquetado(self, batch: int) -> dict[str, int]:
        """Una pasada para este tenant: compara el XML vigente de cada AIP empaquetado con la
        huella embebida y reempaqueta las que difieren. Un fallo de LECTURA en signature NO se
        interpreta como «sin renovación» (backoff, nunca se asume el original)."""
        res = {"revisados": 0, "reempaquetados": 0, "sin_cambio": 0, "fallidos": 0, "agotados": 0}
        intervalo = timedelta(seconds=settings.aip_reempaquetado_intervalo_s)
        vistos: set[uuid.UUID] = set()
        while True:
            filas = [
                f
                for f in await self._aip.candidatos_reempaque(batch)
                if f["expediente_id"] not in vistos
            ]
            if not filas:
                break
            vistos.update(f["expediente_id"] for f in filas)
            res["revisados"] += len(filas)
            lote = await signature_client.artefactos_vigentes(
                tenant_slug=self._tenant_slug,
                firma_ids=sorted({str(f["firma_id"]) for f in filas}),
            )
            if lote is None:
                for f in filas:
                    r = await self._bump_reempaque_backoff(
                        f["expediente_id"],
                        intentos_previos=int(f["reempaque_intentos"]),
                        motivo="artefacto_vigente_no_resuelto",
                        puede_agotar=False,
                    )
                    res["fallidos"] += 1 if r == "pendiente" else 0
                continue
            sin_cambio: list[uuid.UUID] = []
            for f in filas:
                v = lote.get(str(f["firma_id"]))
                if v is None or v.xml_sha256 == f["indice_xml_sha256"]:
                    sin_cambio.append(f["expediente_id"])
                    res["sin_cambio"] += 1
                    continue
                resultado = await self.reempaquetar_por_renovacion(f["expediente_id"], v)
                if resultado == "reempaquetado":
                    res["reempaquetados"] += 1
                elif resultado == "agotado":
                    res["agotados"] += 1
                elif resultado == "pendiente":
                    res["fallidos"] += 1
                else:
                    sin_cambio.append(f["expediente_id"])
            await self._aip.marcar_revisados(sin_cambio, proximo_at=datetime.now(tz=UTC) + intervalo)
        return res
