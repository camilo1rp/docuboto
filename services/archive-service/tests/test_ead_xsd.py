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
multinivel (`fonds` > `subfonds`* > `series` > `subseries` > `file`) estaba afirmada por
el código y por pruebas que miraban atributos sueltos, no la forma del documento.

Los niveles de serie salen de la POSICIÓN en la cadena `trd_series.parent_id` (ADR-015):
la raíz es la serie y su única descendiente posible, la subserie (tope de dos niveles
impuesto por la migración 069). Entre el fondo y la serie, C-4/C8 (ADR-040) inserta un
`c[subfonds]` por cada nodo del organigrama de la dependencia productora, solo si la
serie declara EXACTAMENTE una; este test exige que el resultado valide en cada caso.

El XSD (`tests/fixtures/ead.xsd`, más `xlink.xsd` del que depende) es el oficial de la
Library of Congress, vendorizado para validar sin red.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from lxml import etree
from orpycamcp_common.tenant import AtencionPublica

from app.main import app
from tests.oai_fakes import fetch_oai, productora


@pytest.fixture(autouse=True)
def _entidad_operativa():
    """ADR-035: aquí se prueba el EAD, no el estado de la entidad (`test_estado_entidad.py`)."""
    with patch(
        "app.routers.oai.atencion_publica", new=AsyncMock(return_value=AtencionPublica.COMPLETA)
    ):
        yield

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


async def _ead(
    client: AsyncClient,
    conn: AsyncMock,
    *,
    niveles: int,
    productoras: tuple = (),
    ruta_viva: tuple = (),
) -> bytes:
    cadena = _cadena(niveles)
    conn.fetchrow.return_value = _expediente(cadena[-1]["id"] if cadena else None)
    conn.fetch.side_effect = fetch_oai(cadena, productoras=productoras, ruta_viva=ruta_viva)
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
@pytest.mark.parametrize("niveles", [0, 1, 2])
async def test_el_ead_difundido_valida_contra_el_xsd_oficial(
    oai_ctx, ead_schema: etree.XMLSchema, niveles: int
) -> None:
    """Sin serie (fallback), con serie y con serie > subserie (el tope del motor)."""
    client, conn = oai_ctx
    ead = _ead_de_oai(await _ead(client, conn, niveles=niveles))
    assert ead_schema.validate(ead), str(ead_schema.error_log)


@pytest.mark.asyncio
async def test_un_ccd_de_dos_niveles_anida_serie_subserie(oai_ctx) -> None:
    """Los niveles salen de la cadena `parent_id`, no se inventan fuera de ella.

    Mutación: volver a `level = "series"` para todos (o a otro valor fijo) rompe la
    segunda posición; sacar `idx == 0` del cálculo rompe la primera.
    """
    client, conn = oai_ctx
    xml = await _ead(client, conn, niveles=2)
    assert _niveles(xml) == ["fonds", "series", "subseries", "file"]


_RUTA_2 = [
    {"codigo": 100, "sigla": "DG", "nombre": "Dirección General"},
    {"codigo": 110, "sigla": "SA", "nombre": "Subdirección Administrativa"},
]


@pytest.mark.asyncio
async def test_una_productora_con_ruta_convalidada_emite_dos_subfonds_y_valida(
    oai_ctx, ead_schema: etree.XMLSchema
) -> None:
    """C-4/C8: sección y subsección del organigrama = `c[subfonds]` entre el fondo y la
    serie, con la ruta que fijó la convalidación (lo aprobado)."""
    client, conn = oai_ctx
    xml = await _ead(client, conn, niveles=1, productoras=(productora(110, _RUTA_2),))
    assert _niveles(xml) == ["fonds", "subfonds", "subfonds", "series", "file"]
    ead = _ead_de_oai(xml)
    assert ead_schema.validate(ead), str(ead_schema.error_log)
    titulos = [t.text for t in ead.iter(f"{EAD}unittitle")]
    assert titulos[1:3] == ["Dirección General (DG)", "Subdirección Administrativa (SA)"]


