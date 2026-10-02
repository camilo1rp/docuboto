# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La firma declarativa ya no confía en el cliente (AREA-05-03-S).

`POST /documents/{id}/signatures` registraba una «firma» con la huella que elegía
el cliente, a nivel de lectura y sin asiento. Endurecimiento mínimo (la variante
de retirar el endpoint con 410 queda a decisión del usuario):

* exige `PERM_FIRMA` nivel >= 2;
* la huella la resuelve el servidor desde storage, y la del cuerpo se ignora;
* el asiento `document.signature_created` va en la misma transacción.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.clients import storage_client
from tests.conftest import TENANT_HEADERS

_USER = str(uuid.uuid4())
_HUELLA_SERVIDOR = "c" * 64
_HUELLA_CLIENTE = "d" * 64


def _fila(content_hash: str) -> dict:
    return {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "anexo_id": uuid.uuid4(),
        "signer_id": uuid.UUID(_USER),
        "signer_name": "Ana",
        "content_hash": content_hash,
        "reason": None,
        "signed_at": datetime.now(tz=UTC),
    }


def _storage_devuelve(monkeypatch, huella: str | None) -> None:
    async def _f(*, tenant_slug, file_id):
        return huella

    monkeypatch.setattr(storage_client, "fetch_file_sha256", _f)


@pytest.mark.asyncio
async def test_exige_nivel_2_de_perm_firma(client_with_conn, monkeypatch) -> None:
    client, mock_conn = client_with_conn
    _storage_devuelve(monkeypatch, _HUELLA_SERVIDOR)
    mock_conn.fetchval.return_value = False  # el nivel del llamante no alcanza
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"anexo_id": str(uuid.uuid4())},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    consulta = mock_conn.fetchval.await_args_list[0]
    assert consulta.args[2] == "PERM_FIRMA"
    assert consulta.args[3] == 2, "el gate debe pedir min_crud=2, no el 1 por defecto"


@pytest.mark.asyncio
async def test_la_huella_del_cuerpo_se_ignora(client_with_conn, monkeypatch) -> None:
    client, mock_conn = client_with_conn
    _storage_devuelve(monkeypatch, _HUELLA_SERVIDOR)
    mock_conn.fetchval.side_effect = [True, uuid.uuid4(), None]  # + hash previo del audit
    mock_conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 1},
        _fila(_HUELLA_SERVIDOR),
    ]
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"anexo_id": str(uuid.uuid4()), "content_hash": _HUELLA_CLIENTE},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    insert = mock_conn.fetchrow.await_args_list[1]
    assert "INSERT INTO signatures" in insert.args[0]
    assert _HUELLA_SERVIDOR in insert.args
    assert _HUELLA_CLIENTE not in insert.args, "la huella del cliente llegó al INSERT"


@pytest.mark.asyncio
async def test_sin_anexo_no_hay_de_donde_sacar_la_huella(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {"is_root": False, "max_level": 1}
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"content_hash": _HUELLA_CLIENTE},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "anexo_requerido"


@pytest.mark.asyncio
async def test_storage_caido_no_firma(client_with_conn, monkeypatch) -> None:
    async def _cae(*, tenant_slug, file_id):
        raise storage_client.StorageContentError("caído")

    monkeypatch.setattr(storage_client, "fetch_file_sha256", _cae)
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, uuid.uuid4()]
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}]
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"anexo_id": str(uuid.uuid4())},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "huella_no_verificable"


@pytest.mark.asyncio
async def test_la_firma_deja_su_asiento_en_la_misma_transaccion(
    client_with_conn, monkeypatch
) -> None:
    client, mock_conn = client_with_conn
    _storage_devuelve(monkeypatch, _HUELLA_SERVIDOR)
    mock_conn.fetchval.side_effect = [True, uuid.uuid4(), None]  # + hash previo del audit
    mock_conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 1},
        _fila(_HUELLA_SERVIDOR),
    ]
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"anexo_id": str(uuid.uuid4())},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert mock_conn.transaction.called, "registro y asiento deben ir en una transacción"
    asientos = [
        c
        for c in mock_conn.execute.await_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(asientos) == 1
    assert "document.signature_created" in asientos[0].args
