# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-04-12 — la política XAdES personal la declara la ENTIDAD, no el despliegue.

`firma_personal_require_xades` (`off|clasificados|todos`) vivía solo en el entorno
de signature-service: una decisión de cada entidad (depende de si ya enroló los
certificados de sus firmantes) estaba en manos de quien despliega, y valía igual
para todas. Ahora se lee de `sgd_param_admin` del tenant (clave sembrada por la
migración 010 de tenant-service) y el entorno es respaldo solo si la entidad no
la define.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.core import params_entidad
from app.core.config import settings
from app.core.http_clients import SigningKeyStatusUnavailableError
from tests.conftest import USER as USER_ID
from tests.test_cadena import _exec_sig, _make_conn, _sol_row, _turno_row
from tests.test_cadena_2fa import _patch_audit
from tests.test_cadena_personal_signing import (
    _clear,
    _override,
    _patch_sign_digest_remote,
    _patch_signing_key_status,
)
from tests.test_firma_xades_gate import (
    _CLEARANCE_ROOT,
    _post_sign,
    _queue_denial,
    _queue_native_fallback,
)

# La función REAL, capturada antes de que el autouse de conftest la sustituya.
_LEER_REAL = params_entidad.leer_param


# ── la lectura y el respaldo (unidad, sin HTTP) ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [('"clasificados"', "clasificados"), ('"todos"', "todos"), ('"off"', "off"), (None, None)],
)
async def test_leer_param_decodifica_el_jsonb(crudo, esperado) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = crudo
    assert await _LEER_REAL(conn, "firma_personal_require_xades") == esperado
    sql = conn.fetchval.await_args.args[0]
    assert "sgd_param_admin" in sql and "$1" in sql


@pytest.mark.asyncio
async def test_sin_tabla_de_parametros_la_entidad_no_define_nada() -> None:
    """Un tenant que aún no migró tenant-service no rompe la firma: respalda el entorno."""
    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.UndefinedTableError("no existe sgd_param_admin")
    assert await _LEER_REAL(conn, "firma_personal_require_xades") is None


@pytest.mark.asyncio
async def test_un_fallo_de_base_distinto_PROPAGA(monkeypatch: pytest.MonkeyPatch) -> None:
    """No poder leer la política NO es «la política es la laxa»."""
    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.PostgresConnectionError("se cayó")
    with pytest.raises(asyncpg.PostgresConnectionError):
        await _LEER_REAL(conn, "firma_personal_require_xades")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entidad", "entorno", "efectivo"),
    [
        ("clasificados", "off", "clasificados"),  # la entidad manda sobre un entorno laxo
        ("off", "todos", "off"),  # y también sobre uno estricto: es SU decisión
        ("todos", "off", "todos"),
        (None, "clasificados", "clasificados"),  # no la define: respalda el entorno
        (None, "off", "off"),
        ("  Clasificados ", "off", "clasificados"),  # tolera mayúsculas/espacios
    ],
)
async def test_modo_efectivo(monkeypatch, entidad, entorno, efectivo) -> None:
    monkeypatch.setattr(settings, "firma_personal_require_xades", entorno)
    monkeypatch.setattr(params_entidad, "leer_param", AsyncMock(return_value=entidad))
    assert await params_entidad.modo_xades_de_la_entidad(AsyncMock()) == efectivo


@pytest.mark.asyncio
@pytest.mark.parametrize("raro", ["clasificado", "", "si", 3, True, ["todos"]])
async def test_un_valor_desconocido_aplica_el_mas_estricto_y_lo_dice(
    monkeypatch, caplog, raro
) -> None:
    """Un valor corrupto no puede relajar en silencio una política de seguridad."""
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    monkeypatch.setattr(params_entidad, "leer_param", AsyncMock(return_value=raro))
    with caplog.at_level(logging.ERROR, logger="signature.params"):
        assert await params_entidad.modo_xades_de_la_entidad(AsyncMock()) == "todos"
    assert "no reconocido" in caplog.text


# ── el efecto en la firma (HTTP) ────────────────────────────────────────────


def _con_entidad(monkeypatch, valor) -> None:
    monkeypatch.setattr(params_entidad, "leer_param", AsyncMock(return_value=valor))


