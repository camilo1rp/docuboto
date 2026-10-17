# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`/challenge` exige las mismas precondiciones que `/sign` (B7, F1 y F2).

Hasta B7 el desafío se emitía —y el OTP se ENVIABA por correo— en casos que
`firmar` rechazaba después: con credencial personal activa (que pide TOTP y nada
más), con el documento ya modificado, sobre un turno de visto bueno o sobre un
radicado anulado. Era correo desperdiciado y, con el documento cambiado, una
invitación a firmar algo que ya no era lo aprobado. Un colapso que solo existe en
uno de dos caminos no es una defensa: es una incoherencia.

Los dos caminos comparten `_precondiciones_de_turno`, y el guardián de paridad
(`test_paridad_challenge_sign`) exige que, para el MISMO escenario, respondan el
mismo `error`: quitar un gate de uno solo de los caminos lo hace caer.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.http_clients import HuellaNoVerificableError, SignerIdentity
from app.services import cadena_service as mod
from app.services.cadena_service import CadenaFirmaService
from tests.conftest import HUELLA_PRODUCTOR, USER

SOL_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTRO = uuid.UUID("22222222-2222-2222-2222-222222222222")
HUELLA_OTRA = "b" * 64


def _sol(**kw) -> dict:
    base = {
        "id": SOL_ID,
        "objeto_tipo": "radicado",
        "objeto_id": "2024-DEMO-E-000001",
        "payload_sha256": HUELLA_PRODUCTOR,
        "estado": "pendiente",
        "nivel_seguridad": 1,
        "radicado_status": "active",
    }
    base.update(kw)
    return base


def _turno(firmante=USER, rol: str = "firma") -> dict:
    return {"id": uuid.uuid4(), "firmante_id": firmante, "rol": rol, "estado": "pendiente"}


def _servicio(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sol: dict | None = None,
    es_firmante: bool = True,
    turno: dict | None = None,
) -> tuple[CadenaFirmaService, AsyncMock, AsyncMock]:
    """Servicio con repo doblado; devuelve (servicio, envío de correo, identidad)."""
    conn = MagicMock()
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    svc = CadenaFirmaService(conn, "demo")
    sol = sol or _sol()
    turno = turno or _turno()
    repo = AsyncMock()
    repo.get_solicitud.return_value = sol
    repo.get_solicitud_for_update.return_value = sol
    repo.es_firmante.return_value = es_firmante
    repo.turno_activo.return_value = turno
    repo.turno_activo_for_update.return_value = turno
    repo.actualizar_nivel_seguridad.return_value = 1
    svc._repo = repo
    monkeypatch.setattr(mod, "resolve_caller_clearance", AsyncMock(return_value=3))
    monkeypatch.setattr(mod._audit, "append", AsyncMock(return_value="h"))
    enviar = AsyncMock(return_value=None)
    identidad = AsyncMock(return_value=SignerIdentity(email="a@b.co", user_id=str(USER)))
    monkeypatch.setattr(mod, "send_otp_email", enviar)
    monkeypatch.setattr(mod, "get_signer_identity", identidad)
    return svc, enviar, identidad


def _credencial_activa(monkeypatch: pytest.MonkeyPatch, user_id: uuid.UUID = USER) -> AsyncMock:
    m = AsyncMock(return_value={"estado": "activo", "cert_pem": "x", "user_id": str(user_id)})
    monkeypatch.setattr(mod, "get_signing_key_status_remote", m)
    return m


async def _error(coro) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        await coro
    return exc.value


def _codigo(exc: HTTPException) -> str:
    return exc.detail["error"]


def _no_se_crea_nada(svc: CadenaFirmaService, enviar: AsyncMock, identidad: AsyncMock) -> None:
    enviar.assert_not_awaited()
    identidad.assert_not_awaited()
    assert not svc._conn.fetchrow.called
    mod._audit.append.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# F1 — credencial personal activa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_con_credencial_personal_activa_responde_totp_requerido_sin_correo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, enviar, identidad = _servicio(monkeypatch)
    _credencial_activa(monkeypatch)
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 409 and _codigo(exc) == "totp_requerido"
    _no_se_crea_nada(svc, enviar, identidad)


