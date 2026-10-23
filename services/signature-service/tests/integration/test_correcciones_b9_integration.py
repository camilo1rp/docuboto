# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B9, bloque S contra Postgres REAL: S1 (bandeja), S3 (monotonía) y S7 (CHECK).

Mutaciones demostradas (cada una cae):
- S1: gatear la bandeja con `s.nivel_seguridad <= $4` (solo la copia)  -> cae la oculta;
- S3: el predicado `<>` también con `solo_subir=True`                   -> cae la hermana;
- S7: quitar el `ADD CONSTRAINT` de la 025                              -> cae el CHECK.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest

from app.core.database import _schema as tenant_schema
from app.repositories.cadena_repository import CadenaRepository

pytestmark = pytest.mark.asyncio

_MIG_025 = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "tenant" / "025_firma_nativa_exige_key_version.sql"
)
_FILA_SINTETICA = "d4e62969-824d-4802-9e40-b402d977b6fc"


async def _fijar(conn, slug: str) -> None:
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')


async def _radicado(conn, nivel: int) -> str:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "tracking_number text, nivel_seguridad int NOT NULL)"
    )
    return str(
        await conn.fetchval(
            "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ($1, $2) RETURNING id",
            f"R-{uuid.uuid4().hex[:6]}", nivel,
        )
    )


async def _cadena(conn, objeto_id: str, *, copia: int, estado: str = "pendiente", firmante=None):
    sol_id = await conn.fetchval(
        "INSERT INTO firma_solicitud (objeto_tipo, objeto_id, creado_por, nivel_seguridad, estado) "
        "VALUES ('radicado', $1, $2, $3, $4) RETURNING id",
        objeto_id, uuid.uuid4(), copia, estado,
    )
    if firmante is not None:
        await conn.execute(
            "INSERT INTO firma_cadena (solicitud_id, orden, firmante_id) VALUES ($1, 1, $2)",
            sol_id, firmante,
        )
    return sol_id


# --------------------------------------------------------------------------- S3


async def test_s3_la_correccion_al_firmar_no_baja_cadenas_hermanas(tenant_ctx) -> None:
    """Dos cadenas del mismo objeto: la abierta (copia 1) y una ya firmada (copia 3).
    Con nivel vivo 2 la relectura SUBE la abierta a 2 y deja la hermana en 3."""
    slug, conn = tenant_ctx
    await _fijar(conn, slug)
    rid = await _radicado(conn, 2)
    abierta = await _cadena(conn, rid, copia=1)
    hermana = await _cadena(conn, rid, copia=3, estado="firmado")

    filas = await CadenaRepository(conn).actualizar_nivel_seguridad(
        "radicado", [rid], 2, solo_subir=True
    )

    assert filas == 1
    niveles = {
        r["id"]: r["nivel_seguridad"]
        for r in await conn.fetch("SELECT id, nivel_seguridad FROM firma_solicitud")
    }
    assert niveles[abierta] == 2 and niveles[hermana] == 3


async def test_s3_el_evento_reclassified_si_puede_bajar(tenant_ctx) -> None:
    """Control positivo: la reclasificación autoritativa (sin `solo_subir`) conserva su
    semántica y baja la copia (evita que S3 la convierta en monótona)."""
    slug, conn = tenant_ctx
    await _fijar(conn, slug)
    rid = await _radicado(conn, 1)
    sol = await _cadena(conn, rid, copia=3)
    assert await CadenaRepository(conn).actualizar_nivel_seguridad("radicado", [rid], 1) == 1
    assert await conn.fetchval("SELECT nivel_seguridad FROM firma_solicitud WHERE id=$1", sol) == 1


# --------------------------------------------------------------------------- S1 bandeja


