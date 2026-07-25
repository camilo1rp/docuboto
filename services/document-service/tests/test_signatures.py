# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de firma electrónica (E06, ADR-016)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

_HASH = "a" * 64
_USER = str(uuid.uuid4())


def _sig_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "anexo_id": None,
        "signer_id": uuid.UUID(_USER),
        "signer_name": "Ana Firmante",
        "content_hash": _HASH,
        "reason": "Aprobación",
        "signed_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_sign_document(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _sig_row()
    headers = {**TENANT_HEADERS, "X-User-Id": _USER, "X-Username": "Ana Firmante"}

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"content_hash": _HASH, "reason": "Aprobación"},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["signer_name"] == "Ana Firmante"
    assert body["content_hash"] == _HASH


@pytest.mark.asyncio
async def test_sign_rejects_bad_hash(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"content_hash": "not-a-sha256"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_verify_valid(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_sig_row()]

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/signatures/verify?content_hash={_HASH}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert len(body["signatures"]) == 1


@pytest.mark.asyncio
async def test_verify_invalid_when_no_match(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []  # contenido alterado → ninguna firma coincide

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/signatures/verify?content_hash={'b' * 64}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


# ---------------------------------------------------------------------------
# Autorización: PERM_FIRMA + no-read-up (RF-SEG-08)
#
# Las tres rutas carecían de CUALQUIER gate: sin permiso y sin clearance.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("post", "/api/v1/documents/{rid}/signatures"),
        ("get", "/api/v1/documents/{rid}/signatures"),
        ("get", "/api/v1/documents/{rid}/signatures/verify?content_hash=" + _HASH),
    ],
)
@pytest.mark.asyncio
async def test_firmas_exigen_perm_firma(client_with_conn, metodo, ruta) -> None:
    """Sin PERM_FIRMA → 403 en las tres rutas (mismo permiso que signature-service)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → fail-closed

    url = ruta.format(rid=uuid.uuid4())
    headers = {**TENANT_HEADERS, "X-User-Id": _USER}
    resp = await (
        client.post(url, json={"content_hash": _HASH}, headers=headers)
        if metodo == "post"
        else client.get(url, headers=headers)
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_listar_firmas_de_radicado_sobre_clearance_404(client_with_conn) -> None:
    """Radicado invisible → 404 neutro, nunca `[]`.

    La distinción importa: `[]` significa «visible y sin firmas». Si un
    sub-clearance recibiera `[]` en vez de 404, el par 404/`[]` sería un oráculo
    de existencia. Aquí el sub-clearance recibe SIEMPRE 404.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, False]  # PERM_FIRMA ok; radicado_visible → False
    mock_conn.fetchrow.return_value = {"is_root": False, "max_level": 1}

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


@pytest.mark.asyncio
async def test_verify_de_radicado_sobre_clearance_404(client_with_conn) -> None:
    """`verify` gateado: el `valid` true/false era un oráculo de CONTENIDO.

    Permitía confirmar por fuerza bruta el hash de un documento reservado sin
    llegar a leerlo.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, False]
    mock_conn.fetchrow.return_value = {"is_root": False, "max_level": 1}

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/signatures/verify?content_hash={_HASH}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_firmar_radicado_sobre_clearance_404(client_with_conn) -> None:
    """El INSERT condicional no encuentra radicado visible → None → 404 neutro."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # PERM_FIRMA concedido
    # clearance del llamante; luego el INSERT ... WHERE EXISTS no devuelve fila.
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}, None]

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"content_hash": _HASH, "reason": "intento"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"

    # El gate viaja DENTRO del INSERT (atomicidad), no como consulta previa.
    sql = mock_conn.fetchrow.call_args[0][0]
    assert "INSERT INTO signatures" in sql
    assert "nivel_seguridad <= $7" in sql
