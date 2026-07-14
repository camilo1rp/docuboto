# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Smoke test de la infraestructura de integración (piloto).

NO prueba atomicidad ni reglas de negocio (eso lo cubre `testing-engineer` después):
solo valida que la infra funciona de punta a punta — conexión real, provisión de
schema + tenant migrations, bootstrap de `audit_log`, y que el commit real + la
cadena de auditoría sobreviven fuera de la transacción del SUT.
"""

from __future__ import annotations

import asyncpg
import pytest
from orpycamcp_common import audit

from app.core.database import tenant_schema
from app.schemas.archive import ExpedienteCreate
from app.services.archive_service import ArchiveService

from .conftest import DSN


@pytest.mark.integration
async def test_create_expediente_persists_and_chains(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    service = ArchiveService(conn, slug)
    payload = ExpedienteCreate(name="itest expediente", nivel_seguridad=1)

    # `opened_by=None` → `caller_clearance(None)` resuelve a PUBLICA=1 sin tocar
    # las tablas RBAC (`resolve_caller_clearance` corta antes de consultarlas), así
    # que `nivel_seguridad=1` pasa el gate no-write-up sin sembrar datos de RBAC.
    response = await service.create_expediente(payload=payload, opened_by=None)

    # Durabilidad real: se lee desde una SEGUNDA conexión asyncpg, no desde `conn`
    # (que ya vio su propio commit) — así se prueba el commit real, no solo la
    # visibilidad intra-conexión/intra-tx.
    schema = tenant_schema(slug)
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        persisted_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expedientes WHERE code = $1',
            response.code,
        )
        audit_count = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_created",
            response.code,
        )
    finally:
        await second_conn.close()

    assert persisted_count == 1
    assert audit_count == 1

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None
