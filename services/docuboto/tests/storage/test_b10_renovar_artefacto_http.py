# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 (SEG #10): un anterior que no es artefacto de firma o un nuevo que no es XML no se registra
(lo prueba contra Postgres `tests/integration/test_b10_renovar_artefacto_sql.py`). Aquí: el
rechazo del servicio sube a signature como `StorageError`, nunca como renovación registrada."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from storage import api as storage_api
from storage.services import preservation_worm_service as svc
from tests.storage.conftest import TENANT_SLUG


@pytest.mark.asyncio
async def test_artefacto_no_es_de_firma_no_se_registra(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc.PreservationWormService, "renovar_artefacto",
        AsyncMock(side_effect=svc.ArtefactoNoEsDeFirmaError("el anterior es un AIP")),
    )
    with pytest.raises(storage_api.StorageError, match="el anterior es un AIP"):
        await storage_api.renew_artifact(
            tenant_slug=TENANT_SLUG,
            file_id_anterior=uuid.uuid4(),
            file_id_nuevo=uuid.uuid4(),
            sha256_nuevo="a" * 64,
            firma_id=uuid.uuid4(),
            ordinal=1,
        )
