# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Procedencia de un fichero — `POST /storage/internal/files/procedencia` (B2, ADR-031).

document-service la usa al registrar el acuse de un envío: la constancia de
entrega solo vale si la subió quien la registra y nadie la usa ya como otra cosa.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS

_F = uuid.uuid4()
_AUTOR = uuid.uuid4()
_URL = "/api/v1/storage/internal/files/procedencia"


def _resolver(conn, *, fila, tablas_presentes=True, usada_en=()):
    """Responde por el SQL, no por el turno."""

    async def _fetchrow(sql, *a, **k):
        return fila if "FROM files WHERE id" in sql else None

    async def _fetchval(sql, *a, **k):
        if "to_regclass" in sql:
            return "t" if tablas_presentes else None
        for tabla in usada_en:
            if f"FROM {tabla} " in sql:
                return 1
        return None

    conn.fetchrow.side_effect = _fetchrow
    conn.fetchval.side_effect = _fetchval


@pytest.mark.asyncio
async def test_devuelve_autor_huella_y_sin_referencias(client_with_conn) -> None:
    client, conn, _ = client_with_conn
    _resolver(conn, fila={"sha256": "a" * 64, "uploaded_by": _AUTOR})

    r = await client.post(_URL, json={"file_id": str(_F)}, headers=INTERNAL_HEADERS)

    assert r.status_code == 200, r.text
    assert r.json() == {"sha256": "a" * 64, "uploaded_by": str(_AUTOR), "referencias": []}


@pytest.mark.asyncio
async def test_un_anexo_aparece_como_referenciado(client_with_conn) -> None:
    client, conn, _ = client_with_conn
    _resolver(conn, fila={"sha256": "a" * 64, "uploaded_by": _AUTOR}, usada_en=("anexos",))

    r = await client.post(_URL, json={"file_id": str(_F)}, headers=INTERNAL_HEADERS)

    assert r.json()["referencias"] == ["anexos.file_id"]


@pytest.mark.asyncio
async def test_tabla_ausente_cuenta_como_referenciado(client_with_conn) -> None:
    """«No sé» no es «nadie»: fail-closed."""
    client, conn, _ = client_with_conn
    _resolver(conn, fila={"sha256": "a" * 64, "uploaded_by": _AUTOR}, tablas_presentes=False)

    r = await client.post(_URL, json={"file_id": str(_F)}, headers=INTERNAL_HEADERS)

    assert r.json()["referencias"], "una tabla ausente no puede leerse como 'nadie lo usa'"


@pytest.mark.asyncio
async def test_autor_nulo_se_devuelve_nulo(client_with_conn) -> None:
    client, conn, _ = client_with_conn
    _resolver(conn, fila={"sha256": "a" * 64, "uploaded_by": None})

    r = await client.post(_URL, json={"file_id": str(_F)}, headers=INTERNAL_HEADERS)

    assert r.json()["uploaded_by"] is None


@pytest.mark.asyncio
async def test_fichero_inexistente_es_404(client_with_conn) -> None:
    client, conn, _ = client_with_conn
    _resolver(conn, fila=None)

    r = await client.post(_URL, json={"file_id": str(_F)}, headers=INTERNAL_HEADERS)

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_sin_token_interno_no_se_alcanza(client_with_conn) -> None:
    client, conn, _ = client_with_conn

    r = await client.post(_URL, json={"file_id": str(_F)}, headers=TENANT_HEADERS)

    assert r.status_code in (401, 403)
    conn.fetchrow.assert_not_called()
