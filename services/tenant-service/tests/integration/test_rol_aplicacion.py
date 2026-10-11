# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Tier de integración: tenant-service con un rol SIN privilegios de propietario (OMI-02).

Postgres REAL. La premisa que se prueba: el servicio hace todo lo suyo (alta del
registro con asiento atómico, cambio de estado, parámetros, dependencias) con un
rol que no puede ejecutar DDL ni mutar `audit_log`.

El rol es DESECHABLE (`itest_app_<hex>`): lo crea y lo borra la propia prueba con
los mismos privilegios que la migración 008 de auth-service concede a
`orpycamcp_app`. NO se toca `orpycamcp_app` ni su LOGIN.

Gate: solo corre con `RUN_INTEGRATION=1`. Nunca re-ejecuta migraciones globales:
solo aplica las de tenant sobre un esquema `tenant_itest_<hex>` propio.
"""

from __future__ import annotations

import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import asyncpg
import pytest
import pytest_asyncio

from app.repositories.config_repository import ConfigRepository
from app.repositories.dependencias_repository import DependenciaRepository
from app.schemas.tenant import TenantCreate, TenantUpdate
from app.services.tenant_service import TenantService
from migrations import runner

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1", reason="carril de integración: RUN_INTEGRATION=1"
)

_RAW = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
_OWNER_DSN = _RAW.replace("postgresql+asyncpg://", "postgresql://")


@dataclass
class Ctx:
    owner: asyncpg.Connection
    app: asyncpg.Connection
    schema: str
    slug: str
    role: str


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[Ctx, None]:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    if host not in {"postgres", "localhost", "127.0.0.1"}:
        raise RuntimeError(f"host {host!r} fuera de la allowlist de integración")
    hexa = uuid.uuid4().hex[:8]
    slug = f"alta-{hexa}"
    schema = f"tenant_itest_{hexa}"
    role = f"itest_app_{hexa}"
    password = uuid.uuid4().hex
    owner = await asyncpg.connect(_OWNER_DSN)
    app: asyncpg.Connection | None = None
    try:
        db = await owner.fetchval("SELECT current_database()")
        await owner.execute(f'CREATE SCHEMA "{schema}"')
        for path in runner._sql_files(runner.TENANT_DIR):
            async with owner.transaction():
                await owner.execute(f'SET LOCAL search_path = "{schema}", public')
                await owner.execute(path.read_text())
        # Tablas mínimas de workflow-service (otro servicio) para la correlación por código.
        await owner.execute(
            f'CREATE TABLE "{schema}".flow_steps (id serial PRIMARY KEY, '
            "to_dept_code int, status varchar(20))"
        )
        await owner.execute(
            f'CREATE TABLE "{schema}".workflow_rules (id serial PRIMARY KEY, '
            "assign_to_dept_code int, dest_dept_code int, conditions jsonb NOT NULL DEFAULT '[]')"
        )
        # Mismos privilegios que 008 concede a orpycamcp_app.
        await owner.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        await owner.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {role}')
        for esq in ("public", schema):
            await owner.execute(f'GRANT USAGE ON SCHEMA "{esq}" TO {role}')
            await owner.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{esq}" TO {role}'
            )
            await owner.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{esq}" TO {role}')
            await owner.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA "{esq}" TO {role}')
        await owner.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_log FROM {role}")
        for parte in await owner.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid "
            "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'audit_log'"
        ):
            await owner.execute(
                f'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public."{parte["relname"]}" FROM {role}'
            )
        p = urllib.parse.urlparse(_OWNER_DSN)
        app = await asyncpg.connect(
            host=p.hostname, port=p.port or 5432, user=role, password=password, database=db
        )
        yield Ctx(owner, app, schema, slug, role)
    finally:
        if app is not None:
            await app.close()
        assert schema.startswith("tenant_itest_")
        await owner.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await owner.execute("DELETE FROM public.tenants WHERE slug = $1", slug)
        await owner.execute(f"DROP OWNED BY {role}")
        await owner.execute(f"DROP ROLE IF EXISTS {role}")
        await owner.close()


def _create(slug: str) -> TenantCreate:
    return TenantCreate(slug=slug, name="Entidad itest", code=f"IT{slug[-8:].upper()}")


async def test_el_rol_no_puede_estructurar_ni_reescribir_la_auditoria(ctx: Ctx) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await ctx.app.execute("UPDATE public.audit_log SET actor = 'x' WHERE false")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await ctx.app.execute("DELETE FROM public.audit_log WHERE false")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await ctx.app.execute('CREATE SCHEMA "tenant_itest_nope"')


async def test_alta_con_rol_de_aplicacion_asienta_y_no_crea_esquema(ctx: Ctx) -> None:
    tenant = await TenantService(ctx.app).create(_create(ctx.slug))
    assert tenant.slug == ctx.slug
    pend = await ctx.owner.fetchval(
        "SELECT provisioning_status FROM public.tenants WHERE slug = $1", ctx.slug
    )
    assert pend == "pending"
    asiento = await ctx.owner.fetchrow(
        "SELECT action, tenant_slug, canal, payload FROM public.audit_log "
        "WHERE action = 'tenant.created' AND object_ref = $1",
        ctx.slug,
    )
    assert asiento is not None
    assert asiento["tenant_slug"] == "__system__" and asiento["canal"] == "system"
    # El esquema NO se crea desde el servicio (AREA-10-13).
    esq = "tenant_" + ctx.slug.replace("-", "_")
    assert await ctx.owner.fetchval(
        "SELECT 1 FROM pg_namespace WHERE nspname = $1", esq
    ) is None


async def test_alta_sin_asiento_revierte_la_fila(ctx: Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import audit as audit_core

    async def boom(*a: object, **k: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit_core, "append", boom)
    with pytest.raises(RuntimeError):
        await TenantService(ctx.app).create(_create(ctx.slug))
    assert await ctx.owner.fetchval("SELECT 1 FROM public.tenants WHERE slug = $1", ctx.slug) is None


async def test_suspension_asienta_antes_y_despues(ctx: Ctx) -> None:
    svc = TenantService(ctx.app)
    t = await svc.create(_create(ctx.slug))
    await svc.update(t.id, TenantUpdate(status="suspended"))
    payload = await ctx.owner.fetchval(
        "SELECT payload::text FROM public.audit_log WHERE action = 'tenant.updated' AND object_ref = $1",
        ctx.slug,
    )
    assert '"antes": {"name": "Entidad itest", "status": "active"}' in payload.replace('":"', '": "')
    assert "suspended" in payload


async def test_parametro_asienta_valor_previo(ctx: Ctx) -> None:
    await ctx.app.execute(f'SET search_path = "{ctx.schema}", public')
    repo = ConfigRepository(ctx.app)
    existia, antes = await repo.upsert_param("consec_digits", 9, None)
    assert existia is True and antes == 6  # sembrado por 002_params.sql
    existia, antes = await repo.upsert_param("consec_digits", 10, None)
    assert antes == 9


async def test_dependencia_con_paso_vivo_se_detecta_por_codigo(ctx: Ctx) -> None:
    await ctx.owner.execute(
        f'INSERT INTO "{ctx.schema}".flow_steps (to_dept_code, status) VALUES '
        "(901, 'pending'), (901, 'completed'), (902, 'pending')"
    )
    await ctx.owner.execute(
        f'INSERT INTO "{ctx.schema}".workflow_rules (assign_to_dept_code) VALUES (901)'
    )
    await ctx.app.execute(f'SET search_path = "{ctx.schema}", public')
    vivos = await DependenciaRepository(ctx.app).tramites_vivos(901)
    assert vivos == {"pasos_vivos": 1, "reglas": 1}
    assert await DependenciaRepository(ctx.app).tramites_vivos(999) == {
        "pasos_vivos": 0,
        "reglas": 0,
    }


async def test_paso_en_curso_cuenta_como_tramite_vivo(ctx: Ctx) -> None:
    """`in_progress` es estado vivo igual que `pending` (columnas reales de workflow)."""
    await ctx.owner.execute(
        f'INSERT INTO "{ctx.schema}".flow_steps (to_dept_code, status) VALUES '
        "(903, 'in_progress'), (903, 'completed'), (903, 'cancelled')"
    )
    await ctx.app.execute(f'SET search_path = "{ctx.schema}", public')
    vivos = await DependenciaRepository(ctx.app).tramites_vivos(903)
    assert vivos == {"pasos_vivos": 1, "reglas": 0}


async def test_regla_con_condicion_por_codigo_cuenta_como_viva(ctx: Ctx) -> None:
    """Una condición `dest_dept_code` (escalar o lista) cita la dependencia."""
    await ctx.owner.execute(
        f'INSERT INTO "{ctx.schema}".workflow_rules (assign_to_dept_code, conditions) VALUES '
        "(1, '[{\"field\": \"dest_dept_code\", \"op\": \"eq\", \"value\": 905}]'), "
        "(1, '[{\"field\": \"origin_dept_code\", \"op\": \"in\", \"value\": [906, 907]}]'), "
        "(1, '[{\"field\": \"subject\", \"op\": \"contains\", \"value\": \"905\"}]')"
    )
    await ctx.app.execute(f'SET search_path = "{ctx.schema}", public')
    repo = DependenciaRepository(ctx.app)
    assert (await repo.tramites_vivos(905))["reglas"] == 1
    assert (await repo.tramites_vivos(907))["reglas"] == 1
    assert (await repo.tramites_vivos(908))["reglas"] == 0