@pytest.mark.asyncio
async def test_entidad_clasificados_con_canal_caido_en_nivel_2_falla_cerrado_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El entorno dice `off`, la entidad `clasificados`: manda la entidad, y el
    acto NO degrada a nativa."""
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    _con_entidad(monkeypatch, "clasificados")
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    turno = _turno_row(orden=1, firmante_id=USER_ID, estado="pendiente")
    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, _sol_row(nivel_seguridad=2), turno, clearance=_CLEARANCE_ROOT)
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post_sign()
    finally:
        _clear()
    assert status_code == 503, body
    assert body["detail"]["error"] == "canal_firma_personal_no_disponible"
    asiento = next(
        c for c in audit_mock.await_args_list
        if c.kwargs.get("action") == "firma.xades_requerida_denegada"
    )
    assert asiento.kwargs["payload"]["gate_modo"] == "clasificados"
    assert asiento.kwargs["payload"]["factor_consumido"] is False


@pytest.mark.asyncio
async def test_entidad_clasificados_sin_certificado_en_nivel_2_es_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    _con_entidad(monkeypatch, "clasificados")
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    conn = _make_conn()
    turno = _turno_row(orden=1, firmante_id=USER_ID, estado="pendiente")
    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, _sol_row(nivel_seguridad=2), turno, clearance=_CLEARANCE_ROOT)
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post_sign()
    finally:
        _clear()
    assert status_code == 422, body
    assert body["detail"]["error"] == "firma_xades_requerida"


@pytest.mark.asyncio
async def test_entidad_off_conserva_la_degradacion_con_su_asiento_aunque_el_entorno_exija(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con `off` en la entidad se conserva la degradación a nativa con su asiento
    `firma.fallback_nativa_canal_caido` (la decisión es de la entidad)."""
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "todos")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _con_entidad(monkeypatch, "off")
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER_ID, estado="pendiente")
    conn.fetchval.side_effect = [True, 1]
    _queue_native_fallback(conn, sol, _sol_row(estado="firmado", nivel_seguridad=1), turno)
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post_sign()
    finally:
        _clear()
    assert status_code == 200, body
    fb = next(
        c for c in audit_mock.await_args_list
        if c.kwargs.get("action") == "firma.fallback_nativa_canal_caido"
    )
    assert fb.kwargs["payload"]["gate_modo"] == "off"


@pytest.mark.asyncio
async def test_sin_parametro_de_la_entidad_manda_el_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    _con_entidad(monkeypatch, None)
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    conn = _make_conn()
    turno = _turno_row(orden=1, firmante_id=USER_ID, estado="pendiente")
    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, _sol_row(nivel_seguridad=3), turno, clearance=_CLEARANCE_ROOT)
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post_sign()
    finally:
        _clear()
    assert status_code == 503, body



@pytest.mark.asyncio
async def test_entidad_todos_en_nivel_1_sin_certificado_es_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`todos` aplica en CUALQUIER nivel (mata la mutación «todos devuelve False»)."""
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    _con_entidad(monkeypatch, "todos")
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))
    conn = _make_conn()
    turno = _turno_row(orden=1, firmante_id=USER_ID, estado="pendiente")
    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, _sol_row(nivel_seguridad=1), turno, clearance={})
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post_sign()
    finally:
        _clear()
    assert status_code == 422, body
    assert body["detail"]["error"] == "firma_xades_requerida"
    asiento = next(
        c for c in audit_mock.await_args_list
        if c.kwargs.get("action") == "firma.xades_requerida_denegada"
    )
    assert asiento.kwargs["payload"]["gate_modo"] == "todos"


@pytest.mark.asyncio
async def test_entidad_todos_en_nivel_1_con_canal_caido_es_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    _con_entidad(monkeypatch, "todos")
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    conn = _make_conn()
    turno = _turno_row(orden=1, firmante_id=USER_ID, estado="pendiente")
    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, _sol_row(nivel_seguridad=1), turno, clearance={})
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post_sign()
    finally:
        _clear()
    assert status_code == 503, body
    assert body["detail"]["error"] == "canal_firma_personal_no_disponible"
