# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Renovación del `ArchiveTimeStamp` de las firmas XAdES-LTA (B10 §8, ETSI EN 319 132-1 §5.5.2).

Un sello de archivo prueba mientras viva el certificado de su TSA. Antes de que caduque se SELLA DE
NUEVO el conjunto —incluidos los sellos anteriores y un `TimeStampValidationData` con la cadena de la
TSA del último—: el XML resultante es un objeto NUEVO en storage (el original está bajo COMPLIANCE y
es evidencia), registrado en `firma_sello_archivo`.

Por firma candidata (la última fila `vigente` con la TSA a menos de `lta_renovacion_margen_dias`):

  0. Si el certificado de la TSA del último sello YA caducó, no se renueva (EN 319 102-1 §5.6.2: un
     sello nuevo sobre una TSA caducada rompe la cadena y el artefacto se invalidaría a sí mismo,
     ya bajo COMPLIANCE): el eslabón pasa a `caducado` y se asienta `firma.lta_cadena_caducada`.
  1. Se descarga el XML y se VERIFICA (`verify_xades`, toda la cadena). Si no verifica NO se renueva:
     renovar sellaría algo inválido (asiento `firma.lta_renovacion_rechazada`).
  2. La TSA tiene que dar más plazo que la anterior (+ margen); si no, se aborta
     (`firma.lta_renovacion_tsa_por_vencer`) — de lo contrario se renovaría a diario.
     Y el `TimeStampValidationData` necesita la revocación de la TSA anterior (CRL de su CA o, si no
     publica CRL, su OCSP; la TSA autofirmada no tiene): si no se puede obtener o verificar, no se
     renueva (`firma.lta_renovacion_sin_revocacion_tsa`); si la TSA figura revocada ANTES de sellar,
     tampoco (`firma.lta_renovacion_tsa_revocada`).
     El plazo de cada eslabón es lo primero que caduque de la TSA y de su CA (`vence_at`, C-6).
     Los asientos de rechazo, TSA por vencer y sin revocación se escriben UNA vez por motivo y
     eslabón (`ultimo_aviso`); las pasadas siguientes solo dejan un WARNING en el log.
  3. Se construye el XML renovado, se sube, y en UNA tx se inserta el eslabón (`pendiente_worm`) y el
     asiento `firma.lta_renovada`.
  4. Storage hereda la política WORM y deja el evento PREMIS; entonces el eslabón pasa a `vigente`.
     Si el paso 4 falla, el eslabón queda `pendiente_worm` y la siguiente vuelta reintenta SOLO eso.

Exclusión mutua por firma: advisory lock de sesión `hashtext('lta:'||firma_id)`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
from orpycamcp_common.request_log import canal_de_fondo

from app.clients import preservacion_client, storage_client
from app.core import tsa
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import acquire_tenant, get_pool
from app.core.events import publish_lta_renovada
from app.core.redis_client import get_redis
from app.providers.local import renovar_archive_timestamp, verify_xades
from app.repositories.firma_repository import FirmaRepository

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger("signature.renovacion_lta")
_SERVICE = "signature-service"


async def _asiento(
    conn: asyncpg.Connection, slug: str, action: str, firma_id: uuid.UUID, payload: dict
) -> None:
    if not _AUDIT_AVAILABLE:
        return
    await _audit.append(
        conn,
        tenant_slug=slug,
        service=_SERVICE,
        action=action,
        object_type="firma",
        object_ref=str(firma_id),
        actor=str(SYSTEM_RECONCILER_ID),
        payload=payload,
        canal="system",
    )


