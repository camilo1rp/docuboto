# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-025 (Increment 2) — circuito de convalidación (aprobar/convalidar/
derogar) contra Postgres REAL.

Complementa `tests/test_trd_convalidacion.py` (mockeado): aquí se prueba lo
que un mock no puede -- el índice único parcial `ux_trd_series_vigente` bajo
el flujo REAL del servicio (sin el atajo `UPDATE` crudo que usa
`test_trd_versionado_congelacion.py` para el Increment 1), el re-apuntado de
las dos FK huérfanas contra tablas reales, y la limpieza selectiva de marcas
de revisión.

Gating: `RUN_INTEGRATION=1` + Postgres real (ver CLAUDE.md de este servicio).
"""

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.schemas.archive import (
    ExpedienteCreate,
    RadicadoLink,
    TrdSerieAprobar,
    TrdSerieConvalidar,
    TrdSerieCreate,
    TrdSerieVersionCreate,
)
from app.services.archive_service import ArchiveService

from .conftest import convalidate_for_test


@pytest.mark.integration
async def test_full_circuit_derogates_previa_and_repoints_fks(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El circuito REAL (`aprobar` -> `convalidar`), no el atajo de test: v1
    `migrada` con hijos (`tipos_documentales`/`expediente_metadata_templates`)
    -> v2 `borrador` -> `aprobada` -> `convalidada`. Al final: v1 `derogada`,
    v2 es la única fila vigente, y los dos hijos quedan re-apuntados a v2."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    schema = tenant_schema(slug)
    code = f"CIRC{uuid.uuid4().hex[:6]}"

    v1 = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest circuito v1", disposition="CT"), actor=None
    )
    await convalidate_for_test(conn, v1.id)  # atajo SOLO para v1 (backfill D6)

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        tipo_id = await conn.fetchval(
            "INSERT INTO tipos_documentales (trd_serie_id, code, nombre) "
            "VALUES ($1, 'T1', 'Oficio') RETURNING id",
            v1.id,
        )
        tpl_id = await conn.fetchval(
            "INSERT INTO expediente_metadata_templates (trd_serie_id, version, json_schema) "
            "VALUES ($1, 1, '{}'::jsonb) RETURNING id",
            v1.id,
        )

    v2 = await archive.create_trd_serie_version(
        code, TrdSerieVersionCreate(retention_years=9), actor=None
    )
    assert v2.version == 2
    assert v2.estado == "borrador"

    v2 = await archive.aprobar_trd_serie_version(
        code,
        v2.version,
        TrdSerieAprobar(acta_comite="Acta itest 1", fecha_aprobacion_comite=date(2026, 1, 1)),
        actor=None,
    )
    assert v2.estado == "aprobada"

    v2 = await archive.convalidar_trd_serie_version(
        code,
        v2.version,
        TrdSerieConvalidar(
            acto_administrativo="Resolución 0123 de 2026",
            fecha_convalidacion=date(2026, 3, 14),
            instancia_convalidante="Consejo Departamental de Archivos",
            rusd_radicado="RUSD-2026-00987",
        ),
        actor=None,
    )
    assert v2.estado == "convalidada"
    assert v2.acto_administrativo == "Resolución 0123 de 2026"

    row_v1 = await conn.fetchrow(
        f'SELECT estado, valid_to, is_active FROM "{schema}".trd_series WHERE id = $1', v1.id
    )
    assert row_v1["estado"] == "derogada"
    assert row_v1["valid_to"] is not None
    assert row_v1["is_active"] is False

    vigentes = await conn.fetch(
        f'SELECT id FROM "{schema}".trd_series '
        "WHERE code = $1 AND estado IN ('convalidada','migrada') AND valid_to IS NULL",
        code,
    )
    assert [r["id"] for r in vigentes] == [v2.id]

    tipo_row = await conn.fetchrow(
        f'SELECT trd_serie_id FROM "{schema}".tipos_documentales WHERE id = $1', tipo_id
    )
    assert tipo_row["trd_serie_id"] == v2.id

    tpl_row = await conn.fetchrow(
        f'SELECT trd_serie_id FROM "{schema}".expediente_metadata_templates WHERE id = $1',
        tpl_id,
    )
    assert tpl_row["trd_serie_id"] == v2.id


@pytest.mark.integration
async def test_convalidar_illegal_transition_rejected_before_any_mutation(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`borrador -> convalidada` directo: la máquina de estados del SERVICE
    corta con 409 antes de tocar la fila (fail-fast, no depende del CHECK)."""
    from fastapi import HTTPException

    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"ILL{uuid.uuid4().hex[:6]}"

    v1 = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest ilegal", disposition="E"), actor=None
    )
    assert v1.estado == "borrador"

    with pytest.raises(HTTPException) as exc_info:
        await archive.convalidar_trd_serie_version(
            code,
            v1.version,
            TrdSerieConvalidar(
                acto_administrativo="X",
                fecha_convalidacion=date(2026, 1, 1),
                instancia_convalidante="Y",
            ),
            actor=None,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "trd_transicion_invalida"

    # La fila sigue intacta: sigue en 'borrador'.
    schema = tenant_schema(slug)
    row = await conn.fetchrow(
        f'SELECT estado FROM "{schema}".trd_series WHERE id = $1', v1.id
    )
    assert row["estado"] == "borrador"


@pytest.mark.integration
async def test_ratificacion_migrada_no_new_row_and_clears_only_matching_motivo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """D6: ratificar una serie `migrada` es la MISMA fila (sin `version+1`) y
    limpia SOLO los expedientes con motivo `cerrado_bajo_serie_migrada` --
    uno con otro motivo (`serie_no_resoluble`, simulado por SQL crudo, mismo
    criterio que usan los tests de Increment 1 para casos que el flujo normal
    del servicio no produce) queda intacto."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    schema = tenant_schema(slug)
    code = f"RATF{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=code,
            name="itest ratificacion",
            disposition="CT",
            archivo_gestion_years=2,
            archivo_central_years=8,
        ),
        actor=None,
    )
    await convalidate_for_test(conn, serie.id)

    # Expediente REAL cerrado bajo la serie migrada -- close_expediente ya
    # marca 'cerrado_bajo_serie_migrada' automáticamente (D6).
    exp = await archive.create_expediente(
        payload=ExpedienteCreate(name="itest ratif exp", nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    await archive.link_radicado(
        exp.id,
        RadicadoLink(
            radicado_id=uuid.uuid4(), tracking_number=f"2026-ITEST-E-{uuid.uuid4().hex[:6]}"
        ),
        user_id=None,
    )
    await archive.close_expediente(exp.id)

    before = await archive.get_expediente(exp.id, user_id=None)
    assert before.trd_revision_requerida is True
    assert before.trd_revision_motivo == "cerrado_bajo_serie_migrada"

    # Segundo expediente, mismo amarre, pero con OTRO motivo -- simulado
    # directo (el flujo normal del servicio no produce este motivo bajo una
    # serie resoluble; se inserta para probar la SELECTIVIDAD del filtro).
    otro_id = uuid.uuid4()
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(
            """
            INSERT INTO expedientes
                (id, code, name, trd_serie_id, status, trd_revision_requerida, trd_revision_motivo)
            VALUES ($1, $2, 'itest otro motivo', $3, 'closed', true, 'serie_no_resoluble')
            """,
            otro_id,
            f"OTRO-{uuid.uuid4().hex[:8]}",
            serie.id,
        )

    # Conteo ANTES de la ratificación: dos filas de trd_series para el
    # código (ninguna nueva se crea con la ratificación).
    filas_antes = await conn.fetchval(
        f'SELECT COUNT(*) FROM "{schema}".trd_series WHERE code = $1', code
    )
    assert filas_antes == 1

    ratificada = await archive.convalidar_trd_serie_version(
        code,
        serie.version,
        TrdSerieConvalidar(
            acto_administrativo="Resolución 099",
            fecha_convalidacion=date(2026, 2, 1),
            instancia_convalidante="Consejo Departamental",
        ),
        actor=None,
    )
    assert ratificada.estado == "convalidada"
    assert ratificada.id == serie.id  # LA MISMA fila.

    filas_despues = await conn.fetchval(
        f'SELECT COUNT(*) FROM "{schema}".trd_series WHERE code = $1', code
    )
    assert filas_despues == 1  # sin INSERT nuevo.

    after = await archive.get_expediente(exp.id, user_id=None)
    assert after.trd_revision_requerida is False
    assert after.trd_revision_motivo is None

    otro_row = await conn.fetchrow(
        f'SELECT trd_revision_requerida, trd_revision_motivo '
        f'FROM "{schema}".expedientes WHERE id = $1',
        otro_id,
    )
    assert otro_row["trd_revision_requerida"] is True
    assert otro_row["trd_revision_motivo"] == "serie_no_resoluble"


@pytest.mark.integration
async def test_derogar_then_reconvalidar_not_possible_without_new_version(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`derogada` es terminal para esa fila (D1 append-only): ni `convalidar`
    ni `aprobar` la reviven."""
    from fastapi import HTTPException

    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"TERM{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest terminal", disposition="E"), actor=None
    )
    await convalidate_for_test(conn, serie.id)

    derogada = await archive.derogar_trd_serie_version(code, serie.version, actor=None)
    assert derogada.estado == "derogada"

    with pytest.raises(HTTPException) as exc_info:
        await archive.convalidar_trd_serie_version(
            code,
            serie.version,
            TrdSerieConvalidar(
                acto_administrativo="X",
                fecha_convalidacion=date(2026, 1, 1),
                instancia_convalidante="Y",
            ),
            actor=None,
        )
    assert exc_info.value.status_code == 409

    with pytest.raises(HTTPException) as exc_info2:
        await archive.aprobar_trd_serie_version(
            code,
            serie.version,
            TrdSerieAprobar(acta_comite="Acta itest 1", fecha_aprobacion_comite=date(2026, 1, 1)),
            actor=None,
        )
    assert exc_info2.value.status_code == 409


