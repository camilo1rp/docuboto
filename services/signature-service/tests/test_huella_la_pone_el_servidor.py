# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-04-09 — la huella que se firma no la elige el cliente.

Para ``radicado`` y ``expediente`` el ``payload_sha256`` del body se IGNORA: antes
quien abría la cadena podía declarar un hash arbitrario y la firma valía sobre ese
hash, no sobre el documento. Y donde la cadena no fija huella, el firmante tampoco
elige los bytes en el momento de firmar (``payload_xml``): el desafío 2FA se había
atado al digest del identificador y la firma cubría otra cosa.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import (
    SOL_ID,
    _clear,
    _exec_sig,
    _make_conn,
    _override,
    _sol_row,
    _turno_row,
)
from tests.test_cadena_2fa import _patch_audit
from tests.test_cadena_personal_signing import _patch_signing_key_status

HUELLA_DEL_CLIENTE = "a" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["radicado", "expediente"])
async def test_cadena_radicado_ignora_huella_del_cliente(tipo: str) -> None:
    """La fila guardada lleva NULL (se firma el identificador), nunca 'a'*64."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row()
    conn.fetchval.side_effect = [True, 1, None, None, None]
    conn.fetchrow.side_effect = [{}, sol]
    conn.fetch.return_value = [turno]
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": tipo,
                    "objeto_id": "2024-DEMO-E-000001",
                    "payload_sha256": HUELLA_DEL_CLIENTE,
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 201, resp.text
    finally:
        _clear()

    insert = next(
        c for c in conn.fetchrow.await_args_list if "INSERT INTO firma_solicitud" in c.args[0]
    )
    # (objeto_tipo, objeto_id, titulo, payload_sha256, creado_por, nivel_seguridad)
    assert insert.args[4] is None
    assert HUELLA_DEL_CLIENTE not in insert.args[1:]


@pytest.mark.asyncio
async def test_firmar_sin_huella_rechaza_payload_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cadena sin huella fijada + ``payload_xml`` del firmante -> 422, y no se
    firma ni se toca el factor."""
    _patch_audit(monkeypatch)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    conn = _make_conn()
    sol = _sol_row()  # payload_sha256 None (test_cadena._sol_row)
    turno = _turno_row(firmante_id=USER)
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno]  # solo la fase previa al lock
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"payload_xml": "<otro>lo que el firmante quiera</otro>"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer x"},
            )
    finally:
        _clear()
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "payload_xml_no_admitido"
    assert conn.fetchrow.await_count == 3  # nunca llegó al lock ni al INSERT


@pytest.mark.asyncio
async def test_firmar_con_huella_de_la_cadena_ignora_el_payload_xml_del_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: con la huella fijada por la cadena, lo firmado es esa huella y el
    ``payload_xml`` del body no cambia los bytes (no hay 422: ya no decide nada)."""
    import hashlib

    from app.core.config import settings
    from app.services import signer

    audit = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    capturados: list[bytes] = []
    real = signer.firmar

    def _espia(payload, *a, **kw):
        capturados.append(payload)
        return real(payload, *a, **kw)

    monkeypatch.setattr("app.services.cadena_service.signer.firmar", _espia)

    conn = _make_conn()
    sol = {**_sol_row(), "payload_sha256": "c" * 64}
    turno = _turno_row(firmante_id=USER)
    from tests.test_cadena_totp import _FIRMA_INS

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol, {}, turno, turno, _FIRMA_INS, None, {**sol, "estado": "firmado"}, {},
    ]
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"payload_xml": "<otro/>"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer x"},
            )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    assert capturados == [("c" * 64).encode()]
    assert hashlib.sha256(b"<otro/>").hexdigest() not in str(audit.await_args_list)