async def _tenants_de_conservacion(pool: asyncpg.Pool) -> list[str]:
    """Entidades cuyos sellos de archivo hay que vigilar: ACTIVAS y SUSPENDIDAS. La obligación de
    conservar (Ley 594 art. 46; Ac. AGN 003/2015) no depende del estado administrativo de la
    entidad, y un plazo que corre sin que nadie lo vea es el mismo defecto de B9. Se excluyen las
    dadas de baja (`inactive`) y las sin aprovisionar."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug FROM public.tenants "
            "WHERE status IN ('active', 'suspended') AND provisioning_status = 'active'"
        )
    return [r["slug"] for r in rows]


async def _aviso_unico(
    conn: asyncpg.Connection, slug: str, c: asyncpg.Record, aviso: str, action: str, payload: dict
) -> None:
    """Asienta ``action`` solo si el motivo CAMBIÓ respecto a la pasada anterior de este eslabón;
    si es el mismo, solo un WARNING (el audit_log es inmutable: no se llena a diario)."""
    async with conn.transaction():
        nuevo = await FirmaRepository(conn).registrar_aviso(c["id"], aviso[:80])
        if nuevo:
            await _asiento(conn, slug, action, c["firma_id"], payload)
    logger.warning(
        "renovación LTA firma=%s ordinal=%s: %s%s", c["firma_id"], c["ordinal"], aviso,
        "" if nuevo else " (ya asentado)",
    )


def _file_id(minio_key: str) -> uuid.UUID:
    return uuid.UUID(minio_key.split("/", 1)[0])


async def _completar_worm(
    conn: asyncpg.Connection, slug: str, sello: asyncpg.Record, minio_key_anterior: str
) -> bool:
    """Paso de storage (herencia WORM + PREMIS) y cierre del eslabón. `False` si storage falló."""
    try:
        await preservacion_client.renovar_artefacto(
            tenant_slug=slug,
            file_id_anterior=_file_id(minio_key_anterior),
            file_id_nuevo=_file_id(sello["minio_key"]),
            sha256_nuevo=sello["xml_sha256"],
            firma_id=sello["firma_id"],
            ordinal=sello["ordinal"],
        )
    except preservacion_client.PreservacionError:
        logger.warning(
            "renovación LTA firma=%s ordinal=%s: storage no registró la herencia WORM; "
            "queda pendiente_worm y se reintenta",
            sello["firma_id"], sello["ordinal"], exc_info=True,
        )
        return False
    async with conn.transaction():
        await FirmaRepository(conn).marcar_sello_vigente(sello["id"])
        # El paso a `vigente` es lo que hace que /verify acepte el XML nuevo y que GET /firmas
        # lo sirva como vigente: deja asiento en la misma transacción.
        await _asiento(
            conn, slug, "firma.lta_sello_vigente", sello["firma_id"],
            {
                "ordinal": sello["ordinal"],
                "xml_sha256": sello["xml_sha256"],
                "file_id": str(_file_id(sello["minio_key"])),
            },
        )
    # Aviso best-effort (B10 §8): el eslabón ya es vigente y no se deshace por un fallo de Redis.
    try:
        await publish_lta_renovada(
            get_redis(),
            tenant_slug=slug,
            firma_id=str(sello["firma_id"]),
            ordinal=sello["ordinal"],
            minio_key=sello["minio_key"],
            xml_sha256=sello["xml_sha256"],
            gen_time=sello["gen_time"].isoformat(),
        )
    except Exception:  # noqa: BLE001
        logger.warning("renovación LTA firma=%s: evento no publicado", sello["firma_id"], exc_info=True)
    return True


async def _renovar_firma(conn: asyncpg.Connection, slug: str, c: asyncpg.Record) -> str:
    """Renueva UNA firma candidata. Devuelve el resultado: renovada | rechazada | tsa_por_vencer |
    pendiente_worm | error."""
    repo = FirmaRepository(conn)
    firma_id = c["firma_id"]
    margen = timedelta(days=settings.lta_renovacion_margen_dias)

    # 0. ¿Ya caducó la TSA del último sello (o su CA, lo primero que venza)? Renovar ahora sellaría con
    #    un hueco en la prueba.
    if datetime.now(UTC) >= c["vence_at"]:
        async with conn.transaction():
            await repo.marcar_sello_caducado(c["id"])
            await _asiento(
                conn, slug, "firma.lta_cadena_caducada", firma_id,
                {
                    "ordinal": c["ordinal"],
                    "tsa_cert_not_after": c["tsa_cert_not_after"].isoformat(),
                    "vence_at": c["vence_at"].isoformat(),
                    "tsa_cert_fingerprint": c["tsa_cert_fingerprint"],
                },
            )
        logger.error(
            "renovación LTA firma=%s ordinal=%s: el certificado de su TSA caducó el %s sin "
            "renovarse; la cadena de sellos ya no prueba",
            firma_id, c["ordinal"], c["vence_at"].isoformat(),
        )
        return "caducada"

    sellos = await repo.sellos_de(firma_id)
    try:
        xml = await preservacion_client.descargar_xml(tenant_slug=slug, minio_key=c["minio_key"])
    except preservacion_client.PreservacionError:
        logger.warning("renovación LTA firma=%s: no se pudo descargar el XML", firma_id, exc_info=True)
        return "error"

    # 1. Verificar ANTES de renovar.
    motivo: str | None = None
    if hashlib.sha256(xml).hexdigest() != c["xml_sha256"]:
        motivo = "xml_no_coincide_con_el_registro"
    else:
        outcome = verify_xades(
            xml,
            c["certificado_fingerprint"],
            expected_tsa_fingerprint=c["firma_tsa_fp"],
            expected_ca_fingerprint=c["ca_fingerprint"],
            expected_ocsp_responder_fingerprint=c["ocsp_responder_fingerprint"],
            tsa_fingerprints_archivo=[s["tsa_cert_fingerprint"] for s in sellos],
        )
        if not outcome.valida:
            motivo = outcome.motivo or "no_verifica"
        elif outcome.xades_level != "XAdES-LTA":
            motivo = f"nivel_{outcome.xades_level}_no_es_lta"
    if motivo is not None:
        await _aviso_unico(
            conn, slug, c, f"rechazada:{motivo}", "firma.lta_renovacion_rechazada",
            {"ordinal": c["ordinal"], "motivo": motivo},
        )
        return "rechazada"

    # 2. ¿La TSA con que se sellaría da más plazo? (la local se conoce sin pedir nada)
    async def _por_vencer(not_after_nuevo) -> str:
        await _aviso_unico(
            conn, slug, c, "tsa_por_vencer", "firma.lta_renovacion_tsa_por_vencer",
            {
                "ordinal": c["ordinal"],
                "tsa_cert_not_after_anterior": c["vence_at"].isoformat(),
                "tsa_cert_not_after_nueva": not_after_nuevo.isoformat(),
                "margen_dias": settings.lta_renovacion_margen_dias,
            },
        )
        return "tsa_por_vencer"

    try:
        cert_previo = tsa.cert_tsa_vigente()
        if cert_previo is not None and cert_previo.not_valid_after_utc <= c["vence_at"] + margen:
            return await _por_vencer(cert_previo.not_valid_after_utc)
        # 3. XML renovado (el sellado puede bloquear: TSA remota).
        renovacion = await asyncio.to_thread(renovar_archive_timestamp, xml)
    except tsa.RevocacionTsaNoDisponibleError as exc:
        await _aviso_unico(
            conn, slug, c, "sin_revocacion_tsa", "firma.lta_renovacion_sin_revocacion_tsa",
            {"ordinal": c["ordinal"], "motivo": str(exc)},
        )
        return "sin_revocacion"
    except tsa.TsaRevocadaError as exc:
        await _aviso_unico(
            conn, slug, c, "tsa_revocada", "firma.lta_renovacion_tsa_revocada",
            {"ordinal": c["ordinal"], "motivo": str(exc)},
        )
        return "tsa_revocada"
    except tsa.TsaUnavailableError:
        logger.warning("renovación LTA firma=%s: TSA no disponible", firma_id, exc_info=True)
        return "error"
    # La TSA nueva tiene que dar más plazo que lo que vencía: lo primero que caduque de ella y de su CA.
    if renovacion.vence_at <= c["vence_at"] + margen:
        return await _por_vencer(renovacion.vence_at)

    nombre_original = sellos[0]["minio_key"].split("/", 1)[1]
    base = nombre_original.removesuffix(".xml")
    filename = f"{base}-lta{renovacion.ordinal}.xml"
    try:
        minio_key_nuevo = await storage_client.upload_signed_xml(
            tenant_slug=slug, filename=filename, content=renovacion.signed_xml,
            uploaded_by=SYSTEM_RECONCILER_ID,
        )
    except storage_client.StorageUploadError:
        logger.warning("renovación LTA firma=%s: no se pudo subir el XML renovado", firma_id, exc_info=True)
        return "error"

    xml_sha256 = hashlib.sha256(renovacion.signed_xml).hexdigest()
    async with conn.transaction():
        sello = await repo.insert_sello_archivo(
            firma_id=firma_id,
            ordinal=renovacion.ordinal,
            minio_key=minio_key_nuevo,
            xml_sha256=xml_sha256,
            gen_time=renovacion.token.gen_time,
            tsa_cert_fingerprint=renovacion.token.tsa_cert_fingerprint,
            tsa_cert_not_after=renovacion.tsa_cert_not_after,
            tsa_cadena_not_after=renovacion.tsa_cadena_not_after,
            tsa_revocacion_fuente=renovacion.tsa_revocacion_fuente,
            revocacion_tsa_previa=renovacion.revocacion_tsa_previa,
            estado="pendiente_worm",
        )
        await _asiento(
            conn, slug, "firma.lta_renovada", firma_id,
            {
                "ordinal": renovacion.ordinal,
                "xml_sha256": xml_sha256,
                "gen_time": renovacion.token.gen_time.isoformat(),
                "tsa_cert_fingerprint": renovacion.token.tsa_cert_fingerprint,
                "tsa_cert_not_after": renovacion.tsa_cert_not_after.isoformat(),
                "vence_at": renovacion.vence_at.isoformat(),
                "revocacion_tsa_previa": renovacion.revocacion_tsa_previa,
                "file_anterior": c["minio_key"].split("/", 1)[0],
            },
        )

    # 4. Herencia WORM + PREMIS en storage.
    return "renovada" if await _completar_worm(conn, slug, sello, c["minio_key"]) else "pendiente_worm"


async def renovar_tenant(conn: asyncpg.Connection, slug: str) -> dict[str, int]:
    """Una pasada sobre un tenant: primero los pasos de storage pendientes, luego las candidatas."""
    resumen = {
        "pendientes_completados": 0, "renovadas": 0, "rechazadas": 0,
        "tsa_por_vencer": 0, "pendiente_worm": 0, "errores": 0, "caducadas": 0,
        "sin_revocacion": 0, "tsa_revocada": 0,
    }
    repo = FirmaRepository(conn)

    for pendiente in await repo.renovaciones_pendientes_worm():
        if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", f"lta:{pendiente['firma_id']}"):
            continue
        try:
            if await _completar_worm(conn, slug, pendiente, pendiente["minio_key_anterior"]):
                resumen["pendientes_completados"] += 1
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", f"lta:{pendiente['firma_id']}")

    for candidata in await repo.firmas_a_renovar(settings.lta_renovacion_margen_dias):
        clave = f"lta:{candidata['firma_id']}"
        if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", clave):
            continue  # otra réplica la está renovando
        try:
            resultado = await _renovar_firma(conn, slug, candidata)
        except Exception:
            logger.exception("renovación LTA firma=%s falló", candidata["firma_id"])
            resultado = "error"
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", clave)
        clave_resumen = {"renovada": "renovadas", "rechazada": "rechazadas", "error": "errores", "caducada": "caducadas"}.get(
            resultado, resultado
        )
        resumen[clave_resumen] += 1
    return resumen


async def run_once() -> dict[str, dict[str, int]]:
    """Una pasada por todos los tenants activos y suspendidos (la conservación no se suspende)."""
    totales: dict[str, dict[str, int]] = {}
    for slug in await _tenants_de_conservacion(get_pool()):
        try:
            async with acquire_tenant(slug) as conn:
                totales[slug] = await renovar_tenant(conn, slug)
        except asyncpg.UndefinedTableError:
            logger.debug("renovación LTA: tenant=%s sin las tablas de signature-service", slug)
        except Exception:
            logger.warning("renovación LTA: tenant=%s omitido por un fallo", slug, exc_info=True)
    return totales


async def run() -> None:
    """Tarea de fondo del `lifespan` (`spawn_supervised`): espera un poco y renueva cada intervalo."""
    canal_de_fondo()
    await asyncio.sleep(settings.lta_renovacion_retraso_inicial_s)
    while True:
        try:
            resumen = await run_once()
            if any(any(v.values()) for v in resumen.values()):
                logger.info("renovación LTA: %s", resumen)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("renovación LTA: pasada fallida")
        await asyncio.sleep(settings.lta_renovacion_intervalo_s)
