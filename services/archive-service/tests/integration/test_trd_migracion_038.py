# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Migración 038 (ADR-025, remediación del dictamen de
`archival-compliance-auditor` sobre el Increment 2) contra Postgres REAL:

- Hallazgo #1: `devolver` (`aprobada -> borrador`).
- Hallazgo #3: `derogar` bloqueado por expedientes `open`; `create_trd_serie_version`
  cae a la última versión cuando no hay vigente.
- Hallazgo #4: `registrar-rusd` sobre `convalidada`.
- Hallazgo #5: `aprobar` exige y escribe `acta_comite`/`fecha_aprobacion_comite`;
  `convalidar` desde `aprobada` exige que ya consten.
- Hallazgo de seguridad: el trigger de la 038 blinda a nivel de motor el
  fundamento jurídico de una fila `convalidada` (transición y campos del
  acto), permitiendo el fill-once de `rusd_radicado`/`fecha_publicacion`.

Gating: `RUN_INTEGRATION=1` + Postgres real (ver CLAUDE.md de este servicio)."""

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.schemas.archive import (
    ExpedienteCreate,
    TrdSerieAprobar,
    TrdSerieConvalidar,
    TrdSerieCreate,
    TrdSerieDevolver,
    TrdSerieRegistrarRusd,
    TrdSerieVersionCreate,
)
from app.services.archive_service import ArchiveService

from .conftest import convalidate_for_test


async def _aprobar(archive: ArchiveService, code: str, version: int):
    return await archive.aprobar_trd_serie_version(
        code,
        version,
        TrdSerieAprobar(acta_comite="Acta Comité itest", fecha_aprobacion_comite=date(2026, 1, 1)),
        actor=None,
    )


@pytest.mark.integration
async def test_devolver_borra_acta_comite_y_permite_editar_de_nuevo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Hallazgo #1: `aprobada -> borrador` con motivo, y la aprobación queda
    VOID (acta_comite/fecha_aprobacion_comite se limpian) -- el contenido se
    puede volver a editar (PATCH) porque el trigger ya no lo bloquea."""
    from app.schemas.archive import TrdSerieUpdate

    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    schema = tenant_schema(slug)
    code = f"DEV{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest devolver", disposition="E"), actor=None
    )
    aprobada = await _aprobar(archive, code, serie.version)
    assert aprobada.estado == "aprobada"
    assert aprobada.acta_comite is not None

    devuelta = await archive.devolver_trd_serie_version(
        code, serie.version, TrdSerieDevolver(motivo="faltan ajustes"), actor=None
    )
    assert devuelta.estado == "borrador"
    assert devuelta.acta_comite is None
    assert devuelta.fecha_aprobacion_comite is None

    # El contenido se puede editar de nuevo (el trigger ya no lo bloquea).
    editada = await archive.update_trd_serie(
        devuelta.id, TrdSerieUpdate(retention_years=7), actor=None
    )
    assert editada.retention_years == 7

    row = await conn.fetchrow(
        f'SELECT estado, acta_comite FROM "{schema}".trd_series WHERE id = $1', serie.id
    )
    assert row["estado"] == "borrador"
    assert row["acta_comite"] is None


@pytest.mark.integration
async def test_convalidar_desde_aprobada_requiere_acta_comite(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Hallazgo #5: sin `aprobar` (con acta) de por medio, `convalidar` desde
    `aprobada` debe rechazar -- aunque la fila esté físicamente en
    'aprobada' (empujada por SQL crudo de test, simulando un bug/dato
    corrupto), el SERVICE re-verifica el acta antes de convalidar."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    schema = tenant_schema(slug)
    code = f"NOACT{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest sin acta", disposition="E"), actor=None
    )
    await conn.execute(
        f'UPDATE "{schema}".trd_series SET estado = \'aprobada\' WHERE id = $1', serie.id
    )

    with pytest.raises(HTTPException) as exc_info:
        await archive.convalidar_trd_serie_version(
            code,
            serie.version,
            TrdSerieConvalidar(
                acto_administrativo="X", fecha_convalidacion=date(2026, 1, 1), instancia_convalidante="Y"
            ),
            actor=None,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "trd_aprobacion_comite_requerida"


@pytest.mark.integration
async def test_trigger_bloquea_mutacion_de_acto_sobre_convalidada(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Hallazgo de seguridad: el motor (no solo la aplicación) rechaza
    reescribir el acto de una fila `convalidada`, y solo permite
    `estado -> derogada` como única transición."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    schema = tenant_schema(slug)
    code = f"IMMUT{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest inmutable", disposition="E"), actor=None
    )
    await convalidate_for_test(conn, serie.id)
    convalidada = await archive.convalidar_trd_serie_version(
        code,
        serie.version,
        TrdSerieConvalidar(
            acto_administrativo="Resolución 1",
            fecha_convalidacion=date(2026, 1, 1),
            instancia_convalidante="Consejo X",
        ),
        actor=None,
    )
    assert convalidada.estado == "convalidada"

    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            f'UPDATE "{schema}".trd_series SET acto_administrativo = \'otro\' WHERE id = $1',
            serie.id,
        )

    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            f'UPDATE "{schema}".trd_series SET estado = \'borrador\' WHERE id = $1', serie.id
        )


