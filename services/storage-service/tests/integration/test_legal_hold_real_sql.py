# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retenciones legales por motivo (AREA-08-07, C-5/C7) contra Postgres REAL: el orden del acto, su
atomicidad y las CHECK/triggers de la migración 024 se prueban donde de verdad hay transacciones.

Corre con: `docker compose run --rm -e RUN_INTEGRATION=1 storage-service
pytest -q -m integration tests/integration/test_legal_hold_real_sql.py`

NO se ejecutó al escribirse (regla del lote C: la verificación es una sola, al cierre).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import HoldFijarRequest, HoldLevantarRequest
from app.services import legal_hold_service as lhs
from app.services.legal_hold_service import LegalHoldService

pytestmark = pytest.mark.integration


async def _seed(
    slug: str,
    conn: asyncpg.Connection,
    *,
    hold: bool,
    diferida: bool = False,
    tipo: str = "indice",
    exp: str | None = None,
) -> str:
    exp = exp or str(uuid.uuid4())
    await PreservacionWormRepository(conn, slug).insert(
        expediente_id=exp,
        tipo=tipo,
        bucket="orpycamcp-itest-preservation",
        object_key=f"{exp}/{tipo}.xml",
        version_id="v1",
        sha256="a" * 64,
        retain_until=None if diferida else datetime.now(tz=UTC) + timedelta(days=30),
        retention_mode=None if diferida else "COMPLIANCE",
        legal_hold=True if diferida else hold,
        file_id=None,
        retencion_origen="valoracion_pendiente" if diferida else "calendario",
    )
    return exp


def _minio() -> MagicMock:
    m = MagicMock()
    m.enable_object_legal_hold = AsyncMock()
    m.disable_object_legal_hold = AsyncMock()
    m.is_object_legal_hold_enabled = AsyncMock(return_value=True)
    return m


async def _acciones(conn: asyncpg.Connection, slug: str, exp: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT action FROM public.audit_log WHERE tenant_slug = $1 AND object_ref = $2 "
        "ORDER BY id",
        slug,
        exp,
    )
    return [r["action"] for r in rows]


def _fijar(exp: str, motivo: str = "judicial") -> HoldFijarRequest:
    return HoldFijarRequest(
        expediente_id=exp,
        motivo_tipo=motivo,  # type: ignore[arg-type]
        fundamento="Orden del juzgado 12 por proceso en curso",
        acta_ref="ACTA-1",
    )


def _levantar(motivo: str) -> HoldLevantarRequest:
    return HoldLevantarRequest(
        motivo_tipo_esperado=motivo,  # type: ignore[arg-type]
        fundamento="Se levanta por orden del juzgado 12",
        acta_ref="ACTA-L-1",
    )


@pytest.fixture(autouse=True)
def _habilitado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lhs.settings, "preservation_enabled", True)