@pytest.mark.asyncio
async def test_sin_ruta_convalidada_se_usa_la_ruta_viva(oai_ctx, ead_schema: etree.XMLSchema) -> None:
    client, conn = oai_ctx
    viva = [
        {"codigo": 110, "sigla": "SA", "nombre": "Subdirección Administrativa", "es_ciclo": False, "prof": 1},
        {"codigo": 100, "sigla": "DG", "nombre": "Dirección General", "es_ciclo": False, "prof": 2},
    ]
    # `ORDER BY prof DESC` en SQL: la productora es prof=1 y la raíz la mayor; sale raíz primero.
    viva = sorted(viva, key=lambda r: -r["prof"])
    xml = await _ead(client, conn, niveles=1, productoras=(productora(110),), ruta_viva=tuple(viva))
    assert _niveles(xml) == ["fonds", "subfonds", "subfonds", "series", "file"]
    assert ead_schema.validate(_ead_de_oai(xml))


@pytest.mark.asyncio
async def test_con_dos_productoras_no_se_inventa_el_subfondo(oai_ctx, ead_schema: etree.XMLSchema) -> None:
    """El expediente no lleva columna de dependencia: elegir una de dos sería inventar.
    Fallback honesto, igual que el de «sin serie». Control positivo del caso anterior."""
    client, conn = oai_ctx
    xml = await _ead(
        client,
        conn,
        niveles=1,
        productoras=(productora(110, _RUTA_2), productora(120, _RUTA_2[:1])),
    )
    assert _niveles(xml) == ["fonds", "series", "file"]
    assert ead_schema.validate(_ead_de_oai(xml))


@pytest.mark.asyncio
async def test_un_ciclo_en_la_ruta_viva_degrada_sin_subfonds(oai_ctx) -> None:
    """El endpoint es público: un organigrama corrupto no lo tumba, solo omite el nivel."""
    client, conn = oai_ctx
    ciclo = ({"codigo": 100, "sigla": "A", "nombre": "A", "es_ciclo": True, "prof": 1},)
    xml = await _ead(client, conn, niveles=1, productoras=(productora(100),), ruta_viva=ciclo)
    assert _niveles(xml) == ["fonds", "series", "file"]


@pytest.mark.asyncio
async def test_el_fondo_se_titula_con_el_nombre_de_la_entidad(oai_ctx) -> None:
    client, conn = oai_ctx
    conn.fetchval.return_value = "Fondo de Desarrollo de Cundinamarca"
    xml = await _ead(client, conn, niveles=1)
    ead = _ead_de_oai(xml)
    fonds_did = ead.find(f"{EAD}archdesc/{EAD}did")
    assert fonds_did.find(f"{EAD}unittitle").text == "Fondo de Desarrollo de Cundinamarca"


@pytest.mark.asyncio
async def test_sin_nombre_de_entidad_el_fondo_conserva_su_titulo_por_omision(oai_ctx) -> None:
    client, conn = oai_ctx
    conn.fetchval.return_value = ""
    xml = await _ead(client, conn, niveles=1)
    fonds_did = _ead_de_oai(xml).find(f"{EAD}archdesc/{EAD}did")
    assert fonds_did.find(f"{EAD}unittitle").text == "Fondo documental — demo"


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


@pytest.mark.asyncio
async def test_el_subfondo_se_identifica_por_el_codigo_no_por_la_sigla(oai_ctx) -> None:
    """A5: la sigla se puede renombrar; el código es inmutable (ADR-032). La sigla va en el título."""
    client, conn = oai_ctx
    xml = await _ead(client, conn, niveles=1, productoras=(productora(110, _RUTA_2),))
    ead = _ead_de_oai(xml)
    subfondos = [c for c in ead.iter(f"{EAD}c") if c.get("level") == "subfonds"]
    ids = [c.find(f"{EAD}did/{EAD}unitid").text for c in subfondos]
    titulos = [c.find(f"{EAD}did/{EAD}unittitle").text for c in subfondos]
    assert ids == ["100", "110"]
    assert titulos == ["Dirección General (DG)", "Subdirección Administrativa (SA)"]


@pytest.mark.asyncio
async def test_una_ruta_viva_cortada_por_el_tope_de_profundidad_no_se_emite_a_medias(oai_ctx) -> None:
    client, conn = oai_ctx
    cortada = (
        {"codigo": 110, "sigla": "SA", "nombre": "Sub", "es_ciclo": False, "prof": 2, "tiene_padre": True},
        {"codigo": 111, "sigla": "TH", "nombre": "Talento", "es_ciclo": False, "prof": 1, "tiene_padre": True},
    )
    # `ORDER BY prof DESC`: la más alta primero; la cadena termina con un padre pendiente
    xml = await _ead(client, conn, niveles=1, productoras=(productora(111),), ruta_viva=cortada)
    assert _niveles(xml) == ["fonds", "series", "file"]
