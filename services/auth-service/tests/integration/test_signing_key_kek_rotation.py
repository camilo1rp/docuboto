# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Rotación de la KEK de custodia (E17/F4) — re-wrap REAL contra Postgres.

Ejercita `app.ops.rewrap_signing_keys._rewrap_tenant` de verdad: inserta una
credencial cifrada bajo la KEK v1, la re-envuelve a la v2, y comprueba que el
material queda descifrable SOLO con la v2 (y falla con la v1), con un nonce
NUEVO, `key_version=2`, e idempotente en una segunda pasada. Es lógica de
manejo de claves + atomicidad por-fila: se prueba por mutación real, no con
mocks (memoria del proyecto: la atomicidad no se prueba con mocks)."""

from __future__ import annotations

import base64
import uuid

import asyncpg
import pytest
from cryptography.exceptions import InvalidTag

from app.core.config import settings
from app.core.database import tenant_schema
from app.core.signing_keys import (
    build_aad,
    decrypt_private_key,
    encrypt_private_key,
    generate_keypair,
    private_key_to_pem,
)
from app.ops.rewrap_signing_keys import _rewrap_all, _rewrap_tenant
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not RUN_INTEGRATION,
        reason="requires RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)",
    ),
]

_V1 = b"\x11" * 32
_V2 = b"\x22" * 32
_KEYRING = {1: _V1, 2: _V2}


def _configure_v2_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "signing_key_encryption_key", base64.b64encode(_V1).decode())
    monkeypatch.setattr(settings, "signing_key_encryption_keys", {2: base64.b64encode(_V2).decode()})
    monkeypatch.setattr(settings, "signing_key_active_version", 2)


async def _insert_user(conn: asyncpg.Connection, username: str) -> uuid.UUID:
    row = await conn.fetchrow(
        "INSERT INTO auth_users (username) VALUES ($1) RETURNING id", username
    )
    return row["id"]


async def _insert_key_v1(
    conn: asyncpg.Connection, *, slug: str, user_id: uuid.UUID, estado: str = "activo"
) -> tuple[uuid.UUID, bytes, bytes]:
    """Inserta una credencial cifrada bajo v1 (key_version=1). Devuelve
    (id, pem_original, nonce_v1)."""
    pem = private_key_to_pem(generate_keypair())
    aad = build_aad(tenant_slug=slug, user_id=str(user_id))
    ciphertext, nonce = encrypt_private_key(pem, key=_V1, aad=aad)
    row = await conn.fetchrow(
        """
        INSERT INTO auth_user_signing_key
            (user_id, private_key_cifrado, private_key_nonce, key_version, estado)
        VALUES ($1, $2, $3, 1, $4)
        RETURNING id
        """,
        user_id,
        ciphertext,
        nonce,
        estado,
    )
    return row["id"], pem, nonce


async def test_rewrap_v1_to_v2_reencrypts_and_row_moves_version(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    _configure_v2_active(monkeypatch)
    schema = tenant_schema(slug)
    user_id = await _insert_user(conn, "ROT_ACTIVO")
    key_id, pem, nonce_v1 = await _insert_key_v1(conn, slug=slug, user_id=user_id)
    aad = build_aad(tenant_slug=slug, user_id=str(user_id))

    hechas, fallidas = await _rewrap_tenant(
        conn, schema=schema, slug=slug, active_v=2, active_key=_V2, keyring=_KEYRING, dry_run=False
    )
    assert (hechas, fallidas) == (1, 0)

    after = await conn.fetchrow(
        "SELECT key_version, private_key_cifrado, private_key_nonce "
        "FROM auth_user_signing_key WHERE id = $1",
        key_id,
    )
    # Ahora es v2, y el material descifra con la v2 al MISMO plaintext.
    assert after["key_version"] == 2
    assert (
        decrypt_private_key(
            after["private_key_cifrado"], nonce=after["private_key_nonce"], key=_V2, aad=aad
        )
        == pem
    )
    # Ya NO descifra con la v1 (fue re-envuelto) — confidencialidad rota respecto de la KEK vieja.
    with pytest.raises(InvalidTag):
        decrypt_private_key(
            after["private_key_cifrado"], nonce=after["private_key_nonce"], key=_V1, aad=aad
        )
    # Nonce NUEVO (jamás se reutiliza el viejo con la KEK nueva).
    assert after["private_key_nonce"] != nonce_v1


async def test_rewrap_is_idempotent_second_pass_noop(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    _configure_v2_active(monkeypatch)
    schema = tenant_schema(slug)
    user_id = await _insert_user(conn, "ROT_IDEM")
    await _insert_key_v1(conn, slug=slug, user_id=user_id)

    first = await _rewrap_tenant(
        conn, schema=schema, slug=slug, active_v=2, active_key=_V2, keyring=_KEYRING, dry_run=False
    )
    assert first == (1, 0)
    # Segunda pasada: ninguna fila con key_version <> 2 -> no-op.
    second = await _rewrap_tenant(
        conn, schema=schema, slug=slug, active_v=2, active_key=_V2, keyring=_KEYRING, dry_run=False
    )
    assert second == (0, 0)


async def test_rewrap_covers_revoked_rows(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Las credenciales 'revocado' TAMBIÉN se re-envuelven: si se dejaran atrás
    anclarían la KEK vieja para siempre e impedirían retirarla."""
    slug, conn = tenant_ctx
    _configure_v2_active(monkeypatch)
    schema = tenant_schema(slug)
    user_id = await _insert_user(conn, "ROT_REVOCADO")
    key_id, pem, _ = await _insert_key_v1(conn, slug=slug, user_id=user_id, estado="revocado")
    aad = build_aad(tenant_slug=slug, user_id=str(user_id))

    hechas, fallidas = await _rewrap_tenant(
        conn, schema=schema, slug=slug, active_v=2, active_key=_V2, keyring=_KEYRING, dry_run=False
    )
    assert (hechas, fallidas) == (1, 0)
    after = await conn.fetchrow(
        "SELECT key_version, estado, private_key_cifrado, private_key_nonce "
        "FROM auth_user_signing_key WHERE id = $1",
        key_id,
    )
    assert after["key_version"] == 2
    assert after["estado"] == "revocado"  # estado intacto
    assert (
        decrypt_private_key(
            after["private_key_cifrado"], nonce=after["private_key_nonce"], key=_V2, aad=aad
        )
        == pem
    )


