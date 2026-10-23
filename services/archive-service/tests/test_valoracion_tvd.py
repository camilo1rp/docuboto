# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Valoración de la TVD y retención legal reversible del fondo vencido (C-5 / C7, ADR-043).

Cubre `ValoracionService` (el acto del Comité, el hold `valoracion_pendiente`, la aplicación por
expediente) con repositorio y storage sustituidos en sus bordes, y los gates del router.

Mutaciones que deben caer (cada una descrita junto al test que la atrapa):
  * invertir el orden CT (levantar el hold ANTES de fijar la retención) ->
    `test_ct_fija_la_retencion_ANTES_de_levantar_el_hold`
  * levantar cuando el 1.er paso CT falló -> `test_ct_si_falla_fijar_la_retencion_no_se_levanta_el_hold`
  * aplicar mientras queda un artefacto sin proteger -> `test_espera_si_queda_un_artefacto_...`
  * motivo distinto de `valoracion_pendiente` al levantar (liberaría un hold judicial) ->
    `test_el_levantamiento_de_valoracion_confirma_el_motivo_de_sistema`
  * no asentar o asentar fuera de la tx -> `test_registrar_...asienta_en_la_misma_transaccion`
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.clients.storage_client import StorageLegalHoldError
from app.core.config import settings
from app.schemas.retencion_legal import ValoracionTvdRequest
from app.services.retention_math import RetencionDiferida
from app.services.valoracion_service import (
    MOTIVO_VALORACION,
    ValoracionHoldError,
    ValoracionService,
)
from tests.conftest import TENANT_HEADERS

SERIE_ID = uuid.uuid4()
EXP_ID = uuid.uuid4()
HOLD_ID = uuid.uuid4()
ACTOR = uuid.uuid4()
_AUDIT = "app.services.valoracion_service.audit.append"
_ST = "app.services.valoracion_service.storage_client"


class _Tx:
    """`conn.transaction()` como context manager que registra entrada y salida."""

    def __init__(self, eventos: list[str]) -> None:
        self._eventos = eventos

    async def __aenter__(self) -> None:
        self._eventos.append("tx_begin")

    async def __aexit__(self, *exc) -> bool:
        self._eventos.append("tx_end")
        return False


def _svc() -> tuple[ValoracionService, list[str]]:
    eventos: list[str] = []
    conn = MagicMock()
    conn.transaction = lambda: _Tx(eventos)
    svc = ValoracionService(conn, "demo")
    svc._repo = AsyncMock()
    svc._archive = AsyncMock()
    return svc, eventos


def _tvd(estado: str = "aprobada", disposition: str = "CT") -> SimpleNamespace:
    return SimpleNamespace(
        id=SERIE_ID, code="TVD-1", version=1, estado=estado, disposition=disposition
    )


_PAYLOAD_S = ValoracionTvdRequest(
    acta_comite="Acta 13 de 2026", fecha_acta=date(2026, 9, 1),
    fundamento="Valoracion de seleccion: se conserva una muestra del fondo",
    metodo_muestreo="Muestreo sistematico: un expediente de cada diez, por orden de codigo",
)
_PAYLOAD = ValoracionTvdRequest(
    acta_comite="Acta 12 de 2026", fecha_acta=date(2026, 9, 1),
    fundamento="Valoracion secundaria: el fondo se conserva por su valor historico",
)


# --- registrar la valoración ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["borrador", "migrada", "derogada"])
async def test_valoracion_con_tvd_que_no_esta_aprobada_es_409(estado: str) -> None:
    svc, _ = _svc()
    svc._archive.get_tvd_agrupacion_by_id.return_value = _tvd(estado)
    with pytest.raises(HTTPException) as exc:
        await svc.registrar_valoracion(SERIE_ID, _PAYLOAD, actor=ACTOR)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "tvd_no_aprobada"
    svc._repo.insert_valoracion.assert_not_called()