@pytest.mark.asyncio
async def test_challenge_credencial_personal_de_otra_identidad_incoherente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, enviar, identidad = _servicio(monkeypatch)
    _credencial_activa(monkeypatch, user_id=OTRO)
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 403 and _codigo(exc) == "identidad_incoherente"
    enviar.assert_not_awaited()
    identidad.assert_not_awaited()
    [llamada] = mod._audit.append.await_args_list  # type: ignore[attr-defined]
    assert llamada.kwargs["action"] == "firma.identidad_incoherente"
    assert llamada.kwargs["payload"]["via"] == "signing_key_status"


@pytest.mark.asyncio
async def test_challenge_sin_certificado_bajo_gate_xades_responde_422_sin_correo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, enviar, identidad = _servicio(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="todos"))
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 422 and _codigo(exc) == "firma_xades_requerida"
    # Sin el asiento `firma.xades_requerida_denegada`: es del acto de firmar.
    _no_se_crea_nada(svc, enviar, identidad)


@pytest.mark.asyncio
async def test_challenge_canal_de_credencial_caido_bajo_gate_responde_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.http_clients import SigningKeyStatusUnavailableError

    svc, enviar, identidad = _servicio(monkeypatch)
    monkeypatch.setattr(mod, "modo_xades_de_la_entidad", AsyncMock(return_value="todos"))
    monkeypatch.setattr(
        mod, "get_signing_key_status_remote", AsyncMock(side_effect=SigningKeyStatusUnavailableError("x"))
    )
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 503 and _codigo(exc) == "canal_firma_personal_no_disponible"
    _no_se_crea_nada(svc, enviar, identidad)


# ---------------------------------------------------------------------------
# F2 — el documento tiene que seguir siendo el aprobado
# ---------------------------------------------------------------------------


