# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-09-05 — contrato del relé del outbox contra Postgres REAL.

Los casos de `test_t13_cumplimiento_integration.py` fijan el ENCOLADO y el camino feliz
del relé. Aquí, los bordes que solo existen con la tabla de verdad:
- la gracia: un pendiente joven NO se reenvía (lo atiende aún el camino rápido);
- el reenvío conserva el `event_id` (el consumidor deduplica por él) y el `dedup_key`;
- ante un fallo de Redis se detiene, cuenta el intento y deja el motivo;
- la retención retira SOLO lo ya enviado y viejo, nunca un pendiente.

Mutaciones verificadas: quitar `AND creado_at < now() - ...` → cae el de gracia; generar
un `event_id` nuevo en `retransmitir` → cae el de identidad; quitar el `break` → cae el de
fallo; quitar `enviado_at IS NOT NULL` del DELETE → cae el de retención.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.services import outbox

pytestmark = pytest.mark.integration


async def _en(tenant_ctx) -> asyncpg.Connection:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    return conn


async def _encolar(conn, clave: str) -> str:
    p = await outbox.encolar(
        conn,
        stream="orpycamcp.document.events",
        event_type="document.radicado.reclassified",
        tenant_slug="demo",
        actor=None,
        dedup_key=clave,
        payload={"tracking_number": clave, "nivel_nuevo": 3},
    )
    return p["event"]["event_id"]


class _Redis:
    def __init__(self, falla_en: int | None = None) -> None:
        self.campos: list[dict] = []
        self.falla_en = falla_en

    async def xadd(self, stream, campos, *a, **k):
        if self.falla_en is not None and len(self.campos) + 1 == self.falla_en:
            raise ConnectionError("redis caído")
        self.campos.append(campos)
        return b"1-0"


def _sobre(campos: dict) -> dict:
    """El sobre publicado: lo que el consumidor lee (cualquier forma serializada del evento)."""
    return json.loads(json.dumps({k: v.decode() if isinstance(v, bytes) else v for k, v in campos.items()}))


@pytest.mark.asyncio
async def test_un_pendiente_joven_no_se_reenvia_y_uno_viejo_si(tenant_ctx) -> None:
    conn = await _en(tenant_ctx)
    joven = await _encolar(conn, "J-1")
    viejo = await _encolar(conn, "V-1")
    await conn.execute(
        "UPDATE eventos_salientes SET creado_at = now() - interval '5 minutes' WHERE event_id = $1",
        viejo,
    )
    redis = _Redis()
    assert await outbox.retransmitir(conn, redis) == 1  # gracia por defecto: 60 s
    assert viejo in repr(redis.campos) and joven not in repr(redis.campos)
    assert (
        await conn.fetchval("SELECT enviado_at FROM eventos_salientes WHERE event_id = $1", joven)
    ) is None


@pytest.mark.asyncio
async def test_el_reenvio_conserva_el_event_id_y_la_clave_de_deduplicacion(tenant_ctx) -> None:
    conn = await _en(tenant_ctx)
    eid = await _encolar(conn, "T-9:3")
    redis = _Redis()
    assert await outbox.retransmitir(conn, redis, gracia_segundos=0) == 1
    texto = repr(redis.campos)
    assert eid in texto, "el consumidor deduplica por event_id: un id nuevo duplicaría el efecto"
    assert "T-9:3" in texto


@pytest.mark.asyncio
async def test_un_fallo_de_redis_se_detiene_cuenta_el_intento_y_deja_el_motivo(tenant_ctx) -> None:
    conn = await _en(tenant_ctx)
    ids = [await _encolar(conn, f"F-{i}") for i in range(3)]
    redis = _Redis(falla_en=2)  # el 1.º sale; el 2.º falla; el 3.º ni se intenta
    assert await outbox.retransmitir(conn, redis, gracia_segundos=0) == 1
    filas = {
        r["event_id"]: r
        for r in await conn.fetch(
            "SELECT event_id, enviado_at, intentos, ultimo_error FROM eventos_salientes"
        )
    }
    assert filas[ids[0]]["enviado_at"] is not None
    assert filas[ids[1]]["enviado_at"] is None and filas[ids[1]]["intentos"] == 1
    assert "redis" in filas[ids[1]]["ultimo_error"]
    assert filas[ids[2]]["enviado_at"] is None and filas[ids[2]]["intentos"] == 0
    # Vuelve Redis: sale lo que faltaba, sin repetir lo ya enviado, y limpia el error.
    bueno = _Redis()
    assert await outbox.retransmitir(conn, bueno, gracia_segundos=0) == 2
    assert ids[0] not in repr(bueno.campos)
    assert await conn.fetchval("SELECT COUNT(*) FROM eventos_salientes WHERE ultimo_error IS NOT NULL") == 0


@pytest.mark.asyncio
async def test_la_retencion_retira_lo_enviado_viejo_y_nunca_un_pendiente(tenant_ctx) -> None:
    conn = await _en(tenant_ctx)
    enviado_viejo = await _encolar(conn, "R-1")
    enviado_reciente = await _encolar(conn, "R-2")
    pendiente_viejo = await _encolar(conn, "R-3")
    await conn.execute(
        "UPDATE eventos_salientes SET enviado_at = now() - interval '30 days' WHERE event_id = $1",
        enviado_viejo,
    )
    await conn.execute(
        "UPDATE eventos_salientes SET enviado_at = now() WHERE event_id = $1", enviado_reciente
    )
    await conn.execute(
        "UPDATE eventos_salientes SET creado_at = now() - interval '30 days' WHERE event_id = $1",
        pendiente_viejo,
    )
    # Con Redis caído el pendiente viejo no sale; igual debe sobrevivir a la retención.
    await outbox.retransmitir(conn, _Redis(falla_en=1), gracia_segundos=0)
    quedan = {r["event_id"] for r in await conn.fetch("SELECT event_id FROM eventos_salientes")}
    assert enviado_viejo not in quedan
    assert enviado_reciente in quedan and pendiente_viejo in quedan
    assert uuid.UUID(pendiente_viejo)