async def test_rewrap_all_derives_hyphenated_slug_for_aad(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión del hallazgo Alto: `_rewrap_all` deriva el slug del schema
    (`tenant_itest_<hex>`) para el AAD. El slug del fixture LLEVA GUIÓN
    (`itest-<hex>`), así que el AAD del enroll usa guión pero el schema usa
    guión bajo; si la derivación no deshace `_`->`-`, el AAD no casa e InvalidTag
    hace fallar TODAS las filas (rotación bloqueada). Este test recorre
    `_rewrap_all` de punta a punta (no `_rewrap_tenant` con el slug ya correcto),
    que es donde vive la derivación — habría fallado antes del fix."""
    slug, conn = tenant_ctx
    assert "-" in slug  # el fixture garantiza un slug hyphenado
    _configure_v2_active(monkeypatch)
    user_id = await _insert_user(conn, "ROT_HYPHEN")
    key_id, pem, _ = await _insert_key_v1(conn, slug=slug, user_id=user_id)
    aad = build_aad(tenant_slug=slug, user_id=str(user_id))

    rc = await _rewrap_all(conn, only_tenant=slug, dry_run=False)
    assert rc == 0  # exit-code 0 = sin fallos (antes del fix daba 1)

    # `_rewrap_all` resetea search_path a public al terminar — restaurar el del
    # schema desechable para las aserciones directas.
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    after = await conn.fetchrow(
        "SELECT key_version, private_key_cifrado, private_key_nonce "
        "FROM auth_user_signing_key WHERE id = $1",
        key_id,
    )
    assert after["key_version"] == 2
    assert (
        decrypt_private_key(
            after["private_key_cifrado"], nonce=after["private_key_nonce"], key=_V2, aad=aad
        )
        == pem
    )


async def test_rewrap_dry_run_does_not_mutate(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    _configure_v2_active(monkeypatch)
    schema = tenant_schema(slug)
    user_id = await _insert_user(conn, "ROT_DRY")
    key_id, _, _ = await _insert_key_v1(conn, slug=slug, user_id=user_id)

    hechas, fallidas = await _rewrap_tenant(
        conn, schema=schema, slug=slug, active_v=2, active_key=_V2, keyring=_KEYRING, dry_run=True
    )
    assert (hechas, fallidas) == (1, 0)  # cuenta lo que haría
    still_v1 = await conn.fetchval(
        "SELECT key_version FROM auth_user_signing_key WHERE id = $1", key_id
    )
    assert still_v1 == 1  # NO mutó
