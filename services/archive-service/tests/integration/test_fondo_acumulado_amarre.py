# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-026 (Increment 2) — amarre y calendario del fondo acumulado, contra
Postgres REAL.

El ADR es explícito: "Este punto [D4, la fecha base del calendario] EXIGE
test de integración con Postgres real con fechas históricas antes de
habilitar cualquier ruta WORM para fondo acumulado" -- este módulo es ese
test. Un mock no prueba nada aquí: la propiedad que hay que verificar es que
`close_expediente`/`compute_retention` usan `fecha_extrema_final` (1980s) y
NO `closed_at` (hoy) -- si el test pasara con cualquiera de las dos bases,
no probaría nada (instrucción explícita de la tarea). La prueba de mutación
es el calendario VENCIDO: `fin_archivo_central` solo puede caer en el
pasado si la base fue la fecha extrema, nunca si fuera `closed_at` (hoy +
años de retención siempre cae en el futuro).

Cubre también las dos mitades de la Capa 4 relajada (migración 041,
`origen <-> tipo_instrumento`) y el CHECK de forma de `expedientes.origen`
-- por mutación, bypaseando deliberadamente la capa de servicio (SQL crudo)
para probar el motor, no la aplicación.

Gating: `RUN_INTEGRATION=1` + Postgres real (ver CLAUDE.md de este servicio).
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.schemas.archive import ExpedienteCreate, TrdSerieCreate, TvdAgrupacionCreate
from app.services.archive_service import ArchiveService

from .conftest import convalidate_for_test


async def _seed_tvd(
    archive: ArchiveService,
    conn: asyncpg.Connection,
    *,
    code: str,
    fecha_extrema_inicial: date,
    fecha_extrema_final: date,
    archivo_central_years: int = 5,
    disposition: str = "CT",
):
    tvd = await archive.create_tvd_agrupacion(
        TvdAgrupacionCreate(
            code=code,
            name=f"itest fondo acumulado {code}",
            disposition=disposition,
            archivo_central_years=archivo_central_years,
            fondo_nombre="Fondo Instituto Departamental itest (liquidado)",
            fecha_extrema_inicial=fecha_extrema_inicial,
            fecha_extrema_final=fecha_extrema_final,
            justificacion_valoracion="itest: valoración secundaria, eliminación por muestreo.",
        ),
        actor=None,
    )
    # Mismo atajo de test que el resto de este tier (`convalidate_for_test`):
    # sustituye al circuito real aprobar/convalidar (que ya existe, pero no
    # es lo que este módulo prueba) -- nunca simula un acto que no ocurrió,
    # solo dispensa a la fila un `estado` que SÍ gobierna un cierre (D5/D6).
    await convalidate_for_test(conn, tvd.id)
    return tvd


