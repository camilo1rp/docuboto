# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests OAI-PMH de EXPEDIENTES (E11, RF-INT-06). El endpoint usa
`app.state.db_pool` directo (público por-path, gemelo del de document-service).
El recorte de seguridad (solo público) se ejerce en SQL; aquí se cubre el
protocolo (verbos, errores, Dublin Core, EAD multinivel, resumptionToken,
guarda anti-fuga item-level)."""

import base64
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from orpycamcp_common.tenant import AtencionPublica

from app.main import app
from tests.oai_fakes import fetch_oai

OAI = "{http://www.openarchives.org/OAI/2.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
EAD = "{urn:isbn:1-931666-22-9}"

SERIE_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _entidad_operativa():
    """ADR-035: estos tests ejercen el protocolo, no el estado (eso lo cubre
    `test_estado_entidad.py`), así que la entidad se da por operativa."""
    with patch("app.routers.oai.atencion_publica", new=AsyncMock(return_value=AtencionPublica.COMPLETA)):
        yield


def _exp(
    code: str = "2026-DEMO-001",
    name: str = "Contratación pública",
    trd_serie_id=SERIE_ID,
    status: str = "open",
    radicado_count: int = 3,
) -> dict:
    return {
        "id": uuid.uuid4(),
        "code": code,
        "name": name,
        "description": "Expediente de seguimiento contractual",
        "trd_serie_id": trd_serie_id,
        "status": status,
        "opened_at": datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
        "closed_at": None,
        "radicado_count": radicado_count,
    }


def _serie_chain() -> list[dict]:
    """serie (raíz) -> subserie, ordenada de lo general a lo específico."""
    return [
        {"id": SERIE_ID, "code": "SER-01", "name": "Gestión Contractual", "parent_id": None},
    ]


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
    return f"/api/v1/public/archive/oai/demo?{qs}"


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
    # RF-INT-06: dos formatos de diseminación — Dublin Core y EAD/ISAD(G) multinivel.
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
        (await client.get(_url(verb="ListRecords", resumptionToken="x", set="open"))).content
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
    conn.fetch.return_value = [_exp(name="Convenio interadministrativo")]
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", metadataPrefix="oai_dc"))).content
    )
    lr = root.find(f"{OAI}ListRecords")
    rec = lr.find(f"{OAI}record")
    assert rec.find(f"{OAI}header/{OAI}identifier").text == "oai:demo:expediente:2026-DEMO-001"
    assert rec.find(f"{OAI}header/{OAI}setSpec").text == "open"
    assert rec.find(f".//{DC}title").text == "Convenio interadministrativo"
    assert rec.find(f".//{DC}type").text == "Abierto"
    # completeListSize refleja SOLO el conteo público (=1); token vacío (última página).
    rt = lr.find(f"{OAI}resumptionToken")
    assert rt.get("completeListSize") == "1"
    assert (rt.text or "") == ""


@pytest.mark.asyncio
async def test_list_records_pagination_emits_token(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchval.return_value = 250  # > una página (100)
    conn.fetch.return_value = [_exp(code=f"2026-DEMO-{i:03d}") for i in range(100)]
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", metadataPrefix="oai_dc"))).content
    )
    rt = root.find(f"{OAI}ListRecords/{OAI}resumptionToken")
    assert rt.get("completeListSize") == "250"
    assert rt.text  # token no vacío → hay más páginas


@pytest.mark.asyncio
async def test_get_record_public(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchrow.return_value = _exp()
    root = ET.fromstring(
        (
            await client.get(
                _url(
                    verb="GetRecord",
                    identifier="oai:demo:expediente:2026-DEMO-001",
                    metadataPrefix="oai_dc",
                )
            )
        ).content
    )
    assert root.find(f"{OAI}GetRecord/{OAI}record/{OAI}header/{OAI}identifier").text == (
        "oai:demo:expediente:2026-DEMO-001"
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
                _url(
                    verb="GetRecord",
                    identifier="oai:demo:expediente:2026-DEMO-999",
                    metadataPrefix="oai_dc",
                )
            )
        ).content
    )
    assert root.find(f"{OAI}error").get("code") == "idDoesNotExist"


@pytest.mark.asyncio
async def test_get_record_bad_format(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetchrow.return_value = _exp()
    root = ET.fromstring(
        (
            await client.get(
                _url(
                    verb="GetRecord",
                    identifier="oai:demo:expediente:2026-DEMO-001",
                    metadataPrefix="marc",
                )
            )
        ).content
    )
    assert root.find(f"{OAI}error").get("code") == "cannotDisseminateFormat"


@pytest.mark.asyncio
async def test_list_sets_from_public_only(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = oai_ctx
    conn.fetch.return_value = [{"status": "closed"}, {"status": "open"}]
    root = ET.fromstring((await client.get(_url(verb="ListSets"))).content)
    specs = [e.text for e in root.iter(f"{OAI}setSpec")]
    assert specs == ["closed", "open"]


@pytest.mark.asyncio
async def test_nonexistent_tenant_is_400_not_500(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    """Un tenant con formato válido pero inexistente responde uniforme
    (400 badArgument), NO revienta en 500 (oráculo de existencia)."""
    client, conn = oai_ctx
    with patch(
        "app.routers.oai.atencion_publica", new=AsyncMock(return_value=AtencionPublica.NINGUNA)
    ):
        resp = await client.get(_url(verb="Identify"))
    assert resp.status_code == 400
    assert ET.fromstring(resp.content).find(f"{OAI}error").get("code") == "badArgument"


@pytest.mark.asyncio
async def test_badargument_request_omits_attributes(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    client, _ = oai_ctx
    root = ET.fromstring((await client.get(_url(verb="ListRecords"))).content)  # falta prefix
    assert root.find(f"{OAI}error").get("code") == "badArgument"
    assert root.find(f"{OAI}request").attrib == {}


@pytest.mark.asyncio
async def test_negative_offset_token_is_bad_resumption_token(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
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


# --- RF-INT-06: formato EAD / ISAD(G) multinivel -----------------------------


@pytest.mark.asyncio
async def test_get_record_ead_isadg_hierarchy(oai_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    """RF-INT-06: `metadataPrefix=ead` disemina el expediente como `<c level="file">`
    anidado bajo `<c level="series">` (jerarquía ISAD(G) 2.2/2.4), con los 6
    obligatorios."""
    client, conn = oai_ctx
    conn.fetchrow.return_value = _exp(name="Convenio marco")
    conn.fetch.side_effect = fetch_oai(_serie_chain())  # get_serie_chain
    root = ET.fromstring(
        (
            await client.get(
                _url(
                    verb="GetRecord",
                    identifier="oai:demo:expediente:2026-DEMO-001",
                    metadataPrefix="ead",
                )
            )
        ).content
    )
    arch = root.find(f".//{EAD}archdesc")
    assert arch.get("level") == "fonds"
    series_c = arch.find(f"{EAD}dsc/{EAD}c")
    assert series_c.get("level") == "series"
    file_c = series_c.find(f"{EAD}c")
    assert file_c.get("level") == "file"
    did = file_c.find(f"{EAD}did")
    unitid = did.find(f"{EAD}unitid")
    assert unitid.text == "2026-DEMO-001"
    # 3.1.1 código de referencia para intercambio: país (ISO 3166) + repositorio.
    assert unitid.get("countrycode") == "CO"
    assert unitid.get("repositorycode") == "demo"
    # 3.1.2/3.1.3/3.1.4/3.1.5
    assert did.find(f"{EAD}unittitle").text == "Convenio marco"
    assert did.find(f"{EAD}unitdate").get("normal") == "2026-03-01"
    assert did.find(f"{EAD}physdesc").text == "3 documentos"
    # 3.2.1 productor = la INSTITUCIÓN (tenant), no la serie: una serie es categoría
    # de clasificación, no agente productor, y su contexto ya lo da el <c level="series">.
    assert did.find(f"{EAD}origination").text == "demo"
    # el contexto de serie/subserie vive en los niveles <c> superiores, no en origination.
    assert series_c.find(f"{EAD}did/{EAD}unittitle").text == "Gestión Contractual"
    # 3.3.1 / 3.4.1 / 3.7.2
    assert file_c.find(f"{EAD}scopecontent/{EAD}p") is not None
    assert file_c.find(f"{EAD}accessrestrict/{EAD}p") is not None
    assert "NTC 4095" in root.find(f".//{EAD}descrules").text


@pytest.mark.asyncio
async def test_get_record_ead_sin_serie_fallback(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sin `trd_serie_id`, fallback honesto: `archdesc[level=file]` directo,
    sin `dsc`/`c` (no se inventa jerarquía)."""
    client, conn = oai_ctx
    conn.fetchrow.return_value = _exp(trd_serie_id=None)
    root = ET.fromstring(
        (
            await client.get(
                _url(
                    verb="GetRecord",
                    identifier="oai:demo:expediente:2026-DEMO-001",
                    metadataPrefix="ead",
                )
            )
        ).content
    )
    arch = root.find(f".//{EAD}archdesc")
    assert arch.get("level") == "file"
    assert arch.find(f"{EAD}dsc") is None
    assert arch.find(f"{EAD}did/{EAD}unitid") is not None
    conn.fetch.assert_not_called()  # no se resuelve cadena de series sin trd_serie_id


