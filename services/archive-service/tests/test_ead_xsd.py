# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-07-16: el EAD que difunde el OAI de expedientes VALIDA contra el XSD de EAD 2002.

Hasta hoy ningún test ni job de CI validaba el XML contra el esquema: la jerarquía
multinivel (`fonds` > `series` > `subseries` > `file`) estaba afirmada por el código
y por pruebas que miraban atributos sueltos, no la forma del documento.

Los niveles salen de la POSICIÓN en la cadena `trd_series.parent_id` porque ese es el
modelo (ADR-015): la raíz es la serie y toda descendiente es subserie. No existe
columna de nivel que leer, así que un CCD de tres niveles emite
`series > subseries > subseries`, anidamiento que EAD 2002 admite. Si algún día el CCD
declara sección/subsección (decisión de dominio, ADR), el discriminador pasa a ser esa
columna; este test seguirá exigiendo que el resultado valide.

El XSD (`tests/fixtures/ead.xsd`, más `xlink.xsd` del que depende) es el oficial de la
Library of Congress, vendorizado para validar sin red.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from lxml import etree

from app.main import app

EAD = "{urn:isbn:1-931666-22-9}"
FIXTURES = Path(__file__).parent / "fixtures"


class _FakePool:
    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _CM()


@pytest.fixture(autouse=True)
def _habilitar_interop(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "public_interop_enabled", True)


@pytest.fixture(scope="module")
def ead_schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(FIXTURES / "ead.xsd")))


@pytest_asyncio.fixture
async def oai_ctx() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = 0
    app.state.db_pool = _FakePool(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    if hasattr(app.state, "db_pool"):
        delattr(app.state, "db_pool")


def _expediente(serie_id: uuid.UUID | None) -> dict:
    return {
        "id": uuid.uuid4(),
        "code": "2026-DEMO-001",
        "name": "Convenio marco",
        "description": "Expediente de seguimiento contractual",
        "trd_serie_id": serie_id,
        "status": "open",
        "opened_at": datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
        "closed_at": None,
        "radicado_count": 3,
    }


def _cadena(n: int) -> list[dict]:
    """`n` nodos del CCD, de la raíz (serie) hacia abajo."""
    ids = [uuid.uuid4() for _ in range(n)]
    return [
        {
            "id": ids[i],
            "code": f"NIV-{i}",
            "name": f"Nivel {i}",
            "parent_id": ids[i - 1] if i else None,
        }
        for i in range(n)
    ]


async def _ead(client: AsyncClient, conn: AsyncMock, *, niveles: int) -> bytes:
    cadena = _cadena(niveles)
    conn.fetchrow.return_value = _expediente(cadena[-1]["id"] if cadena else None)
    conn.fetch.return_value = cadena
    resp = await client.get(
        "/api/v1/public/archive/oai/demo"
        "?verb=GetRecord&identifier=oai:demo:expediente:2026-DEMO-001&metadataPrefix=ead"
    )
    assert resp.status_code == 200
    return resp.content


def _niveles(xml: bytes) -> list[str]:
    """Los `level` desde `archdesc` hasta el `c[file]`, bajando por el anidamiento."""
    root = ET.fromstring(xml)
    arch = root.find(f".//{EAD}archdesc")
    assert arch is not None
    out = [arch.get("level") or ""]
    nodo = arch.find(f"{EAD}dsc")
    while nodo is not None:
        c = nodo.find(f"{EAD}c")
        if c is None:
            break
        out.append(c.get("level") or "")
        nodo = c
    return out


def _ead_de_oai(xml: bytes) -> etree._Element:
    """Extrae el `<ead>` del sobre OAI para validarlo por sí solo."""
    root = etree.fromstring(xml)
    ead = root.find(f".//{EAD}ead")
    assert ead is not None
    return ead


@pytest.mark.asyncio
@pytest.mark.parametrize("niveles", [0, 1, 2, 3, 5])
async def test_el_ead_difundido_valida_contra_el_xsd_oficial(
    oai_ctx, ead_schema: etree.XMLSchema, niveles: int
) -> None:
    """Sin serie (fallback), con una, dos, tres y cinco niveles de CCD."""
    client, conn = oai_ctx
    ead = _ead_de_oai(await _ead(client, conn, niveles=niveles))
    assert ead_schema.validate(ead), str(ead_schema.error_log)


@pytest.mark.asyncio
async def test_un_ccd_de_tres_niveles_anida_serie_subserie_subserie(oai_ctx) -> None:
    """Los niveles salen de la cadena `parent_id`, no se inventan fuera de ella.

    Mutación: volver a `level = "series"` para todos (o a otro valor fijo) rompe la
    segunda posición; sacar `idx == 0` del cálculo rompe la primera.
    """
    client, conn = oai_ctx
    xml = await _ead(client, conn, niveles=3)
    assert _niveles(xml) == ["fonds", "series", "subseries", "subseries", "file"]


@pytest.mark.asyncio
async def test_sin_serie_no_se_inventa_un_ccd(oai_ctx) -> None:
    client, conn = oai_ctx
    assert _niveles(await _ead(client, conn, niveles=0)) == ["file"]


def test_el_xsd_vendorizado_rechaza_un_nivel_inexistente(ead_schema: etree.XMLSchema) -> None:
    """Control positivo: el validador no aprueba cualquier cosa (un test que no
    puede fallar no valida nada)."""
    malo = etree.fromstring(
        b'<ead xmlns="urn:isbn:1-931666-22-9"><eadheader><eadid>x</eadid><filedesc><titlestmt>'
        b"<titleproper>t</titleproper></titlestmt></filedesc></eadheader>"
        b'<archdesc level="nivel-que-no-existe"><did><unittitle>t</unittitle></did></archdesc></ead>'
    )
    assert not ead_schema.validate(malo)
