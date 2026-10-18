# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 (SEG #10): `renovar-artefacto-internal` rechaza con 422 `artefacto_no_es_de_firma` un anterior
que no es un artefacto de firma o un nuevo que no es XML (lo prueba contra Postgres
`tests/integration/test_b10_renovar_artefacto_sql.py`). Mutación: quitar la rama del mapeador de
errores -> el servicio lanza y la ruta responde 500."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.services import preservation_worm_service as svc
from tests.conftest import TENANT_HEADERS, TEST_INTERNAL_TOKEN


@pytest.mark.asyncio
async def test_artefacto_no_es_de_firma_es_422(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _conn, _ = client_with_conn
    monkeypatch.setattr(
        svc.PreservationWormService, "renovar_artefacto",
        AsyncMock(side_effect=svc.ArtefactoNoEsDeFirmaError("el anterior es un AIP")),
    )
    r = await client.post(
        "/api/v1/preservacion/renovar-artefacto-internal",
        json={
            "file_id_anterior": str(uuid.uuid4()), "file_id_nuevo": str(uuid.uuid4()),
            "sha256_nuevo": "a" * 64, "firma_id": str(uuid.uuid4()), "ordinal": 1,
        },
        headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "artefacto_no_es_de_firma"