@pytest.mark.asyncio
async def test_valoracion_de_un_id_que_no_es_tvd_es_404() -> None:
    """La lectura va por `v_tvd_agrupaciones`: un id de TRD ni siquiera está en la vista."""
    svc, _ = _svc()
    svc._archive.get_tvd_agrupacion_by_id.return_value = None
    with pytest.raises(HTTPException) as exc:
        await svc.registrar_valoracion(SERIE_ID, _PAYLOAD, actor=ACTOR)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("estado", "disposition", "esperada"),
    [("aprobada", "CT", "CT"), ("convalidada", "conserve", "CT"), ("convalidada", "eliminate", "E"),
     ("aprobada", "S", "S"), ("aprobada", "digitize", "M")],
)
async def test_registrar_valoracion_congela_la_disposicion_agn_y_cuenta_los_afectados(
    estado: str, disposition: str, esperada: str
) -> None:
    """Control positivo: aprobada y convalidada se aceptan; los alias legados se normalizan a CT/E/S/M."""
    svc, _ = _svc()
    svc._archive.get_tvd_agrupacion_by_id.return_value = _tvd(estado, disposition)
    svc._repo.insert_valoracion.return_value = {"id": uuid.uuid4()}
    svc._repo.amarrar_valoracion.return_value = []  # sin expedientes aún: el hold nacerá valorado
    payload = _PAYLOAD_S if esperada == "S" else _PAYLOAD
    with patch(_AUDIT, new=AsyncMock()):
        res = await svc.registrar_valoracion(SERIE_ID, payload, actor=ACTOR)
    assert res.disposicion == esperada and res.expedientes_afectados == 0
    assert svc._repo.insert_valoracion.await_args.kwargs["disposicion"] == esperada
    assert svc._repo.insert_valoracion.await_args.kwargs["decidida_por"] == ACTOR


@pytest.mark.asyncio
async def test_valoracion_ya_registrada_es_409_y_no_asienta() -> None:
    svc, _ = _svc()
    svc._archive.get_tvd_agrupacion_by_id.return_value = _tvd()
    svc._repo.insert_valoracion.return_value = None  # ON CONFLICT DO NOTHING
    with patch(_AUDIT, new=AsyncMock()) as audit:
        with pytest.raises(HTTPException) as exc:
            await svc.registrar_valoracion(SERIE_ID, _PAYLOAD, actor=ACTOR)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "valoracion_ya_registrada"
    audit.assert_not_called()
    svc._repo.amarrar_valoracion.assert_not_called()


@pytest.mark.asyncio
async def test_registrar_valoracion_amarra_y_asienta_en_la_misma_transaccion() -> None:
    svc, eventos = _svc()
    svc._archive.get_tvd_agrupacion_by_id.return_value = _tvd()
    svc._repo.insert_valoracion.side_effect = lambda **_: (
        eventos.append("insert") or {"id": uuid.uuid4()}
    )
    svc._repo.amarrar_valoracion.side_effect = lambda *_: eventos.append("amarrar") or []
    audit = AsyncMock(side_effect=lambda *a, **k: eventos.append("asiento"))
    with patch(_AUDIT, new=audit):
        await svc.registrar_valoracion(SERIE_ID, _PAYLOAD, actor=ACTOR)
    assert eventos == ["tx_begin", "insert", "amarrar", "asiento", "tx_end"]
    kw = audit.await_args.kwargs
    assert kw["action"] == "archive.tvd_valoracion_registrada"
    assert kw["payload"]["acta_comite"] == "Acta 12 de 2026"
    assert kw["actor"] == str(ACTOR)