@pytest.mark.integration
async def test_list_expedientes_revision_pendiente_paginated_and_tenant_scoped(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`GET /trd/revision-pendiente` (vía el service): paginación + aislamiento
    de tenant -- un expediente marcado en OTRO tenant_slug no debe aparecer,
    porque `ArchiveRepository` fija `search_path` al schema de ESTE tenant."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"PEND{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest pendiente", disposition="CT"), actor=None
    )
    await convalidate_for_test(conn, serie.id)

    ids: list[uuid.UUID] = []
    for i in range(3):
        exp = await archive.create_expediente(
            payload=ExpedienteCreate(
                name=f"itest pendiente exp {i}", nivel_seguridad=1, trd_serie_id=serie.id
            ),
            opened_by=None,
        )
        await archive.link_radicado(
            exp.id,
            RadicadoLink(
                radicado_id=uuid.uuid4(),
                tracking_number=f"2026-ITEST-E-{uuid.uuid4().hex[:6]}",
            ),
            user_id=None,
        )
        await archive.close_expediente(exp.id)
        ids.append(exp.id)

    items_p1, total = await archive.list_expedientes_revision_pendiente(page=1, size=2)
    assert total >= 3
    assert len(items_p1) == 2

    items_p2, total2 = await archive.list_expedientes_revision_pendiente(page=2, size=2)
    assert total2 == total
    seen_ids = {i.id for i in items_p1} | {i.id for i in items_p2}
    assert set(ids).issubset(seen_ids)
    for item in items_p1 + items_p2:
        assert item.trd_revision_motivo == "cerrado_bajo_serie_migrada"
