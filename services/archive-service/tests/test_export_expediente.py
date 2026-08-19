# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Exportación de expediente a ZIP (E02/export, archive-service) — nivel servicio."""

import base64
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.archive import Expediente, ExpedienteRadicado
from app.services.archive_service import ArchiveService

NOW = datetime.now(tz=UTC)


def _mock_conn() -> AsyncMock:
    from unittest.mock import MagicMock

    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _radicado(*, tracking: str, estado: str = "vinculado") -> ExpedienteRadicado:
    return ExpedienteRadicado(
        expediente_id=uuid.uuid4(),
        radicado_id=uuid.uuid4(),
        tracking_number=tracking,
        added_at=NOW,
        estado=estado,
    )


def _expediente(radicados: list[ExpedienteRadicado], status: str = "closed") -> Expediente:
    return Expediente(
        id=uuid.uuid4(),
        code="EXP-2026-0001",
        name="exp",
        description=None,
        trd_serie_id=uuid.uuid4(),
        status=status,
        opened_by=None,
        opened_at=NOW,
        closed_at=NOW,
        updated_at=NOW,
        radicados=radicados,
    )


def _service_with(exp) -> ArchiveService:
    svc = ArchiveService(_mock_conn(), "demo")
    svc._repo.caller_clearance = AsyncMock(return_value=3)
    svc._repo.get_expediente_by_id = AsyncMock(return_value=exp)
    return svc


@pytest.mark.asyncio
async def test_export_no_read_up_404(monkeypatch) -> None:
    """Expediente sobre-clearance (get_expediente_by_id → None) → 404."""
    svc = _service_with(None)
    with pytest.raises(HTTPException) as ei:
        await svc.export_expediente_zip(uuid.uuid4(), user_id=uuid.uuid4(), canal="api")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_export_manifest_shows_excluido_no_bytes(monkeypatch) -> None:
    vinc = _radicado(tracking="2026-DEMO-E-000001", estado="vinculado")
    excl = _radicado(tracking="2026-DEMO-E-000002", estado="excluido")
    excl.excluido_causal = "documento duplicado"
    svc = _service_with(_expediente([vinc, excl]))

    file_id = uuid.uuid4()
    # Ambos radicados VISIBLES (clearance-aware): el vinculado con su anexo, el
    # excluido presente con lista vacía (sin bytes, pero representado).
    fetch_mock = AsyncMock(
        return_value={
            str(vinc.radicado_id): [{"file_id": str(file_id), "filename": "oficio.pdf"}],
            str(excl.radicado_id): [],
        }
    )
    monkeypatch.setattr("app.clients.document_client.fetch_anexos_internal", fetch_mock)
    monkeypatch.setattr(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(
            return_value={
                str(vinc.radicado_id): {"subject": "Asunto del oficio", "doc_type": "E"},
                str(excl.radicado_id): {"subject": "Asunto excluido", "doc_type": "S"},
            }
        ),
    )

    captured = {}

    async def _fake_export(*, tenant_slug, items, extra_files):
        captured["items"] = items
        captured["extra_files"] = extra_files
        return b"ZIPBYTES"

    monkeypatch.setattr("app.clients.storage_client.export_zip_internal", _fake_export)
    monkeypatch.setattr(
        "app.services.index_service.IndexService.get_xml",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="no indice")),
    )

    zip_bytes, filename = await svc.export_expediente_zip(
        uuid.uuid4(), user_id=uuid.uuid4(), canal="api"
    )
    assert zip_bytes == b"ZIPBYTES"
    assert filename == "expediente-EXP-2026-0001.zip"

    # Se pide el inventario de TODOS los radicados con el clearance del llamante.
    fetch_mock.assert_awaited_once()
    assert set(fetch_mock.await_args.kwargs["radicado_ids"]) == {vinc.radicado_id, excl.radicado_id}
    assert fetch_mock.await_args.kwargs["max_clearance"] == 3
    # Solo el vinculado aporta bytes; el excluido no.
    assert len(captured["items"]) == 1
    assert captured["items"][0]["arcname"] == "documentos/2026-DEMO-E-000001/0-oficio.pdf"

    arcnames = {e["arcname"] for e in captured["extra_files"]}
    assert "manifiesto.csv" in arcnames
    assert "LEEME.txt" in arcnames
    assert "indice-electronico.xml" not in arcnames
    manifest = base64.b64decode(
        next(e["contenido_b64"] for e in captured["extra_files"] if e["arcname"] == "manifiesto.csv")
    ).decode()
    # Ambos radicados figuran; el excluido con su causal, sin bytes; con asunto.
    assert "2026-DEMO-E-000001" in manifest
    assert "2026-DEMO-E-000002" in manifest
    assert "excluido" in manifest and "documento duplicado" in manifest
    assert "Asunto del oficio" in manifest  # metadata enriquecida (asunto/tipo)


