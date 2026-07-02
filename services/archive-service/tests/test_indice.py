# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del índice electrónico (E15, Acuerdo AGN 001/2024)."""

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.index_builder import (
    NS,
    XSD_VERSION,
    IndiceData,
    IndiceItemData,
    build_xml,
    content_hash,
    xml_sha256,
)
from tests.conftest import TENANT_HEADERS, make_expediente_record, make_radicado_link_record

NOW = datetime.now(tz=UTC)


def _indice_row(*, version=1, estado="vigente", content="x" * 64, num=1, xsd_version="v2") -> dict:
    # `xml_sha256` es el HASH REAL de `xml` (no una constante ficticia): `verify()`
    # re-hashea la columna almacenada y lo contrasta (fixity del propio índice).
    xml = "<indice/>"
    return {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": version,
        "xml": xml,
        "xml_sha256": xml_sha256(xml),
        "content_hash": content,
        "algoritmo": "SHA-256",
        "xsd_version": xsd_version,
        "num_documentos": num,
        "estado": estado,
        "firma_id": None,
        "minio_key_firmado": None,
        "generado_por": "system",
        "generado_ts": NOW,
    }


# -- IndexBuilder (unidad, sin BD) ------------------------------------


def test_build_xml_has_normative_fields() -> None:
    """E15 Inc.3 (RT-15): el índice se emite en el namespace v2 —
    `xsdVersion="v2"` en la raíz — actualizado tras el salto v1->v2."""
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2024-0001",
        name="Contrato",
        serie_id=str(uuid.uuid4()),
        fecha_creacion=NOW,
        items=[IndiceItemData(document_id=str(uuid.uuid4()), orden=1, titulo="2024-E-001")],
    )
    xml = build_xml(data)
    assert NS == "urn:orpycamcp:indice:v2"
    assert XSD_VERSION == "v2"
    assert NS in xml
    assert 'xsdVersion="v2"' in xml
    assert "identificadorSistema" in xml
    assert "valorHuella" in xml and "funcionResumen" in xml
    assert "<" in xml and "documento" in xml


def test_content_hash_is_order_sensitive() -> None:
    a = IndiceItemData(document_id="11111111-1111-1111-1111-111111111111", orden=1)
    b = IndiceItemData(document_id="22222222-2222-2222-2222-222222222222", orden=2)
    assert content_hash([a, b]) != content_hash([b, a])
    assert content_hash([a, b]) == content_hash([a, b])  # determinista


# ---------------------------------------------------------------------------
# Metadatos RT-15 del índice v2 (E15 Inc.3, Acuerdo AGN 001/2024 art. 4.3.2.3)
# ---------------------------------------------------------------------------


def test_build_xml_v2_emits_formato_tamano_folios_fecha_incorporacion() -> None:
    """(a) Cuando el vínculo declaró formato/tamaño/folios, el XML v2 los
    emite junto con <fechaIncorporacion> (renombre de fechaArchivado), en el
    namespace urn:orpycamcp:indice:v2 con xsdVersion="v2"."""
    fecha = NOW
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2024-0001",
        name="Contrato",
        serie_id=str(uuid.uuid4()),
        fecha_creacion=NOW,
        items=[
            IndiceItemData(
                document_id=str(uuid.uuid4()),
                orden=1,
                titulo="2024-E-001",
                fecha_archivado=fecha,
                valor_huella="a" * 64,
                formato="application/pdf",
                tamano_bytes=204800,
                folios=12,
            )
        ],
    )
    xml = build_xml(data)
    assert NS in xml
    assert 'xsdVersion="v2"' in xml
    assert "formato" in xml and "application/pdf" in xml
    assert "tamano" in xml and ">204800<" in xml
    assert "folios" in xml and ">12<" in xml
    assert "fechaIncorporacion" in xml and fecha.isoformat() in xml
    # Campos core siempre presentes, con o sin RT-15.
    assert "identificador" in xml
    assert "valorHuella" in xml
    assert "orden" in xml


