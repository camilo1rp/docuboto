# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Job periódico de mantenimiento de claves de firma (purga + re-wrap) contra
Postgres REAL. Mutaciones que deben ponerlo en rojo: purgar antes del plazo,
re-envolver una fila con KEK >= activa, no dejar asiento de la ejecución, ignorar
el advisory lock o mutar sin auditoría disponible."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.config import settings
from app.core.database import tenant_schema
from app.core.signing_keys import (
    build_aad,
    encrypt_private_key,
    generate_keypair,
    private_key_to_pem,
)
from app.jobs import signing_key_maintenance as job
from tests.integration.conftest import DSN, RUN_INTEGRATION, ensure_audit_log

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not RUN_INTEGRATION,
        reason="requires RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)",
    ),
]

_V1 = b"\x11" * 32
_V2 = b"\x22" * 32


@pytest.fixture(autouse=True)
def _kek_v2_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "signing_key_encryption_key", base64.b64encode(_V1).decode())
    monkeypatch.setattr(settings, "signing_key_encryption_keys", {2: base64.b64encode(_V2).decode()})
    monkeypatch.setattr(settings, "signing_key_active_version", 2)
    monkeypatch.setattr(settings, "signing_key_purge_after_days", 30)


async def _user(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO auth_users (username) VALUES ($1) RETURNING id", f"MNT_{uuid.uuid4().hex[:8]}"
    )


async def _key(
    conn: asyncpg.Connection,
    *,
    slug: str,
    version: int = 1,
    estado: str = "activo",
    revocado_hace_dias: int | None = None,
) -> uuid.UUID:
    user_id = await _user(conn)
    pem = private_key_to_pem(generate_keypair())
    aad = build_aad(tenant_slug=slug, user_id=str(user_id))
    ct, nonce = encrypt_private_key(pem, key=_V1 if version == 1 else _V2, aad=aad)
    revocado = (
        datetime.now(UTC) - timedelta(days=revocado_hace_dias) if revocado_hace_dias else None
    )
    return await conn.fetchval(
        """INSERT INTO auth_user_signing_key
           (user_id, private_key_cifrado, private_key_nonce, key_version, estado, revocado_at)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
        user_id, ct, nonce, version, estado, revocado,
    )


async def _runs(conn: asyncpg.Connection, slug: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT payload FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug, job.ACTION_RUN,
    )
    return [json.loads(r["payload"]) if isinstance(r["payload"], str) else dict(r["payload"]) for r in rows]


async def _run(conn: asyncpg.Connection, slug: str) -> job.TenantResult:
    """El job deja la conexión en `public` (así la devuelve al pool); el test
    reencamina la suya al schema del tenant para seguir inspeccionando."""
    try:
        return await job.run_tenant(conn, slug)
    finally:
        schema = tenant_schema(slug)
        await conn.execute(f'SET search_path = "{schema}", public')


async def _prep(tenant_ctx: tuple[str, asyncpg.Connection]) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await ensure_audit_log(conn)
    return slug, conn


async def test_purga_solo_pasado_el_plazo_y_deja_asiento_con_conteos(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = await _prep(tenant_ctx)
    vencida = await _key(conn, slug=slug, estado="revocado", revocado_hace_dias=60)
    reciente = await _key(conn, slug=slug, estado="revocado", revocado_hace_dias=5)
    activa = await _key(conn, slug=slug, version=2)

    res = await _run(conn, slug)

    assert (res.purgadas, res.purga_fallidas) == (1, 0)
    mat = {
        r["id"]: r["private_key_cifrado"]
        for r in await conn.fetch("SELECT id, private_key_cifrado FROM auth_user_signing_key")
    }
    assert mat[vencida] is None
    assert mat[reciente] is not None  # antes del plazo NO se purga
    assert mat[activa] is not None
    (run,) = await _runs(conn, slug)
    assert run["purgadas"] == 1 and run["purga_fallidas"] == 0


async def test_rewrap_solo_de_versiones_mas_viejas_que_la_activa(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = await _prep(tenant_ctx)
    vieja = await _key(conn, slug=slug, version=1)
    al_dia = await _key(conn, slug=slug, version=2)

    res = await _run(conn, slug)
    assert (res.rewrapped, res.rewrap_fallidas) == (1, 0)
    v = {r["id"]: r["key_version"] for r in await conn.fetch("SELECT id, key_version FROM auth_user_signing_key")}
    assert v[vieja] == 2 and v[al_dia] == 2
    (run,) = await _runs(conn, slug)
    assert run["rewrapped"] == 1

    # Activa retrocede por error (v1 < v2 de la fila): NO se re-envuelve hacia atrás.
    monkeypatch.setattr(settings, "signing_key_active_version", 1)
    res2 = await _run(conn, slug)
    assert res2.rewrapped == 0
    assert {r["key_version"] for r in await conn.fetch("SELECT key_version FROM auth_user_signing_key")} == {2}


async def test_sin_trabajo_no_hay_asiento_y_es_idempotente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = await _prep(tenant_ctx)
    await _key(conn, slug=slug, version=2)
    res = await _run(conn, slug)
    assert (res.purgadas, res.rewrapped) == (0, 0)
    assert await _runs(conn, slug) == []


async def test_lock_ocupado_se_salta_el_tenant(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = await _prep(tenant_ctx)
    key_id = await _key(conn, slug=slug, estado="revocado", revocado_hace_dias=60)
    otra = await asyncpg.connect(dsn=DSN)
    try:
        assert await otra.fetchval("SELECT pg_try_advisory_lock($1)", job._lock_key(slug))
        res = await _run(conn, slug)
        assert res.omitido == "lock_ocupado"
        assert await conn.fetchval(
            "SELECT private_key_cifrado IS NOT NULL FROM auth_user_signing_key WHERE id = $1", key_id
        )
    finally:
        await otra.close()


async def test_sin_auditoria_no_muta_nada(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = await _prep(tenant_ctx)
    key_id = await _key(conn, slug=slug, estado="revocado", revocado_hace_dias=60)
    monkeypatch.setattr(job, "_AUDIT_AVAILABLE", False)
    res = await _run(conn, slug)
    assert res.omitido == "sin_auditoria"
    assert await conn.fetchval(
        "SELECT private_key_cifrado IS NOT NULL FROM auth_user_signing_key WHERE id = $1", key_id
    )


async def test_llavero_invalido_omite_rewrap_pero_purga(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = await _prep(tenant_ctx)
    await _key(conn, slug=slug, estado="revocado", revocado_hace_dias=60)
    vieja = await _key(conn, slug=slug, version=1)
    monkeypatch.setattr(settings, "signing_key_encryption_key", "")
    res = await _run(conn, slug)
    assert res.purgadas == 1 and res.rewrapped == 0
    assert await conn.fetchval("SELECT key_version FROM auth_user_signing_key WHERE id = $1", vieja) == 1
    (run,) = await _runs(conn, slug)
    assert run["rewrap_omitido"] is True


async def test_run_once_barre_tenants_activos(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = await _prep(tenant_ctx)
    await _key(conn, slug=slug, estado="revocado", revocado_hace_dias=60)
    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)

    async def _tenants(_pool: asyncpg.Pool) -> list[str]:
        return [slug]

    monkeypatch.setattr(job, "list_active_tenants", _tenants)
    try:
        out = await job.run_once(pool)
    finally:
        await pool.close()
    assert out[slug].purgadas == 1
