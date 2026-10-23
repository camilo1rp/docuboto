# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El acuse de recibo es de UN solo envío: guarda de motor (B2, ADR-031, migración 045).

Contra Postgres REAL: el servicio rechaza un acuse ya referenciado, pero la carrera
entre dos registros simultáneos que pasan el cotejo a la vez solo la cierra el índice
único parcial. Mutación verificada: quitar el índice de la 045 → el segundo UPDATE pasa.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.postal_repository import PostalRepository

pytestmark = pytest.mark.integration


async def _envio(conn: asyncpg.Connection, n: int) -> asyncpg.Record:
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, "
        "tenant_code, registered_by, nivel_seguridad) "
        "VALUES ($1, 'S', 2026, $2, 'x', 'T', gen_random_uuid(), 1) RETURNING id",
        f"2026-T-S-00000{n}",
        n,
    )
    repo = PostalRepository(conn)
    envio = await repo.insert(
        radicado_id=rid, operador="4-72", guia=f"C472-{n}", destinatario=None,
        direccion=None, max_clearance=3,
    )
    await repo.update_estado(
        envio["id"], "en_transito", None, estado_previo="registrado", max_clearance=3
    )
    return envio


@pytest.mark.asyncio
async def test_el_mismo_acuse_no_puede_ser_de_dos_envios(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    repo = PostalRepository(conn)
    a, b = await _envio(conn, 1), await _envio(conn, 2)
    acuse = uuid.uuid4()

    await repo.update_estado(
        a["id"], "entregado", None, estado_previo="en_transito", max_clearance=3,
        acuse_file_id=acuse, acuse_sha256="a" * 64,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.update_estado(
            b["id"], "entregado", None, estado_previo="en_transito", max_clearance=3,
            acuse_file_id=acuse, acuse_sha256="a" * 64,
        )
    # El segundo no quedó entregado ni con acuse.
    fila = await conn.fetchrow(
        "SELECT estado, acuse_file_id FROM postal_shipment WHERE id = $1", b["id"]
    )
    assert (fila["estado"], fila["acuse_file_id"]) == ("en_transito", None)


@pytest.mark.asyncio
async def test_envios_sin_acuse_no_chocan_entre_si(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El índice es PARCIAL: NULL no cuenta (los envíos sin acuse son la mayoría)."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await _envio(conn, 1)
    await _envio(conn, 2)
    assert await conn.fetchval(
        "SELECT count(*) FROM postal_shipment WHERE acuse_file_id IS NULL"
    ) == 2


@pytest.mark.asyncio
async def test_la_045_aborta_si_ya_hay_acuses_repetidos(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Datos previos a la guarda: no se escoge cuál conservar, se corta y se dice."""
    from pathlib import Path

    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    a, b = await _envio(conn, 1), await _envio(conn, 2)
    acuse = uuid.uuid4()
    await conn.execute("DROP INDEX IF EXISTS postal_shipment_acuse_file_id_uq")
    await conn.execute(
        "UPDATE postal_shipment SET acuse_file_id = $1 WHERE id = ANY($2::uuid[])",
        acuse, [a["id"], b["id"]],
    )
    sql = (
        Path(__file__).resolve().parents[2] / "migrations" / "tenant" / "045_acuse_unico.sql"
    ).read_text(encoding="utf-8")
    with pytest.raises(asyncpg.RaiseError, match=str(acuse)):
        await conn.execute(sql)
