# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-025 (Increment 1) — TRD como instrumento convalidado, versionado
append-only, contra Postgres REAL.

Este módulo prueba, en orden de importancia (ver ADR-025 §"Por qué es
Crítico"), la fuga que el ADR cierra y los tres refuerzos de motor que la
sostienen:

  (1) LA FUGA CERRADA (el test más importante de este incremento): cerrar un
      expediente congela su calendario de disposición; una versión nueva de
      la MISMA serie, convalidada/migrada DESPUÉS del cierre, no lo mueve.
      Antes de ADR-025 esto era exactamente el hallazgo Crítico —
      `compute_retention`/`close_expediente` releían la fila viva de
      `trd_series` en cada invocación.
  (2) El trigger de congelación del amarre (`trg_expedientes_trd_pin_
      immutable`): un intento de mover `trd_serie_id` de un expediente ya
      cerrado, incluso por SQL crudo, lo rechaza el motor.
  (3) El índice único parcial `ux_trd_series_vigente`: como máximo una fila
      vigente (convalidada/migrada) por código — probado por mutación
      (bypass deliberado de la capa de servicio).
  (4) El CHECK `trd_series_migrada_sin_acto_check` (D6): una fila `migrada`
      no puede llevar datos de acto administrativo — el motor impide que el
      backfill se disfrace de una convalidación real.

Gating: `RUN_INTEGRATION=1` + Postgres real (ver CLAUDE.md de este servicio
y el docstring de `tests/integration/conftest.py`).
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.schemas.archive import (
    ExpedienteCreate,
    RadicadoLink,
    TrdSerieCreate,
    TrdSerieVersionCreate,
)
from app.services.archive_service import ArchiveService

from .conftest import DSN, convalidate_for_test


async def _seed_closed_expediente(
    archive: ArchiveService, conn: asyncpg.Connection, code: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Crea la serie `code` (v1), la mueve a `migrada` (D6, atajo de test --
    ver `convalidate_for_test`), abre un expediente bajo ella, vincula un
    radicado y lo CIERRA. Devuelve `(expediente_id, serie_v1_id)`."""
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=code,
            name="itest serie congelacion v1",
            disposition="CT",
            archivo_gestion_years=2,
            archivo_central_years=8,
        ),
        actor=None,
    )
    await convalidate_for_test(conn, serie.id)

    expediente = await archive.create_expediente(
        payload=ExpedienteCreate(
            name="itest expediente congelado", nivel_seguridad=1, trd_serie_id=serie.id
        ),
        opened_by=None,
    )
    radicado_id = uuid.uuid4()
    await archive.link_radicado(
        expediente.id,
        RadicadoLink(
            radicado_id=radicado_id,
            tracking_number=f"2026-ITEST-E-{uuid.uuid4().hex[:6]}",
        ),
        user_id=None,
    )
    await archive.close_expediente(expediente.id)
    return expediente.id, serie.id


# ---------------------------------------------------------------------------
# (1) LA FUGA CERRADA — el test que sostiene todo el incremento.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_closing_serie_version_after_close_does_not_move_frozen_disposition(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"CONG{uuid.uuid4().hex[:6]}"

    expediente_id, serie_v1_id = await _seed_closed_expediente(archive, conn, code)

    before = await archive.get_expediente(expediente_id, user_id=None)
    assert before.status == "closed"
    assert before.trd_serie_id == serie_v1_id
    assert before.trd_serie_code == code
    disposition_before = dict(before.disposition)
    assert disposition_before["fin_archivo_central"] is not None
    assert disposition_before["serie_version"] == 1
    assert disposition_before["serie_estado"] == "migrada"

    # Se abre una versión NUEVA de la MISMA serie con una retención MUY
    # distinta (2+8 años -> 20+50) y una disposición distinta (CT -> E), y se
    # convalida -- esto es EXACTAMENTE lo que ADR-025 permite: el instrumento
    # evoluciona hacia adelante sin tocar la fila v1.
    v2 = await archive.create_trd_serie_version(
        code,
        TrdSerieVersionCreate(
            archivo_gestion_years=20,
            archivo_central_years=50,
            disposition="E",
        ),
        actor=None,
    )
    assert v2.version == 2
    assert v2.estado == "borrador"

    # Promover v2 a vigente exige primero derogar v1 (el índice único parcial
    # `ux_trd_series_vigente` solo admite UNA fila vigente por código) --
    # este es el atajo de TEST que sustituye al circuito real de
    # `derogar`/`convalidar` (Increment 2, inexistente todavía). Se hace por
    # SQL crudo, igual que `convalidate_for_test`, y JAMÁS simula un acto que
    # no ocurrió: v2 termina en `migrada`, no en `convalidada`.
    schema = tenant_schema(slug)
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(
            "UPDATE trd_series SET estado = 'derogada', valid_to = CURRENT_DATE WHERE id = $1",
            serie_v1_id,
        )
        await conn.execute(
            "UPDATE trd_series SET estado = 'migrada' WHERE id = $1", v2.id
        )

    # La v2 es ahora la vigente del código -- el instrumento SÍ cambió.
    vigente_ahora = await conn.fetchrow(
        f'SELECT id, archivo_central_years FROM "{schema}".trd_series '
        "WHERE code = $1 AND estado IN ('convalidada','migrada') AND valid_to IS NULL",
        code,
    )
    assert vigente_ahora["id"] == v2.id
    assert vigente_ahora["archivo_central_years"] == 50

    # EL ENSAYO: el expediente YA CERRADO no se movió un ápice.
    after = await archive.get_expediente(expediente_id, user_id=None)
    assert after.status == "closed"
    assert after.trd_serie_id == serie_v1_id  # amarre congelado, NUNCA v2
    assert after.trd_serie_code == code
    assert dict(after.disposition) == disposition_before  # snapshot bit-a-bit intacto
    assert after.disposition["fin_archivo_central"] == disposition_before["fin_archivo_central"]
    assert after.disposition["serie_version"] == 1

    # Confirmado también con una conexión NUEVA -- no es un artefacto de
    # cache dentro de la misma sesión.
    other_conn = await asyncpg.connect(dsn=DSN)
    try:
        await other_conn.execute(f'SET search_path = "{schema}", public')
        row = await other_conn.fetchrow(
            "SELECT trd_serie_id, disposition FROM expedientes WHERE id = $1", expediente_id
        )
    finally:
        await other_conn.close()
    assert row["trd_serie_id"] == serie_v1_id
    raw_disposition = row["disposition"]
    persisted_disposition = (
        json.loads(raw_disposition) if isinstance(raw_disposition, str) else raw_disposition
    )
    assert persisted_disposition["fin_archivo_central"] == disposition_before["fin_archivo_central"]
    assert persisted_disposition["serie_version"] == 1


# ---------------------------------------------------------------------------
# (2) Congelación del amarre a nivel de MOTOR: el trigger la sostiene incluso
# ante un intento de bypass por SQL crudo.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_engine_rejects_moving_trd_serie_id_of_closed_expediente_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"PIN{uuid.uuid4().hex[:6]}"

    expediente_id, serie_v1_id = await _seed_closed_expediente(archive, conn, code)

    otra_serie = await archive.create_trd_serie(
        TrdSerieCreate(code=f"OTRA{uuid.uuid4().hex[:6]}", name="itest otra serie"),
        actor=None,
    )
    await convalidate_for_test(conn, otra_serie.id)

    schema = tenant_schema(slug)
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        with pytest.raises(
            asyncpg.exceptions.PostgresError, match=r"es inmutable tras el cierre"
        ):
            await conn.execute(
                "UPDATE expedientes SET trd_serie_id = $2 WHERE id = $1",
                expediente_id,
                otra_serie.id,
            )

    # El amarre sigue intacto tras el intento fallido (la tx interna del
    # `with` ya revirtió esa sentencia).
    row = await conn.fetchrow(
        f'SELECT trd_serie_id FROM "{schema}".expedientes WHERE id = $1', expediente_id
    )
    assert row["trd_serie_id"] == serie_v1_id


# ---------------------------------------------------------------------------
# (3) Máximo UNA versión vigente por código -- índice único parcial, probado
# por MUTACIÓN (bypass deliberado de `ArchiveService`).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unique_index_rejects_second_vigente_row_for_same_code_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Una modificación sustantiva crea una versión nueva (D1) y el índice
    `ux_trd_series_vigente` garantiza que, de las filas resultantes, como
    máximo una queda vigente por código -- este test bypassa el flujo del
    servicio (que ya lo impide con el 409 `trd_version_en_tramite`/al exigir
    derogar antes de convalidar) y ataca el índice directamente."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"UNQ{uuid.uuid4().hex[:6]}"

    v1 = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest v1"), actor=None
    )
    await convalidate_for_test(conn, v1.id)  # v1 -> migrada (vigente)

    schema = tenant_schema(slug)
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            # Segunda fila del MISMO código, también "vigente"
            # (convalidada/migrada, valid_to NULL) -- sin derogar v1 antes.
            await conn.execute(
                """
                INSERT INTO trd_series (code, name, version, estado)
                VALUES ($1, 'itest v2 bypass', 2, 'migrada')
                """,
                code,
            )


@pytest.mark.integration
async def test_create_trd_serie_version_max_one_vigente_after_ratification(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Camino feliz (no bypass): tras crear v2 y ratificarla (atajo de test,
    Increment 2 real pendiente), sigue habiendo como máximo UNA fila vigente
    para el código -- v1 quedó derogada, v2 es la única `migrada`."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"MAX1{uuid.uuid4().hex[:6]}"

    v1 = await archive.create_trd_serie(TrdSerieCreate(code=code, name="itest v1"), actor=None)
    await convalidate_for_test(conn, v1.id)

    v2 = await archive.create_trd_serie_version(
        code, TrdSerieVersionCreate(retention_years=9), actor=None
    )
    assert v2.version == 2

    schema = tenant_schema(slug)
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(
            "UPDATE trd_series SET estado = 'derogada', valid_to = CURRENT_DATE WHERE id = $1",
            v1.id,
        )
        await conn.execute("UPDATE trd_series SET estado = 'migrada' WHERE id = $1", v2.id)

    vigentes = await conn.fetch(
        f'SELECT id FROM "{schema}".trd_series '
        "WHERE code = $1 AND estado IN ('convalidada','migrada') AND valid_to IS NULL",
        code,
    )
    assert [r["id"] for r in vigentes] == [v2.id]


# ---------------------------------------------------------------------------
# (4) D6 — el CHECK impide que `migrada` se disfrace de `convalidada`.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_check_rejects_migrada_row_with_acto_administrativo_data_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    code = f"MIG{uuid.uuid4().hex[:6]}"

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO trd_series
                    (code, name, estado, acto_administrativo, fecha_convalidacion,
                     instancia_convalidante)
                VALUES ($1, 'itest migrada disfrazada', 'migrada', 'Resolución 001',
                        CURRENT_DATE, 'Consejo Departamental')
                """,
                code,
            )


@pytest.mark.integration
@pytest.mark.parametrize(
    "acto_field",
    ["acto_administrativo", "fecha_convalidacion", "instancia_convalidante", "rusd_radicado"],
)
async def test_check_rejects_migrada_row_with_single_acto_field_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection], acto_field: str
) -> None:
    """Gemelo por-campo del test anterior: CADA uno de los cuatro campos del
    acto, por sí solo, ya viola el CHECK sobre una fila `migrada` -- no hace
    falta rellenar los cuatro a la vez para que el motor lo rechace."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    code = f"MIG1{uuid.uuid4().hex[:6]}"
    value: object = date(2026, 1, 1) if acto_field == "fecha_convalidacion" else "algun valor"

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                f"""
                INSERT INTO trd_series (code, name, estado, {acto_field})
                VALUES ($1, 'itest migrada campo suelto', 'migrada', $2)
                """,
                code,
                value,
            )


@pytest.mark.integration
async def test_check_accepts_migrada_row_without_any_acto_data_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Contraparte honesta: `migrada` SIN ningún dato de acto es exactamente
    lo que el backfill produce -- debe insertarse sin problema."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    code = f"MIGOK{uuid.uuid4().hex[:6]}"

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        row = await conn.fetchrow(
            """
            INSERT INTO trd_series (code, name, estado)
            VALUES ($1, 'itest migrada honesta', 'migrada')
            RETURNING acto_administrativo, fecha_convalidacion, instancia_convalidante,
                      rusd_radicado
            """,
            code,
        )
    assert row["acto_administrativo"] is None
    assert row["fecha_convalidacion"] is None
    assert row["instancia_convalidante"] is None
    assert row["rusd_radicado"] is None


@pytest.mark.integration
async def test_check_convalidada_requires_acto_administrativo_data_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Espejo del anterior: `convalidada` EXIGE los tres campos centrales del
    acto (`trd_series_convalidada_acto_check`) -- sin ellos, el motor
    tampoco deja pasar una convalidación a medias."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    code = f"CVOK{uuid.uuid4().hex[:6]}"

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO trd_series (code, name, estado)
                VALUES ($1, 'itest convalidada sin acto', 'convalidada')
                """,
                code,
            )

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        row = await conn.fetchrow(
            """
            INSERT INTO trd_series
                (code, name, estado, acto_administrativo, fecha_convalidacion,
                 instancia_convalidante)
            VALUES ($1, 'itest convalidada con acto', 'convalidada', 'Resolución 099',
                    CURRENT_DATE, 'Consejo Departamental de Archivos')
            RETURNING estado
            """,
            code,
        )
    assert row["estado"] == "convalidada"
