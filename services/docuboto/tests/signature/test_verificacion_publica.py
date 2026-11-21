# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-054 §1-§2 — la firma se nombra por lo que prueba y se verifica en público por código.

- ``GET /api/v1/public/signature/{tenant}/{firma_id}`` responde sin sesión, con el
  mismo motor que ``POST /verify``, y sin devolver el XML ni el documento.
- Una constancia de firma simple (HMAC) no se reporta «válida» ante un tercero.
- Un identificador inexistente, uno inalcanzable y una entidad inválida dan el
  mismo 404.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest

from signature.core.constants import SIGNATURE_KIND_ELECTRONIC, SIGNATURE_KIND_SIMPLE
from signature.providers.base import SignerRef
from signature.routers.public import get_public_tenant_conn
from storage import api as storage_api
from tests.signature.app import app
from tests.signature.conftest import TENANT_HEADERS
from tests.signature.test_sign_xml_xades import (  # noqa: F401 — fixtures y helpers compartidos
    _generate_cert,
    _sign_indice,
    signature_env,
)

_SLUG = "demo"


def _sello(cert_pem: bytes, key_pem: bytes) -> SignerRef:
    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=None,
        tipo_firma="sello_sistema",
        nivel_conformidad="xades_b_no_acreditado",
        acreditado=False,
    )


@pytest.fixture
def publico(signature_env, monkeypatch):  # noqa: F811
    """El entorno de firma, con la conexión pública apuntando a la misma tabla falsa."""
    client, conn, table, uploads, install_seal, _ = signature_env
    install_seal(_sello(*_generate_cert("sello-publico")))

    async def _conn_publica() -> AsyncGenerator:
        yield conn

    app.dependency_overrides[get_public_tenant_conn] = _conn_publica

    async def _descargar(*, tenant_slug: str, object_key: str) -> bytes:
        for u in uploads:
            if u["object_key"] == object_key:
                return u["content"]
        raise storage_api.StorageError("no existe")

    monkeypatch.setattr(storage_api, "read_object", _descargar)

    # La huella del documento se busca en la cadena (`firma_cadena` ⨝ `firma_solicitud`).
    # `table.cadena[firma_id]` es la fila de la solicitud; sin entrada, la firma no es de cadena.
    table.cadena = {}
    real = table.fetchrow

    async def _con_cadena(query: str, *args):
        if "FROM firma_cadena" in query:
            return table.cadena.get(str(args[0]))
        return await real(query, *args)

    conn.fetchrow.side_effect = _con_cadena
    return client, conn, table, uploads


async def _firmar(client, conn) -> dict:
    resp = await _sign_indice(conn)
    assert resp.status_code == 201, resp.text
    # Tras firmar: `FirmaRepository.get` pregunta si existe la columna del nombre; la
    # tabla falsa no tiene catálogo de usuarios.
    conn.fetchval.side_effect = None
    conn.fetchval.return_value = False
    return resp.json()


async def _publica(client, firma_id: str, slug: str = _SLUG):
    # Sin cabeceras de sesión: la verificación pública no conoce a nadie.
    return await client.get(
        f"/api/v1/public/signature/{slug}/{firma_id}",
        headers={"X-User-Id": "", "X-Tenant-Slug": ""},
    )