@pytest.mark.asyncio
async def test_la_aplicacion_sincrona_esta_acotada_y_un_fallo_no_tumba_el_acto() -> None:
    """Se aplican como máximo `valoracion_sync_apply_max`; una excepción en una no revierte el acto."""
    svc, _ = _svc()
    svc._archive.get_tvd_agrupacion_by_id.return_value = _tvd()
    svc._repo.insert_valoracion.return_value = {"id": uuid.uuid4()}
    ids = [uuid.uuid4() for _ in range(settings.valoracion_sync_apply_max + 7)]
    svc._repo.amarrar_valoracion.return_value = ids
    svc.aplicar_expediente = AsyncMock(side_effect=RuntimeError("storage"))  # type: ignore[method-assign]
    with patch(_AUDIT, new=AsyncMock()):
        res = await svc.registrar_valoracion(SERIE_ID, _PAYLOAD, actor=ACTOR)
    assert res.expedientes_afectados == len(ids)
    assert svc.aplicar_expediente.await_count == settings.valoracion_sync_apply_max


# --- aplicar la valoración a un expediente ---------------------------------------------------


def _fila(disposicion: str = "CT", intentos: int = 0, seleccion: str | None = None) -> dict:
    return {
        "seleccion": seleccion,
        "expediente_id": EXP_ID, "hold_id": HOLD_ID, "trd_serie_id": SERIE_ID,
        "valoracion_id": uuid.uuid4(), "intentos": intentos, "disposicion": disposicion,
        "acta_comite": "Acta 12 de 2026", "fecha_acta": date(2026, 9, 1),
        "serie_code": "TVD-1", "serie_version": 1,
        "archivo_gestion_years": None, "archivo_central_years": 10, "expediente_code": "EXP-1",
    }


def _listo(svc: ValoracionService, fila: dict, orden: list[str]) -> MagicMock:
    svc._repo.get_para_aplicar.return_value = fila
    svc._repo.artefactos_pendientes.return_value = False
    svc._repo.marcar_aplicada.side_effect = lambda *a: orden.append("marcar") or {"x": 1}
    st = MagicMock()
    st.StorageLegalHoldError = StorageLegalHoldError
    st.fijar_retencion_internal = AsyncMock(
        side_effect=lambda **_: orden.append("fijar_retencion")
        or {"filas_fijadas": 2, "retain_until": "2036-10-21T00:00:00+00:00"}
    )
    st.levantar_legal_hold = AsyncMock(
        side_effect=lambda **_: orden.append("levantar")
        or {"hold_id": str(HOLD_ID), "objetos_liberados_s3": 2, "holds_restantes": 0}
    )
    return st


@pytest.mark.asyncio
async def test_ct_fija_la_retencion_ANTES_de_levantar_el_hold() -> None:
    svc, _ = _svc()
    orden: list[str] = []
    st = _listo(svc, _fila("CT"), orden)
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()) as audit:
        res = await svc.aplicar_expediente(EXP_ID)
    assert res == "aplicada"
    assert orden == ["fijar_retencion", "levantar", "marcar"]
    assert st.fijar_retencion_internal.await_args.kwargs["retencion_anios"] == 10
    svc._repo.propagar_espejo_retencion.assert_awaited_once()  # el espejo local deja de ser NULL
    assert svc._repo.marcar_aplicada.await_args.args[1] == "retencion_ct"
    assert audit.await_args.kwargs["action"] == "archive.expediente_valoracion_aplicada"
    assert audit.await_args.kwargs["payload"]["resultado"] == "retencion_ct"


@pytest.mark.asyncio
async def test_ct_si_falla_fijar_la_retencion_no_se_levanta_el_hold() -> None:
    """Invertir el orden y fallar el 2.º paso dejaría contenido sin protección: aquí el hold
    SIGUE si la retención no se fijó."""
    svc, _ = _svc()
    orden: list[str] = []
    st = _listo(svc, _fila("CT"), orden)
    st.fijar_retencion_internal = AsyncMock(side_effect=StorageLegalHoldError(503, "caído"))
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()):
        res = await svc.aplicar_expediente(EXP_ID)
    assert res == "pendiente"
    st.levantar_legal_hold.assert_not_called()
    svc._repo.marcar_aplicada.assert_not_called()
    svc._repo.bump_intento.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("disposicion", "seleccion"), [("E", None), ("S", "descartar")])
