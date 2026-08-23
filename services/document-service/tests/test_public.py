# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de consulta pública por código de verificación (E13, F2)."""

from datetime import UTC, datetime

import pytest

# Nota: NO se envían cabeceras de tenant — el endpoint es público y toma el
# tenant de la ruta.


@pytest.mark.asyncio
async def test_verify_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {
        "tracking_number": "2024-DEMO-E-000001",
        "doc_type": "E",
        "status": "registered",
        "registered_at": datetime.now(tz=UTC),
        "dest_dept": "Jurídica",
    }

    resp = await client.get("/api/v1/public/demo/verify/abc123code")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tracking_number"] == "2024-DEMO-E-000001"
    assert body["dest_dept"] == "Jurídica"
    # No se filtran datos sensibles (sin remitente ni contenido).
    assert "sender_name" not in body and "subject" not in body


@pytest.mark.asyncio
async def test_verify_no_alcanza_a_un_radicado_CLASIFICADO(client_with_conn) -> None:
    """Superficie ANÓNIMA: el llamante tiene, por definición, clearance 1.

    `verification_code` lo tiene **todo** radicado (migración 010, `DEFAULT
    gen_random_uuid()`), incluidos los internos y los clasificados que nunca
    tuvieron un ciudadano detrás. Sin el filtro, quien tuviera el código de un
    radicado RESERVADO obtenía sin autenticarse su número oficial, su tipo, su
    estado y su fecha — y el resto del sistema trata la EXISTENCIA de un
    reservado como protegida (el acceso directo devuelve 404, no 403,
    precisamente para no confirmarla).

    Se comprueba sobre el SQL enviado, no sobre el resultado del mock: con un
    mock, el «no encontrado» lo decide el mock y no el `WHERE` — el fallo de
    método que ya se pagó una vez en este proyecto.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    resp = await client.get("/api/v1/public/demo/verify/codigo-de-un-reservado")

    assert resp.status_code == 404
    sql = mock_conn.fetchrow.await_args.args[0]
    assert "nivel_seguridad = 1" in sql, (
        "la consulta pública no recorta por nivel de seguridad: con el código, "
        "un anónimo leería metadatos de un radicado clasificado"
    )


@pytest.mark.asyncio
async def test_verify_SIGUE_mostrando_un_radicado_anulado(client_with_conn) -> None:
    """Anulado no es lo mismo que oculto.

    El chokepoint de OAI/CMIS excluye los anulados —allí el documento se retira
    de la circulación pública—, pero aquí ocultarlo haría creer al ciudadano
    que su código es inválido, cuando lo que pasó es que su radicado se anuló:
    justo lo que tiene derecho a saber. Por eso esta consulta NO reutiliza
    `_PUBLIC`.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {
        "tracking_number": "2026-DEMO-E-000009",
        "doc_type": "E",
        "status": "anulado",
        "registered_at": datetime.now(tz=UTC),
        "dest_dept": None,
    }

    resp = await client.get("/api/v1/public/demo/verify/codigo")

    assert resp.status_code == 200
    assert resp.json()["status"] == "anulado"
    sql = mock_conn.fetchrow.await_args.args[0]
    assert "anulado" not in sql, "la consulta pública no debe excluir los anulados"
    assert "publicable" not in sql, (
        "exigir `publicable` rompería la consulta para toda PQRS ciudadana: "
        "una petición de un ciudadano no es un documento publicado de oficio"
    )


@pytest.mark.asyncio
async def test_verify_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    resp = await client.get("/api/v1/public/demo/verify/inexistente")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"


@pytest.mark.asyncio
async def test_verify_invalid_tenant_slug(client_with_conn) -> None:
    client, _ = client_with_conn
    # Slug con caracteres inválidos → 400 (anti-inyección en search_path).
    resp = await client.get("/api/v1/public/bad;slug/verify/abc")
    assert resp.status_code == 400


# ── PQRS / radicación ciudadana (público, Ley 1755/2015) ──────────────


@pytest.mark.asyncio
async def test_pqrs_radicacion_ciudadana(client_with_conn) -> None:
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.services.document_service import DocumentService

    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"  # verification_code
    fake_create = AsyncMock(
        return_value=SimpleNamespace(id=rid, tracking_number="2024-DEMO-E-000123")
    )
    with patch.object(DocumentService, "create", fake_create):
        resp = await client.post(
            "/api/v1/public/demo/pqrs",
            json={
                "tipo": "peticion",
                "nombre": "Ciudadano X",
                "asunto": "Solicitud de información",
                "descripcion": "Requiero copia de...",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tracking_number"] == "2024-DEMO-E-000123"
    assert len(body["verification_code"]) == 32
    # El radicado se creó como Entrada con canal PQRS.
    payload = fake_create.call_args[0][0]
    assert payload.doc_type == "E"
    assert payload.metadata["canal"] == "pqrs_ciudadano"


@pytest.mark.asyncio
async def test_pqrs_tenant_invalido_400(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.post(
        "/api/v1/public/Bad/pqrs",
        json={"nombre": "X", "asunto": "A", "descripcion": "D"},
    )
    assert resp.status_code == 400
