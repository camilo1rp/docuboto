# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`archive.api.index_fingerprint` (AREA-04-09 Media-2).

signature fija la huella del ÍNDICE ELECTRÓNICO vigente del expediente al abrir
una cadena de firma sobre un `expediente` y la relee antes de cada firma (antes
firmaba el identificador). El SQL real se prueba en
`tests/archive/integration/test_internal_indice_huella.py`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import asyncpg
import pytest

from archive import api as archive_api
from tests.archive.conftest import TENANT_SLUG

REF = "EXP-2024-0001"
EXP = {"id": uuid.uuid4(), "code": REF}
INDICE = {"version": 3, "estado": "firmado", "xml_sha256": "AB" * 32, "num_documentos": 7}


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    doble = AsyncMock()

    @asynccontextmanager
    async def _acquire(slug: str):
        assert slug == TENANT_SLUG
        yield doble

    monkeypatch.setattr(archive_api, "acquire_tenant", _acquire)
    return doble


async def _huella() -> dict:
    return await archive_api.index_fingerprint(tenant_slug=TENANT_SLUG, ref=REF)


@pytest.mark.asyncio
async def test_devuelve_la_huella_canonica_del_indice_vigente(conn) -> None:
    conn.fetchrow.side_effect = [EXP, INDICE]
    body = await _huella()
    assert body["huella_sha256"] == INDICE["xml_sha256"].lower()
    assert body["version"] == 3 and body["code"] == REF
    # La referencia viaja como parámetro; el índice es la ÚLTIMA versión.
    sqls = [c.args[0] for c in conn.fetchrow.await_args_list]
    assert all(REF not in s for s in sqls)
    assert "ORDER BY version DESC LIMIT 1" in sqls[1]


@pytest.mark.asyncio
async def test_expediente_inexistente(conn) -> None:
    conn.fetchrow.side_effect = [None]
    with pytest.raises(archive_api.ExpedienteNotFoundError):
        await _huella()


@pytest.mark.asyncio
async def test_expediente_sin_indice_no_tiene_contenido_que_firmar(conn) -> None:
    conn.fetchrow.side_effect = [EXP, None]
    with pytest.raises(archive_api.IndexNotGeneratedError) as exc:
        await _huella()
    assert exc.value.code == "indice_no_disponible"


@pytest.mark.asyncio
async def test_un_fallo_de_la_bd_no_es_sin_cambios(conn) -> None:
    conn.fetchrow.side_effect = asyncpg.PostgresError("caída")
    with pytest.raises(archive_api.IndexUnavailableError):
        await _huella()