def test_build_xml_v2_omits_rt15_fields_when_not_declared() -> None:
    """(b) Un vínculo SIN los tres campos RT-15 (formato/tamaño/folios NULL)
    omite esos elementos del <documento> — no se emiten vacíos, para no
    sugerir un valor declarado. Los campos core (identificador/valorHuella/
    orden) siguen presentes."""
    doc_id = str(uuid.uuid4())
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2024-0001",
        name="Contrato",
        serie_id=None,
        fecha_creacion=NOW,
        items=[
            IndiceItemData(
                document_id=doc_id,
                orden=1,
                titulo="2024-E-001",
                valor_huella="b" * 64,
                # formato/tamano_bytes/folios/fecha_archivado -> None (default)
            )
        ],
    )
    xml = build_xml(data)
    assert "formato" not in xml
    assert "tamano" not in xml
    assert "folios" not in xml
    assert "fechaIncorporacion" not in xml
    # Ausencia de RT-15 no afecta a los campos core.
    assert doc_id in xml
    assert "b" * 64 in xml  # valorHuella
    assert "<" in xml and "orden" in xml


def test_content_hash_is_sensitive_to_rt15_metadata() -> None:
    """(d, canon unitario) Mismo documento/huella/orden pero folios distinto
    (uno sin declarar) produce content_hash distinto — declarar/corregir un
    metadato RT-15 en un expediente abierto dispara versión nueva."""
    base = dict(
        document_id="33333333-3333-3333-3333-333333333333",
        orden=1,
        valor_huella="a" * 64,
    )
    sin_folios = IndiceItemData(**base)
    con_folios = IndiceItemData(**base, folios=10)
    assert content_hash([sin_folios]) != content_hash([con_folios])
    # Idempotencia: mismos metadatos -> mismo hash.
    assert content_hash([con_folios]) == content_hash([IndiceItemData(**base, folios=10)])


def test_xml_sha256_rehash_matches_stored_v1_hash() -> None:
    """(c) Retrocompat: un índice v1 legado (namespace :v1, previo al salto a
    v2) sigue re-hasheando igual — `xml_sha256()` es agnóstico del namespace/
    versión del contenido, solo hashea la cadena serializada almacenada."""
    v1_xml = (
        '<indice xmlns="urn:orpycamcp:indice:v1" xsdVersion="v1">'
        "<cabecera><identificadorSistema>abc</identificadorSistema></cabecera>"
        "<documentos/></indice>"
    )
    expected = hashlib.sha256(v1_xml.encode("utf-8")).hexdigest()
    assert xml_sha256(v1_xml) == expected
    # Determinismo: re-hashear el mismo texto almacenado da siempre lo mismo.
    assert xml_sha256(v1_xml) == xml_sha256(v1_xml)


@pytest.mark.asyncio
async def test_insert_version_persists_xsd_version_v2() -> None:
    """(e) Toda versión nueva del índice persiste xsd_version='v2' de forma
    explícita — el repositorio NO confía en el DEFAULT 'v1' de la columna
    (migración 015)."""
    from app.repositories.index_repository import IndexRepository

    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=AsyncMock())
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4()}
    repo = IndexRepository(mock_conn)

    await repo.insert_version(
        expediente_id=uuid.uuid4(),
        version=1,
        xml="<indice/>",
        xml_sha256="a" * 64,
        content_hash="b" * 64,
        num_documentos=0,
        generado_por="system",
        items=[],
    )

    args, _kwargs = mock_conn.fetchrow.call_args
    assert args[-1] == "v2"
    assert args[-1] == XSD_VERSION


