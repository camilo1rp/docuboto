# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El borrado de binarios huérfanos por `storage.api.purge_orphans` (AREA-09-14).

Un resultado POR archivo para que quien orquesta distinga lo que debe reintentar
(`error`) de lo que es una decisión correcta (`referenciado`, `protegido_worm`,
`preexistente`).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from storage import api as storage_api
from storage.services.storage_service import PurgaHuerfano
from tests.storage.conftest import TENANT_SLUG


def _kwargs(file_ids, **extra):
    base = {
        "tenant_slug": TENANT_SLUG,
        "file_ids": list(file_ids),
        "referencia": str(uuid.uuid4()),
        "motivo": "importación revertida",
        "subido_desde": datetime.now(tz=UTC),
        "actor": uuid.uuid4(),
    }
    return base | extra


@pytest.mark.asyncio
async def test_devuelve_un_resultado_por_archivo_y_no_repite_ids(client) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    vistos: list[uuid.UUID] = []

    async def _purgar(self, file_id, **kw):
        vistos.append(file_id)
        assert kw["referencia"] and kw["motivo"] and kw["subido_desde"].tzinfo is not None
        return PurgaHuerfano(file_id, "purgado" if file_id == a else "referenciado", "anexos.file_id")

    with patch("storage.services.storage_service.StorageService.purge_orphan", _purgar):
        resultados = await storage_api.purge_orphans(**_kwargs([a, b, a]))

    assert vistos == [a, b], "un id repetido se procesa una sola vez, en orden"
    assert [(r.file_id, r.resultado) for r in resultados] == [(a, "purgado"), (b, "referenciado")]


@pytest.mark.asyncio
async def test_subido_desde_sin_zona_se_rechaza(client) -> None:
    """Sin zona la comparación con `uploaded_at` sería ambigua: error, no un borrado."""
    with patch("storage.services.storage_service.StorageService.purge_orphan", AsyncMock()) as p:
        with pytest.raises(storage_api.StorageError):
            await storage_api.purge_orphans(
                **_kwargs([uuid.uuid4()], subido_desde=datetime(2026, 10, 2, 10, 0))
            )
    p.assert_not_awaited()