@pytest.mark.asyncio
async def test_fijar_sobre_un_expediente_sin_objetos_deja_hold_y_asiento(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    exp = str(uuid.uuid4())
    monkeypatch.setattr(lhs, "get_minio", lambda: _minio())

    res = await LegalHoldService(conn, slug).fijar(
        _fijar(exp), actor=str(uuid.uuid4()), actor_id=uuid.uuid4()
    )

    assert res.creado and res.objetos_afectados == 0
    assert await _acciones(conn, slug, exp) == ["preservacion.legal_hold_fijado"]
    # El gate de purga ve el hold aunque no exista NINGUNA fila de objeto (H2).
    assert await PreservacionWormRepository(conn, slug).hold_activo_del_expediente(exp) is not None


@pytest.mark.asyncio
async def test_levantar_el_ultimo_hold_deja_solicitud_levantamiento_y_la_fila_cambia(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    exp = await _seed(slug, conn, hold=False)
    minio = _minio()
    monkeypatch.setattr(lhs, "get_minio", lambda: minio)
    svc = LegalHoldService(conn, slug)
    uid = uuid.uuid4()
    hold = await svc.fijar(_fijar(exp), actor=str(uid), actor_id=uid)

    res = await svc.levantar(hold.hold_id, _levantar("judicial"), actor=str(uid), actor_id=uid)

    assert (res.objetos_liberados_s3, res.holds_restantes) == (1, 0)
    fila = await PreservacionWormRepository(conn, slug).get_by_expediente_and_tipo(exp, "indice")
    assert fila["legal_hold"] is False
    assert await _acciones(conn, slug, exp) == [
        "worm.legal_hold_fijado",
        "preservacion.legal_hold_fijado",
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantado",
        "preservacion.legal_hold_levantado",
    ]
    minio.disable_object_legal_hold.assert_awaited_once()


@pytest.mark.asyncio
async def test_levantar_valoracion_con_un_judicial_vivo_no_libera_y_la_fila_deja_de_ser_pendiente(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H1 contra el SQL real: tras levantar la valoración sigue el hold judicial, el almacén NO se
    toca, y la fila pasa a `valorada_sin_retencion` SIN violar `ck_pwo_diferida_exige_hold`."""
    slug, conn = tenant_ctx
    exp = await _seed(slug, conn, hold=True, diferida=True)
    minio = _minio()
    monkeypatch.setattr(lhs, "get_minio", lambda: minio)
    svc = LegalHoldService(conn, slug)
    uid = uuid.uuid4()
    valoracion = await svc.fijar(_fijar(exp, "valoracion_pendiente"), actor=None, actor_id=None)
    await svc.fijar(_fijar(exp, "judicial"), actor=str(uid), actor_id=uid)

    res = await svc.levantar(
        valoracion.hold_id, _levantar("valoracion_pendiente"), actor=None, actor_id=None
    )

    assert (res.objetos_liberados_s3, res.holds_restantes) == (0, 1)
    minio.disable_object_legal_hold.assert_not_awaited()
    fila = await PreservacionWormRepository(conn, slug).get_by_expediente_and_tipo(exp, "indice")
    assert fila["legal_hold"] is True and fila["retencion_origen"] == "valorada_sin_retencion"


@pytest.mark.asyncio
async def test_si_falla_el_almacen_al_levantar_el_hold_sigue_activo_y_hay_traza(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    exp = await _seed(slug, conn, hold=True)
    minio = _minio()
    monkeypatch.setattr(lhs, "get_minio", lambda: minio)
    svc = LegalHoldService(conn, slug)
    uid = uuid.uuid4()
    hold = await svc.fijar(_fijar(exp), actor=str(uid), actor_id=uid)
    minio.disable_object_legal_hold = AsyncMock(side_effect=RuntimeError("s3 caido"))

    with pytest.raises(RuntimeError, match="s3 caido"):
        await svc.levantar(hold.hold_id, _levantar("judicial"), actor=str(uid), actor_id=uid)

    assert await PreservacionWormRepository(conn, slug).hold_activo_del_expediente(exp) is not None
    acciones = await _acciones(conn, slug, exp)
    assert acciones[-2:] == [
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantamiento_fallido",
    ]
    # El lock de sesion se libero: el acto se puede reintentar sin colgarse.
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_si_falla_el_asiento_final_el_hold_no_se_levanta_pero_queda_la_solicitud(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad registro+fila+asiento: si el asiento final falla, NADA se levanta (misma tx). Y la
    solicitud previa ya está asentada: el almacén liberado nunca queda sin ningún registro."""
    slug, conn = tenant_ctx
    exp = await _seed(slug, conn, hold=True)
    monkeypatch.setattr(lhs, "get_minio", lambda: _minio())
    svc = LegalHoldService(conn, slug)
    uid = uuid.uuid4()
    hold = await svc.fijar(_fijar(exp), actor=str(uid), actor_id=uid)

    real_append = lhs.audit.append

    async def _falla_el_final(c, **kw):
        if kw["action"] == "worm.legal_hold_levantado":
            raise RuntimeError("audit caido")
        return await real_append(c, **kw)

    monkeypatch.setattr(lhs.audit, "append", _falla_el_final)

    with pytest.raises(RuntimeError, match="audit caido"):
        await svc.levantar(hold.hold_id, _levantar("judicial"), actor=str(uid), actor_id=uid)

    assert await PreservacionWormRepository(conn, slug).hold_activo_del_expediente(exp) is not None
    fila = await PreservacionWormRepository(conn, slug).get_by_expediente_and_tipo(exp, "indice")
    assert fila["legal_hold"] is True
    assert "worm.legal_hold_levantamiento_solicitado" in await _acciones(conn, slug, exp)


# ── migración 024: lo que la base impone aunque el código se equivoque ──────────────────


@pytest.mark.asyncio
async def test_la_base_rechaza_una_fila_diferida_sin_hold(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`ck_pwo_diferida_exige_hold`: un objeto sin retención y sin hold sería borrable."""
    slug, conn = tenant_ctx
    with pytest.raises(asyncpg.CheckViolationError):
        await PreservacionWormRepository(conn, slug).insert(
            expediente_id=str(uuid.uuid4()),
            tipo="indice",
            bucket="b",
            object_key="k",
            version_id="v",
            sha256="a" * 64,
            retain_until=None,
            retention_mode=None,
            legal_hold=False,
            retencion_origen="valoracion_pendiente",
        )


@pytest.mark.asyncio
async def test_la_base_rechaza_calendario_sin_fecha(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`ck_pwo_retencion_coherente`: `calendario` <=> retain_until y retention_mode presentes.
    Mutación: quitar la CHECK -> una fila `calendario` con NULL pasa y el gate dejaría de verla."""
    slug, conn = tenant_ctx
    with pytest.raises(asyncpg.CheckViolationError):
        await PreservacionWormRepository(conn, slug).insert(
            expediente_id=str(uuid.uuid4()),
            tipo="indice",
            bucket="b",
            object_key="k",
            version_id="v",
            sha256="a" * 64,
            retain_until=None,
            retention_mode=None,
            legal_hold=True,
            retencion_origen="calendario",
        )


@pytest.mark.asyncio
async def test_el_registro_de_holds_es_append_only_salvo_levantar_una_vez(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    exp = str(uuid.uuid4())
    monkeypatch.setattr(lhs, "get_minio", lambda: _minio())
    svc = LegalHoldService(conn, slug)
    uid = uuid.uuid4()
    hold = await svc.fijar(_fijar(exp), actor=str(uid), actor_id=uid)
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')

    with pytest.raises(asyncpg.RaiseError):  # reescribir el fundamento
        await conn.execute(
            "UPDATE preservacion_legal_hold SET fundamento = 'otro fundamento largo' WHERE id = $1",
            hold.hold_id,
        )
    with pytest.raises(asyncpg.RaiseError):  # borrar
        await conn.execute("DELETE FROM preservacion_legal_hold WHERE id = $1", hold.hold_id)

    await svc.levantar(hold.hold_id, _levantar("judicial"), actor=str(uid), actor_id=uid)
    with pytest.raises(asyncpg.RaiseError):  # re-levantar / mutar lo ya levantado
        await conn.execute(
            "UPDATE preservacion_legal_hold SET levantado_at = now() WHERE id = $1", hold.hold_id
        )


@pytest.mark.asyncio
async def test_solo_puede_haber_un_hold_de_valoracion_activo_por_expediente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    exp = str(uuid.uuid4())
    insertar = (
        "INSERT INTO preservacion_legal_hold (expediente_id, motivo_tipo, fundamento, acta_ref) "
        "VALUES ($1, 'valoracion_pendiente', 'fondo acumulado vencido', 'TVD S v1')"
    )
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    await conn.execute(insertar, exp)
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(insertar, exp)
