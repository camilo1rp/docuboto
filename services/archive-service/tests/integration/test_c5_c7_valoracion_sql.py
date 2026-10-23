# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C7 (ADR-043) contra Postgres REAL: valoración de la TVD y cola de holds `valoracion_pendiente`.

Se ejerce el motor con SQL crudo (CHECK y triggers de la migración 073) y el servicio de punta a
punta con storage sustituido en el borde HTTP.

Mutaciones que deben caer:
  * quitar el `UNIQUE (trd_serie_id)` de `tvd_valoracion` -> `test_una_valoracion_por_version`;
  * quitar el trigger fill-once -> `test_la_valoracion_aplicada_es_inmutable`;
  * `amarrar_valoracion` sin `WHERE valoracion_id IS NULL` -> `test_registrar_amarra_solo_...`;
  * aplicar sin esperar los artefactos -> `test_no_se_aplica_con_un_artefacto_pendiente`;
  * asiento fuera de la tx del acto -> `test_el_acto_y_su_asiento_son_atomicos`.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.core.database import tenant_schema
from app.repositories.valoracion_repository import ValoracionRepository
from app.schemas.archive import TvdAgrupacionCreate
from app.schemas.retencion_legal import ValoracionTvdRequest
from app.services.archive_service import ArchiveService
from app.services.valoracion_service import ValoracionService

pytestmark = pytest.mark.integration

_PAYLOAD = ValoracionTvdRequest(
    acta_comite="Acta 12 de 2026", fecha_acta=date(2026, 9, 1),
    fundamento="Valoracion secundaria: el fondo se conserva por su valor historico",
)


async def _tvd(conn: asyncpg.Connection, slug: str, *, disposition: str = "CT", estado: str = "aprobada"):
    tvd = await ArchiveService(conn, slug).create_tvd_agrupacion(
        TvdAgrupacionCreate(
            code=f"TVD{uuid.uuid4().hex[:6]}", name="itest valoracion", disposition=disposition,
            archivo_central_years=10, fondo_nombre="Fondo itest",
            fecha_extrema_inicial=date(1980, 1, 1), fecha_extrema_final=date(1985, 12, 31),
            justificacion_valoracion="itest: valoracion del fondo acumulado liquidado.",
        ),
        actor=None,
    )
    if estado != "borrador":
        await conn.execute("UPDATE trd_series SET estado = $2 WHERE id = $1", tvd.id, estado)
    return tvd


async def _expediente(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'fondo', 'closed', 1) RETURNING id",
        f"EXP-{uuid.uuid4().hex[:6]}",
    )


async def _hold(conn: asyncpg.Connection, exp: uuid.UUID, serie: uuid.UUID) -> uuid.UUID:
    hold_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO expediente_valoracion_hold (expediente_id, hold_id, trd_serie_id) "
        "VALUES ($1, $2, $3)", exp, hold_id, serie,
    )
    return hold_id


def _storage(*, liberados: int = 2, restantes: int = 0) -> tuple[AsyncMock, AsyncMock]:
    fijar = AsyncMock(return_value={
        "expediente_id": "x", "retain_until": "2036-10-21T00:00:00+00:00", "filas_fijadas": 2,
        "filas_pendientes_restantes": 0,
    })
    levantar = AsyncMock(return_value={
        "hold_id": str(uuid.uuid4()), "objetos_liberados_s3": liberados, "holds_restantes": restantes,
    })
    return fijar, levantar


# --- el motor ---------------------------------------------------------------------------------


async def test_una_valoracion_por_version_y_append_only(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug)
    repo = ValoracionRepository(conn, slug)
    kw = dict(trd_serie_id=tvd.id, disposicion="CT", acta_comite="A", fecha_acta=date(2026, 1, 1),
              fundamento="x" * 25, decidida_por=uuid.uuid4())
    assert await repo.insert_valoracion(**kw) is not None
    assert await repo.insert_valoracion(**kw) is None  # UNIQUE: no se rehace

    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("UPDATE tvd_valoracion SET acta_comite = 'otra'")
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("DELETE FROM tvd_valoracion")
    with pytest.raises(asyncpg.RaiseError):
        # la tabla de holds la referencia por FK: se trunca junto para que llegue el trigger
        await conn.execute("TRUNCATE tvd_valoracion, expediente_valoracion_hold")
    with pytest.raises(asyncpg.CheckViolationError):  # fundamento < 20
        await conn.execute(
            "INSERT INTO tvd_valoracion (trd_serie_id, disposicion, acta_comite, fecha_acta, "
            "fundamento, decidida_por) VALUES ($1, 'E', 'A', current_date, 'corto', gen_random_uuid())",
            tvd.id,  # el CHECK se evalúa antes que la unicidad: gana el de fundamento corto
        )


