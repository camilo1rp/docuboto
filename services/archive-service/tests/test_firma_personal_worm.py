# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-07-10: el XML de cada firma PERSONAL del acta entra al almacén WORM.

Solo el sello institucional del acta tenía Object-Lock; el XML de elabora/entrega/recibe vivía
en el bucket de documentos, sin retención. Se cablea como el acta: bookkeeping en la MISMA tx
que la firma, disparo post-commit best-effort y reconciliación con backoff. Lo que NO entra
aquí es la decisión de dominio pendiente («firma_inobtenible»): esto solo protege lo firmado.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.signature_client import SignPersonalOutcome
from app.clients.storage_client import WormProtectionOutcome
from app.core.actor import Actor
from app.core.config import settings
from app.services.transferencia_service import TransferenciaService
from tests.conftest import (
    PROTEGER_WORM_FIRMA_PERSONAL_REAL,
    USER_ID,
    make_expediente_record,
)
from tests.test_transferencia_acta_firma_personal import _personal_row, _signed_personal
from tests.test_transferencias import _acta_row, _transfer_row

TENANT = "demo"


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    conn.fetch.return_value = []
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _fila_worm(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "transferencia_id": uuid.uuid4(),
        "rol": "recibe",
        "minio_key_firmado": "file-id-1/acta-personal-firmado.xml",
        "creada_en": datetime(2026, 10, 1, tzinfo=UTC),
        "expediente_id": uuid.uuid4(),
        "worm_protegido_at": None,
        "worm_reconcile_intentos": 0,
    }
    base.update(kw)
    return base