async def test_destruccion_solo_levanta_el_hold_y_no_fija_retencion(
    disposicion: str, seleccion: str | None
) -> None:
    svc, _ = _svc()
    orden: list[str] = []
    st = _listo(svc, _fila(disposicion, seleccion=seleccion), orden)
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()):
        res = await svc.aplicar_expediente(EXP_ID)
    assert res == "aplicada" and orden == ["levantar", "marcar"]
    st.fijar_retencion_internal.assert_not_called()
    assert svc._repo.marcar_aplicada.await_args.args[1] == "sin_retencion"


@pytest.mark.asyncio
@pytest.mark.parametrize(("disposicion", "seleccion"), [("M", None), ("S", "conservar")])
async def test_m_y_lo_seleccionado_siguen_el_camino_de_ct(
    disposicion: str, seleccion: str | None
) -> None:
    """M (medio técnico) en electrónicos se conserva; S con `conservar` también: primero la
    retención, después el levantamiento. Mutación: tratar M/conservar como destrucción deja
    el contenido sin retención."""
    svc, _ = _svc()
    orden: list[str] = []
    st = _listo(svc, _fila(disposicion, seleccion=seleccion), orden)
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()):
        res = await svc.aplicar_expediente(EXP_ID)
    assert res == "aplicada" and orden == ["fijar_retencion", "levantar", "marcar"]
    assert svc._repo.marcar_aplicada.await_args.args[1] == "retencion_ct"


@pytest.mark.asyncio
async def test_una_valoracion_s_sin_seleccion_no_se_aplica() -> None:
    """El repositorio no devuelve la fila de una S sin selección (`get_para_aplicar` la excluye):
    el hold se queda donde está."""
    svc, _ = _svc()
    svc._repo.get_para_aplicar.return_value = None
    assert await svc.aplicar_expediente(EXP_ID) == "omitida"


@pytest.mark.asyncio
async def test_registrar_valoracion_s_exige_metodo_de_muestreo_y_no_aplica_nada() -> None:
    svc, _ = _svc()
    svc._archive.get_tvd_agrupacion_by_id.return_value = _tvd("aprobada", "S")
    with pytest.raises(HTTPException) as exc:
        await svc.registrar_valoracion(SERIE_ID, _PAYLOAD, actor=ACTOR)
    assert exc.value.status_code == 422 and exc.value.detail["error"] == "metodo_muestreo_requerido"
    svc._repo.insert_valoracion.assert_not_called()

    svc._repo.insert_valoracion.return_value = {"id": uuid.uuid4()}
    svc._repo.amarrar_valoracion.return_value = [EXP_ID]
    svc.aplicar_expediente = AsyncMock()
    with patch(_AUDIT, new=AsyncMock()):
        res = await svc.registrar_valoracion(SERIE_ID, _PAYLOAD_S, actor=ACTOR)
    assert res.seleccion_pendiente == 1
    svc.aplicar_expediente.assert_not_awaited()  # ningún expediente se levanta por la valoración
    assert svc._repo.insert_valoracion.await_args.kwargs["metodo_muestreo"] == _PAYLOAD_S.metodo_muestreo


def _hold(**over) -> dict:
    base = {
        "expediente_id": EXP_ID, "valoracion_id": uuid.uuid4(), "aplicada_at": None,
        "agotada": False, "seleccion": None, "disposicion": "S", "expediente_code": "EXP-1",
    }
    return base | over


