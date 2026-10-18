# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 §8: el AIP embebe el XML VIGENTE del índice (el del ArchiveTimeStamp renovado), no el original.

Mutaciones: quitar la consulta a `signature_client.artefacto_vigente` del cableado (cae
`test_el_aip_embebe_el_xml_renovado`); usar el vigente aunque la consulta no responda (cae
`test_sin_respuesta_se_conserva_el_original`)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.storage_client import AipPackagingOutcome
from tests.test_aip_wiring_service_indice_only import (
    _expediente,
    _indice_by_id_row,
    _mock_conn,
    _service,
    _wire_happy_path_prereqs,
)

pytestmark = pytest.mark.asyncio


async def _empaquetar(vigente: str | None, original: str, firma_id: uuid.UUID | None):
    service = _service(_mock_conn())
    exp = _expediente(radicados=[])
    _wire_happy_path_prereqs(
        service, exp=exp, indice_row=_indice_by_id_row(minio_key_firmado=original, firma_id=firma_id)
    )
    service._resolve_aip_retention = AsyncMock(return_value=(5, __import__("datetime").date(2026, 1, 1)))
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    with (
        patch("app.clients.signature_client.artefacto_vigente", new=AsyncMock(return_value=vigente)) as consulta,
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        await service.intentar_empaquetar(exp.id)
    return storage.await_args.kwargs["indice_file_id"], consulta


async def test_el_aip_embebe_el_xml_renovado() -> None:
    original, renovado = uuid.uuid4(), uuid.uuid4()
    firma = uuid.uuid4()
    file_id, consulta = await _empaquetar(f"{renovado}/ind-lta1.xml", f"{original}/ind.xml", firma)
    assert file_id == renovado
    assert consulta.await_args.kwargs["firma_id"] == str(firma)


async def test_sin_respuesta_se_conserva_el_original() -> None:
    original = uuid.uuid4()
    file_id, _ = await _empaquetar(None, f"{original}/ind.xml", uuid.uuid4())
    assert file_id == original


async def test_sin_firma_id_no_se_consulta() -> None:
    original = uuid.uuid4()
    file_id, consulta = await _empaquetar(f"{uuid.uuid4()}/x.xml", f"{original}/ind.xml", None)
    assert file_id == original
    consulta.assert_not_awaited()