# -- Endpoints --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_indice_current(client_with_conn) -> None:
    """El JSON consultable (E15 Inc.3, RT-15) expone formato/tamano_bytes/folios
    y renombra la fecha a `fecha_incorporacion` — alineado con <fechaIncorporacion>
    del XML v2 (antes el response schema los descartaba)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _indice_row(version=2, num=1)
    mock_conn.fetch.return_value = [
        {
            "document_id": uuid.uuid4(),
            "orden": 1,
            "titulo": "2024-E-001",
            "tipologia": None,
            "fecha_declaracion": None,
            "fecha_archivado": NOW,
            "valor_huella": None,
            "funcion_resumen": "SHA-256",
            "formato": "application/pdf",
            "tamano_bytes": 2048,
            "folios": 3,
        },
    ]
    resp = await client.get(f"/api/v1/expedientes/{uuid.uuid4()}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["formato"] == "application/pdf"
    assert item["tamano_bytes"] == 2048
    assert item["folios"] == 3
    assert item["fecha_incorporacion"] is not None


@pytest.mark.asyncio
async def test_get_indice_item_tolerates_missing_rt15_keys(client_with_conn) -> None:
    """Filas de item sin formato/tamano_bytes/folios proyectados (legado) no
    rompen la respuesta — se omiten como None."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _indice_row(version=1, num=1)
    mock_conn.fetch.return_value = [
        {
            "document_id": uuid.uuid4(),
            "orden": 1,
            "titulo": "2024-E-001",
            "tipologia": None,
            "fecha_declaracion": None,
            "fecha_archivado": NOW,
            "valor_huella": None,
            "funcion_resumen": "SHA-256",
        },
    ]
    resp = await client.get(f"/api/v1/expedientes/{uuid.uuid4()}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["formato"] is None
    assert item["tamano_bytes"] is None
    assert item["folios"] is None


@pytest.mark.asyncio
async def test_get_indice_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/expedientes/{uuid.uuid4()}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_indice_not_found"


def test_item_valor_huella_from_link_hash() -> None:
    """El content_hash del vínculo se usa como valor_huella del item del índice.

    (RT-15, E15 Inc.3) `_items_from` también lee formato/tamano_bytes/folios
    del vínculo — el objeto de dominio debe exponerlos, aunque sea NULL.
    """
    from app.models.archive import Expediente
    from app.services.index_service import IndexService

    h = "c" * 64
    rad = make_radicado_link_record(content_hash=h)
    exp = Expediente(
        id=uuid.uuid4(),
        code="EXP",
        name="x",
        description=None,
        trd_serie_id=None,
        status="open",
        opened_by=None,
        opened_at=NOW,
        closed_at=None,
        updated_at=NOW,
        radicados=[
            type(
                "R",
                (),
                {
                    "radicado_id": rad["radicado_id"],
                    "tracking_number": rad["tracking_number"],
                    "added_at": NOW,
                    "content_hash": h,
                    "formato": None,
                    "tamano_bytes": None,
                    "folios": None,
                },
            )()
        ],
    )
    items = IndexService.__new__(IndexService)._items_from(exp)
    assert items[0].valor_huella == h
    assert items[0].formato is None
    assert items[0].tamano_bytes is None
    assert items[0].folios is None


@pytest.mark.asyncio
async def test_link_to_closed_expediente_rejected(client_with_conn) -> None:
    """No se pueden añadir documentos a un expediente cerrado (inmutabilidad)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="closed")
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.post(
        f"/api/v1/expedientes/{uuid.uuid4()}/radicados",
        json={"radicado_id": str(uuid.uuid4()), "tracking_number": "2024-E-009"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_open"


@pytest.mark.asyncio
async def test_verify_intact(client_with_conn) -> None:
    """Si la huella del índice coincide con la actual del documento → válido."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    h = "d" * 64
    indice = _indice_row(version=2, num=1)
    item = {
        "document_id": rad_id,
        "orden": 1,
        "titulo": "2024-E-001",
        "tipologia": None,
        "fecha_declaracion": None,
        "fecha_archivado": NOW,
        "valor_huella": h,
        "funcion_resumen": "SHA-256",
        "formato": None,
        "tamano_bytes": None,
    }
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)
    # fetchrow: get_current(indice), get_expediente(exp); fetch: items, radicados
    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[item], [link]]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["valido"] is True
    assert body["documentos"][0]["coincide"] is True
    assert body["firma"]["presente"] is False


@pytest.mark.asyncio
async def test_verify_tampered(client_with_conn) -> None:
    """Si el documento cambió (hash distinto al del índice) → inválido."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    item = {
        "document_id": rad_id,
        "orden": 1,
        "titulo": "2024-E-001",
        "tipologia": None,
        "fecha_declaracion": None,
        "fecha_archivado": NOW,
        "valor_huella": "d" * 64,
        "funcion_resumen": "SHA-256",
        "formato": None,
        "tamano_bytes": None,
    }
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash="e" * 64)
    mock_conn.fetchrow.side_effect = [_indice_row(version=2, num=1), exp]
    mock_conn.fetch.side_effect = [[item], [link]]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["valido"] is False
    assert body["documentos"][0]["coincide"] is False


@pytest.mark.asyncio
async def test_verify_detects_indice_xml_tampering(client_with_conn) -> None:
    """(nuevo) Si el XML almacenado del índice (columna `xml`) fue alterado
    directamente en BD — huellas de documento intactas, sin firma — `verify`
    lo detecta re-hasheando contra `xml_sha256` persistido; antes esa
    alteración quedaba indetectada (E15, Acuerdo AGN 001/2024)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    h = "7" * 64
    indice = _indice_row(version=1, num=1)
    indice["xml"] = "<indice-alterado/>"  # xml_sha256 sigue siendo el original
    item = {
        "document_id": rad_id,
        "orden": 1,
        "titulo": "2024-E-001",
        "tipologia": None,
        "fecha_declaracion": None,
        "fecha_archivado": NOW,
        "valor_huella": h,
        "funcion_resumen": "SHA-256",
        "formato": None,
        "tamano_bytes": None,
    }
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)
    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[item], [link]]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["huella_indice_ok"] is False
    assert body["valido"] is False
    # Fixity por documento sigue OK — el fallo es específico de la huella del índice.
    assert body["documentos"][0]["coincide"] is True