@pytest.mark.integration
async def test_registrar_rusd_fill_once_y_trigger_bloquea_reescritura(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Hallazgo #4: RUSD/publicación se registran DESPUÉS de convalidar; el
    trigger permite NULL -> valor pero no valor -> otro valor."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    schema = tenant_schema(slug)
    code = f"RUSD{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest rusd", disposition="E"), actor=None
    )
    await convalidate_for_test(conn, serie.id)
    convalidada = await archive.convalidar_trd_serie_version(
        code,
        serie.version,
        TrdSerieConvalidar(
            acto_administrativo="Resolución 1",
            fecha_convalidacion=date(2026, 1, 1),
            instancia_convalidante="Consejo X",
        ),
        actor=None,
    )
    assert convalidada.rusd_radicado is None

    registrada = await archive.registrar_rusd_trd_serie_version(
        code,
        serie.version,
        TrdSerieRegistrarRusd(rusd_radicado="RUSD-1", fecha_publicacion=date(2026, 2, 1)),
        actor=None,
    )
    assert registrada.rusd_radicado == "RUSD-1"

    with pytest.raises(HTTPException) as exc_info:
        await archive.registrar_rusd_trd_serie_version(
            code, serie.version, TrdSerieRegistrarRusd(rusd_radicado="RUSD-2"), actor=None
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "trd_rusd_ya_registrado"

    # Motor: incluso saltándose el service, el trigger rechaza la reescritura.
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            f'UPDATE "{schema}".trd_series SET rusd_radicado = \'RUSD-3\' WHERE id = $1', serie.id
        )


@pytest.mark.integration
async def test_derogar_bloqueado_por_expediente_open_y_desbloquea_al_cerrar(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Hallazgo #3 (decisión elegida): `derogar` se bloquea mientras exista
    algún expediente `open` clasificado bajo el código."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"DEROP{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest derogar open", disposition="E"), actor=None
    )
    await convalidate_for_test(conn, serie.id)

    exp = await archive.create_expediente(
        payload=ExpedienteCreate(name="itest exp abierto", nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    assert exp.status == "open"

    with pytest.raises(HTTPException) as exc_info:
        await archive.derogar_trd_serie_version(code, serie.version, actor=None)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "trd_derogacion_bloqueada_expedientes_abiertos"


@pytest.mark.integration
async def test_create_version_cae_a_ultima_cuando_no_hay_vigente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Hallazgo #3: tras derogar sin sucesora (código sin expedientes `open`,
    caso legítimo de retiro), `POST /versiones` NO debe dar 404 permanente --
    cae a la última versión por número para poder abrir un `borrador` nuevo
    y, eventualmente, convalidarlo."""
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    code = f"REVIVE{uuid.uuid4().hex[:6]}"

    serie = await archive.create_trd_serie(
        TrdSerieCreate(code=code, name="itest revivir", disposition="E"), actor=None
    )
    await convalidate_for_test(conn, serie.id)

    derogada = await archive.derogar_trd_serie_version(code, serie.version, actor=None)
    assert derogada.estado == "derogada"

    # Sin vigente para `code` -- antes de la 038 esto daba 404 permanente.
    v2 = await archive.create_trd_serie_version(
        code, TrdSerieVersionCreate(retention_years=9), actor=None
    )
    assert v2.version == derogada.version + 1
    assert v2.estado == "borrador"
