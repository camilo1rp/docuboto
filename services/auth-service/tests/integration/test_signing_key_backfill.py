# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""R3 (Baja de robustez, Incremento 2): el backfill de ``certificado_
fingerprint`` en ``migrations/tenant/012_signing_key_revocacion.sql`` debe
aislar el PRIMER bloque ``BEGIN/END CERTIFICATE`` (el leaf) antes de
hashear — si ``cert_pem`` guardara una CADENA (leaf+CA concatenados), hashear
el PEM completo daría una huella que NO coincide con
``cert.fingerprint(hashes.SHA256()).hex()`` de Python (lo que
``activar_certificado``/``_cert_fingerprint`` calculan), produciendo un
``desconocido`` espurio en ``GET /internal/signing-key/revocation-status``.

Ejercita el backfill SQL de verdad contra Postgres real (pgcrypto), no un
mock — es lógica de migración, no de aplicación."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tests.integration.conftest import RUN_INTEGRATION

# NOTA: `pytestmark` solo tiene efecto cuando se define en EL PROPIO módulo
# de test — un `pytestmark` puesto en `conftest.py` NO se propaga a los
# módulos de test de ese directorio (a diferencia de fixtures). El gate vive
# aquí, no en el conftest.
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not RUN_INTEGRATION,
        reason="requires RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)",
    ),
]


def _generate_ca(cn: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _issue_leaf(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate, cn: str = "OPERADOR"
) -> x509.Certificate:
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .sign(ca_key, hashes.SHA256())
    )


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


async def _insert_user(conn: asyncpg.Connection, username: str) -> uuid.UUID:
    row = await conn.fetchrow(
        "INSERT INTO auth_users (username) VALUES ($1) RETURNING id", username
    )
    return row["id"]


async def _insert_signing_key_row(
    conn: asyncpg.Connection, *, user_id: uuid.UUID, cert_pem: str
) -> uuid.UUID:
    row = await conn.fetchrow(
        """
        INSERT INTO auth_user_signing_key
            (user_id, cert_pem, private_key_cifrado, private_key_nonce, estado)
        VALUES ($1, $2, $3, $4, 'activo')
        RETURNING id
        """,
        user_id,
        cert_pem,
        b"\x00" * 32,
        b"\x00" * 12,
    )
    return row["id"]


async def _run_backfill(conn: asyncpg.Connection) -> None:
    """Misma sentencia UPDATE que el backfill de la migración 012 — se
    re-ejecuta aquí directamente (idempotente por ``WHERE ... IS NULL``) para
    poder probarla sobre filas insertadas DESPUÉS de que 012 ya corrió como
    parte de ``tenant_ctx`` (que aplica 001..012 en orden)."""
    await conn.execute(
        """
        UPDATE auth_user_signing_key
        SET certificado_fingerprint = encode(
            public.digest(
                decode(
                    regexp_replace(
                        substring(
                            cert_pem
                            from '-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----'
                        ),
                        '-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\\s',
                        '',
                        'g'
                    ),
                    'base64'
                ),
                'sha256'
            ),
            'hex'
        )
        WHERE cert_pem IS NOT NULL
          AND certificado_fingerprint IS NULL
        """
    )


async def test_backfill_single_certificate_matches_python_fingerprint(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    _slug, conn = tenant_ctx
    ca_key, ca_cert = _generate_ca("ca-test-single")
    leaf = _issue_leaf(ca_key, ca_cert)
    user_id = await _insert_user(conn, "OPERADOR_SINGLE")

    key_id = await _insert_signing_key_row(conn, user_id=user_id, cert_pem=_pem(leaf))
    # 012 ya corrió como parte de tenant_ctx (backfill sobre filas
    # PRE-existentes) — esta fila se insertó DESPUÉS, así que se re-ejecuta
    # la misma sentencia explícitamente.
    await _run_backfill(conn)

    row = await conn.fetchrow(
        "SELECT certificado_fingerprint FROM auth_user_signing_key WHERE id = $1", key_id
    )
    expected = leaf.fingerprint(hashes.SHA256()).hex()
    assert row["certificado_fingerprint"] == expected


async def test_backfill_chain_takes_leaf_not_full_chain_hash(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El caso que R3 cierra: ``cert_pem`` con leaf+CA concatenados (una
    CADENA) — el backfill debe hashear SOLO el primer bloque (el leaf), no
    la cadena completa."""
    _slug, conn = tenant_ctx
    ca_key, ca_cert = _generate_ca("ca-test-chain")
    leaf = _issue_leaf(ca_key, ca_cert, cn="OPERADOR_CHAIN")
    user_id = await _insert_user(conn, "OPERADOR_CHAIN")

    chain_pem = _pem(leaf) + _pem(ca_cert)  # leaf primero, CA concatenada después
    key_id = await _insert_signing_key_row(conn, user_id=user_id, cert_pem=chain_pem)
    await _run_backfill(conn)

    row = await conn.fetchrow(
        "SELECT certificado_fingerprint FROM auth_user_signing_key WHERE id = $1", key_id
    )
    leaf_fp = leaf.fingerprint(hashes.SHA256()).hex()
    ca_fp = ca_cert.fingerprint(hashes.SHA256()).hex()
    assert row["certificado_fingerprint"] == leaf_fp
    assert row["certificado_fingerprint"] != ca_fp


async def test_backfill_is_idempotent_on_rerun(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    _slug, conn = tenant_ctx
    ca_key, ca_cert = _generate_ca("ca-test-idem")
    leaf = _issue_leaf(ca_key, ca_cert, cn="OPERADOR_IDEM")
    user_id = await _insert_user(conn, "OPERADOR_IDEM")

    key_id = await _insert_signing_key_row(conn, user_id=user_id, cert_pem=_pem(leaf))
    await _run_backfill(conn)
    first = await conn.fetchval(
        "SELECT certificado_fingerprint FROM auth_user_signing_key WHERE id = $1", key_id
    )
    # Re-aplicar: WHERE certificado_fingerprint IS NULL ya no casa esta fila
    # — no debe cambiar (ni fallar) en una segunda pasada.
    await _run_backfill(conn)
    second = await conn.fetchval(
        "SELECT certificado_fingerprint FROM auth_user_signing_key WHERE id = $1", key_id
    )
    assert first == second == leaf.fingerprint(hashes.SHA256()).hex()