@pytest.mark.asyncio
@pytest.mark.parametrize("accion", ["conservar", "descartar"])
async def test_el_acto_de_seleccion_se_asienta_y_aplica(accion: str) -> None:
    from app.schemas.retencion_legal import SeleccionValoracionRequest

    svc, _ = _svc()
    svc._repo.bloquear_hold_de_serie.return_value = _hold()
    svc._repo.registrar_seleccion.return_value = {"x": 1}
    svc.aplicar_expediente = AsyncMock(return_value="aplicada")
    with patch(_AUDIT, new=AsyncMock()) as audit:
        res = await svc.registrar_seleccion(
            SERIE_ID, EXP_ID,
            SeleccionValoracionRequest(accion=accion, fundamento="Entra a la muestra por su valor"),
            actor=ACTOR,
        )
    assert res.accion == accion and res.aplicacion == "aplicada"
    assert audit.await_args.kwargs["action"] == "archive.expediente_seleccion_registrada"
    assert audit.await_args.kwargs["actor"] == str(ACTOR)
    svc._repo.registrar_seleccion.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hold", "codigo", "http"),
    [
        (None, "valoracion_expediente_not_found", 404),
        (_hold(disposicion="CT"), "valoracion_no_es_seleccion", 409),
        (_hold(valoracion_id=None), "valoracion_no_es_seleccion", 409),
        (_hold(seleccion="conservar"), "seleccion_ya_registrada", 409),
        (_hold(aplicada_at=date(2026, 9, 1)), "seleccion_ya_registrada", 409),
    ],
)
async def test_la_seleccion_solo_se_registra_una_vez_y_sobre_una_valoracion_s(
    hold, codigo: str, http: int
) -> None:
    from app.schemas.retencion_legal import SeleccionValoracionRequest

    svc, _ = _svc()
    svc._repo.bloquear_hold_de_serie.return_value = hold
    with patch(_AUDIT, new=AsyncMock()) as audit, pytest.raises(HTTPException) as exc:
        await svc.registrar_seleccion(
            SERIE_ID, EXP_ID,
            SeleccionValoracionRequest(accion="descartar", fundamento="No entra a la muestra"),
            actor=ACTOR,
        )
    assert exc.value.status_code == http and exc.value.detail["error"] == codigo
    svc._repo.registrar_seleccion.assert_not_called()
    audit.assert_not_called()


@pytest.mark.asyncio
async def test_el_levantamiento_de_valoracion_confirma_el_motivo_de_sistema() -> None:
    """Levanta SOLO el hold de valoración: un hold judicial concurrente sobrevive (storage no
    libera S3 mientras quede otro). Mutación: otro `motivo_tipo_esperado` -> rojo."""
    svc, _ = _svc()
    st = _listo(svc, _fila("E"), [])
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()):
        await svc.aplicar_expediente(EXP_ID)
    kw = st.levantar_legal_hold.await_args.kwargs
    assert kw["motivo_tipo_esperado"] == MOTIVO_VALORACION == "valoracion_pendiente"
    assert kw["hold_id"] == HOLD_ID and kw["actor_id"] is None
    assert kw["acta_ref"] == "Acta 12 de 2026"


@pytest.mark.asyncio
async def test_hold_judicial_concurrente_no_impide_aplicar_y_se_asienta_el_resto() -> None:
    svc, _ = _svc()
    st = _listo(svc, _fila("E"), [])
    st.levantar_legal_hold = AsyncMock(
        return_value={"hold_id": str(HOLD_ID), "objetos_liberados_s3": 0, "holds_restantes": 1}
    )
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()) as audit:
        assert await svc.aplicar_expediente(EXP_ID) == "aplicada"
    p = audit.await_args.kwargs["payload"]
    assert p["objetos_liberados_s3"] == 0 and p["holds_restantes"] == 1


@pytest.mark.asyncio
async def test_espera_si_queda_un_artefacto_sin_proteger_y_no_consume_intentos() -> None:
    svc, _ = _svc()
    svc._repo.get_para_aplicar.return_value = _fila("CT")
    svc._repo.artefactos_pendientes.return_value = True
    st = MagicMock()
    st.StorageLegalHoldError = StorageLegalHoldError
    st.fijar_retencion_internal = AsyncMock()
    st.levantar_legal_hold = AsyncMock()
    with patch(_ST, new=st):
        assert await svc.aplicar_expediente(EXP_ID) == "esperando"
    st.fijar_retencion_internal.assert_not_called()
    st.levantar_legal_hold.assert_not_called()
    svc._repo.bump_intento.assert_not_called()


