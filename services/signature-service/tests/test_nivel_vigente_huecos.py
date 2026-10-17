# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B9-2: huecos que dejaba el fixture ``_nivel_vigente_neutro`` (revisión de pruebas).

El fixture autouse devuelve la solicitud tal cual para no alterar las secuencias de
mocks. Eso esconde todo gate que SOLO se evalúa tras la relectura: aquí corre la
relectura REAL (marca ``nivel_vigente_real``) sobre el arnés de servicio de
``test_challenge_mismas_precondiciones_que_firmar``.

Cubre lo que ninguna prueba real ejercitaba: el gate XAdES de ``/challenge`` (movido
dentro de la transacción), el veto del correo en el camino de ``/sign`` (``_gate_2fa``),
la ausencia de corrección cuando el nivel no sube, la solicitud desaparecida bajo
bloqueo y el PARECIDO BYTE A BYTE de todos los 404 neutros (anti-enumeración).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.http_clients import SigningKeyStatusUnavailableError
from app.schemas.cadena import FirmarRequest
from app.services import cadena_service as mod
from tests.conftest import USER
from tests.test_challenge_mismas_precondiciones_que_firmar import (
    SOL_ID,
    _codigo,
    _error,
    _servicio,
    _sol,
)

pytestmark = [pytest.mark.nivel_vigente_real, pytest.mark.asyncio]


def _sin_certificado(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(
        mod,
        "get_signing_key_status_remote",
        AsyncMock(return_value={"estado": "no_configurado", "cert_pem": None}),
    )


# --------------------------------------------------------------------------- XAdES en /challenge


async def test_challenge_el_gate_xades_clasificados_se_activa_por_el_nivel_vivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copia 1, objeto vivo 2, modo `clasificados`, sin certificado: 422 y NINGÚN correo.
    El gate se movió dentro de la transacción, detrás de la relectura; con la copia
    (1) el desafío se emitía y el OTP salía por correo en un acto que /sign iba a
    rechazar. Mutación: evaluar `_xades_gate_activo` con el `sol` previo => cae."""
    svc, enviar, identidad = _servicio(monkeypatch)
    _sin_certificado(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="clasificados"))
    svc._repo.nivel_vigente_for_update.return_value = (2, 1)

    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))

    assert exc.status_code == 422 and _codigo(exc) == "firma_xades_requerida"
    enviar.assert_not_awaited()
    identidad.assert_not_awaited()
    svc._repo.nivel_vigente_for_update.assert_awaited_once()


async def test_challenge_con_nivel_vivo_1_el_gate_clasificados_no_se_activa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control positivo: con el nivel vivo igual a la copia (1) el modo `clasificados`
    NO dispara, y la petición sigue más allá del gate (cualquier error posterior es
    de otra cosa). Sin este control, el caso de arriba aprobaría aunque el gate
    estuviera siempre activo."""
    svc, enviar, _ = _servicio(monkeypatch)
    _sin_certificado(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="clasificados"))
    svc._repo.nivel_vigente_for_update.return_value = (1, 1)

    try:
        await svc.emitir_challenge(SOL_ID, USER, "Bearer x")
    except HTTPException as exc:
        assert _codigo(exc) != "firma_xades_requerida"
    except Exception:  # noqa: BLE001 — el arnés no modela el resto del desafío
        pass
    svc._repo.actualizar_nivel_seguridad.assert_not_awaited()


async def test_challenge_canal_caido_bajo_gate_por_nivel_vivo_es_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El 503 fail-closed del gate también depende del nivel vivo."""
    svc, enviar, _ = _servicio(monkeypatch)
    monkeypatch.setattr(
        mod,
        "get_signing_key_status_remote",
        AsyncMock(side_effect=SigningKeyStatusUnavailableError("x")),
    )
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="clasificados"))
    svc._repo.nivel_vigente_for_update.return_value = (2, 1)

    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))

    assert exc.status_code == 503 and _codigo(exc) == "canal_firma_personal_no_disponible"
    enviar.assert_not_awaited()


# --------------------------------------------------------------------------- veto en /sign


async def test_sign_el_veto_del_correo_usa_el_nivel_vivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copia 1, objeto vivo 2, TOTP activo, el firmante manda `challenge_id`+`otp`:
    409 `totp_requerido` y el desafío NO se toca. Con la copia (1) el correo valía y
    bastaba controlar el buzón para saltarse el autenticador en un acto de nivel 2.
    Mutación: pasar el `sol` previo a `_gate_2fa` => firma por correo."""
    svc, _, _ = _servicio(monkeypatch)
    _sin_certificado(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="off"))
    totp = AsyncMock(return_value={"activo": True, "user_id": str(USER)})
    monkeypatch.setattr(mod, "get_totp_status_remote", totp)
    svc._repo.nivel_vigente_for_update.return_value = (2, 1)
    body = FirmarRequest(challenge_id="11111111-1111-1111-1111-111111111111", otp="123456")

    exc = await _error(svc.firmar(SOL_ID, USER, body, "Bearer x"))

    assert exc.status_code == 409 and _codigo(exc) == "totp_requerido"
    totp.assert_awaited()


