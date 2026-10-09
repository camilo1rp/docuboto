# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""E10 debt (AREA-07-10): renovación de la retención WORM CT del XML de cada firma PERSONAL
del acta (`acta_firma_{elabora,entrega,recibe}`), igual que el sello y el índice: ventana
rodante, monotónica, con asiento y backoff. Pure-unit; el SQL real (filtro CT, `GREATEST`)
está en `tests/integration/test_firma_inobtenible_sql.py`."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_client import WormRenewalOutcome
from app.core.config import settings
from app.jobs import indice_reconciliation
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.index_service import IndexService
from app.services.transferencia_service import TransferenciaService

TENANT = "demo"


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _sql(conn: AsyncMock, fragmento: str) -> list:
    return [c for c in conn.execute.call_args_list if c.args and fragmento in str(c.args[0])]


def _svc(conn: AsyncMock, anios: int | None = 25) -> TransferenciaService:
    svc = TransferenciaService(conn, TENANT)
    svc._resolve_ct_retencion_anios_acta = AsyncMock(return_value=anios)  # type: ignore[method-assign]
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("rol", ["elabora", "entrega", "recibe"])
async def test_renueva_con_el_tipo_del_rol_marca_y_asienta(rol: str) -> None:
    conn = _conn()
    fid, tid, eid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    nueva = datetime(2052, 1, 1, tzinfo=UTC)
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
    ) as storage:
        res = await _svc(conn).renovar_worm_firma_personal(fid, tid, eid, rol, intentos_previos=0)

    assert res == "renovado"
    kw = storage.await_args.kwargs
    assert kw["tipo"] == f"acta_firma_{rol}"
    assert kw["expediente_id"] == str(eid) and kw["retencion_anios"] == 25
    # ventana rodante desde HOY, nunca una fecha fija lejana
    assert kw["fecha_inicio"] == datetime.now(tz=UTC).date()
    marca = _sql(conn, "worm_renovacion_intentos = 0")
    assert len(marca) == 1 and marca[0].args[1:] == (fid, nueva)
    assert "GREATEST(worm_retain_until" in marca[0].args[0]  # monotónico también en el espejo
    asientos = _sql(conn, "INSERT INTO public.audit_log")
    assert len(asientos) == 1
    assert asientos[0].args[5] == "archive.acta_firma_personal_worm_retencion_renovada"
    assert asientos[0].args[6] == "transferencia_acta_firma_personal"


@pytest.mark.asyncio
async def test_no_op_confirmado_marca_pero_no_asienta() -> None:
    conn = _conn()
    vigente = datetime(2052, 1, 1, tzinfo=UTC)
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=vigente, renovado=False)),
    ):
        res = await _svc(conn).renovar_worm_firma_personal(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "recibe", intentos_previos=0
        )
    assert res == "omitido"
    assert len(_sql(conn, "worm_renovacion_intentos = 0")) == 1
    assert _sql(conn, "INSERT INTO public.audit_log") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "motivo"),
    [
        (WormRenewalOutcome(ok=False, disabled=True), "worm_disabled"),
        (WormRenewalOutcome(ok=False, not_found=True), "worm_object_not_found"),
        (WormRenewalOutcome(ok=False, transient=True), "worm_no_confirmado"),
    ],
)
async def test_los_fallos_de_storage_programan_backoff(outcome, motivo) -> None:
    conn = _conn()
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=outcome),
    ):
        res = await _svc(conn).renovar_worm_firma_personal(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "entrega", intentos_previos=0
        )
    assert res == "pendiente"
    bump = _sql(conn, "worm_renovacion_intentos = worm_renovacion_intentos + 1")
    assert len(bump) == 1 and bump[0].args[3] == motivo and bump[0].args[4] is False
    assert _sql(conn, "worm_renovacion_intentos = 0") == []


@pytest.mark.asyncio
async def test_trd_sin_retencion_no_inventa_una() -> None:
    conn = _conn()
    with patch("app.clients.storage_client.renovar_retencion_worm_internal", new=AsyncMock()) as st:
        res = await _svc(conn, anios=None).renovar_worm_firma_personal(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "recibe", intentos_previos=0
        )
    assert res == "pendiente"
    st.assert_not_awaited()
    assert _sql(conn, "worm_renovacion_intentos + 1")[0].args[3] == "trd_sin_retencion"


@pytest.mark.asyncio
async def test_agotado_asienta_la_alarma() -> None:
    conn = _conn()
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, transient=True)),
    ):
        res = await _svc(conn).renovar_worm_firma_personal(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "recibe",
            intentos_previos=settings.indice_reconciliation_max_intentos - 1,
        )
    assert res == "agotado"
    bump = _sql(conn, "worm_renovacion_intentos + 1")[0]
    assert bump.args[2] is None and bump.args[4] is True
    asientos = _sql(conn, "INSERT INTO public.audit_log")
    assert asientos[0].args[5] == "archive.acta_firma_personal_worm_renovacion_agotado"


@pytest.mark.asyncio
async def test_nunca_propaga_una_excepcion_imprevista() -> None:
    conn = _conn()
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        res = await _svc(conn).renovar_worm_firma_personal(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "recibe", intentos_previos=0
        )
    assert res == "pendiente"