@pytest.mark.asyncio
async def test_list_records_ead_preserves_prefix_token(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """El formato EAD también fluye por ListRecords y el resumptionToken preserva el
    prefijo (la reanudación sigue en EAD, no cae a oai_dc)."""
    client, conn = oai_ctx
    conn.fetchval.return_value = 250  # > una página → emite token
    exp_row = _exp(code="2026-DEMO-000", trd_serie_id=None)  # fallback: sin 2ª query de fetch

    async def _fetch(*args, **kwargs):
        return [exp_row]

    conn.fetch.side_effect = _fetch
    root = ET.fromstring(
        (await client.get(_url(verb="ListRecords", metadataPrefix="ead"))).content
    )
    assert root.find(f".//{EAD}archdesc/{EAD}did/{EAD}unitid") is not None
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
                _url(
                    verb="GetRecord",
                    identifier="oai:demo:expediente:2026-DEMO-999",
                    metadataPrefix="ead",
                )
            )
        ).content
    )
    assert root.find(f"{OAI}error").get("code") == "idDoesNotExist"


@pytest.mark.asyncio
async def test_no_tracking_number_leaks_in_ead_or_dc(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    """Guarda anti-fuga item-level (frontera crítica del brief): ni el EAD ni el
    Dublin Core del expediente mencionan ningún tracking_number de radicado hijo —
    solo el CONTEO agregado (`physdesc`)."""
    client, conn = oai_ctx
    conn.fetchrow.return_value = _exp()
    conn.fetch.side_effect = fetch_oai(_serie_chain())
    for prefix in ("oai_dc", "ead"):
        resp = await client.get(
            _url(
                verb="GetRecord",
                identifier="oai:demo:expediente:2026-DEMO-001",
                metadataPrefix=prefix,
            )
        )
        body_text = resp.content.decode()
        assert "2026-DEMO-E-" not in body_text  # patrón de tracking_number de radicado
        assert "tracking_number" not in body_text.lower()


@pytest.mark.asyncio
async def test_ead_physdesc_reflects_radicado_count(
    oai_ctx: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = oai_ctx
    conn.fetchrow.return_value = _exp(radicado_count=7, trd_serie_id=None)
    root = ET.fromstring(
        (
            await client.get(
                _url(
                    verb="GetRecord",
                    identifier="oai:demo:expediente:2026-DEMO-001",
                    metadataPrefix="ead",
                )
            )
        ).content
    )
    assert root.find(f".//{EAD}physdesc").text == "7 documentos"


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