@pytest.mark.asyncio
async def test_export_omits_over_clearance_radicado(monkeypatch) -> None:
    """Un radicado sobre-clearance (ausente del inventario visible) se omite del
    ZIP Y del manifiesto — no se filtra ni su existencia (no-read-up)."""
    visible = _radicado(tracking="2026-DEMO-E-000001", estado="vinculado")
    clasificado = _radicado(tracking="2026-DEMO-E-000009", estado="vinculado")
    svc = _service_with(_expediente([visible, clasificado]))

    # document-service solo devuelve el VISIBLE; el clasificado no aparece.
    fetch_mock = AsyncMock(return_value={str(visible.radicado_id): []})
    monkeypatch.setattr("app.clients.document_client.fetch_anexos_internal", fetch_mock)
    monkeypatch.setattr(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(return_value={str(visible.radicado_id): {"subject": "x", "doc_type": "E"}}),
    )
    captured = {}

    async def _fake_export(*, tenant_slug, items, extra_files):
        captured["extra_files"] = extra_files
        captured["items"] = items
        return b"ZIP"

    monkeypatch.setattr("app.clients.storage_client.export_zip_internal", _fake_export)
    monkeypatch.setattr(
        "app.services.index_service.IndexService.get_xml",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="no")),
    )

    await svc.export_expediente_zip(uuid.uuid4(), user_id=uuid.uuid4(), canal="api")
    manifest = base64.b64decode(
        next(e["contenido_b64"] for e in captured["extra_files"] if e["arcname"] == "manifiesto.csv")
    ).decode()
    assert "2026-DEMO-E-000001" in manifest
    assert "2026-DEMO-E-000009" not in manifest  # sobre-clearance: ni se enumera
    assert captured["items"] == []


@pytest.mark.asyncio
async def test_export_includes_indice_when_present(monkeypatch) -> None:
    vinc = _radicado(tracking="2026-DEMO-E-000001")
    svc = _service_with(_expediente([vinc]))
    monkeypatch.setattr(
        "app.clients.document_client.fetch_anexos_internal", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(return_value={}),
    )
    captured = {}

    async def _fake_export(*, tenant_slug, items, extra_files):
        captured["extra_files"] = extra_files
        return b"ZIP"

    monkeypatch.setattr("app.clients.storage_client.export_zip_internal", _fake_export)
    monkeypatch.setattr(
        "app.services.index_service.IndexService.get_xml",
        AsyncMock(return_value="<indice/>"),
    )

    await svc.export_expediente_zip(uuid.uuid4(), user_id=uuid.uuid4(), canal="api")
    arcnames = {e["arcname"] for e in captured["extra_files"]}
    assert "indice-electronico.xml" in arcnames
    xml = base64.b64decode(
        next(e["contenido_b64"] for e in captured["extra_files"] if e["arcname"] == "indice-electronico.xml")
    ).decode()
    assert xml == "<indice/>"