@pytest.mark.asyncio
async def test_hold_ya_levantado_se_trata_como_hecho_idempotencia() -> None:
    svc, _ = _svc()
    orden: list[str] = []
    st = _listo(svc, _fila("E"), orden)
    st.levantar_legal_hold = AsyncMock(
        side_effect=StorageLegalHoldError(409, "ya", "hold_ya_levantado")
    )
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()):
        assert await svc.aplicar_expediente(EXP_ID) == "aplicada"
    assert orden == ["marcar"]


@pytest.mark.asyncio
async def test_otra_instancia_ya_la_aplico_no_hay_asiento_fantasma() -> None:
    svc, _ = _svc()
    st = _listo(svc, _fila("E"), [])
    svc._repo.marcar_aplicada.side_effect = None
    svc._repo.marcar_aplicada.return_value = None
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()) as audit:
        assert await svc.aplicar_expediente(EXP_ID) == "aplicada"
    audit.assert_not_called()


@pytest.mark.asyncio
async def test_agota_los_intentos_deja_la_fila_agotada_y_deja_asiento() -> None:
    svc, _ = _svc()
    st = _listo(svc, _fila("E", intentos=settings.indice_reconciliation_max_intentos - 1), [])
    st.levantar_legal_hold = AsyncMock(side_effect=StorageLegalHoldError(503, "caído"))
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()) as audit:
        assert await svc.aplicar_expediente(EXP_ID) == "agotada"
    assert svc._repo.bump_intento.await_args.kwargs["agotada"] is True
    assert svc._repo.bump_intento.await_args.kwargs["proximo_at"] is None
    assert audit.await_args.kwargs["action"] == "archive.expediente_valoracion_agotada"


@pytest.mark.asyncio
async def test_sin_fila_pendiente_no_hace_nada() -> None:
    svc, _ = _svc()
    svc._repo.get_para_aplicar.return_value = None
    st = MagicMock()
    with patch(_ST, new=st):
        assert await svc.aplicar_expediente(EXP_ID) == "omitida"
    assert not st.method_calls


# --- el hold `valoracion_pendiente` antes de proteger ----------------------------------------------


def _exp() -> SimpleNamespace:
    return SimpleNamespace(id=EXP_ID, code="EXP-1", trd_serie_id=SERIE_ID)


def _serie() -> SimpleNamespace:
    return SimpleNamespace(code="TVD-1", version=2, archivo_gestion_years=None, archivo_central_years=10)


@pytest.mark.asyncio
async def test_sin_amarre_fija_el_hold_de_sistema_y_lo_amarra_a_la_version() -> None:
    svc, eventos = _svc()
    svc._repo.get_hold.return_value = None
    svc._repo.insert_hold.return_value = {"valoracion_id": None}
    st = MagicMock()
    st.StorageLegalHoldError = StorageLegalHoldError
    st.fijar_legal_hold = AsyncMock(
        return_value={"hold_id": str(HOLD_ID), "creado": True, "objetos_afectados": 0}
    )
    with patch(_ST, new=st), patch(_AUDIT, new=AsyncMock()) as audit:
        res = await svc.resolver_diferida(_exp(), _serie())
    assert isinstance(res, RetencionDiferida)
    kw = st.fijar_legal_hold.await_args.kwargs
    assert kw["motivo_tipo"] == "valoracion_pendiente" and kw["actor_id"] is None
    assert kw["acta_ref"] == "TVD TVD-1 v2"  # lo genera el servidor, no el cliente
    assert svc._repo.insert_hold.await_args.kwargs == {
        "expediente_id": EXP_ID, "hold_id": HOLD_ID, "trd_serie_id": SERIE_ID,
    }
    assert audit.await_args.kwargs["action"] == "archive.expediente_valoracion_hold_fijado"
    assert eventos == ["tx_begin", "tx_end"]  # amarre + asiento dentro de la tx