@pytest.mark.integration
async def test_close_fondo_acumulado_usa_fecha_extrema_final_y_calendario_vencido(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """EL TEST que exige ADR-026 D4: un expediente de fondo acumulado con
    documentos de 1985, registrado y cerrado HOY, debe calendarizarse desde
    1985 -- no desde hoy -- y el resultado debe ser un calendario YA VENCIDO
    (D4.4, el caso normal, no un error)."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"TVD{uuid.uuid4().hex[:6]}"

    extrema_inicial = date(1980, 1, 1)
    extrema_final = date(1985, 6, 30)
    tvd = await _seed_tvd(
        archive,
        conn,
        code=code,
        fecha_extrema_inicial=extrema_inicial,
        fecha_extrema_final=extrema_final,
        archivo_central_years=5,
        disposition="CT",
    )

    expediente = await archive.create_expediente(
        payload=ExpedienteCreate(
            name="itest fondo acumulado -- expediente 1985",
            trd_serie_id=tvd.id,
            origen="fondo_acumulado",
            fecha_extrema_inicial=extrema_inicial,
            fecha_extrema_final=extrema_final,
        ),
        opened_by=None,
    )
    assert expediente.origen == "fondo_acumulado"
    assert expediente.fecha_extrema_final == extrema_final

    result = await archive.close_expediente(expediente.id)
    assert result.status == "closed"

    after = await conn.fetchrow(
        f'SELECT disposition FROM "{tenant_schema(slug)}".expedientes WHERE id = $1',
        expediente.id,
    )
    disposition = (
        json.loads(after["disposition"])
        if isinstance(after["disposition"], str)
        else after["disposition"]
    )

    # (1) LA BASE: declarada explícitamente, y es la fecha extrema -- NUNCA
    # `closed_at` (que ya no existe como clave en el snapshot, ver
    # RetentionSchedule).
    assert disposition["base_date"] == extrema_final.isoformat()
    assert disposition["base_date_origen"] == "fecha_extrema_final"
    assert disposition["tipo_instrumento"] == "TVD"
    assert disposition["fondo_nombre"] == "Fondo Instituto Departamental itest (liquidado)"
    assert disposition["justificacion_valoracion"] is not None

    # (2) LA FASE DE GESTIÓN ES 0 (D4.2) -- TVD nunca tiene archivo de gestión.
    assert disposition["archivo_gestion_years"] == 0
    assert disposition["fin_archivo_gestion"] == extrema_final.isoformat()

    # (3) LA PRUEBA DE MUTACIÓN que la tarea exige: el calendario debe estar
    # VENCIDO. Esto es IMPOSIBLE si la base hubiera sido `closed_at` (hoy):
    # hoy + N años de retención (N >= 0) nunca cae en el pasado. Que
    # `fin_archivo_central` esté vencido es, por construcción aritmética, una
    # prueba de que la base usada fue la fecha extrema y no la de cierre --
    # el test NO pasaría con la base equivocada.
    fin_archivo_central = date.fromisoformat(disposition["fin_archivo_central"])
    assert fin_archivo_central == date(1990, 6, 30)  # 1985-06-30 + 5 años
    assert fin_archivo_central < date.today()

    # (4) El expediente queda listo para disposición final desde su
    # registro (D4.4) -- no se redondea hacia adelante, se ve tal cual.
    assert disposition["disposition_code"] == "CT"


@pytest.mark.integration
async def test_capa4_relajada_rechaza_cruce_origen_tipo_instrumento(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Migración 041: la Capa 4 sigue existiendo -- solo cambió la regla que
    aplica (`origen <-> tipo_instrumento` en vez del bloqueo total de la
    039). Prueba las DOS direcciones del cruce, bypaseando la capa de
    servicio (SQL crudo) para probar el motor, no la aplicación."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    schema = tenant_schema(slug)

    trd = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"TRD{uuid.uuid4().hex[:6]}",
            name="itest TRD ordinaria",
            disposition="CT",
            archivo_gestion_years=2,
            archivo_central_years=8,
        ),
        actor=None,
    )
    tvd = await _seed_tvd(
        archive,
        conn,
        code=f"TVD{uuid.uuid4().hex[:6]}",
        fecha_extrema_inicial=date(1980, 1, 1),
        fecha_extrema_final=date(1985, 12, 31),
    )

    async def _insert_expediente(*, origen: str, trd_serie_id: uuid.UUID, extra: dict) -> None:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            cols = ["code", "name", "trd_serie_id", "origen", *extra.keys()]
            placeholders = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
            await conn.execute(
                f"INSERT INTO expedientes ({', '.join(cols)}) VALUES ({placeholders})",
                f"MANUAL-{uuid.uuid4().hex[:8]}",
                "itest cruce origen/tipo",
                trd_serie_id,
                origen,
                *extra.values(),
            )

    # origen='corriente' -> TVD: rechazado.
    with pytest.raises(asyncpg.RaiseError, match="origen=corriente no puede amarrarse"):
        await _insert_expediente(origen="corriente", trd_serie_id=tvd.id, extra={})

    # origen='fondo_acumulado' -> TRD: rechazado.
    with pytest.raises(asyncpg.RaiseError, match="origen=fondo_acumulado no puede amarrarse"):
        await _insert_expediente(
            origen="fondo_acumulado",
            trd_serie_id=trd.id,
            extra={"fecha_extrema_final": date(1985, 12, 31)},
        )

    # Sanity: las combinaciones CORRECTAS sí pasan (regresión -- el 039
    # seguía rechazando corriente/TRD y esta migración no debe romperlo).
    await _insert_expediente(origen="corriente", trd_serie_id=trd.id, extra={})
    await _insert_expediente(
        origen="fondo_acumulado",
        trd_serie_id=tvd.id,
        extra={"fecha_extrema_final": date(1985, 12, 31)},
    )


@pytest.mark.integration
async def test_expedientes_fondo_acumulado_forma_check(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Migración 041, Capa 3 (espejo de la de `trd_series`, 039): un
    expediente `fondo_acumulado` SIN `fecha_extrema_final` no puede existir
    en la fila, y un `corriente` no puede cargar fechas extremas -- negación
    declarada en ambas direcciones."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    async def _insert(*, origen: str, fecha_extrema_final: date | None) -> None:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await conn.execute(
                "INSERT INTO expedientes (code, name, origen, fecha_extrema_final) "
                "VALUES ($1, $2, $3, $4)",
                f"MANUAL-{uuid.uuid4().hex[:8]}",
                "itest CHECK forma",
                origen,
                fecha_extrema_final,
            )

    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(origen="fondo_acumulado", fecha_extrema_final=None)

    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(origen="corriente", fecha_extrema_final=date(1985, 1, 1))

    # Sanity: ambas formas correctas sí pasan.
    await _insert(origen="corriente", fecha_extrema_final=None)
    await _insert(origen="fondo_acumulado", fecha_extrema_final=date(1985, 1, 1))


@pytest.mark.integration
async def test_worm_resolvers_cablean_el_fondo_acumulado_con_la_fecha_extrema_y_el_clamp(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """ADR-026 D4 + AREA-06-04 (Increment 3), contra Postgres REAL con fechas históricas
    (lo que el ADR exige antes de habilitar cualquier ruta WORM para un fondo).

    Antes los resolutores devolvían `None` (fail-closed) y el índice, el acta y el AIP de
    un fondo acumulado quedaban SIN Object-Lock para siempre. Ahora la base es
    `fecha_extrema_final` con clamp: un fondo de 1985 con 5 años de retención (venció en
    1990) acaba con `retain_until == hoy + margen`; si la base fuera `closed_at` (hoy)
    saldría hoy + 5 años, y si no hubiera clamp saldría 1990 (storage lo rechaza)."""
    from datetime import timedelta

    from app.core.config import settings
    from app.services.aip_wiring_service import AipWiringService
    from app.services.index_service import IndexService

    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)

    tvd = await _seed_tvd(
        archive,
        conn,
        code=f"TVD{uuid.uuid4().hex[:6]}",
        fecha_extrema_inicial=date(1980, 1, 1),
        fecha_extrema_final=date(1985, 12, 31),
        archivo_central_years=5,
    )
    expediente = await archive.create_expediente(
        payload=ExpedienteCreate(
            name="itest WORM fondo acumulado",
            trd_serie_id=tvd.id,
            origen="fondo_acumulado",
            fecha_extrema_inicial=date(1980, 1, 1),
            fecha_extrema_final=date(1985, 12, 31),
        ),
        opened_by=None,
    )
    await archive.close_expediente(expediente.id)
    from app.core.clearance import CLEARANCE_SIN_RESTRICCION
    from app.repositories.archive_repository import ArchiveRepository

    cerrado = await ArchiveRepository(conn, slug).get_expediente_by_id(
        expediente.id, max_clearance=CLEARANCE_SIN_RESTRICCION
    )
    assert cerrado is not None and cerrado.closed_at is not None

    hoy = date.today()
    minimo = hoy + timedelta(days=settings.worm_fondo_acumulado_margen_dias)

    def _retain_until(total: int, inicio: date) -> date:
        try:
            return inicio.replace(year=inicio.year + total)
        except ValueError:
            return inicio.replace(month=2, day=28, year=inicio.year + total)

    index = IndexService(conn, slug)
    retention = await index._resolve_worm_retention(expediente.id)
    assert retention is not None, "el índice de un fondo acumulado quedó sin WORM"
    total, inicio = retention
    assert total == 5
    assert abs((_retain_until(total, inicio) - minimo).days) <= 1
    # y no es la base equivocada: closed_at + 5 años estaría ~5 años más lejos
    assert _retain_until(total, inicio) < _retain_until(5, cerrado.closed_at.date())

    aip = AipWiringService(conn, slug)
    retention_aip = await aip._resolve_aip_retention(cerrado)
    assert retention_aip == retention
    assert await index._resolve_ct_retencion_anios(expediente.id) == 5
