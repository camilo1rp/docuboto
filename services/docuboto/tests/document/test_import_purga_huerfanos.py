# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Contrato saliente y cableado de la purga de binarios huérfanos (AREA-09-14).

Las pruebas con Postgres real (`tests/integration/test_import_huerfanos_integration.py`)
cubren la cola y los asientos; aquí lo que solo se ve desde fuera: qué viaja a storage
(la petición lleva el token interno, y el tenant) y que la purga se dispara también
cuando un ítem se omite por carrera tras CONFIRMAR la transacción.
"""

import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from storage import api as storage_api
from tests.document.test_import import _pkg, _svc


@pytest.mark.asyncio
async def test_un_item_omitido_por_carrera_dispara_la_purga_tras_confirmar() -> None:
    """Mutación: quitar la llamada posterior a la transacción → esos binarios solo se
    asientan y quedan en el almacén hasta que el barrido pase."""
    svc = _svc()
    svc._repo.insert_radicado.side_effect = asyncpg.UniqueViolationError("dup")
    purga = AsyncMock()
    svc._huerfanos.registrar = AsyncMock()
    svc._huerfanos.purgar = purga
    pkg = _pkg([{"tracking": "2026-OTRA-E-000001", "anexos": [("a.pdf", b"PDF")]}])
    with patch(
        "document.services.import_service.storage_api.upload_file",
        new=AsyncMock(return_value=storage_api.UploadedFile(file_id=uuid.uuid4(), sha256="x")),
    ), patch("document.services.import_service.audit.append", new=AsyncMock(return_value="h")):
        rep = await svc.import_package(zip_bytes=pkg, max_clearance=3, actor=None)
    assert rep["importados"] == 0
    svc._huerfanos.registrar.assert_awaited_once()
    purga.assert_awaited_once()
    assert purga.await_args.kwargs["import_uuid"] == uuid.UUID(rep["import_uuid"])
