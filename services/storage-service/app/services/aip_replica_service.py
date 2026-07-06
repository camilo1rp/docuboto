# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Réplica best-effort del AIP empaquetado (E10 Increment C1b, RF-FIR-15).

Da una SEGUNDA copia inmutable del ZIP del AIP ya sellado en el bucket WORM
primario (`aip_service.empaquetar_aip`), gateada por
``PRESERVATION_REPLICA_ENABLED`` y el ``num_copias`` del plan de preservación
vigente:

- ``num_copias`` es el TOTAL de copias (1 primaria + N réplicas).
  ``num_copias <= 1`` (o sin plan) -> CERO réplicas, se respeta la política
  de copia única. ``num_copias >= 2`` -> se dispara UNA réplica (el fan-out
  multi-réplica, para ``num_copias > 2``, es roadmap — con un solo target de
  réplica disponible en este incremento, el déficit se registra con
  honestidad en el evento, nunca se simulan N copias en el mismo MinIO).

- Sin ``MINIO_SECONDARY_ENDPOINT`` configurado, el target es LOCAL: un
  segundo bucket del MISMO MinIO primario (server-side ``copy_object``).
  Esto NO es una copia geográfica y así se etiqueta siempre (RF-FIR-15) —
  nunca "cross-site" cuando es el mismo endpoint. Con el endpoint secundario
  configurado, el target es CROSS-SITE: un segundo cliente MinIO hacia un
  host distinto, con ``put_object`` (no existe copia server-side entre
  endpoints distintos).

- BEST-EFFORT NO-FATAL: el AIP primario ya está sellado + WORM cuando se
  intenta la réplica. `replicate_aip` NUNCA propaga una excepción — cualquier
  fallo de I/O de MinIO (bucket, red, credenciales) se captura aquí mismo y
  se refleja en el `ReplicaResult` devuelto (`ok=False`); es responsabilidad
  del llamador (`aip_service.empaquetar_aip`) invocar esta función ANTES de
  abrir la transacción crítica del AIP, para que un fallo de réplica jamás
  la contamine ni la revierta.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from miniopy_async import Minio  # type: ignore[attr-defined]
from miniopy_async.commonconfig import CopySource
from miniopy_async.retention import Retention

from app.core.config import settings
from app.core.minio_client import ensure_object_lock_bucket

logger = logging.getLogger(__name__)

_BUCKET_PREFIX = "orpycamcp"

_secondary_minio: Minio | None = None


def replica_bucket_name(tenant_slug: str) -> str:
    """Nombre del bucket réplica del tenant.

    ``tenant_slug`` SIEMPRE viene del claim JWT re-derivado por el gateway
    (mismo contrato que ``preservation_bucket_name`` del bucket primario) —
    nunca del body de la petición: envenenar el WORM de otro tenant con una
    réplica cruzada es tan irreversible como envenenar el original.
    """
    return f"{_BUCKET_PREFIX}-{tenant_slug}-{settings.preservation_replica_bucket_suffix}"


def _get_secondary_minio() -> Minio:
    """Cliente MinIO perezoso hacia el segundo endpoint (réplica cross-site).

    Singleton propio, separado de ``app.core.minio_client`` porque apunta a
    un HOST DISTINTO — nunca se comparte con el cliente primario. Las
    credenciales secundarias son material secreto: no se loguean en ningún
    punto de este módulo.
    """
    global _secondary_minio
    if _secondary_minio is None:
        _secondary_minio = Minio(
            settings.minio_secondary_endpoint,
            access_key=settings.minio_secondary_access_key,
            secret_key=settings.minio_secondary_secret_key,
            secure=settings.minio_secondary_use_ssl,
        )
    return _secondary_minio


@dataclass(slots=True)
class ReplicaResult:
    """Resultado de un intento de réplica. `replicate_aip` solo devuelve esto
    (o ``None`` si la réplica no aplica) — nunca lanza."""

    ok: bool
    target: Literal["local", "cross-site"]
    bucket: str
    object_key: str
    copias_solicitadas: int
    copias_hechas: int
    motivo_fallo: str | None = None

    @property
    def detalle(self) -> dict:
        """Payload para `preservacion_evento.detalle` (tipo=REPLICA, PIEZA 5)."""
        nota = (
            "réplica local (segundo bucket, mismo MinIO) — NO es copia geográfica"
            if self.target == "local"
            else "réplica cross-site (segundo endpoint MinIO configurado)"
        )
        d: dict = {
            "target": self.target,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "copias_solicitadas": self.copias_solicitadas,
            "copias_hechas": self.copias_hechas,
            "nota": nota,
        }
        if self.motivo_fallo is not None:
            d["motivo_fallo"] = self.motivo_fallo
        return d


async def replicate_aip(
    *,
    primary_minio: Minio,
    tenant_slug: str,
    primary_bucket: str,
    object_key: str,
    zip_bytes: bytes,
    retention_mode: str,
    retain_until: datetime,
    legal_hold: bool,
    num_copias: int,
) -> ReplicaResult | None:
    """Intenta UNA réplica del AIP ya subido al bucket primario.

    Devuelve ``None`` cuando la réplica NO aplica (`PRESERVATION_REPLICA_ENABLED`
    apagado, o ``num_copias <= 1`` — copia única declarada por el plan). En
    cualquier otro caso SIEMPRE devuelve un `ReplicaResult` — un fallo de I/O
    de MinIO se captura y se refleja como ``ok=False`` (best-effort no-fatal,
    nunca propaga para no tumbar la transacción del AIP primario).
    """
    if not settings.preservation_replica_enabled:
        return None
    if num_copias <= 1:
        return None

    cross_site = bool(settings.minio_secondary_endpoint)
    target: Literal["local", "cross-site"] = "cross-site" if cross_site else "local"
    bucket = replica_bucket_name(tenant_slug)
    retention = Retention(retention_mode, retain_until)

    try:
        if cross_site:
            minio = _get_secondary_minio()
            await ensure_object_lock_bucket(minio, bucket)
            await minio.put_object(
                bucket,
                object_key,
                io.BytesIO(zip_bytes),
                length=len(zip_bytes),
                content_type="application/zip",
                retention=retention,
                legal_hold=legal_hold,
            )
        else:
            await ensure_object_lock_bucket(primary_minio, bucket)
            await primary_minio.copy_object(
                bucket,
                object_key,
                CopySource(primary_bucket, object_key),
                retention=retention,
                legal_hold=legal_hold,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort no-fatal, ver docstring del módulo.
        logger.error(
            "Réplica del AIP falló (tenant=%s, target=%s, bucket=%s, object_key=%s): %s",
            tenant_slug,
            target,
            bucket,
            object_key,
            exc,
        )
        return ReplicaResult(
            ok=False,
            target=target,
            bucket=bucket,
            object_key=object_key,
            copias_solicitadas=num_copias,
            copias_hechas=1,
            motivo_fallo=str(exc),
        )

    if num_copias > 2:
        # Déficit honesto (PIEZA 3): el plan pide más copias totales de las
        # que este incremento puede materializar con un único target de
        # réplica. Nunca se simulan N buckets locales — "3 copias en el
        # mismo MinIO es teatro" (contrato C1b).
        logger.warning(
            "Plan de preservación pide num_copias=%d pero solo hay un target "
            "de réplica disponible en C1b (tenant=%s): copias_hechas=2, "
            "déficit registrado honestamente en el evento.",
            num_copias,
            tenant_slug,
        )

    return ReplicaResult(
        ok=True,
        target=target,
        bucket=bucket,
        object_key=object_key,
        copias_solicitadas=num_copias,
        copias_hechas=2,
    )
