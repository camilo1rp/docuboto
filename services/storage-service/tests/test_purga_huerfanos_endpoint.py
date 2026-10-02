# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""La puerta al borrado de binarios huérfanos (AREA-09-14).

Sellada con `X-Internal-Token`, sin gateway, y con un resultado POR archivo para
que quien orquesta distinga lo que debe reintentar (`error`) de lo que es una
decisión correcta (`referenciado`, `protegido_worm`, `preexistente`).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services.storage_service import PurgaHuerfano
from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS

RUTA = "/api/v1/storage/internal/purgar-huerfanos"


def _cuerpo(file_ids, **extra):
    base = {
        "file_ids": [str(f) for f in file_ids],
        "referencia": str(uuid.uuid4()),
        "motivo": "importación revertida",
        "subido_desde": datetime.now(tz=UTC).isoformat(),
        "actor": str(uuid.uuid4()),
    }
    return base | extra


@pytest.mark.asyncio
async def test_sin_token_interno_no_se_borra_nada(client) -> None:
    with patch("app.services.storage_service.StorageService.purge_orphan", AsyncMock()) as p:
        resp = await client.post(RUTA, json=_cuerpo([uuid.uuid4()]), headers=TENANT_HEADERS)
    assert resp.status_code in (401, 403)
    p.assert_not_awaited()


@pytest.mark.asyncio
async def test_devuelve_un_resultado_por_archivo_y_no_repite_ids(client) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    vistos: list[uuid.UUID] = []

    async def _purgar(self, file_id, **kw):
        vistos.append(file_id)
        assert kw["referencia"] and kw["motivo"] and kw["subido_desde"].tzinfo is not None
        return PurgaHuerfano(file_id, "purgado" if file_id == a else "referenciado", "anexos.file_id")

    with patch("app.services.storage_service.StorageService.purge_orphan", _purgar):
        resp = await client.post(RUTA, json=_cuerpo([a, b, a]), headers=INTERNAL_HEADERS)

    assert resp.status_code == 200
    assert vistos == [a, b], "un id repetido se procesa una sola vez, en orden"
    assert [(r["file_id"], r["resultado"]) for r in resp.json()["resultados"]] == [
        (str(a), "purgado"),
        (str(b), "referenciado"),
    ]


@pytest.mark.asyncio
async def test_subido_desde_sin_zona_se_rechaza(client) -> None:
    """Sin zona la comparación con `uploaded_at` sería ambigua: 422, no un borrado."""
    resp = await client.post(
        RUTA,
        json=_cuerpo([uuid.uuid4()], subido_desde="2026-10-02T10:00:00"),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 422
