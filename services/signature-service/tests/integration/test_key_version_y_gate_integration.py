# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-04-11 y AREA-04-10 contra Postgres REAL.

- ``key_version`` (migraciones 020 y 024): lo nuevo se escribe en 1, el esquema 0
  quedó retirado (no verifica) y la CHECK rechaza cualquier otro valor. Un doble de conexión acepta cualquier fila:
  quien impone esto es la base.
- El gate no-read-up de ``FirmaRepository.get`` es SQL con ``NOT EXISTS`` sobre
  ``radicados``/``expedientes`` — su sintaxis y su comparación por identidad natural
  y por UUID solo las valida un Postgres de verdad.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.config import settings
from app.core.database import _schema as tenant_schema
from app.repositories.firma_repository import FirmaRepository
from app.services import signer

pytestmark = pytest.mark.asyncio

SELLO = datetime(2024, 1, 1, tzinfo=UTC)
SELLO_STR = SELLO.astimezone(UTC).isoformat()


async def _en_tenant(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, db_conn = tenant_ctx
    await db_conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, db_conn


async def test_la_firma_anterior_a_la_020_con_key_version_0_ya_no_verifica(tenant_ctx) -> None:
    """B9-5: una fila NATIVA con ``key_version=0`` y el HMAC correcto de aquel
    esquema (fórmula literal de entonces) ya no verifica. Mutación: restaurar la
    rama v0 en ``signer._mac`` => ``True`` y esta prueba cae."""
    slug, conn = await _en_tenant(tenant_ctx)
    payload = b"<indice/>"
    firmante = uuid.uuid4()
    sha = hashlib.sha256(payload).hexdigest()
    blob = hmac.new(
        settings.signature_secret.encode(),
        f"{sha}|{firmante}|{SELLO_STR}".encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    # Desde la migración 025 (B9-S7) la base ya no admite esa fila (CHECK
    # `provider <> 'nativa' OR key_version = 1`; lo prueba
    # `test_correcciones_b9_integration`), así que no se inserta: se verifica el
    # esquema retirado directamente con los valores que tendría.
    assert signer.verificar(
        payload, str(firmante), blob, SELLO.astimezone(UTC).isoformat(),
        settings.signature_secret, key_version=0, tenant_slug=slug,
        objeto_tipo="indice", objeto_id="exp-1",
    ) is False


async def test_la_firma_nueva_se_escribe_en_1_y_solo_verifica_en_su_entidad(tenant_ctx) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    firmante = uuid.uuid4()
    r = signer.firmar(
        b"<indice/>", str(firmante), SELLO_STR, settings.signature_secret,
        tenant_slug=slug, objeto_tipo="indice", objeto_id="exp-1",
    )
    row = await FirmaRepository(conn).insert(
        {
            "objeto_tipo": "indice", "objeto_id": "exp-1", "firmante_id": firmante,
            "payload_sha256": r["payload_sha256"], "firma_blob": r["firma_blob"],
            "provider": r["provider"], "sello_tiempo": SELLO,
            "key_version": r["key_version"],
        }
    )
    assert row["key_version"] == 1

    def _ok(tenant: str) -> bool:
        return signer.verificar(
            b"<indice/>", str(firmante), row["firma_blob"],
            row["sello_tiempo"].astimezone(UTC).isoformat(), settings.signature_secret,
            key_version=row["key_version"], tenant_slug=tenant,
            objeto_tipo="indice", objeto_id="exp-1",
        )

    assert _ok(slug) is True
    assert _ok("otra-entidad") is False


@pytest.mark.parametrize("tabla", ["firma", "firma_challenge"])
async def test_la_check_de_key_version_rechaza_valores_desconocidos(tenant_ctx, tabla) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    with pytest.raises(asyncpg.CheckViolationError):
        if tabla == "firma":
            await conn.execute(
                """INSERT INTO firma (objeto_tipo, firmante_id, payload_sha256, firma_blob,
                       provider, sello_tiempo, tipo_firma, key_version)
                   VALUES ('indice', $1, 'a', 'b', 'nativa', now(), 'nativa', 7)""",
                uuid.uuid4(),
            )
        else:
            await conn.execute(
                """INSERT INTO firma_challenge (solicitud_id, firmante_id, hash_documento,
                       otp_hash, max_intentos, expira_at, canal_masked, tenant_slug, key_version)
                   VALUES ($1, $2, $3, 'h', 5, now(), 'x', 't', 7)""",
                uuid.uuid4(), uuid.uuid4(), "a" * 64,
            )


async def _firma_sobre(conn, objeto_id: str) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO firma (objeto_tipo, objeto_id, firmante_id, payload_sha256,
                              firma_blob, provider, sello_tiempo, tipo_firma, key_version)
           VALUES ('radicado', $1, $2, 'a', 'b', 'nativa', now(), 'nativa', 1) RETURNING id""",
        objeto_id, uuid.uuid4(),
    )


async def test_gate_no_read_up_contra_radicados_y_expedientes_reales(tenant_ctx) -> None:
    """El gate en el WHERE: nivel del objeto > clearance -> la consulta no devuelve
    la fila. Por identidad natural (número/código) y por UUID."""
    slug, conn = await _en_tenant(tenant_ctx)
    await conn.execute(
        "CREATE TABLE radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "tracking_number text, nivel_seguridad int NOT NULL)"
    )
    await conn.execute(
        "CREATE TABLE expedientes (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "code text, nivel_seguridad int NOT NULL)"
    )
    pub = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ('R-PUB', 1) RETURNING id"
    )
    res = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ('R-RES', 3) RETURNING id"
    )
    await conn.execute("INSERT INTO expedientes (code, nivel_seguridad) VALUES ('E-RES', 2)")

    repo = FirmaRepository(conn)
    f_pub = await _firma_sobre(conn, "R-PUB")
    f_res = await _firma_sobre(conn, "R-RES")
    f_res_uuid = await _firma_sobre(conn, str(res))
    f_exp = await _firma_sobre(conn, "E-RES")
    f_sin_objeto = await _firma_sobre(conn, "indice-sin-nivel")

    assert await repo.get(f_pub, max_clearance=1) is not None
    assert await repo.get(f_res, max_clearance=1) is None        # radicado nivel 3
    assert await repo.get(f_res_uuid, max_clearance=1) is None   # mismo, por UUID
    assert await repo.get(f_exp, max_clearance=1) is None        # expediente nivel 2
    assert await repo.get(f_res, max_clearance=2) is None
    assert await repo.get(f_res, max_clearance=3) is not None
    assert await repo.get(f_exp, max_clearance=2) is not None
    assert await repo.get(f_sin_objeto, max_clearance=1) is not None  # no resuelve: visible
    assert await repo.get(f_res) is not None                          # servicio a servicio
    assert pub  # (silencia linter: el id público existe)


async def test_gate_sin_tablas_de_nivel_no_rompe_la_verificacion(tenant_ctx) -> None:
    """Tenant sin document-/archive-service: sin ``radicados``/``expedientes`` la
    consulta cae al camino por tablas presentes y la firma se lee."""
    slug, conn = await _en_tenant(tenant_ctx)
    f = await _firma_sobre(conn, "cualquiera")
    assert await FirmaRepository(conn).get(f, max_clearance=1) is not None