@pytest.mark.parametrize("con_expedientes", [False, True])
async def test_s1_la_bandeja_oculta_la_cadena_cuyo_objeto_vivo_supera_el_clearance(
    tenant_ctx, con_expedientes: bool
) -> None:
    """Copia 1, objeto vivo 2: con clearance 1 ni la fila ni el total (misma condición);
    con clearance 2 aparece. Cubre el camino sin `expedientes` (UndefinedTable ->
    reintento con las tablas presentes) y con las dos tablas."""
    slug, conn = tenant_ctx
    await _fijar(conn, slug)
    if con_expedientes:
        await conn.execute(
            "CREATE TABLE expedientes (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "code text, nivel_seguridad int NOT NULL)"
        )
    rid = await _radicado(conn, 2)
    firmante = uuid.uuid4()
    await _cadena(conn, rid, copia=1, firmante=firmante)
    repo = CadenaRepository(conn)

    filas, total = await repo.pendientes_de_firmante(firmante, 0, 20, max_clearance=1)
    assert (list(filas), total) == ([], 0)
    filas, total = await repo.pendientes_de_firmante(firmante, 0, 20, max_clearance=2)
    assert len(filas) == 1 and total == 1


async def test_s1_la_desclasificacion_pendiente_gana_el_nivel_mayor(tenant_ctx) -> None:
    """S9: copia 3, objeto ya en 1 (evento sin llegar): rige 3 hasta que converja."""
    slug, conn = tenant_ctx
    await _fijar(conn, slug)
    rid = await _radicado(conn, 1)
    firmante = uuid.uuid4()
    await _cadena(conn, rid, copia=3, firmante=firmante)
    _, total = await CadenaRepository(conn).pendientes_de_firmante(firmante, 0, 20, max_clearance=2)
    assert total == 0


# --------------------------------------------------------------------------- S7 CHECK


_INSERT_FIRMA = (
    "INSERT INTO firma (objeto_tipo, objeto_id, firmante_id, payload_sha256, firma_blob, "
    "provider, sello_tiempo, tipo_firma, key_version) "
    "VALUES ('indice', 'x', $1, 'a', 'b', $2, now(), $3, $4)"
)


async def test_s7_el_check_rechaza_nativa_con_key_version_0(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await _fijar(conn, slug)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(_INSERT_FIRMA, uuid.uuid4(), "nativa", "nativa", 0)


async def test_s7_control_positivo_nativa_con_1_y_no_nativa_con_0(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await _fijar(conn, slug)
    await conn.execute(_INSERT_FIRMA, uuid.uuid4(), "nativa", "nativa", 1)
    await conn.execute(_INSERT_FIRMA, uuid.uuid4(), "xades_local", "nativa", 0)


async def test_s7_la_025_borra_la_fila_sintetica_y_es_idempotente(tenant_ctx) -> None:
    """Se simula el estado previo (sin CHECK, con la fila sintética v0), se aplica la 025
    dos veces: la fila desaparece, el CHECK queda y las demás firmas no se tocan."""
    slug, conn = tenant_ctx
    await _fijar(conn, slug)
    await conn.execute("ALTER TABLE firma DROP CONSTRAINT firma_nativa_exige_key_version")
    await conn.execute(
        "INSERT INTO firma (id, objeto_tipo, objeto_id, firmante_id, payload_sha256, firma_blob, "
        "provider, sello_tiempo, tipo_firma, key_version) "
        "VALUES ($1, 'radicado', 'x', $2, 'a', 'b', 'nativa', now(), 'nativa', 0)",
        uuid.UUID(_FILA_SINTETICA), uuid.uuid4(),
    )
    await conn.execute(_INSERT_FIRMA, uuid.uuid4(), "nativa", "nativa", 1)
    sql = _MIG_025.read_text(encoding="utf-8")
    await conn.execute(sql)
    await conn.execute(sql)
    assert await conn.fetchval("SELECT count(*) FROM firma WHERE id = $1", uuid.UUID(_FILA_SINTETICA)) == 0
    assert await conn.fetchval("SELECT count(*) FROM firma") == 1
    assert await conn.fetchval(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'firma_nativa_exige_key_version' "
        "AND conrelid = 'firma'::regclass"
    ) == 1
