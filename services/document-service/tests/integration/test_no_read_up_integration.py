# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) del no-read-up en firmas, anulación y envíos (RF-SEG-08).

Cierra el mismo patrón que `test_disposition_no_read_up_integration.py` en los
tres repositorios restantes que alcanzaban `radicados` sin gate de clearance.

Todos los gates son cláusulas SQL (`WHERE EXISTS`, `nivel_seguridad <= $N`), así
que la única prueba que vale es contra el motor: donde hay escritura se verifica
**por mutación** — tras el intento sobre-clearance se cuentan las filas de la
tabla destino y deben seguir en cero.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.anulacion_repository import AnulacionRepository
from app.repositories.postal_repository import PostalRepository
from app.repositories.signature_repository import SignatureRepository

pytestmark = pytest.mark.integration

PUBLICA = 1
CLASIFICADA = 3

_HASH = "a" * 64


async def _seed_radicado(conn: asyncpg.Connection, *, nivel: int, doc_type: str = "E") -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, "
        "tenant_code, nivel_seguridad, clasificacion_fundamento) VALUES "
        "($1, $2, 2026, 1, 'ASUNTO', 'DEMO', $3, "
        "CASE WHEN $3::smallint >= 2 THEN 'Ley 1712/2014 art. 19 (prueba)' END) "
        "RETURNING id",
        f"2026-DEMO-{doc_type}-{uuid.uuid4().hex[:6]}",
        doc_type,
        nivel,
    )


async def _en_tenant(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    return slug, conn


# ---------------------------------------------------------------------------
# Firmas (E06)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firma_sobre_clearance_no_se_inserta(tenant_ctx) -> None:
    """Firmar un radicado que no se puede ver no crea la firma (verificado por mutación).

    Una firma es un acto probatorio: si se persistiera, quedaría en el expediente
    una atestación sobre un documento que el firmante nunca pudo leer.
    """
    _, conn = await _en_tenant(tenant_ctx)
    repo = SignatureRepository(conn)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA)

    assert (
        await repo.insert(
            radicado_id=rid,
            anexo_id=None,
            signer_id=None,
            signer_name="intruso",
            content_hash=_HASH,
            reason="intento",
            max_clearance=PUBLICA,
        )
        is None
    )
    assert await conn.fetchval("SELECT count(*) FROM signatures WHERE radicado_id = $1", rid) == 0

    firmada = await repo.insert(
        radicado_id=rid,
        anexo_id=None,
        signer_id=None,
        signer_name="autorizado",
        content_hash=_HASH,
        reason="ok",
        max_clearance=CLASIFICADA,
    )
    assert firmada is not None
    assert await conn.fetchval("SELECT count(*) FROM signatures WHERE radicado_id = $1", rid) == 1


@pytest.mark.asyncio
async def test_radicado_visible_distingue_nivel_y_no_existencia(tenant_ctx) -> None:
    """`radicado_visible` devuelve False igual para «no existe» y «te supera».

    Es lo que permite al router responder un 404 idéntico en ambos casos, de modo
    que el listado/verificación de firmas no sea un oráculo de existencia.
    """
    _, conn = await _en_tenant(tenant_ctx)
    repo = SignatureRepository(conn)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA)

    assert await repo.radicado_visible(rid, PUBLICA) is False
    assert await repo.radicado_visible(uuid.uuid4(), CLASIFICADA) is False
    assert await repo.radicado_visible(rid, CLASIFICADA) is True


# ---------------------------------------------------------------------------
# Anulación
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anulacion_no_alcanza_radicado_sobre_clearance(tenant_ctx) -> None:
    """Las tres puertas de entrada del servicio de anulación filtran por clearance.

    `radicado_status` es la que decide el 404 de «solicitar»; `get_active` la del
    aprobar/rechazar; `get_latest` la de la consulta de estado. Si alguna no
    filtrara, la causal y el motivo —que describen el documento— se filtrarían.
    """
    _, conn = await _en_tenant(tenant_ctx)
    repo = AnulacionRepository(conn)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA)
    await repo.insert_solicitud(
        radicado_id=rid, causal="ANU-ERR", motivo="MOTIVO RESERVADO", solicitado_por=None
    )

    assert await repo.radicado_status(rid, PUBLICA) is None
    assert await repo.get_active(rid, PUBLICA) is None
    assert await repo.get_latest(rid, PUBLICA) is None

    assert await repo.radicado_status(rid, CLASIFICADA) == "registered"
    assert await repo.get_active(rid, CLASIFICADA) is not None
    latest = await repo.get_latest(rid, CLASIFICADA)
    assert latest is not None and latest["motivo"] == "MOTIVO RESERVADO"


# ---------------------------------------------------------------------------
# Envíos postales (E20)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_envio_sobre_clearance_no_se_registra(tenant_ctx) -> None:
    """Despachar un radicado invisible no crea envío NI su evento inicial.

    El INSERT del envío y el del evento `registrado` van en la misma transacción;
    se comprueban las dos tablas para descartar que el gate cortara solo la
    primera mitad.
    """
    _, conn = await _en_tenant(tenant_ctx)
    repo = PostalRepository(conn)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA, doc_type="S")

    assert (
        await repo.insert(
            radicado_id=rid,
            operador="4-72",
            guia=f"C472-{uuid.uuid4().hex[:8]}",
            destinatario="X",
            direccion="Y",
            max_clearance=PUBLICA,
        )
        is None
    )
    assert await conn.fetchval("SELECT count(*) FROM postal_shipment") == 0
    assert await conn.fetchval("SELECT count(*) FROM postal_shipment_event") == 0

    row = await repo.insert(
        radicado_id=rid,
        operador="4-72",
        guia=f"C472-{uuid.uuid4().hex[:8]}",
        destinatario="X",
        direccion="Y",
        max_clearance=CLASIFICADA,
    )
    assert row is not None
    assert await conn.fetchval("SELECT count(*) FROM postal_shipment_event") == 1