@pytest.mark.asyncio
async def test_firma_xades_valida_sin_devolver_el_documento(publico) -> None:
    client, conn, table, uploads = publico
    firma = await _firmar(client, conn)
    assert firma["kind"] == SIGNATURE_KIND_ELECTRONIC

    resp = await _publica(client, firma["firma_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == SIGNATURE_KIND_ELECTRONIC
    assert body["valida"] is True
    assert body["firma_criptograficamente_valida"] is True
    assert body["acreditado"] is False  # se publica tal cual: válida no es cualificada
    assert body["nivel"]
    assert body["huella_documento"] == f"sha256:{firma['payload_sha256']}"
    assert body["constancia"] == f"/api/v1/signature/firmas/{firma['firma_id']}"
    # Content-free: ni el XML firmado ni el documento viajan en la respuesta.
    xml = uploads[0]["content"].decode()
    assert "<indice" not in resp.text and xml[:40] not in resp.text
    assert set(body) == {
        "firma_id", "kind", "valida", "firma_criptograficamente_valida", "revocacion_status",
        "motivo", "nivel", "firmante_nombre", "firmado_en", "sello_tiempo", "huella_documento",
        "acreditado", "constancia",
    }


@pytest.mark.asyncio
async def test_el_anonimo_consulta_con_clearance_publico(publico) -> None:
    """No-read-up en la superficie anónima: el gate del repositorio recibe clearance 1."""
    client, conn, table, uploads = publico
    firma = await _firmar(client, conn)
    vistos: list[tuple] = []
    real = conn.fetchrow.side_effect

    async def _espia(query: str, *args):
        if "WHERE f.id" in query:
            vistos.append(args)
        return await real(query, *args)

    conn.fetchrow.side_effect = _espia
    resp = await _publica(client, firma["firma_id"])
    assert resp.status_code == 200, resp.text
    assert vistos and vistos[0][1] == 1


@pytest.mark.asyncio
async def test_inexistente_es_404_neutro(publico) -> None:
    client, conn, table, uploads = publico
    resp = await _publica(client, str(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"error": "not_found", "detail": "No encontrado", "status": 404}


@pytest.mark.asyncio
async def test_slug_invalido_es_el_mismo_404(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.get(f"/api/v1/public/signature/NO%20VALE/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"error": "not_found", "detail": "No encontrado", "status": 404}


@pytest.mark.asyncio
async def test_constancia_simple_no_se_reporta_valida(publico) -> None:
    """Una firma HMAC no la puede verificar un tercero: ni True (se leería como firma
    electrónica) ni False (afirmaría una manipulación)."""
    client, conn, table, uploads = publico
    firma_id = uuid.uuid4()
    table.rows[str(firma_id)] = {
        "id": firma_id,
        "provider": "nativa",
        "firmante_id": uuid.uuid4(),
        "payload_sha256": "b" * 64,
        "sello_tiempo": datetime.now(UTC),
        "minio_key_firmado": None,
        "xades_level": None,
        "nivel_conformidad": None,
    }
    conn.fetchval.side_effect = None
    conn.fetchval.return_value = False
    table.cadena[str(firma_id)] = {"payload_sha256": "c" * 64}
    resp = await _publica(client, str(firma_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == SIGNATURE_KIND_SIMPLE
    assert body["valida"] is None
    assert body["motivo"] == "constancia_firma_simple_no_verificable_por_terceros"
    assert body["acreditado"] is False
    # La huella es la del DOCUMENTO (firma_solicitud), no la de los bytes del HMAC.
    assert body["huella_documento"] == "sha256:" + "c" * 64


@pytest.mark.asyncio
async def test_cadena_sin_huella_no_publica_una_que_no_es_la_del_documento(publico) -> None:
    client, conn, table, uploads = publico
    firma_id = uuid.uuid4()
    table.rows[str(firma_id)] = {
        "id": firma_id, "provider": "nativa", "firmante_id": None, "payload_sha256": "b" * 64,
        "sello_tiempo": datetime.now(UTC), "minio_key_firmado": None, "xades_level": None,
        "nivel_conformidad": None,
    }
    conn.fetchval.side_effect = None
    conn.fetchval.return_value = False
    table.cadena[str(firma_id)] = {"payload_sha256": None}
    resp = await _publica(client, str(firma_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["huella_documento"] is None


@pytest.mark.asyncio
async def test_storage_caido_no_es_firma_invalida(publico, monkeypatch) -> None:
    client, conn, table, uploads = publico
    firma = await _firmar(client, conn)

    async def _caido(**_kw) -> bytes:
        raise storage_api.StorageError("storage-service inalcanzable")

    monkeypatch.setattr(storage_api, "read_object", _caido)
    resp = await _publica(client, firma["firma_id"])
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "verificacion_no_disponible"


@pytest.mark.asyncio
async def test_verify_autenticado_dice_que_clase_de_firma_es(publico) -> None:
    """`POST /verify` y `GET /firmas/{id}` nombran la firma por lo que prueba (ADR-054 §1)."""
    client, conn, table, uploads = publico
    firma = await _firmar(client, conn)
    conn.fetchval.return_value = True
    v = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma["firma_id"], "payload_xml": uploads[0]["content"].decode()},
        headers=TENANT_HEADERS,
    )
    assert v.status_code == 200, v.text
    assert v.json()["kind"] == SIGNATURE_KIND_ELECTRONIC
    g = await client.get(f"/api/v1/signature/firmas/{firma['firma_id']}", headers=TENANT_HEADERS)
    assert g.json()["kind"] == SIGNATURE_KIND_ELECTRONIC


def test_el_turno_firmado_dice_que_clase_de_firma_dejo() -> None:
    from signature.services.cadena_service import _build_response

    sol = {
        "id": uuid.uuid4(), "objeto_tipo": "borrador", "objeto_id": "b-1", "titulo": None,
        "estado": "pendiente", "nivel_seguridad": 1, "created_at": datetime.now(UTC),
    }
    base = {"firmante_id": uuid.uuid4(), "rol": "firma", "comentario": None, "decidido_at": None}
    turnos = [
        {**base, "orden": 1, "estado": "firmado", "firma_id": uuid.uuid4(), "provider": "nativa"},
        {**base, "orden": 2, "estado": "firmado", "firma_id": uuid.uuid4(),
         "provider": "xades_personal"},
        {**base, "orden": 3, "estado": "pendiente", "firma_id": None, "provider": None},
    ]
    items = _build_response(sol, turnos).items
    assert [t.kind for t in items] == [SIGNATURE_KIND_SIMPLE, SIGNATURE_KIND_ELECTRONIC, None]
