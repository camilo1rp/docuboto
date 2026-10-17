# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B9-2 (SEG-B4): `firmar` y `/challenge` deciden con el nivel RELEÍDO bajo bloqueo.

La copia `firma_solicitud.nivel_seguridad` (1) va atrasada frente al objeto vivo (2).
Todo gate que depende del nivel usa el registro devuelto por
``CadenaFirmaService._sol_con_nivel_vigente`` y no el leído sin bloqueo. Aquí corre la
relectura REAL (marca ``nivel_vigente_real``: por defecto se neutraliza); la parte SQL
—``FOR UPDATE``, ``max``— la prueba ``tests/integration/test_nivel_vigente_bajo_bloqueo.py``.

Mutación común: ``_sol_con_nivel_vigente`` devolviendo ``dict(sol)`` sin reemplazar el
nivel => caen los cinco.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.repositories.cadena_repository import CadenaRepository
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import SOL_ID, _exec_sig, _make_conn, _turno_row
from tests.test_cadena_2fa import _clear, _override, _patch_audit, _patch_email, _sol_row
from tests.test_cadena_personal_signing import _patch_signing_key_status
from tests.test_firma_coherente_con_el_actor import AUTH, _estado_totp

pytestmark = [pytest.mark.nivel_vigente_real, pytest.mark.asyncio]

_ROOT = {"is_root": True, "max_level": None}
_PUBLICA = {"is_root": False, "max_level": 1}


@pytest.fixture
def repo_nivel(monkeypatch: pytest.MonkeyPatch):
    """Copia = 1 en la fila; nivel vigente del objeto = 2."""
    vigente = AsyncMock(return_value=(2, 1))
    corregir = AsyncMock(return_value=1)
    monkeypatch.setattr(CadenaRepository, "nivel_vigente_for_update", vigente)
    monkeypatch.setattr(CadenaRepository, "actualizar_nivel_seguridad", corregir)
    return vigente, corregir


async def _post(client, path: str):
    resp = await client.post(
        f"/api/v1/signature/requests/{SOL_ID}/{path}",
        json={},
        headers={**TENANT_HEADERS, **AUTH},
    )
    return resp.status_code, resp.json()


async def _llamar(path: str):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await _post(ac, path)


async def test_firmar_copia_1_objeto_2_exige_el_segundo_factor_y_no_firma(
    monkeypatch, repo_nivel
) -> None:
    """Sin `firma_2fa_required` ni factor: con el nivel vivo 2 responde lo que da el
    nivel 2 (409 2fa_requerido) y NO crea la fila `firma`. Corrige la copia."""
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado"})
    _estado_totp(monkeypatch, activo=False)
    vigente, corregir = repo_nivel
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=1), _ROOT, _turno_row(firmante_id=USER), _turno_row(firmante_id=USER),
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _llamar("sign")
    finally:
        _clear()
    assert status_code == 409, body
    assert body["detail"]["error"] == "2fa_requerido"
    vigente.assert_awaited_once()
    corregir.assert_awaited_once_with("radicado", ["2024-DEMO-E-000001"], 2, solo_subir=True)
    assert not [c for c in conn.fetchrow.call_args_list if "INSERT INTO firma " in str(c.args[0])]


async def test_firmar_con_clearance_menor_que_el_nivel_vivo_es_404_y_no_consume(
    monkeypatch, repo_nivel
) -> None:
    """La copia (1) deja pasar el no-read-up previo a un actor de clearance 1; el
    nivel vivo (2) lo corta dentro de la transacción: 404 neutro, copia sin tocar."""
    _patch_audit(monkeypatch)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado"})
    _, corregir = repo_nivel
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=1), _PUBLICA, _turno_row(firmante_id=USER),
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _llamar("sign")
    finally:
        _clear()
    assert status_code == 404, body
    assert body["detail"]["error"] == "solicitud_not_found"
    corregir.assert_not_awaited()
    assert not [c for c in conn.execute.call_args_list if "consumido" in str(c.args[0])]


async def test_firmar_el_gate_xades_clasificados_se_activa_por_el_nivel_vivo(
    monkeypatch, repo_nivel
) -> None:
    """Modo `clasificados` + sin certificado: con la copia (1) firmaba nativa; con el
    nivel vivo (2) es 422 firma_xades_requerida, y el asiento de la denegación lleva
    `nivel_seguridad: 2` (no la copia)."""
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=1), _ROOT, _turno_row(firmante_id=USER), _turno_row(firmante_id=USER),
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _llamar("sign")
    finally:
        _clear()
    assert status_code == 422, body
    assert body["detail"]["error"] == "firma_xades_requerida"
    denegadas = [
        c for c in audit_mock.await_args_list
        if c.kwargs.get("action") == "firma.xades_requerida_denegada"
    ]
    assert len(denegadas) == 1
    assert denegadas[0].kwargs["payload"]["nivel_seguridad"] == 2


async def test_challenge_con_totp_activo_y_nivel_vivo_2_es_409_sin_correo(
    monkeypatch, repo_nivel
) -> None:
    """El veto del correo (nivel >= 2 con TOTP activo) usa el nivel vivo."""
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    _estado_totp(monkeypatch, activo=True)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=1), _ROOT, _turno_row(firmante_id=USER),
        _sol_row(nivel_seguridad=1), _turno_row(firmante_id=USER), None,
    ]
    _override(conn)
    try:
        status_code, body = await _llamar("challenge")
    finally:
        _clear()
    assert status_code == 409, body
    assert body["detail"]["error"] == "totp_requerido"
    mock_send.assert_not_awaited()


async def test_challenge_con_clearance_menor_que_el_nivel_vivo_es_404(
    monkeypatch, repo_nivel
) -> None:
    """Mismo corte que en `sign`: el 404 neutro (y el mismo código) en `/challenge`."""
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=1), _PUBLICA, _turno_row(firmante_id=USER),
        _sol_row(nivel_seguridad=1), _turno_row(firmante_id=USER),
    ]
    _override(conn)
    try:
        status_code, body = await _llamar("challenge")
    finally:
        _clear()
    assert status_code == 404, body
    assert body["detail"]["error"] == "solicitud_not_found"
    mock_send.assert_not_awaited()