@pytest.mark.asyncio
async def test_rebuild_idempotent_when_unchanged(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    added_at = NOW
    # content_hash del único documento (orden 1, sin huella) que el índice ya
    # tiene — `content_hash` (RT-15) también depende de `fecha_archivado`, así
    # que debe coincidir con el `added_at` del vínculo mockeado abajo.
    expected_ch = content_hash(
        [IndiceItemData(document_id=str(rad_id), orden=1, fecha_archivado=added_at)]
    )
    exp = make_expediente_record(exp_id=exp_id)
    # get_expediente_by_id: fetchrow(exp) + fetch(radicados); get_current: fetchrow(indice)
    mock_conn.fetchrow.side_effect = [exp, _indice_row(version=3, content=expected_ch)]
    mock_conn.fetch.return_value = [
        make_radicado_link_record(radicado_id=rad_id, tracking_number="2024-E-001", added_at=added_at)
    ]

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unchanged"] is True
    assert body["version"] == 3


@pytest.mark.asyncio
async def test_rebuild_idempotent_when_rt15_metadata_unchanged(client_with_conn) -> None:
    """(d) Un vínculo con metadatos RT-15 YA declarados que no cambian entre
    rebuilds no crea versión nueva — idempotencia también con formato/tamaño/
    folios (no solo sin ellos)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    h = "7" * 64
    added_at = NOW
    expected_ch = content_hash(
        [
            IndiceItemData(
                document_id=str(rad_id),
                orden=1,
                valor_huella=h,
                formato="application/pdf",
                tamano_bytes=1024,
                folios=5,
                fecha_archivado=added_at,
            )
        ]
    )
    exp = make_expediente_record(exp_id=exp_id)
    mock_conn.fetchrow.side_effect = [exp, _indice_row(version=1, content=expected_ch)]
    mock_conn.fetch.return_value = [
        make_radicado_link_record(
            radicado_id=rad_id,
            content_hash=h,
            formato="application/pdf",
            tamano_bytes=1024,
            folios=5,
            added_at=added_at,
        )
    ]

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unchanged"] is True
    assert body["version"] == 1


@pytest.mark.asyncio
async def test_rebuild_creates_new_version_when_only_folios_changed(client_with_conn) -> None:
    """(d) Declarar folios en un vínculo YA existente de un expediente
    abierto (documento/huella/orden/fecha sin cambio) es un cambio de
    contenido RT-15 — dispara version+1."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    h = "9" * 64
    added_at = NOW
    # El índice vigente (version=3) fue generado ANTES de declarar folios.
    ch_sin_folios = content_hash(
        [IndiceItemData(document_id=str(rad_id), orden=1, valor_huella=h, fecha_archivado=added_at)]
    )
    exp = make_expediente_record(exp_id=exp_id)
    # get_expediente_by_id, get_current, e insert_version (RETURNING) — en ese orden.
    mock_conn.fetchrow.side_effect = [
        exp,
        _indice_row(version=3, content=ch_sin_folios),
        _indice_row(version=4),
    ]
    # El vínculo actual YA tiene folios declarados (RT-15) — content_hash difiere.
    mock_conn.fetch.return_value = [
        make_radicado_link_record(radicado_id=rad_id, content_hash=h, folios=42, added_at=added_at)
    ]

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unchanged"] is False
    assert body["version"] == 4
    assert body["xsd_version"] == "v2"

    # ADR-010: toda regeneración real (rebuild/link/unlink/batch) deja su
    # propio asiento en audit_log — antes solo `close_expediente` lo hacía.
    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    assert any(a[5] == "archive.indice_regenerated" for a in audit_calls)
    regen_payload = next(a[8] for a in audit_calls if a[5] == "archive.indice_regenerated")
    assert '"version": 4' in regen_payload
    assert '"xsd_version": "v2"' in regen_payload


@pytest.mark.asyncio
async def test_rebuild_rejected_on_closed_expediente(client_with_conn) -> None:
    """(nuevo) `POST /indice/rebuild` sobre un expediente NO 'open' responde
    409 — el conjunto documental de un expediente cerrado está congelado; un
    rebuild ad-hoc posterior no debe crear version+1 (distinto de la
    regeneración final que SÍ ocurre dentro de `close_expediente`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    mock_conn.fetchrow.side_effect = [exp_closed]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_open"


# ---------------------------------------------------------------------------
# verify — firma presente: los tres estados posibles del signature-service
# (E15 → E06 best-effort, RF-FIR-15)
# ---------------------------------------------------------------------------


def _indice_row_with_firma(firma_id: uuid.UUID, *, minio_key_firmado: str | None = "file-id/indice.xml") -> dict:
    """Devuelve un indice_row con firma_id no nulo para los tests de verify."""
    row = _indice_row(version=1, num=1)
    row["firma_id"] = firma_id
    row["minio_key_firmado"] = minio_key_firmado
    return row


def _item_row(rad_id: uuid.UUID, h: str) -> dict:
    return {
        "document_id": rad_id,
        "orden": 1,
        "titulo": "2024-E-001",
        "tipologia": None,
        "fecha_declaracion": None,
        "fecha_archivado": NOW,
        "valor_huella": h,
        "funcion_resumen": "SHA-256",
        "formato": None,
        "tamano_bytes": None,
    }


@pytest.mark.asyncio
async def test_verify_v1_indice_firmado_sigue_verificando_ok(client_with_conn) -> None:
    """(c) Retrocompat: un índice legado con xsd_version='v1' (creado antes
    del salto a v2, RT-15/E15 Inc.3) y ya firmado sigue verificando OK — el
    flujo `verify()` no asume v2 en ningún punto: compara huellas de
    documento y reenvía el XML almacenado tal cual a signature-service,
    independientemente del namespace/versión con que se generó."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    h = "3" * 64
    v1_xml = (
        '<indice xmlns="urn:orpycamcp:indice:v1" xsdVersion="v1">'
        "<cabecera><identificadorSistema>abc</identificadorSistema></cabecera>"
        "<documentos/></indice>"
    )
    indice = _indice_row_with_firma(firma_id)
    indice["xsd_version"] = "v1"
    indice["xml"] = v1_xml
    indice["xml_sha256"] = xml_sha256(v1_xml)  # re-hash del xml almacenado
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)

    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[_item_row(rad_id, h)], [link]]

    from app.clients.signature_client import VerifyResult

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value=v1_xml),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True, nivel_conformidad="xades_b_no_acreditado", acreditado=False, motivo=None
                )
            ),
        ),
    ):
        resp = await client.get(
            f"/api/v1/expedientes/{exp_id}/indice/verify",
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["firma"]["presente"] is True
    assert body["firma"]["valida"] is True
    assert body["valido"] is True


@pytest.mark.asyncio
async def test_verify_with_valid_firma_returns_true(client_with_conn) -> None:
    """firma_id presente, XML firmado descargable y signature-service confirma →
    firma.valida=True, valido=True."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    h = "d" * 64
    indice = _indice_row_with_firma(firma_id)
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)

    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[_item_row(rad_id, h)], [link]]

    from app.clients.signature_client import VerifyResult

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<indice-firmado/>"),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True, nivel_conformidad="xades_b_no_acreditado", acreditado=False, motivo=None
                )
            ),
        ),
    ):
        resp = await client.get(
            f"/api/v1/expedientes/{exp_id}/indice/verify",
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["firma"]["presente"] is True
    assert body["firma"]["valida"] is True
    assert body["valido"] is True


@pytest.mark.asyncio
async def test_verify_with_firma_service_unreachable_reports_invalida(client_with_conn) -> None:
    """
    firma_id presente, XML firmado descargable, pero signature-service
    inalcanzable (verify → None): firma.valida=False, valido=False.

    Comportamiento preservado: no se declara conforme lo que no se pudo verificar
    (RF-FIR-15). El comportamiento era False antes del cambio y sigue siendo False.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    h = "e" * 64
    indice = _indice_row_with_firma(firma_id)
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)

    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[_item_row(rad_id, h)], [link]]

    # None simula firma_client.verify cuando el servicio está inalcanzable
    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<indice-firmado/>"),
        ),
        patch("app.clients.signature_client.verify", new=AsyncMock(return_value=None)),
    ):
        resp = await client.get(
            f"/api/v1/expedientes/{exp_id}/indice/verify",
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["firma"]["presente"] is True
    assert body["firma"]["valida"] is False
    assert body["firma"]["motivo"] == "signature_service_unreachable"
    # valido=False porque firma_presente pero !valida (no podemos declarar conforme)
    assert body["valido"] is False


@pytest.mark.asyncio
async def test_verify_with_firma_invalid_signature(client_with_conn) -> None:
    """
    firma_id presente, XML firmado descargable, signature-service confirma
    que la firma NO es válida (adulterada): firma.valida=False, valido=False.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    h = "f" * 64
    indice = _indice_row_with_firma(firma_id)
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)

    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[_item_row(rad_id, h)], [link]]

    from app.clients.signature_client import VerifyResult

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<indice-firmado/>"),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=False, nivel_conformidad=None, acreditado=False, motivo="firma_adulterada"
                )
            ),
        ),
    ):
        resp = await client.get(
            f"/api/v1/expedientes/{exp_id}/indice/verify",
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["firma"]["presente"] is True
    assert body["firma"]["valida"] is False
    assert body["valido"] is False


@pytest.mark.asyncio
async def test_verify_with_firma_but_minio_key_null_reports_unavailable(client_with_conn) -> None:
    """firma_id presente pero minio_key_firmado NULL (firma antigua/legado o
    fallo previo al persistir): no se intenta descargar ni verificar — se
    reporta 'signed_xml_unavailable' sin declarar conforme (RF-FIR-15)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    h = "1" * 64
    indice = _indice_row_with_firma(firma_id, minio_key_firmado=None)
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)

    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[_item_row(rad_id, h)], [link]]

    with (
        patch(
            "app.clients.storage_client.download_signed_xml", new=AsyncMock()
        ) as mock_download,
        patch("app.clients.signature_client.verify", new=AsyncMock()) as mock_verify,
    ):
        resp = await client.get(
            f"/api/v1/expedientes/{exp_id}/indice/verify",
            headers=TENANT_HEADERS,
        )

    mock_download.assert_not_called()
    mock_verify.assert_not_called()
    assert resp.status_code == 200
    body = resp.json()
    assert body["firma"]["presente"] is True
    assert body["firma"]["valida"] is False
    assert body["firma"]["motivo"] == "signed_xml_unavailable"
    assert body["valido"] is False


