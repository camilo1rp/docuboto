# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Purga del material privado de credenciales de firma REVOCADAS (E17/F4) —
contra Postgres real. Prueba por mutación: la ventana (`COALESCE(revocado_at,
updated_at)`), qué se anula/conserva, el CHECK invariante, la idempotencia, y la
interacción con el conteo de retirada de KEK (una purgada deja de anclar su KEK)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.ops.purge_revoked_signing_keys import _purge_tenant
from app.repositories.signing_key_repository import SigningKeyRepository
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not RUN_INTEGRATION,
        reason="requires RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)",
    ),
]


async def _insert_user(conn: asyncpg.Connection, username: str) -> uuid.UUID:
    row = await conn.fetchrow(
        "INSERT INTO auth_users (username) VALUES ($1) RETURNING id", username
    )
    return row["id"]


async def _insert_key(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    estado: str = "revocado",
    revocado_at: datetime | None,
    updated_at: datetime | None = None,
) -> uuid.UUID:
    """Inserta una credencial CON material (bytes arbitrarios — la purga no
    descifra), fijando `revocado_at`/`updated_at` para controlar la ventana."""
    row = await conn.fetchrow(
        """
        INSERT INTO auth_user_signing_key
            (user_id, cert_pem, private_key_cifrado, private_key_nonce,
             key_version, estado, revocado_at, updated_at)
        VALUES ($1, $2, $3, $4, 1, $5, $6, COALESCE($7, now()))
        RETURNING id
        """,
        user_id,
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        b"\x01" * 48,
        b"\x02" * 12,
        estado,
        revocado_at,
        updated_at,
    )
    return row["id"]


