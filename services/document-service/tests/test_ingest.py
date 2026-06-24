# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de ingesta de correo (E19, F2)."""

from email.message import EmailMessage as PyEmail
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.imap_client import EmailMessage, parse_email
from app.main import app
from app.routers.ingest import get_imap_client
from app.services.document_service import DocumentService
from app.services.email_ingest import EmailIngestor
from tests.conftest import TENANT_HEADERS


def _raw_email() -> bytes:
    msg = PyEmail()
    msg["From"] = "Juan Pérez <juan@ciudadano.co>"
    msg["Subject"] = "Solicitud de información"
    msg.set_content("Cuerpo del correo con la petición.")
    msg.add_attachment(
        b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="anexo.pdf"
    )
    return msg.as_bytes()


def test_parse_email() -> None:
    em = parse_email("42", _raw_email())
    assert em.uid == "42"
    assert em.from_addr == "juan@ciudadano.co"
    assert em.from_name == "Juan Pérez"
    assert em.subject == "Solicitud de información"
    assert "petición" in em.body
    assert em.attachments == ["anexo.pdf"]


@pytest.mark.asyncio
async def test_ingestor_maps_to_entrada() -> None:
    fake_service = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(tracking_number="2024-DEMO-E-000007"))
    )
    ingestor = EmailIngestor(fake_service)  # type: ignore[arg-type]
    em = EmailMessage(
        uid="1",
        from_addr="a@b.co",
        from_name="Ana",
        subject="Hola",
        body="texto",
        attachments=["x.pdf"],
    )
    tn = await ingestor.ingest(em)
    assert tn == "2024-DEMO-E-000007"
    payload = fake_service.create.call_args[0][0]
    assert payload.doc_type == "E"
    assert payload.sender_name == "Ana"
    assert payload.metadata["source"] == "email"
    assert payload.metadata["sender_email"] == "a@b.co"
    assert payload.metadata["attachments"] == ["x.pdf"]


@pytest.mark.asyncio
async def test_poll_not_configured(client_with_conn) -> None:
    client, _ = client_with_conn
    app.dependency_overrides[get_imap_client] = lambda: None
    try:
        resp = await client.post("/api/v1/ingest/email/poll", headers=TENANT_HEADERS)
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "email_not_configured"
    finally:
        app.dependency_overrides.pop(get_imap_client, None)


@pytest.mark.asyncio
async def test_poll_ingests_messages(client_with_conn) -> None:
    client, _ = client_with_conn
    messages = [
        EmailMessage(uid="1", from_addr="a@b.co", from_name="Ana", subject="Uno", body="x"),
        EmailMessage(uid="2", from_addr="c@d.co", from_name="Carlos", subject="Dos", body="y"),
    ]
    fake_imap = SimpleNamespace(
        fetch_unseen=AsyncMock(return_value=messages),
        mark_seen=AsyncMock(),
    )
    app.dependency_overrides[get_imap_client] = lambda: fake_imap
    fake_create = AsyncMock(
        side_effect=[
            SimpleNamespace(tracking_number="2024-DEMO-E-000001"),
            SimpleNamespace(tracking_number="2024-DEMO-E-000002"),
        ]
    )
    try:
        with patch.object(DocumentService, "create", fake_create):
            resp = await client.post("/api/v1/ingest/email/poll", headers=TENANT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] == 2
        assert body["tracking_numbers"] == ["2024-DEMO-E-000001", "2024-DEMO-E-000002"]
        assert fake_imap.mark_seen.await_count == 2
    finally:
        app.dependency_overrides.pop(get_imap_client, None)