async def test_sign_con_nivel_vivo_1_el_correo_no_esta_vetado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control positivo del anterior: con el nivel vivo en 1 el veto no consulta
    siquiera el estado del TOTP (no aplica por debajo de nivel 2)."""
    svc, _, _ = _servicio(monkeypatch)
    _sin_certificado(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="off"))
    totp = AsyncMock(return_value={"activo": True, "user_id": str(USER)})
    monkeypatch.setattr(mod, "get_totp_status_remote", totp)
    svc._repo.nivel_vigente_for_update.return_value = (1, 1)
    body = FirmarRequest(challenge_id="11111111-1111-1111-1111-111111111111", otp="123456")

    try:
        await svc.firmar(SOL_ID, USER, body, "Bearer x")
    except Exception:  # noqa: BLE001 — el arnés no modela el desafío
        pass
    totp.assert_not_awaited()


# --------------------------------------------------------------------------- la corrección de la copia


@pytest.mark.parametrize("camino", ["challenge", "sign"])
async def test_nivel_vivo_igual_a_la_copia_no_reescribe_la_copia(
    monkeypatch: pytest.MonkeyPatch, camino: str
) -> None:
    """Solo se corrige la copia cuando el nivel SUBE. Un UPDATE incondicional
    reescribiría `firma_solicitud` en cada firma (y su `updated_at`). Mutación:
    `>` por `>=` en `_sol_con_nivel_vigente` => cae."""
    svc, _, _ = _servicio(monkeypatch)
    _sin_certificado(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="todos"))
    svc._repo.nivel_vigente_for_update.return_value = (1, 1)
    llamada = (
        svc.emitir_challenge(SOL_ID, USER, "Bearer x")
        if camino == "challenge"
        else svc.firmar(SOL_ID, USER, None, "Bearer x")
    )
    exc = await _error(llamada)  # `todos` + sin certificado: se detiene en el gate
    assert _codigo(exc) == "firma_xades_requerida"
    svc._repo.nivel_vigente_for_update.assert_awaited_once()
    svc._repo.actualizar_nivel_seguridad.assert_not_awaited()


async def test_el_nivel_que_sube_se_corrige_una_vez_aun_si_el_acto_se_deniega(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La corrección es la misma que haría el evento `reclassified` (idempotente) y se
    hace ANTES del gate: quien lo deniega después no la deshace."""
    svc, _, _ = _servicio(monkeypatch)
    _sin_certificado(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="clasificados"))
    svc._repo.nivel_vigente_for_update.return_value = (2, 1)

    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))

    assert _codigo(exc) == "firma_xades_requerida"
    svc._repo.actualizar_nivel_seguridad.assert_awaited_once_with("radicado", ["2024-DEMO-E-000001"], 2, solo_subir=True)


# --------------------------------------------------------------------------- 404 neutros


def _caso_ausente(mp: pytest.MonkeyPatch):
    svc = _servicio(mp)[0]
    svc._repo.get_solicitud.return_value = None
    return svc


def _caso_bajo_clearance(mp: pytest.MonkeyPatch):
    svc = _servicio(mp, sol=_sol(nivel_seguridad=2))[0]
    mp.setattr(mod, "resolve_caller_clearance", AsyncMock(return_value=1))
    return svc


def _caso_nivel_vivo(mp: pytest.MonkeyPatch):
    svc = _servicio(mp)[0]
    mp.setattr(mod, "resolve_caller_clearance", AsyncMock(return_value=1))
    svc._repo.nivel_vigente_for_update.return_value = (2, 1)
    return svc


def _caso_desaparecida_bajo_bloqueo(mp: pytest.MonkeyPatch):
    svc = _servicio(mp)[0]
    svc._repo.nivel_vigente_for_update.return_value = None
    return svc


CASOS_404 = {
    "ausente": _caso_ausente,
    "bajo_clearance": _caso_bajo_clearance,
    "nivel_vivo": _caso_nivel_vivo,
    "desaparecida_bajo_bloqueo": _caso_desaparecida_bajo_bloqueo,
}


@pytest.mark.parametrize("caso", sorted(CASOS_404))
async def test_todos_los_404_neutros_son_indistinguibles_en_sign_y_en_challenge(
    monkeypatch: pytest.MonkeyPatch, caso: str
) -> None:
    """«No existe», «no la alcanzas» y «cambió bajo bloqueo» deben ser el MISMO cuerpo,
    no solo el mismo código: un `detail` distinto es un oráculo de existencia (el de la
    solicitud ausente en `/sign` decía «…indicada» y el resto no). Se compara contra el
    cuerpo de referencia de `solicitud_not_found`, en los dos caminos."""
    referencia = {
        "error": "solicitud_not_found",
        "detail": "No existe la solicitud de firma",
        "status": 404,
    }
    svc = CASOS_404[caso](monkeypatch)
    en_challenge = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    svc = CASOS_404[caso](monkeypatch)
    en_sign = await _error(svc.firmar(SOL_ID, USER, None, "Bearer x"))

    assert en_challenge.status_code == en_sign.status_code == 404
    assert en_challenge.detail == referencia, caso
    assert en_sign.detail == referencia, caso
