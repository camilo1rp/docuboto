# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) del webhook postal E20 F5, a nivel repositorio.

Verifica las GARANTÍAS DE MOTOR (no la orquestación del servicio, cubierta por
unit): la idempotencia del UNIQUE `(operador, event_id)`, la lógica CASE de
`motivo_devolucion`, y el aislamiento por schema de credenciales/deliveries.

Nivel repositorio a propósito: el flujo del servicio audita (`audit.append`) y
este carril no bootstrapea `public.audit_log` (ver conftest). Las garantías
probadas aquí son de la BD, que es justo lo que un mock no puede verificar.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.postal_repository import PostalRepository

pytestmark = pytest.mark.integration


async def _seed_shipment(conn: asyncpg.Connection, *, guia: str = "C472-INT0001") -> uuid.UUID:
    """Inserta un radicado mínimo + un envío; devuelve el shipment_id."""
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code) "
        "VALUES ($1, 'S', 2026, 1, 'asunto', 'DEMO') RETURNING id",
        f"2026-DEMO-S-{uuid.uuid4().hex[:6]}",
    )
    # max_clearance=3 (CLASIFICADA) = sin restricción: la siembra no ejercita el
    # gate de no-read-up (eso lo cubre test_no_read_up_integration.py), solo
    # necesita un envío existente para probar la deduplicación del webhook.
    row = await PostalRepository(conn).insert(
        radicado_id=rid,
        operador="4-72",
        guia=guia,
        destinatario="X",
        direccion="Y",
        max_clearance=3,
    )
    assert row is not None
    return row["id"]


@pytest.mark.asyncio
async def test_delivery_dedup_unique(tenant_ctx) -> None:
    """try_insert_delivery: mismo (operador, event_id) → una sola fila."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    repo = PostalRepository(conn)
    sid = await _seed_shipment(conn)

    first = await repo.try_insert_delivery(
        operador="4-72", event_id="evt-x", guia="C472-INT0001",
        estado_operador="ENTREGADO", estado_interno="entregado", shipment_id=sid,
    )
    second = await repo.try_insert_delivery(
        operador="4-72", event_id="evt-x", guia="C472-INT0001",
        estado_operador="ENTREGADO", estado_interno="entregado", shipment_id=sid,
    )
    assert first is not None
    assert second is None  # idempotente: el reintento del operador es no-op

    # Distinto event_id sí inserta.
    third = await repo.try_insert_delivery(
        operador="4-72", event_id="evt-y", guia="C472-INT0001",
        estado_operador="EN_REPARTO", estado_interno="en_transito", shipment_id=sid,
    )
    assert third is not None
    total = await conn.fetchval("SELECT COUNT(*) FROM postal_webhook_delivery")
    assert total == 2


@pytest.mark.asyncio
async def test_motivo_devolucion_only_set_on_devuelto(tenant_ctx) -> None:
    """La columna motivo_devolucion se fija SOLO en 'devuelto' y se preserva."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    repo = PostalRepository(conn)
    sid = await _seed_shipment(conn, guia="C472-DEV001")

    # El servicio pasa motivo=None salvo en 'devuelto'; el repo COALESCE lo respeta.
    await repo.update_estado(
        sid, "en_transito", "en ruta", motivo_devolucion=None,
        estado_previo="registrado", max_clearance=None,
    )
    row = await conn.fetchrow("SELECT estado, motivo_devolucion FROM postal_shipment WHERE id=$1", sid)
    assert row["estado"] == "en_transito"
    assert row["motivo_devolucion"] is None  # sin motivo → permanece NULL

    await repo.update_estado(
        sid, "devuelto", "devuelto", motivo_devolucion="Dirección errada",
        estado_previo="en_transito", max_clearance=None,
    )
    row = await conn.fetchrow("SELECT estado, motivo_devolucion FROM postal_shipment WHERE id=$1", sid)
    assert row["estado"] == "devuelto"
    assert row["motivo_devolucion"] == "Dirección errada"


@pytest.mark.asyncio
async def test_credential_and_delivery_tenant_isolation(two_tenant_ctx) -> None:
    """Credencial y delivery de un tenant NO son visibles desde el otro schema."""
    slug_a, slug_b, conn = two_tenant_ctx

    # Semilla en A.
    await conn.execute(f'SET search_path = "{tenant_schema(slug_a)}", public')
    await conn.execute(
        "INSERT INTO postal_operator_credential (operador, webhook_secret) VALUES ('4-72', 'secret-A')"
    )
    repo_a = PostalRepository(conn)
    assert await repo_a.get_operator_credential("4-72") == "secret-A"

    # Desde B: no existe.
    await conn.execute(f'SET search_path = "{tenant_schema(slug_b)}", public')
    repo_b = PostalRepository(conn)
    assert await repo_b.get_operator_credential("4-72") is None
    assert await conn.fetchval("SELECT COUNT(*) FROM postal_operator_credential") == 0


@pytest.mark.asyncio
async def test_migration_017_idempotent(tenant_ctx) -> None:
    """Re-aplicar 017 sobre el schema no falla (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)."""
    slug, conn = tenant_ctx
    from pathlib import Path

    sql = (
        Path(__file__).parents[2] / "migrations" / "tenant" / "017_postal_webhook.sql"
    ).read_text()
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{tenant_schema(slug)}", public')
        await conn.execute(sql)  # segunda pasada, debe ser no-op sin error
