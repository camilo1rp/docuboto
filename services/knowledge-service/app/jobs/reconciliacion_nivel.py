# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Barrido de reconciliación nivel-del-fragmento vs ``radicados.nivel_seguridad`` (B10, ítem 3).

La LECTURA ya no depende de este barrido: ``search`` y ``get_pivot_chunk`` aplican el
nivel vivo en el ``WHERE``. Este job existe para lo que ese gate no alcanza: un fragmento
indexado con un proveedor EXTERNO cuyo radicado subió a RESERVADA o más no puede seguir
en el índice aunque nadie lo busque, y el nivel guardado debe converger al vigente.

**Solo SUBE.** Una reclasificación cuya transacción empezó antes de leer y confirmó
después lleva un ``occurred_at`` anterior a esta lectura; si el barrido bajara el nivel o
marcara ``nivel_evento_at = now()``, o bien ignoraría ese evento legítimo o bien rompería
la red de ``occurred_at``. Bajar sigue siendo cosa exclusiva de un ``reclassified``. Un
fragmento sobreclasificado solo cuesta disponibilidad (no es fuga): se cuenta en el log.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import asyncpg
from orpycamcp_common import audit
from orpycamcp_common.request_log import canal_de_fondo

from app.core.clearance import RESERVADA, is_local_embedding_provider
from app.core.config import settings
from app.core.database import acquire_tenant, get_pool
from app.jobs.deadletter_retention import list_active_tenants
from app.repositories.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)

_SERVICE = "knowledge-service"
_LOTE = 500
_REFS_EN_ASIENTO = 50
_UUID_CERO = uuid.UUID(int=0)


async def reconciliar_tenant(conn: asyncpg.Connection, slug: str) -> dict[str, int]:
    """Un recorrido por los fragmentos de radicado desactualizados de UN tenant.

    Por lote (keyset de 500) y en UNA transacción: las correcciones y el asiento
    ``knowledge.nivel_reconciliado`` (``actor="system"``, ``canal="system"``) van juntos;
    si el asiento falla, ninguna corrección del lote queda.
    """
    repo = KnowledgeRepository(conn)
    externo = not is_local_embedding_provider(settings.embedding_provider)
    subidos = tombstoneados = 0
    despues = _UUID_CERO
    while True:
        filas = await repo.lote_niveles_desactualizados(despues, _LOTE)
        if not filas:
            break
        despues = filas[-1]["id"]
        async with conn.transaction():
            s = t = 0
            refs: list[str] = []
            for f in filas:
                vivo = int(f["vivo"])
                if vivo >= RESERVADA and externo:
                    # Sin ``occurred_at``: el barrido no es un evento (ver módulo).
                    await repo.tombstone_by_source("radicado", f["source_ref"], vivo, None)
                    t += 1
                else:
                    await repo.subir_nivel(f["id"], vivo)
                    s += 1
                if len(refs) < _REFS_EN_ASIENTO:
                    refs.append(f["source_ref"])
            await audit.append(
                conn,
                tenant_slug=slug,
                service=_SERVICE,
                action="knowledge.nivel_reconciliado",
                object_type="knowledge_chunk",
                object_ref=None,
                actor="system",
                canal="system",
                payload={"subidos": s, "tombstoneados": t, "refs": refs},
            )
        subidos += s
        tombstoneados += t
    sobreclasificados = await repo.contar_sobreclasificados()
    if subidos or tombstoneados or sobreclasificados:
        logger.info(
            "Reconciliación de nivel tenant=%s: subidos=%d tombstoneados=%d "
            "sobreclasificados=%d",
            slug, subidos, tombstoneados, sobreclasificados,
        )
    return {
        "subidos": subidos,
        "tombstoneados": tombstoneados,
        "sobreclasificados": sobreclasificados,
    }


async def reconciliar_una_vez() -> dict[str, dict[str, int]]:
    """Una pasada por todos los tenants activos. El fallo de uno no aborta el resto."""
    resumen: dict[str, dict[str, int]] = {}
    for slug in await list_active_tenants(get_pool()):
        try:
            async with acquire_tenant(slug) as conn:
                resumen[slug] = await reconciliar_tenant(conn, slug)
        except asyncpg.UndefinedTableError:
            # Tenant sin ``radicados`` o sin ``knowledge_chunk``: nada que reconciliar.
            logger.debug("reconciliación de nivel: tenant=%s sin las tablas necesarias", slug)
        except Exception:
            logger.exception("reconciliación de nivel: tenant=%s omitido por un fallo", slug)
    return resumen


async def bucle_de_reconciliacion() -> None:
    """Tarea de fondo del ``lifespan``: barre cada ``reconciliacion_nivel_intervalo_s``."""
    canal_de_fondo()  # proceso de fondo: sus asientos son del canal 'system' (AREA-03-07)
    while True:
        await asyncio.sleep(settings.reconciliacion_nivel_intervalo_s)
        try:
            await reconciliar_una_vez()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reconciliación de nivel: la pasada falló")
