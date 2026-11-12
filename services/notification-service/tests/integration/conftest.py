# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

"""Ayudas del carril de integración: un schema de tenant con la cadena REAL (ADR-053).

`public` lo migra antes `services/docuboto/migrations/runner.py`; aquí solo se crea
y destruye el schema de cada prueba con la cadena de tenant completa.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
from orpycamcp_common import migrations

# La cadena única de migraciones (ADR-053): `global/` -> public, `tenant/` -> tenant_*.
CHAIN = Path(__file__).resolve().parents[4] / "services" / "docuboto" / "migrations"


async def drop_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
    assert schema.startswith("tenant_itest_"), (
        f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r}"
    )
    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)


async def provision_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
    """Crea *schema* y le aplica la cadena de tenant completa, la de producción."""
    if await conn.fetchval("SELECT to_regclass('public.schema_migrations')") is None:
        raise RuntimeError(
            "public no está migrado: corre antes `python services/docuboto/migrations/runner.py` "
            "con MIGRATIONS_DATABASE_URL apuntando a esta base."
        )
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await migrations.apply_chain(conn, CHAIN / "tenant", schema, log=lambda _msg: None)


async def insert_radicado(conn: asyncpg.Connection, tracking_number: str, *, nivel: int = 1) -> uuid.UUID:
    """Radicado mínimo VÁLIDO para el esquema real (NOT NULL y CHECK de clasificación)."""
    causal = {1: None, 2: "por_clasificar", 3: "art18_a_intimidad"}[nivel]
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code, "
        "nivel_seguridad, clasificacion_causal, clasificacion_fundamento) "
        "VALUES ($1, 'E', 2026, 1, 'Asunto de prueba', 'ITEST', $2, $3, $4) RETURNING id",
        tracking_number,
        nivel,
        causal,
        "Fundamento de prueba" if nivel >= 2 else None,
    )


async def set_security_level(conn: asyncpg.Connection, radicado_id: uuid.UUID, nivel: int) -> None:
    causal = {1: None, 2: "por_clasificar", 3: "art18_a_intimidad"}[nivel]
    await conn.execute(
        "UPDATE radicados SET nivel_seguridad = $2, clasificacion_causal = $3, "
        "clasificacion_fundamento = $4 WHERE id = $1",
        radicado_id,
        nivel,
        causal,
        "Fundamento de prueba" if nivel >= 2 else None,
    )