@pytest.mark.asyncio
async def test_verify_with_firma_storage_download_fails_reports_unavailable(client_with_conn) -> None:
    """minio_key_firmado presente pero storage-service no pudo servir el XML
    firmado (best-effort → None): 'signed_xml_unavailable', no se llega a
    llamar a signature_client.verify."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    h = "2" * 64
    indice = _indice_row_with_firma(firma_id)
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)

    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[_item_row(rad_id, h)], [link]]

    with (
        patch(
            "app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value=None)
        ),
        patch("app.clients.signature_client.verify", new=AsyncMock()) as mock_verify,
    ):
        resp = await client.get(
            f"/api/v1/expedientes/{exp_id}/indice/verify",
            headers=TENANT_HEADERS,
        )

    mock_verify.assert_not_called()
    assert resp.status_code == 200
    body = resp.json()
    assert body["firma"]["presente"] is True
    assert body["firma"]["valida"] is False
    assert body["firma"]["motivo"] == "signed_xml_unavailable"
    assert body["valido"] is False


# ---------------------------------------------------------------------------
# firmar_indice_retry — reintento explícito (E06 Inc.1, RT-16)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_indice_retry_404_sin_indice(client_with_conn) -> None:
    client, mock_conn = client_with_conn  # noqa: F841
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    svc = IndexService(mock_conn, "demo")
    mock_conn.fetchrow.return_value = None  # get_current → sin índice

    with pytest.raises(Exception) as exc_info:
        await svc.firmar_indice_retry(exp_id, tenant_slug="demo", actor_id=str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_firmar_indice_retry_409_ya_firmado_no_reintenta(client_with_conn) -> None:
    """Idempotente: si el índice vigente ya está 'firmado', 409 SIN llamar a
    signature-service (inmutable, no es un error de negocio)."""
    client, mock_conn = client_with_conn  # noqa: F841
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    svc = IndexService(mock_conn, "demo")
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "estado": "firmado",
        "xml": "<indice/>",
    }

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock()) as mock_sign:
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_indice_retry(exp_id, tenant_slug="demo", actor_id=str(uuid.uuid4()))
    assert exc_info.value.status_code == 409
    mock_sign.assert_not_called()


@pytest.mark.asyncio
async def test_firmar_indice_retry_503_si_firma_sigue_fallando(client_with_conn) -> None:
    client, mock_conn = client_with_conn  # noqa: F841
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    svc = IndexService(mock_conn, "demo")
    row = {"id": uuid.uuid4(), "estado": "pendiente_firma", "xml": "<indice/>", "version": 1}
    mock_conn.fetchrow.side_effect = [row, row]  # check + get_current dentro de firmar_indice

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock(return_value=None)):
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_indice_retry(
                exp_id, tenant_slug="demo", actor_id=str(uuid.uuid4())
            )
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# POST /{expediente_id}/indice/firmar — endpoint de reintento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_endpoint_sin_x_user_id_responde_403(client_with_conn) -> None:
    """D-08 (hardening): `require_permission` es fail-closed sobre la IDENTIDAD
    del llamante, no solo el permiso — sin X-User-Id no hay forma de resolver
    PERM_FIRMA/USUA_PERM_EXPEDIENTE, así que la petición se rechaza en el gate
    de autorización (403) ANTES de llegar a `_actor_id`/al servicio. Antes de
    este hardening, un llamador SIN identidad alguna llegaba hasta
    `IndexService.firmar_indice`, que solo respondía 503 tras mutar el índice
    a 'pendiente_firma' (ver hallazgo Media — indice.py sin require_permission)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()

    resp = await client.post(
        f"/api/v1/expedientes/{exp_id}/indice/firmar",
        headers={"X-Tenant-Slug": "demo"},  # sin X-User-Id
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_firmar_endpoint_x_user_id_malformado_responde_403(client_with_conn) -> None:
    """Un X-User-Id malformado tampoco resuelve identidad para `require_permission`
    (fail-closed a `user_id=None` dentro del propio gate) → 403, antes de que
    `_actor_id` tenga oportunidad de devolver su propio 400."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()

    resp = await client.post(
        f"/api/v1/expedientes/{exp_id}/indice/firmar",
        headers={"X-Tenant-Slug": "demo", "X-User-Id": "no-es-un-uuid"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_firmar_endpoint_ya_firmado_responde_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE + PERM_FIRMA granted
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "estado": "firmado",
        "xml": "<indice/>",
    }

    resp = await client.post(
        f"/api/v1/expedientes/{exp_id}/indice/firmar",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_ya_firmado"


@pytest.mark.asyncio
async def test_firmar_endpoint_exito_devuelve_firma(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE + PERM_FIRMA granted
    exp_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    row = {"id": uuid.uuid4(), "estado": "pendiente_firma", "xml": "<indice/>", "version": 1}
    mock_conn.fetchrow.side_effect = [row, row]

    from app.clients.signature_client import SignedIndice

    signed = SignedIndice(
        firma_id=firma_id,
        minio_key_firmado="file-id/indice.xml",
        nivel_conformidad="xades_b_no_acreditado",
        xades_level="XAdES-B",
        tipo_firma="sello_sistema",
        acreditado=False,
        certificado_fingerprint="ab:cd",
    )

    with patch(
        "app.clients.signature_client.sign_xml", new=AsyncMock(return_value=signed)
    ):
        resp = await client.post(
            f"/api/v1/expedientes/{exp_id}/indice/firmar",
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["firma_id"] == str(firma_id)
    assert body["minio_key_firmado"] == "file-id/indice.xml"
    assert body["acreditado"] is False