async def test_checks_de_coherencia_del_hold(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug)
    exp = await _expediente(conn)
    await _hold(conn, exp, tvd.id)

    # aplicada exige valoración registrada
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "UPDATE expediente_valoracion_hold SET aplicada_at = now(), resultado = 'sin_retencion'"
        )
    # resultado y aplicada_at van juntos
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("UPDATE expediente_valoracion_hold SET resultado = 'sin_retencion'")
    # agotada y aplicada se excluyen: se prueba con una fila ya valorada
    val = await ValoracionRepository(conn, slug).insert_valoracion(
        trd_serie_id=tvd.id, disposicion="E", acta_comite="A", fecha_acta=date(2026, 1, 1),
        fundamento="x" * 25, decidida_por=uuid.uuid4(),
    )
    await ValoracionRepository(conn, slug).amarrar_valoracion(tvd.id, val["id"])
    await ValoracionRepository(conn, slug).marcar_aplicada(exp, "sin_retencion")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("UPDATE expediente_valoracion_hold SET agotada = TRUE")


async def test_la_valoracion_aplicada_es_inmutable(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug)
    exp = await _expediente(conn)
    await _hold(conn, exp, tvd.id)
    repo = ValoracionRepository(conn, slug)
    val = await repo.insert_valoracion(
        trd_serie_id=tvd.id, disposicion="E", acta_comite="A", fecha_acta=date(2026, 1, 1),
        fundamento="x" * 25, decidida_por=uuid.uuid4(),
    )
    await repo.amarrar_valoracion(tvd.id, val["id"])
    assert await repo.marcar_aplicada(exp, "sin_retencion") is not None
    assert await repo.marcar_aplicada(exp, "retencion_ct") is None  # fill-once: la 1.ª gana

    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("UPDATE expediente_valoracion_hold SET resultado = 'retencion_ct'")
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("UPDATE expediente_valoracion_hold SET hold_id = gen_random_uuid()")


# --- amarre ------------------------------------------------------------------------------------


async def test_registrar_amarra_solo_los_expedientes_de_esa_version_y_sin_valoracion(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    a, b = await _tvd(conn, slug), await _tvd(conn, slug)
    e1, e2, e3 = await _expediente(conn), await _expediente(conn), await _expediente(conn)
    await _hold(conn, e1, a.id)
    await _hold(conn, e2, a.id)
    await _hold(conn, e3, b.id)
    svc = ValoracionService(conn, slug)
    fijar, levantar = _storage()
    with (
        patch("app.clients.storage_client.fijar_retencion_internal", new=fijar),
        patch("app.clients.storage_client.levantar_legal_hold", new=levantar),
    ):
        res = await svc.registrar_valoracion(a.id, _PAYLOAD, actor=uuid.uuid4())

    assert res.expedientes_afectados == 2 and res.disposicion == "CT"
    filas = {r["expediente_id"]: r for r in await conn.fetch(
        "SELECT expediente_id, valoracion_id, aplicada_at FROM expediente_valoracion_hold")}
    assert filas[e1]["valoracion_id"] == res.valoracion_id and filas[e2]["valoracion_id"] == res.valoracion_id
    assert filas[e3]["valoracion_id"] is None, "otra versión de TVD: no se toca"
    # sin artefactos de contenido por proteger, la aplicación síncrona las deja aplicadas
    assert filas[e1]["aplicada_at"] is not None and filas[e2]["aplicada_at"] is not None
    assert fijar.await_count == 2 and levantar.await_count == 2


async def test_una_segunda_valoracion_de_la_misma_version_es_409_y_no_deja_rastro(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug, disposition="E")
    svc = ValoracionService(conn, slug)
    await svc.registrar_valoracion(tvd.id, _PAYLOAD, actor=uuid.uuid4())
    antes = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.tvd_valoracion_registrada'", slug,
    )
    with pytest.raises(HTTPException) as e:
        await svc.registrar_valoracion(tvd.id, _PAYLOAD, actor=uuid.uuid4())
    assert e.value.status_code == 409 and e.value.detail["error"] == "valoracion_ya_registrada"
    assert antes == 1 == await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.tvd_valoracion_registrada'", slug,
    )