def _huella_cambiada(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    m = AsyncMock(return_value={"huella_sha256": HUELLA_OTRA})
    monkeypatch.setattr(mod, "get_huella_radicado", m)
    return m


@pytest.mark.asyncio
async def test_challenge_documento_modificado_no_emite(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, enviar, identidad = _servicio(monkeypatch)
    _huella_cambiada(monkeypatch)
    estado = _credencial_activa(monkeypatch)
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 409 and _codigo(exc) == "documento_modificado"
    estado.assert_not_awaited()  # ni siquiera se consulta la credencial
    _no_se_crea_nada(svc, enviar, identidad)


@pytest.mark.asyncio
async def test_challenge_ajeno_no_consulta_huella(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quien no es parte no se le confirma que el texto cambió."""
    svc, enviar, identidad = _servicio(monkeypatch, es_firmante=False)
    huella = _huella_cambiada(monkeypatch)
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 403 and _codigo(exc) == "no_es_firmante"
    huella.assert_not_awaited()
    _no_se_crea_nada(svc, enviar, identidad)


@pytest.mark.asyncio
async def test_challenge_verificacion_no_disponible_503_sin_correo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, enviar, identidad = _servicio(monkeypatch)
    monkeypatch.setattr(
        mod, "get_huella_radicado", AsyncMock(side_effect=HuellaNoVerificableError("caído"))
    )
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 503 and _codigo(exc) == "verificacion_documento_no_disponible"
    _no_se_crea_nada(svc, enviar, identidad)


# ---------------------------------------------------------------------------
# Turno de visto bueno y radicado anulado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_turno_visto_bueno_409(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, enviar, identidad = _servicio(monkeypatch, turno=_turno(rol="visto_bueno"))
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 409 and _codigo(exc) == "turno_es_de_visto_bueno"
    _no_se_crea_nada(svc, enviar, identidad)


@pytest.mark.asyncio
async def test_challenge_radicado_anulado_409(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, enviar, identidad = _servicio(monkeypatch, sol=_sol(radicado_status="anulado"))
    exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
    assert exc.status_code == 409 and _codigo(exc) == "radicado_anulado"
    _no_se_crea_nada(svc, enviar, identidad)


# ---------------------------------------------------------------------------
# Guardián de paridad: mismo escenario, mismo `error` en /challenge y /sign
# ---------------------------------------------------------------------------


def _escenario_ajeno(mp: pytest.MonkeyPatch) -> CadenaFirmaService:
    return _servicio(mp, es_firmante=False)[0]


def _escenario_cerrada(mp: pytest.MonkeyPatch) -> CadenaFirmaService:
    return _servicio(mp, sol=_sol(estado="firmado"))[0]


def _escenario_vobo(mp: pytest.MonkeyPatch) -> CadenaFirmaService:
    return _servicio(mp, turno=_turno(rol="visto_bueno"))[0]


def _escenario_anulado(mp: pytest.MonkeyPatch) -> CadenaFirmaService:
    return _servicio(mp, sol=_sol(radicado_status="anulado"))[0]


def _escenario_modificado(mp: pytest.MonkeyPatch) -> CadenaFirmaService:
    _huella_cambiada(mp)
    return _servicio(mp)[0]


def _escenario_credencial(mp: pytest.MonkeyPatch) -> CadenaFirmaService:
    _credencial_activa(mp)
    return _servicio(mp)[0]


ESCENARIOS = {
    "no_es_firmante": _escenario_ajeno,
    "solicitud_no_pendiente": _escenario_cerrada,
    "turno_es_de_visto_bueno": _escenario_vobo,
    "radicado_anulado": _escenario_anulado,
    "documento_modificado": _escenario_modificado,
    "totp_requerido": _escenario_credencial,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("esperado", sorted(ESCENARIOS))
async def test_paridad_challenge_sign(esperado: str, monkeypatch: pytest.MonkeyPatch) -> None:
    construir = ESCENARIOS[esperado]

    svc = construir(monkeypatch)
    en_challenge = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))

    svc = construir(monkeypatch)
    en_sign = await _error(svc.firmar(SOL_ID, USER, None, "Bearer x"))

    assert _codigo(en_challenge) == esperado
    assert _codigo(en_sign) == esperado
    assert en_challenge.status_code == en_sign.status_code


# ---------------------------------------------------------------------------
# B9-2 — el nivel vivo (releído bajo bloqueo) manda en los dos caminos
# ---------------------------------------------------------------------------


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
async def test_paridad_nivel_vivo_mayor_que_la_copia(monkeypatch: pytest.MonkeyPatch) -> None:
    """Copia 1, objeto vivo 2, actor de clearance 1: los dos caminos responden el
    MISMO 404 neutro —y con el MISMO código `solicitud_not_found` (antes challenge
    decía `solicitud_no_encontrada`)—. Mutación: dejar de reemplazar el nivel en
    `_sol_con_nivel_vigente`, o poner la relectura en un solo camino => cae."""
    resultados = []
    for camino in ("challenge", "sign"):
        svc, enviar, identidad = _servicio(monkeypatch)
        monkeypatch.setattr(mod, "resolve_caller_clearance", AsyncMock(return_value=1))
        svc._repo.nivel_vigente_for_update.return_value = (2, 1)
        if camino == "challenge":
            exc = await _error(svc.emitir_challenge(SOL_ID, USER, "Bearer x"))
        else:
            exc = await _error(svc.firmar(SOL_ID, USER, None, "Bearer x"))
        enviar.assert_not_awaited()
        svc._repo.actualizar_nivel_seguridad.assert_not_awaited()
        resultados.append((exc.status_code, _codigo(exc)))
    assert resultados == [(404, "solicitud_not_found")] * 2