# --- repositorio: el filtro CT y el no-read-up viven en el WHERE -----------------------


@pytest.mark.asyncio
async def test_la_consulta_del_job_filtra_ct_y_ventana_en_el_where() -> None:
    conn = _conn()
    await TransferenciaRepository(conn, TENANT).list_firma_personal_renovacion_pending(
        ventana_dias=400, ct_codes=["CT"], limit=10
    )
    sql, *params = conn.fetch.call_args[0]
    assert "s.disposition = ANY($2::text[])" in sql
    assert "w.worm_protegido_at IS NOT NULL" in sql and "w.worm_renovacion_agotado = FALSE" in sql
    assert params == [400, ["CT"], 10]


@pytest.mark.asyncio
async def test_la_visibilidad_lleva_el_clearance_en_el_where() -> None:
    conn = _conn()
    await TransferenciaRepository(conn, TENANT).list_firma_personal_renovacion_visibilidad(
        max_clearance=2, ct_codes=["CT"], ventana_dias=400
    )
    sql, *params = conn.fetch.call_args[0]
    assert "e.nivel_seguridad <= $2" in sql
    assert params == [["CT"], 2, 400]


# --- visibilidad operacional: las firmas salen con su tipo ------------------------------


@pytest.mark.asyncio
async def test_pendientes_renovacion_incluye_las_firmas_personales_con_su_tipo() -> None:
    conn = _conn()
    fila = {
        "expediente_id": uuid.uuid4(), "expediente_code": "EXP-CT-9", "rol": "entrega",
        "worm_retain_until": datetime.now(tz=UTC) + timedelta(days=5),
        "worm_renovacion_intentos": 3, "worm_renovacion_agotado": True,
        "worm_renovacion_ultimo_error": "worm_no_confirmado",
    }
    with (
        patch("app.services.index_service.IndexRepository.list_worm_renovacion_visibilidad", new=AsyncMock(return_value=[])),
        patch("app.services.index_service.TransferenciaRepository.list_worm_renovacion_visibilidad", new=AsyncMock(return_value=[])),
        patch("app.services.index_service.AipRepository.list_worm_renovacion_visibilidad", new=AsyncMock(return_value=[])),
        patch(
            "app.services.index_service.TransferenciaRepository.list_firma_personal_renovacion_visibilidad",
            new=AsyncMock(return_value=[fila]),
        ),
    ):
        items, total = await IndexService(conn, TENANT).list_pendientes_renovacion(
            page=1, size=20, user_id=None
        )
    assert total == 1 and items[0].tipo == "acta_firma_entrega" and items[0].worm_renovacion_agotado


# --- el job -------------------------------------------------------------------------------


def _pool() -> MagicMock:
    pool = MagicMock()
    gc = AsyncMock()
    gc.fetch.return_value = [{"slug": "tenant-a"}]
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=gc)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


@pytest.mark.asyncio
async def test_el_job_renueva_las_firmas_con_su_propio_lock_y_lo_libera() -> None:
    conn = _conn()
    conn.fetchval.return_value = True
    fila = {
        "firma_personal_id": uuid.uuid4(), "transferencia_id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(), "rol": "recibe",
        "worm_retain_until": None, "worm_renovacion_intentos": 2,
    }

    @asynccontextmanager
    async def fake(slug: str):
        yield conn

    renovar = AsyncMock(return_value="renovado")
    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=_pool()),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake),
        patch("app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending", new=AsyncMock(return_value=[])),
        patch("app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending", new=AsyncMock(return_value=[])),
        patch("app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending", new=AsyncMock(return_value=[])),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_firma_personal_renovacion_pending",
            new=AsyncMock(return_value=[fila]),
        ),
        patch("app.jobs.indice_reconciliation.TransferenciaService.renovar_worm_firma_personal", new=renovar),
    ):
        resumen = await indice_reconciliation.run_once_worm_renovacion()

    assert resumen["pendientes_vistos"] == 1 and resumen["renovados"] == 1
    args = renovar.await_args
    assert args.args[3] == "recibe" and args.kwargs == {"intentos_previos": 2}
    unlock = [
        c for c in conn.execute.call_args_list
        if c.args and "worm_renovacion_firma_personal" in c.args[0] and "unlock" in c.args[0]
    ]
    assert len(unlock) == 1


@pytest.mark.asyncio
async def test_el_lock_de_las_firmas_ocupado_no_bloquea_los_otros_ejes() -> None:
    conn = _conn()
    conn.fetchval.side_effect = [True, True, True, False]  # índice, acta, AIP libres; firmas ocupado

    @asynccontextmanager
    async def fake(slug: str):
        yield conn

    listar = AsyncMock(return_value=[])
    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=_pool()),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake),
        patch("app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending", new=AsyncMock(return_value=[])) as i,
        patch("app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending", new=AsyncMock(return_value=[])),
        patch("app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending", new=AsyncMock(return_value=[])),
        patch("app.jobs.indice_reconciliation.TransferenciaRepository.list_firma_personal_renovacion_pending", new=listar),
    ):
        await indice_reconciliation.run_once_worm_renovacion()
    i.assert_awaited_once()
    listar.assert_not_awaited()
