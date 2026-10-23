# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (ADR-042) contra Postgres REAL: migración 074 (versiones del AIP).

Cubre lo que los dobles no pueden: `mark_empaquetado` deja estado, huella y versión 1 en UNA
transacción; el UNIQUE (expediente_id, indice_xml_sha256) impide registrar dos veces el mismo
índice; el trigger hace append-only la historia; y la rotación de `candidatos_reempaque`
(las filas revisadas sin cambio no acaparan el lote)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.aip_repository import AipRepository

from .test_aip_wiring_atomicity import _seed_ready

pytestmark = pytest.mark.asyncio


@pytest.mark.integration
async def test_mark_empaquetado_deja_version_1_y_es_append_only(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    exp, _ = await _seed_ready(conn, slug, "itest C6 v1", con_radicado=False)
    repo = AipRepository(conn)
    await repo.get_or_create(exp)
    aip, idx = uuid.uuid4(), uuid.uuid4()
    async with conn.transaction():
        assert await repo.mark_empaquetado(exp, aip, indice_file_id=idx, indice_xml_sha256="e" * 64) == 1
    fila = await repo.get_para_reempaque(exp)
    assert fila["aip_vigente_id"] == aip and fila["aip_worm_object_id"] == aip
    assert fila["indice_xml_sha256"].strip() == "e" * 64
    vs = await repo.list_versiones(exp)
    assert [(v["version"], v["motivo"]) for v in vs] == [(1, "ingesta")]

    # misma huella otra vez: UNIQUE (la historia no admite duplicar el índice embebido)
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.insert_version(
            exp, version=2, aip_id=uuid.uuid4(), indice_file_id=idx, indice_xml_sha256="e" * 64,
            firma_id=None, firma_ordinal=2, motivo="renovacion_lta", sustituye_a_aip_id=aip,
        )
    # v2 legítima y encadenada
    await repo.insert_version(
        exp, version=2, aip_id=uuid.uuid4(), indice_file_id=idx, indice_xml_sha256="f" * 64,
        firma_id=None, firma_ordinal=2, motivo="renovacion_lta", sustituye_a_aip_id=aip,
    )
    assert [v["version"] for v in await repo.list_versiones(exp)] == [2, 1]
    # la v1 no puede sustituir a nadie, ni una v>1 carecer de sustituida (CHECK)
    with pytest.raises(asyncpg.CheckViolationError):
        await repo.insert_version(
            exp, version=3, aip_id=uuid.uuid4(), indice_file_id=idx, indice_xml_sha256="9" * 64,
            firma_id=None, firma_ordinal=3, motivo="renovacion_lta", sustituye_a_aip_id=None,
        )
    # append-only
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(f'UPDATE "{schema}".expediente_aip_version SET motivo = \'ingesta\'')
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(f'DELETE FROM "{schema}".expediente_aip_version')


@pytest.mark.integration
async def test_rotacion_y_retencion_solo_sube(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    slug, conn = tenant_ctx
    exp, _ = await _seed_ready(conn, slug, "itest C6 rot", con_radicado=False)
    repo = AipRepository(conn)
    await repo.get_or_create(exp)
    aip = uuid.uuid4()
    mayor = datetime(2050, 1, 1, tzinfo=UTC)
    await repo.mark_empaquetado(
        exp, aip, indice_file_id=uuid.uuid4(), indice_xml_sha256="1" * 64, worm_retain_until=mayor
    )
    assert [r["expediente_id"] for r in await repo.candidatos_reempaque(10)] == [exp]
    await repo.marcar_revisados([exp], proximo_at=datetime.now(tz=UTC) + timedelta(days=1))
    assert await repo.candidatos_reempaque(10) == []  # revisada: sale de la rotación

    # el espejo de retención NUNCA baja
    await repo.mark_reempaquetado(
        exp, aip_vigente_id=uuid.uuid4(), indice_xml_sha256="2" * 64,
        proximo_at=datetime.now(tz=UTC), worm_retain_until=datetime(2030, 1, 1, tzinfo=UTC),
    )
    row = await repo.get_empaquetado(exp)
    assert row["worm_retain_until"] == mayor