def _con_retencion(svc: TransferenciaService, resultado=(10, datetime(2026, 10, 1, tzinfo=UTC).date())):
    svc._resolve_worm_retention_acta = AsyncMock(return_value=resultado)  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_firmar_personal_deja_la_fila_pendiente_en_la_tx_y_dispara_el_worm(
    _firma_personal_worm_doble,
) -> None:
    """La fila de bookkeeping se escribe junto a la firma (no se pierde si el proceso muere
    antes del disparo) y la protección se pide post-commit con el id de la firma y el actor."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    inserted = _personal_row(transferencia_id=tid)
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
        inserted,
    ]
    svc = TransferenciaService(conn, TENANT)

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ):
        await svc.firmar_personal(tid, Actor(id=USER_ID), "123456", "Bearer x")

    bookkeeping = [
        c for c in conn.execute.call_args_list
        if "INSERT INTO transferencia_acta_firma_personal_worm" in str(c.args[0])
    ]
    assert len(bookkeeping) == 1 and bookkeeping[0].args[1] == inserted["id"]
    _firma_personal_worm_doble.assert_awaited_once()
    assert _firma_personal_worm_doble.await_args.args == (inserted["id"],)
    assert _firma_personal_worm_doble.await_args.kwargs == {
        "actor_id": str(USER_ID),
        "origen": "close",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("rol", ["elabora", "entrega", "recibe"])
async def test_la_proteccion_pide_el_tipo_del_rol_y_marca_el_asiento(rol: str) -> None:
    conn = _conn()
    fila = _fila_worm(rol=rol)
    conn.fetchrow.return_value = fila
    svc = TransferenciaService(conn, TENANT)
    _con_retencion(svc)
    worm_id = uuid.uuid4()
    salida = WormProtectionOutcome(ok=True, worm_object_id=worm_id, retain_until=datetime(2036, 10, 1, tzinfo=UTC))

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock(return_value=salida)
    ) as proteger:
        resultado = await PROTEGER_WORM_FIRMA_PERSONAL_REAL(
            svc, fila["id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "protegido"
    kw = proteger.await_args.kwargs
    assert kw["tipo"] == f"acta_firma_{rol}"
    # file_id derivado de la fila (minio_key_firmado), nunca de una entrada externa
    assert kw["file_id"] == "file-id-1"
    assert kw["expediente_id"] == str(fila["expediente_id"])
    assert kw["retencion_anios"] == 10
    marca = [c for c in conn.execute.call_args_list if "worm_protegido_at = now()" in str(c.args[0])]
    assert marca
    asiento = next(
        c.args for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    )
    assert asiento[5] == "archive.acta_firma_personal_worm_protegido"
    assert json.loads(asiento[8])["rol"] == rol


@pytest.mark.asyncio
async def test_la_reconciliacion_usa_la_puerta_interna() -> None:
    conn = _conn()
    fila = _fila_worm()
    conn.fetchrow.return_value = fila
    svc = TransferenciaService(conn, TENANT)
    _con_retencion(svc)
    salida = WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4(), retain_until=None)

    with (
        patch("app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock()) as humano,
        patch(
            "app.clients.storage_client.proteger_artefacto_worm_internal",
            new=AsyncMock(return_value=salida),
        ) as interna,
    ):
        resultado = await PROTEGER_WORM_FIRMA_PERSONAL_REAL(
            svc, fila["id"], actor_id=None, origen="reconciliation"
        )

    assert resultado == "protegido"
    interna.assert_awaited_once()
    humano.assert_not_awaited()


@pytest.mark.asyncio
async def test_si_el_almacen_falla_reprograma_y_no_lanza(monkeypatch) -> None:
    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 8)
    conn = _conn()
    fila = _fila_worm(worm_reconcile_intentos=1)
    conn.fetchrow.return_value = fila
    svc = TransferenciaService(conn, TENANT)
    _con_retencion(svc)

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, transient=True)),
    ):
        resultado = await PROTEGER_WORM_FIRMA_PERSONAL_REAL(
            svc, fila["id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "pendiente"
    bump = next(c for c in conn.execute.call_args_list if "worm_reconcile_intentos + 1" in str(c.args[0]))
    _, _fid, proximo_at, error, agotado = bump.args
    assert error == "worm_no_confirmado" and agotado is False and proximo_at is not None


@pytest.mark.asyncio
async def test_sin_retencion_resoluble_no_se_protege_con_una_fecha_inventada() -> None:
    conn = _conn()
    fila = _fila_worm()
    conn.fetchrow.return_value = fila
    svc = TransferenciaService(conn, TENANT)
    _con_retencion(svc, resultado=None)

    with patch("app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock()) as proteger:
        resultado = await PROTEGER_WORM_FIRMA_PERSONAL_REAL(
            svc, fila["id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "pendiente"
    proteger.assert_not_awaited()
    bump = next(c for c in conn.execute.call_args_list if "worm_reconcile_intentos + 1" in str(c.args[0]))
    assert bump.args[3] == "trd_sin_retencion"


@pytest.mark.asyncio
async def test_ya_protegida_o_sin_archivo_se_omite() -> None:
    svc = TransferenciaService(_conn(), TENANT)
    svc._conn.fetchrow.return_value = _fila_worm(worm_protegido_at=datetime.now(tz=UTC))  # type: ignore[attr-defined]
    with patch("app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock()) as proteger:
        assert await PROTEGER_WORM_FIRMA_PERSONAL_REAL(svc, uuid.uuid4(), actor_id="x", origen="close") == "omitido"
        svc._conn.fetchrow.return_value = _fila_worm(minio_key_firmado=None)  # type: ignore[attr-defined]
        assert await PROTEGER_WORM_FIRMA_PERSONAL_REAL(svc, uuid.uuid4(), actor_id="x", origen="close") == "omitido"
    proteger.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_metodo_real_nunca_lanza() -> None:
    svc = TransferenciaService(_conn(), TENANT)
    svc._conn.fetchrow.side_effect = RuntimeError("la base se cayó")  # type: ignore[attr-defined]
    assert await PROTEGER_WORM_FIRMA_PERSONAL_REAL(svc, uuid.uuid4(), actor_id="x", origen="close") == "pendiente"


@pytest.mark.asyncio
async def test_la_barrida_recoge_las_firmas_sin_proteger() -> None:
    from app.jobs import indice_reconciliation

    pool = MagicMock()
    glob = AsyncMock()
    glob.fetch.return_value = [{"slug": "tenant-a"}]
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=glob)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    conn = _conn()
    conn.fetchval.return_value = True
    conn.fetchval.return_value = True
    filas_personales = [{"firma_personal_id": uuid.uuid4(), "worm_reconcile_intentos": 0}]

    async def _fetch(sql, *args):
        # la misma barrida recoge también las constancias (migración 061): otra tabla
        return filas_personales if "transferencia_acta_firma_personal_worm" in sql else []

    conn.fetch.side_effect = _fetch

    @asynccontextmanager
    async def _acquire(_slug: str):
        yield conn

    proteger = AsyncMock(return_value="protegido")
    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", _acquire),
        patch.object(TransferenciaService, "proteger_worm_firma_personal", proteger),
    ):
        resumen = await indice_reconciliation.run_once_firmas_personales_worm()

    assert resumen == {"pendientes_vistos": 1, "protegidos": 1, "fallidos": 0, "agotados": 0}
    assert proteger.await_args.kwargs == {"actor_id": None, "origen": "reconciliation"}
    sql = conn.fetch.await_args_list[0].args[0]
    assert "worm_protegido_at IS NULL" in sql and "worm_reconcile_agotado = FALSE" in sql


@pytest.mark.asyncio
async def test_la_misma_barrida_protege_las_constancias_de_firma_inobtenible() -> None:
    """Migración 061: el sello y la firma personal del declarante que no pudieron protegerse
    post-commit se repiten con backoff en la barrida de las firmas del acta."""
    from app.jobs import indice_reconciliation

    pool = MagicMock()
    glob = AsyncMock()
    glob.fetch.return_value = [{"slug": "tenant-a"}]
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=glob)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    conn = _conn()
    conn.fetchval.return_value = True
    exc_id = uuid.uuid4()

    async def _fetch(sql, *args):
        if "transferencia_acta_firma_excepcion_worm" in sql:
            return [
                {"excepcion_id": exc_id, "artefacto": "sello", "worm_reconcile_intentos": 0},
                {"excepcion_id": exc_id, "artefacto": "personal", "worm_reconcile_intentos": 1},
            ]
        return []

    conn.fetch.side_effect = _fetch

    @asynccontextmanager
    async def _acquire(_slug: str):
        yield conn

    proteger = AsyncMock(side_effect=["protegido", "agotado"])
    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", _acquire),
        patch.object(TransferenciaService, "proteger_worm_firma_excepcion", proteger),
    ):
        resumen = await indice_reconciliation.run_once_firmas_personales_worm()

    assert resumen == {"pendientes_vistos": 2, "protegidos": 1, "fallidos": 0, "agotados": 1}
    assert [c.args[1] for c in proteger.await_args_list] == ["sello", "personal"]
    assert all(
        c.kwargs == {"actor_id": None, "origen": "reconciliation"} for c in proteger.await_args_list
    )