@pytest.mark.asyncio
async def test_si_storage_no_fija_el_hold_no_hay_amarre_ni_se_devuelve_retencion() -> None:
    """Fail-closed: sin hold no se protege. Mutación: devolver `RetencionDiferida` igual -> rojo."""
    svc, _ = _svc()
    svc._repo.get_hold.return_value = None
    st = MagicMock()
    st.StorageLegalHoldError = StorageLegalHoldError
    st.fijar_legal_hold = AsyncMock(side_effect=StorageLegalHoldError(503, "caído"))
    with patch(_ST, new=st):
        with pytest.raises(ValoracionHoldError):
            await svc.resolver_diferida(_exp(), _serie())
    svc._repo.insert_hold.assert_not_called()


@pytest.mark.asyncio
async def test_con_amarre_vigente_no_vuelve_a_llamar_a_storage() -> None:
    svc, _ = _svc()
    svc._repo.get_hold.return_value = {"aplicada_at": None, "resultado": None}
    st = MagicMock()
    st.fijar_legal_hold = AsyncMock()
    with patch(_ST, new=st):
        assert isinstance(await svc.resolver_diferida(_exp(), _serie()), RetencionDiferida)
    st.fijar_legal_hold.assert_not_called()


@pytest.mark.asyncio
async def test_valoracion_ya_aplicada_con_ct_el_objeto_nuevo_nace_con_ventana_rodante() -> None:
    """Un objeto que nace DESPUÉS de aplicada la valoración CT no se difiere (no habría quien
    levantara su hold): `(total, hoy)`."""
    svc, _ = _svc()
    svc._repo.get_hold.return_value = {
        "aplicada_at": datetime.now(tz=UTC), "resultado": "retencion_ct",
    }
    res = await svc.resolver_diferida(_exp(), _serie())
    assert res == (10, datetime.now(tz=UTC).date())


@pytest.mark.asyncio
async def test_valoracion_ya_aplicada_sin_retencion_no_difiere_ni_inventa_fecha() -> None:
    svc, _ = _svc()
    svc._repo.get_hold.return_value = {
        "aplicada_at": datetime.now(tz=UTC), "resultado": "sin_retencion",
    }
    assert await svc.resolver_diferida(_exp(), _serie()) is None


# --- barrida ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_barrer_cuenta_por_resultado_y_aisla_los_fallos() -> None:
    svc, _ = _svc()
    ids = [uuid.uuid4() for _ in range(4)]
    svc._repo.list_pendientes_aplicar.return_value = [{"expediente_id": i} for i in ids]
    svc.aplicar_expediente = AsyncMock(  # type: ignore[method-assign]
        side_effect=["aplicada", RuntimeError("x"), "esperando", "agotada"]
    )
    r = await svc.barrer(50)
    assert r == {"vistos": 4, "aplicadas": 1, "esperando": 1, "pendientes": 1, "agotadas": 1}


# --- router: gates y forma -------------------------------------------------------------------------

_URL = f"/api/v1/tvd/{SERIE_ID}/valoracion"
_BODY = {"acta_comite": "Acta 12", "fecha_acta": "2026-09-01",
         "fundamento": "Valoracion secundaria del fondo por su valor historico"}


@pytest.mark.asyncio
async def test_el_gate_es_trd_con_escritura_para_valorar_y_lectura_para_consultar(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    r = await client.post(_URL, json=_BODY, headers=TENANT_HEADERS)
    assert r.status_code == 403
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_TRD", 3)

    mock_conn.fetchval.reset_mock()
    mock_conn.fetchval.return_value = False
    r = await client.get(_URL, headers=TENANT_HEADERS)
    assert r.status_code == 403
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_TRD", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {**_BODY, "fundamento": "corto"},
        {k: v for k, v in _BODY.items() if k != "acta_comite"},
        {**_BODY, "fecha_acta": "no-es-fecha"},
        {**_BODY, "disposicion": "E"},  # la disposición la fija la TVD, no el cliente
    ],
)
async def test_valoracion_exige_acta_fecha_y_fundamento_y_prohibe_extras(
    client_with_conn: tuple[AsyncClient, AsyncMock], cuerpo: dict
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    r = await client.post(_URL, json=cuerpo, headers=TENANT_HEADERS)
    assert r.status_code == 422