async def test_una_tvd_en_borrador_no_se_valora(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug, estado="borrador")
    with pytest.raises(HTTPException) as e:
        await ValoracionService(conn, slug).registrar_valoracion(tvd.id, _PAYLOAD, actor=uuid.uuid4())
    assert e.value.detail["error"] == "tvd_no_aprobada"
    assert await conn.fetchval("SELECT count(*) FROM tvd_valoracion") == 0


async def test_el_acto_y_su_asiento_son_atomicos(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    """Si el asiento falla, NO queda `tvd_valoracion` ni amarre (una sola `conn.transaction()`)."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug)
    exp = await _expediente(conn)
    await _hold(conn, exp, tvd.id)
    with patch("app.services.valoracion_service.audit.append", new=AsyncMock(side_effect=RuntimeError)):
        with pytest.raises(RuntimeError):
            await ValoracionService(conn, slug).registrar_valoracion(
                tvd.id, _PAYLOAD, actor=uuid.uuid4()
            )
    assert await conn.fetchval("SELECT count(*) FROM tvd_valoracion") == 0
    assert await conn.fetchval(
        "SELECT valoracion_id FROM expediente_valoracion_hold WHERE expediente_id = $1", exp
    ) is None


# --- aplicación ---------------------------------------------------------------------------------


async def test_no_se_aplica_con_un_artefacto_pendiente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug, disposition="E")
    exp = await _expediente(conn)
    await _hold(conn, exp, tvd.id)
    # el AIP del expediente aún está por empaquetar/proteger
    await conn.execute("INSERT INTO expediente_aip (expediente_id, estado) VALUES ($1, 'pendiente')", exp)
    svc = ValoracionService(conn, slug)
    fijar, levantar = _storage()
    with (
        patch("app.clients.storage_client.fijar_retencion_internal", new=fijar),
        patch("app.clients.storage_client.levantar_legal_hold", new=levantar),
    ):
        await svc.registrar_valoracion(tvd.id, _PAYLOAD, actor=uuid.uuid4())
        assert await svc.aplicar_expediente(exp) == "esperando"
        levantar.assert_not_called()
        assert await conn.fetchval(
            "SELECT aplicada_at FROM expediente_valoracion_hold WHERE expediente_id = $1", exp
        ) is None

        # control positivo: protegido el AIP, se aplica
        await conn.execute("UPDATE expediente_aip SET estado = 'empaquetado' WHERE expediente_id = $1", exp)
        assert await svc.aplicar_expediente(exp) == "aplicada"
    levantar.assert_awaited_once()


async def test_aplicar_ct_propaga_la_fecha_al_espejo_local_del_aip(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El AIP nació con `worm_retain_until` NULL; tras fijar la retención CT el espejo lleva la
    fecha y la renovación (fase 5, que solo selecciona filas con fecha) lo recoge."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug, disposition="CT")
    exp = await _expediente(conn)
    await _hold(conn, exp, tvd.id)
    await conn.execute(
        "INSERT INTO expediente_aip (expediente_id, estado, worm_retain_until) "
        "VALUES ($1, 'empaquetado', NULL)", exp,
    )
    fijar, levantar = _storage()
    with (
        patch("app.clients.storage_client.fijar_retencion_internal", new=fijar),
        patch("app.clients.storage_client.levantar_legal_hold", new=levantar),
    ):
        await ValoracionService(conn, slug).registrar_valoracion(tvd.id, _PAYLOAD, actor=uuid.uuid4())
    assert await conn.fetchval(
        "SELECT worm_retain_until FROM expediente_aip WHERE expediente_id = $1", exp
    ) is not None


async def test_la_cadena_de_auditoria_queda_integra(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug, disposition="E")
    exp = await _expediente(conn)
    await _hold(conn, exp, tvd.id)
    fijar, levantar = _storage()
    with (
        patch("app.clients.storage_client.fijar_retencion_internal", new=fijar),
        patch("app.clients.storage_client.levantar_legal_hold", new=levantar),
    ):
        await ValoracionService(conn, slug).registrar_valoracion(tvd.id, _PAYLOAD, actor=uuid.uuid4())
    acciones = [r["action"] for r in await conn.fetch(
        "SELECT action FROM public.audit_log WHERE tenant_slug = $1 ORDER BY id", slug)]
    assert "archive.tvd_valoracion_registrada" in acciones
    assert "archive.expediente_valoracion_aplicada" in acciones
    fijar.assert_not_called()  # 'E' (destrucción) no fija retención
    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