@pytest.mark.asyncio
async def test_leeme_no_afirma_una_firma_que_no_existe(monkeypatch) -> None:
    """El LEEME decía «Indice electronico FIRMADO incluido: si» leyendo una
    variable que solo significa «el XML se pudo incluir».

    Un expediente abierto se exporta con su índice SIN firmar —el propio código
    lo dice: «un expediente sin índice firmado (aún abierto) se exporta igual»—
    y el paquete de entrega afirmaba lo contrario. Quien recibe esta copia lee
    esa línea como garantía de autenticidad, así que la afirmación falsa es un
    defecto probatorio, no de redacción. Ahora son dos hechos separados y cada
    uno dice la verdad.
    """
    vinc = _radicado(tracking="2026-DEMO-E-000001", estado="vinculado")
    svc = _service_with(_expediente([vinc], status="open"))
    monkeypatch.setattr(
        "app.clients.document_client.fetch_anexos_internal",
        AsyncMock(return_value={str(vinc.radicado_id): []}),
    )
    monkeypatch.setattr(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(return_value={str(vinc.radicado_id): {"subject": "Asunto", "doc_type": "E"}}),
    )
    captured = {}

    async def _fake_export(*, tenant_slug, items, extra_files):
        captured["extra_files"] = extra_files
        return b"ZIPBYTES"

    monkeypatch.setattr("app.clients.storage_client.export_zip_internal", _fake_export)
    monkeypatch.setattr(
        "app.services.index_service.IndexService.get_xml",
        AsyncMock(return_value="<indice/>"),
    )
    monkeypatch.setattr(
        "app.services.index_service.IndexService.get_indice",
        AsyncMock(return_value=SimpleNamespace(estado="pendiente_firma")),
    )

    await svc.export_expediente_zip(uuid.uuid4(), user_id=uuid.uuid4(), canal="api")

    leeme = base64.b64decode(
        next(e["contenido_b64"] for e in captured["extra_files"] if e["arcname"] == "LEEME.txt")
    ).decode()
    assert "Indice electronico incluido: si" in leeme
    assert "Indice electronico firmado: no (pendiente de firma)" in leeme
    # La frase vieja no puede reaparecer: es la que afirmaba la firma.
    assert "Indice electronico firmado incluido: si" not in leeme
    # El estado del expediente se lee en la lengua de trabajo.
    assert "Estado del expediente al exportar: abierto" in leeme


@pytest.mark.asyncio
async def test_leeme_afirma_la_firma_cuando_el_indice_SI_esta_firmado(monkeypatch) -> None:
    """El gemelo del caso anterior: si el índice está firmado, el LEEME lo dice.

    Sin este test, un arreglo perezoso —escribir «no» siempre— pasaría en verde.
    """
    vinc = _radicado(tracking="2026-DEMO-E-000001", estado="vinculado")
    svc = _service_with(_expediente([vinc], status="closed"))
    monkeypatch.setattr(
        "app.clients.document_client.fetch_anexos_internal",
        AsyncMock(return_value={str(vinc.radicado_id): []}),
    )
    monkeypatch.setattr(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(return_value={str(vinc.radicado_id): {"subject": "Asunto", "doc_type": "E"}}),
    )
    captured = {}

    async def _fake_export(*, tenant_slug, items, extra_files):
        captured["extra_files"] = extra_files
        return b"ZIPBYTES"

    monkeypatch.setattr("app.clients.storage_client.export_zip_internal", _fake_export)
    monkeypatch.setattr(
        "app.services.index_service.IndexService.get_xml",
        AsyncMock(return_value="<indice/>"),
    )
    monkeypatch.setattr(
        "app.services.index_service.IndexService.get_indice",
        AsyncMock(return_value=SimpleNamespace(estado="firmado")),
    )

    await svc.export_expediente_zip(uuid.uuid4(), user_id=uuid.uuid4(), canal="api")

    leeme = base64.b64decode(
        next(e["contenido_b64"] for e in captured["extra_files"] if e["arcname"] == "LEEME.txt")
    ).decode()
    assert "Indice electronico firmado: si" in leeme
    assert "Estado del expediente al exportar: cerrado" in leeme
