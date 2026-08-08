# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests OAI-PMH (E11, RF-INT-02). El endpoint usa `app.state.db_pool` directo
(público por-path, como el webhook postal). El recorte de seguridad (solo público)
se ejerce en SQL y se valida contra Postgres real en un smoke aparte; aquí se cubre
el protocolo (verbos, errores, Dublin Core, resumptionToken indistinguible)."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

OAI = "{http://www.openarchives.org/OAI/2.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
EAD = "{urn:isbn:1-931666-22-9}"


def _rad(tn: str = "2026-DEMO-E-000001", subject: str = "Acta pública", doc_type: str = "E") -> dict:
    return {
        "tracking_number": tn,
        "doc_type": doc_type,
        "subject": subject,
        "origin_dept": "Secretaría General",
        "registered_at": datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
        "tenant_code": "DEMO",
    }


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


def _url(**q: str) -> str:
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return f"/api/v1/public/oai/demo?{qs}"


@pytest.mark.asyncio
async def test_identify(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchval.return_value = datetime(2026, 1, 1, tzinfo=UTC)  # earliest
    resp = await client.get(_url(verb="Identify"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(resp.content)
    idn = root.find(f"{OAI}Identify")
    assert idn is not None
    assert idn.find(f"{OAI}protocolVersion").text == "2.0"
    assert idn.find(f"{OAI}deletedRecord").text == "no"


@pytest.mark.asyncio
async def test_list_metadata_formats(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = oai_ctx
    root = ET.fromstring((await client.get(_url(verb="ListMetadataFormats"))).content)
    prefixes = [e.text for e in root.iter(f"{OAI}metadataPrefix")]
    # RF-INT-06: dos formatos de diseminación — Dublin Core y EAD/ISAD(G).
    assert prefixes == ["oai_dc", "ead"]


@pytest.mark.asyncio
async def test_bad_verb(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = oai_ctx
    root = ET.fromstring((await client.get(_url(verb="Frobnicate"))).content)
    err = root.find(f"{OAI}error")
    assert err.get("code") == "badVerb"


@pytest.mark.asyncio
async def test_resumption_token_is_exclusive(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = oai_ctx
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", resumptionToken="x", set="E"))).content
    )
    assert root.find(f"{OAI}error").get("code") == "badArgument"


@pytest.mark.asyncio
async def test_list_records_missing_prefix(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = oai_ctx
    root = ET.fromstring((await client.get(_url(verb="ListRecords"))).content)
    assert root.find(f"{OAI}error").get("code") == "badArgument"


@pytest.mark.asyncio
async def test_list_records_no_records_match(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchval.return_value = 0  # count_public == 0
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", metadataPrefix="oai_dc"))).content
    )
    assert root.find(f"{OAI}error").get("code") == "noRecordsMatch"


@pytest.mark.asyncio
async def test_list_records_dublin_core_and_completelistsize(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = oai_ctx
    conn.fetchval.return_value = 1  # count_public
    conn.fetch.return_value = [_rad(subject="Convenio interadministrativo")]
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", metadataPrefix="oai_dc"))).content
    )
    lr = root.find(f"{OAI}ListRecords")
    rec = lr.find(f"{OAI}record")
    assert rec.find(f"{OAI}header/{OAI}identifier").text == "oai:demo:2026-DEMO-E-000001"
    assert rec.find(f"{OAI}header/{OAI}setSpec").text == "E"
    assert rec.find(f".//{DC}title").text == "Convenio interadministrativo"
    assert rec.find(f".//{DC}type").text == "Entrada"
    # completeListSize refleja SOLO el conteo público (=1); token vacío (última página).
    rt = lr.find(f"{OAI}resumptionToken")
    assert rt.get("completeListSize") == "1"
    assert (rt.text or "") == ""


@pytest.mark.asyncio
async def test_list_records_pagination_emits_token(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchval.return_value = 250  # > una página (100)
    conn.fetch.return_value = [_rad(tn=f"2026-DEMO-E-{i:06d}") for i in range(100)]
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", metadataPrefix="oai_dc"))).content
    )
    rt = root.find(f"{OAI}ListRecords/{OAI}resumptionToken")
    assert rt.get("completeListSize") == "250"
    assert rt.text  # token no vacío → hay más páginas


@pytest.mark.asyncio
async def test_get_record_public(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchrow.return_value = _rad()
    root = ET.fromstring(
        (
            await client.get(
                _url(verb="GetRecord", identifier="oai:demo:2026-DEMO-E-000001", metadataPrefix="oai_dc")
            )
        ).content
    )
    assert root.find(f"{OAI}GetRecord/{OAI}record/{OAI}header/{OAI}identifier").text == (
        "oai:demo:2026-DEMO-E-000001"
    )


@pytest.mark.asyncio
async def test_get_record_reserved_is_iddoesnotexist(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un id reservado/inexistente → `idDoesNotExist` INDISTINGUIBLE: el repo filtra
    `nivel_seguridad=1`, así que `get_public` devuelve None en ambos casos."""
    client, conn = oai_ctx
    conn.fetchrow.return_value = None  # reservado o inexistente: mismo None
    root = ET.fromstring(
        (
            await client.get(
                _url(verb="GetRecord", identifier="oai:demo:2026-DEMO-E-999999", metadataPrefix="oai_dc")
            )
        ).content
    )
    assert root.find(f"{OAI}error").get("code") == "idDoesNotExist"


@pytest.mark.asyncio
async def test_get_record_bad_format(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchrow.return_value = _rad()
    root = ET.fromstring(
        (
            await client.get(
                _url(verb="GetRecord", identifier="oai:demo:2026-DEMO-E-000001", metadataPrefix="marc")
            )
        ).content
    )
    assert root.find(f"{OAI}error").get("code") == "cannotDisseminateFormat"


@pytest.mark.asyncio
async def test_list_sets_from_public_only(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetch.return_value = [{"doc_type": "E"}, {"doc_type": "S"}]
    root = ET.fromstring((await client.get(_url(verb="ListSets"))).content)
    specs = [e.text for e in root.iter(f"{OAI}setSpec")]
    assert specs == ["E", "S"]


# --- remediaciones de auditoría ----------------------------------------------


@pytest.mark.asyncio
async def test_nonexistent_tenant_is_400_not_500(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    """Seg #1: un tenant con formato válido pero inexistente responde uniforme
    (400 badArgument), NO revienta en 500 (oráculo de existencia)."""
    client, conn = oai_ctx
    conn.fetchval.side_effect = [None]  # to_regclass('"tenant_x".radicados') -> NULL
    resp = await client.get(_url(verb="Identify"))
    assert resp.status_code == 400
    assert ET.fromstring(resp.content).find(f"{OAI}error").get("code") == "badArgument"


@pytest.mark.asyncio
async def test_badargument_request_omits_attributes(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """Conf #3: en badArgument el <request> lleva solo baseURL, sin atributos."""
    client, _ = oai_ctx
    root = ET.fromstring((await client.get(_url(verb="ListRecords"))).content)  # falta prefix
    assert root.find(f"{OAI}error").get("code") == "badArgument"
    assert root.find(f"{OAI}request").attrib == {}  # sin verb ni otros atributos


@pytest.mark.asyncio
async def test_iddoesnotexist_request_reflects_args(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """Conf #4: en errores no-badVerb/badArgument el <request> refleja los args."""
    client, conn = oai_ctx
    conn.fetchrow.return_value = None  # id reservado/inexistente
    root = ET.fromstring(
        (
            await client.get(
                _url(verb="GetRecord", identifier="oai:demo:2026-DEMO-S-000009", metadataPrefix="oai_dc")
            )
        ).content
    )
    req = root.find(f"{OAI}request")
    assert root.find(f"{OAI}error").get("code") == "idDoesNotExist"
    assert req.get("verb") == "GetRecord"
    assert req.get("identifier") == "oai:demo:2026-DEMO-S-000009"


@pytest.mark.asyncio
async def test_negative_offset_token_is_bad_resumption_token(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """Seg #2: un token con offset negativo → badResumptionToken, no un 500 de OFFSET."""
    import base64

    client, _ = oai_ctx
    token = base64.urlsafe_b64encode(b"-1|||oai_dc").decode()
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", resumptionToken=token))).content
    )
    assert root.find(f"{OAI}error").get("code") == "badResumptionToken"


@pytest.mark.asyncio
async def test_mixed_date_granularity_is_bad_argument(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """Conf #7 (guarda): mezclar granularidad de from/until → badArgument."""
    client, conn = oai_ctx
    conn.fetchval.return_value = 5
    root = ET.fromstring(
        (
            await client.get(
                _url(verb="ListRecords", metadataPrefix="oai_dc", **{"from": "2026-01-01"})
                + "&until=2026-03-01T00:00:00Z"
            )
        ).content
    )
    assert root.find(f"{OAI}error").get("code") == "badArgument"


# --- INT-06: formato EAD / ISAD(G) -------------------------------------------


@pytest.mark.asyncio
async def test_get_record_ead_isadg(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    """RF-INT-06: `metadataPrefix=ead` disemina el radicado como componente EAD 2002
    con los elementos OBLIGATORIOS de ISAD(G) (código, título, fecha, nivel, productor)."""
    client, conn = oai_ctx
    conn.fetchrow.return_value = _rad(subject="Convenio marco")
    root = ET.fromstring(
        (
            await client.get(
                _url(verb="GetRecord", identifier="oai:demo:2026-DEMO-E-000001", metadataPrefix="ead")
            )
        ).content
    )
    arch = root.find(f".//{EAD}archdesc")
    did = arch.find(f"{EAD}did")
    assert arch.get("level") == "item"  # unidad documental simple
    unitid = did.find(f"{EAD}unitid")
    assert unitid.text == "2026-DEMO-E-000001"
    # 3.1.1 código de referencia para intercambio: país (ISO 3166) + repositorio.
    assert unitid.get("countrycode") == "CO"
    assert unitid.get("repositorycode") == "DEMO"
    assert did.find(f"{EAD}unittitle").text == "Convenio marco"
    assert did.find(f"{EAD}unitdate").get("normal") == "2026-03-01"
    assert did.find(f"{EAD}origination").text == "Secretaría General"
    assert did.find(f"{EAD}langmaterial/{EAD}language").get("langcode") == "spa"
    # 3.4.1 condiciones de acceso (público, Ley 1712) + 3.7.2 reglas de descripción.
    assert arch.find(f"{EAD}accessrestrict/{EAD}p") is not None
    assert "NTC 4095" in root.find(f".//{EAD}descrules").text


@pytest.mark.asyncio
async def test_list_records_ead(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    """El formato EAD también fluye por ListRecords y el resumptionToken preserva el
    prefijo (la reanudación sigue en EAD, no cae a oai_dc)."""
    client, conn = oai_ctx
    conn.fetchval.return_value = 250  # > una página → emite token
    conn.fetch.return_value = [_rad(tn=f"2026-DEMO-E-{i:06d}") for i in range(100)]
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", metadataPrefix="ead"))).content
    )
    assert root.find(f".//{EAD}archdesc/{EAD}did/{EAD}unitid") is not None
    # el token round-trip lleva el prefijo ead embebido.
    import base64
    rt = root.find(f"{OAI}ListRecords/{OAI}resumptionToken").text
    assert base64.urlsafe_b64decode(rt.encode()).decode().endswith("|ead")


@pytest.mark.asyncio
async def test_get_record_reserved_ead_is_iddoesnotexist(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """El chokepoint público es ortogonal al formato: un reservado en EAD también es
    `idDoesNotExist` indistinguible (no hay fuga por elegir otro metadataPrefix)."""
    client, conn = oai_ctx
    conn.fetchrow.return_value = None
    root = ET.fromstring(
        (
            await client.get(
                _url(verb="GetRecord", identifier="oai:demo:2026-DEMO-E-999999", metadataPrefix="ead")
            )
        ).content
    )
    assert root.find(f"{OAI}error").get("code") == "idDoesNotExist"


# ---------------------------------------------------------------------------
# La interoperabilidad pública anónima está APAGADA por defecto (E11): estos
# tests ejercitan el protocolo, así que la encienden explícitamente. El
# comportamiento del interruptor en sí se prueba aparte, en
# `test_public_interop_switch.py`.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _habilitar_interop(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "public_interop_enabled", True)