async def test_purge_expired_revoked_nulls_material_keeps_public(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    user_id = await _insert_user(conn, "PURGE_OLD")
    old = datetime.now(UTC) - timedelta(days=60)
    key_id = await _insert_key(conn, user_id=user_id, revocado_at=old)

    schema = f"tenant_{slug.replace('-', '_')}"
    hechas, fallidas = await _purge_tenant(
        conn, schema=schema, slug=slug, days=30, dry_run=False
    )
    assert (hechas, fallidas) == (1, 0)

    row = await conn.fetchrow(
        "SELECT private_key_cifrado, private_key_nonce, private_key_purgado_at, "
        "estado, cert_pem, key_version FROM auth_user_signing_key WHERE id = $1",
        key_id,
    )
    # Material anulado, marca puesta.
    assert row["private_key_cifrado"] is None
    assert row["private_key_nonce"] is None
    assert row["private_key_purgado_at"] is not None
    # Todo lo público intacto (verificación/revocation-status siguen igual).
    assert row["estado"] == "revocado"
    assert row["cert_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert row["key_version"] == 1  # se conserva (forense), NO se neutraliza


async def test_purge_respects_retention_window(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    user_id = await _insert_user(conn, "PURGE_RECENT")
    recent = datetime.now(UTC) - timedelta(days=5)
    key_id = await _insert_key(conn, user_id=user_id, revocado_at=recent)

    schema = f"tenant_{slug.replace('-', '_')}"
    hechas, _ = await _purge_tenant(conn, schema=schema, slug=slug, days=30, dry_run=False)
    assert hechas == 0  # dentro de la ventana -> NO se purga

    still = await conn.fetchval(
        "SELECT private_key_cifrado IS NOT NULL FROM auth_user_signing_key WHERE id = $1",
        key_id,
    )
    assert still is True


async def test_purge_uses_updated_at_when_revocado_at_null(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El caso `revoke()` (supersede en re-enroll) NO setea `revocado_at`; el
    reloj de la ventana usa `COALESCE(revocado_at, updated_at)` — sin él, la
    mayoría de revocadas nunca se purgarían."""
    slug, conn = tenant_ctx
    user_id = await _insert_user(conn, "PURGE_COALESCE")
    old = datetime.now(UTC) - timedelta(days=90)
    key_id = await _insert_key(conn, user_id=user_id, revocado_at=None, updated_at=old)

    schema = f"tenant_{slug.replace('-', '_')}"
    hechas, _ = await _purge_tenant(conn, schema=schema, slug=slug, days=30, dry_run=False)
    assert hechas == 1

    purged = await conn.fetchval(
        "SELECT private_key_cifrado IS NULL FROM auth_user_signing_key WHERE id = $1",
        key_id,
    )
    assert purged is True


async def test_purge_never_touches_active_and_is_idempotent(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = f"tenant_{slug.replace('-', '_')}"
    active_user = await _insert_user(conn, "PURGE_ACTIVE")
    active_id = await _insert_key(
        conn, user_id=active_user, estado="activo", revocado_at=None,
        updated_at=datetime.now(UTC) - timedelta(days=90),
    )
    rev_user = await _insert_user(conn, "PURGE_REV")
    await _insert_key(
        conn, user_id=rev_user, revocado_at=datetime.now(UTC) - timedelta(days=60)
    )

    first = await _purge_tenant(conn, schema=schema, slug=slug, days=30, dry_run=False)
    assert first == (1, 0)  # solo la revocada
    # La activa nunca se toca (y el CHECK la protegería del NULL).
    active_ok = await conn.fetchval(
        "SELECT private_key_cifrado IS NOT NULL FROM auth_user_signing_key WHERE id = $1",
        active_id,
    )
    assert active_ok is True
    # Segunda pasada: ya no queda material revocado vencido -> no-op.
    second = await _purge_tenant(conn, schema=schema, slug=slug, days=30, dry_run=False)
    assert second == (0, 0)


async def test_revoke_supersede_seals_revocado_at(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Fix del hallazgo Media: `revoke()` (supersede en re-enroll) ahora sella
    `revocado_at` — antes quedaba NULL y el reloj de purga (COALESCE→updated_at)
    lo reiniciaba el re-wrap de KEK indefinidamente."""
    slug, conn = tenant_ctx
    user_id = await _insert_user(conn, "PURGE_SEAL")
    key_id = await _insert_key(conn, user_id=user_id, estado="activo", revocado_at=None)
    repo = SigningKeyRepository(conn)

    await repo.revoke(key_id)

    row = await conn.fetchrow(
        "SELECT estado, revocado_at FROM auth_user_signing_key WHERE id = $1", key_id
    )
    assert row["estado"] == "revocado"
    assert row["revocado_at"] is not None  # sellado (antes: NULL)


async def test_purge_clock_rests_on_revocado_at_not_recent_updated_at(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El reloj de purga descansa en `revocado_at` (estable), no en un
    `updated_at` reciente que un re-wrap de KEK pudo mover: una revocada hace 60d
    se purga aunque su `updated_at` sea de ayer (simula un re-wrap reciente)."""
    slug, conn = tenant_ctx
    user_id = await _insert_user(conn, "PURGE_STABLE")
    key_id = await _insert_key(
        conn,
        user_id=user_id,
        estado="revocado",
        revocado_at=datetime.now(UTC) - timedelta(days=60),
        updated_at=datetime.now(UTC) - timedelta(days=1),
    )
    schema = f"tenant_{slug.replace('-', '_')}"

    hechas, _ = await _purge_tenant(conn, schema=schema, slug=slug, days=30, dry_run=False)
    assert hechas == 1  # revocado_at (60d) vence pese al updated_at reciente

    purged = await conn.fetchval(
        "SELECT private_key_cifrado IS NULL FROM auth_user_signing_key WHERE id = $1", key_id
    )
    assert purged is True


async def test_purged_row_stops_anchoring_its_kek(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El punto crítico del diseño: una purgada conserva su `key_version` pero
    deja de CONTAR en `count_by_key_version`/`fetch_ids_to_rewrap` (predicado
    `private_key_cifrado IS NOT NULL`) — así la purga DESBLOQUEA la retirada de
    esa KEK, que la revocada anclaría."""
    slug, conn = tenant_ctx
    schema = f"tenant_{slug.replace('-', '_')}"
    user_id = await _insert_user(conn, "PURGE_KEK")
    await _insert_key(conn, user_id=user_id, revocado_at=datetime.now(UTC) - timedelta(days=60))
    repo = SigningKeyRepository(conn)

    before = {r["key_version"]: r["n"] for r in await repo.count_by_key_version()}
    assert before.get(1, 0) == 1  # antes de purgar, ancla la KEK v1

    await _purge_tenant(conn, schema=schema, slug=slug, days=30, dry_run=False)

    after = {r["key_version"]: r["n"] for r in await repo.count_by_key_version()}
    assert after.get(1, 0) == 0  # ya no ancla nada -> v1 retirable
    to_rewrap = await repo.fetch_ids_to_rewrap(active_version=2)
    assert to_rewrap == []  # una purgada tampoco entra al re-wrap
