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
import xml.etree.ElementTree as ET
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
    count_vigentes,
    xml_sha256,
)
from tests.conftest import (
    EXPEDIENTE_ID,
    RADICADO_ID,
    TENANT_HEADERS,
    make_expediente_record,
    make_radicado_link_record,
)

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
    """E15 perfil v4 "conforme completo": el índice se emite en el namespace
    v4 — `xsdVersion="v4"` en la raíz — actualizado tras el salto v3->v4."""
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2024-0001",
        name="Contrato",
        serie_id=str(uuid.uuid4()),
        fecha_creacion=NOW,
        items=[IndiceItemData(document_id=str(uuid.uuid4()), orden=1, titulo="2024-E-001")],
    )
    xml = build_xml(data)
    assert NS == "urn:orpycamcp:indice:v4"
    assert XSD_VERSION == "v4"
    assert NS in xml
    assert 'xsdVersion="v4"' in xml
    assert "identificadorSistema" in xml
    assert "valorHuella" in xml and "funcionResumen" in xml
    assert "<" in xml and "documento" in xml


def test_build_xml_is_order_insensitive_and_content_hash_reflects_xml() -> None:
    """v4: `build_xml` siempre serializa por `orden_documental` ESTABLE, así
    que el orden de ENTRADA de `items` en la lista Python no afecta el XML
    resultante — a diferencia de v1-v3, donde el canon de `content_hash` (una
    concatenación de campos) SÍ dependía del orden de entrada. `content_hash`
    (v4) ahora es literalmente el hash del XML ya construido: mismo XML ->
    mismo hash; un conjunto documental distinto -> hash distinto."""
    common = dict(expediente_id=str(uuid.uuid4()), code="EXP-2024-0001", name="Contrato", serie_id=None, fecha_creacion=NOW)
    a = IndiceItemData(document_id="11111111-1111-1111-1111-111111111111", orden=1)
    b = IndiceItemData(document_id="22222222-2222-2222-2222-222222222222", orden=2)
    xml_ab = build_xml(IndiceData(**common, items=[a, b]))
    xml_ba = build_xml(IndiceData(**common, items=[b, a]))
    assert xml_ab == xml_ba
    assert content_hash(xml_ab) == content_hash(xml_ba)
    assert content_hash(xml_ab) == content_hash(xml_ab)  # determinista

    c = IndiceItemData(document_id="33333333-3333-3333-3333-333333333333", orden=1)
    xml_c = build_xml(IndiceData(**common, items=[c]))
    assert content_hash(xml_c) != content_hash(xml_ab)


# ---------------------------------------------------------------------------
# Metadatos RT-15 del índice v2 (E15 Inc.3, Acuerdo AGN 001/2024 art. 4.3.2.3)
# ---------------------------------------------------------------------------


def test_build_xml_v2_emits_formato_tamano_folios_fecha_incorporacion() -> None:
    """(a) Cuando el vínculo declaró formato/tamaño/folios, el XML los emite
    junto con <fechaIncorporacion> (renombre de fechaArchivado, heredado de
    v2) y <fechaDeclaracion> (v4, R2 — mismo valor, equivalencia
    declaracion==incorporacion), ahora en el namespace urn:orpycamcp:indice:v4
    con xsdVersion="v4"."""
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
    assert 'xsdVersion="v4"' in xml
    assert "formato" in xml and "application/pdf" in xml
    assert "tamano" in xml and ">204800<" in xml
    assert "folios" in xml and ">12<" in xml
    assert "fechaIncorporacion" in xml and fecha.isoformat() in xml
    assert "fechaDeclaracion" in xml
    root = ET.fromstring(xml)
    doc = root.find("idx:documentos/idx:documento", _IDX_NS)
    assert doc.findtext("idx:fechaDeclaracion", namespaces=_IDX_NS) == fecha.isoformat()
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
    """(d) Mismo documento/huella/orden pero folios distinto (uno sin
    declarar) produce un XML — y por tanto un content_hash — distinto:
    declarar/corregir un metadato RT-15 en un expediente abierto dispara
    versión nueva (v4: el hash cubre el XML completo, ver
    `test_build_xml_is_order_insensitive_and_content_hash_reflects_xml`)."""
    base = dict(
        document_id="33333333-3333-3333-3333-333333333333",
        orden=1,
        valor_huella="a" * 64,
    )
    common = dict(expediente_id=str(uuid.uuid4()), code="EXP-2024-0001", name="Contrato", serie_id=None, fecha_creacion=NOW)
    sin_folios = IndiceItemData(**base)
    con_folios = IndiceItemData(**base, folios=10)
    xml_sin = build_xml(IndiceData(**common, items=[sin_folios]))
    xml_con = build_xml(IndiceData(**common, items=[con_folios]))
    assert content_hash(xml_sin) != content_hash(xml_con)
    # Idempotencia: mismos metadatos -> mismo XML -> mismo hash.
    xml_con_2 = build_xml(IndiceData(**common, items=[IndiceItemData(**base, folios=10)]))
    assert content_hash(xml_con) == content_hash(xml_con_2)


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
async def test_insert_version_persists_xsd_version_v4() -> None:
    """(e) Toda versión nueva del índice persiste xsd_version='v4' de forma
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
    assert args[-1] == "v4"
    assert args[-1] == XSD_VERSION


# -- Endpoints --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_indice_current(client_with_conn) -> None:
    """El JSON consultable (E15 Inc.3, RT-15) expone formato/tamano_bytes/folios
    y renombra la fecha a `fecha_incorporacion` — alineado con <fechaIncorporacion>
    del XML v2 (antes el response schema los descartaba)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    # fetchrow: resolve_caller_clearance (None -> PUBLICA), get_expediente_by_id
    # (no-read-up, cierre RT-15), get_current(indice); fetch: radicados, items.
    mock_conn.fetchrow.side_effect = [
        None,
        make_expediente_record(exp_id=exp_id),
        _indice_row(version=2, num=1),
    ]
    mock_conn.fetch.side_effect = [
        [],
        [
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
                "orden_documental": 1,
                "nivel_seguridad_snapshot": 1,
                "estado": "vinculado",
            },
        ],
    ]
    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["formato"] == "application/pdf"
    assert item["tamano_bytes"] == 2048
    assert item["folios"] == 3
    assert item["fecha_incorporacion"] is not None
    # (Media, cierre RT-15) `estado`/`nivel_seguridad` ahora visibles en la
    # vista consultable JSON, no solo en el XML probatorio.
    assert item["estado"] == "vinculado"
    assert item["nivel_seguridad"] == 1


@pytest.mark.asyncio
async def test_get_indice_current_exposes_dependencia_productora(client_with_conn) -> None:
    """(v4, R1) La vista JSON consultable expone `dependencia_productora_codigo`/
    `_nombre` por item — antes `IndexService._to_response` los omitía aunque
    `IndexRepository.get_items` ya proyectaba las columnas (regresión: el
    dato quedaba solo en el XML probatorio, no en la vista consultable)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        None,
        make_expediente_record(exp_id=exp_id),
        _indice_row(version=1, num=1),
    ]
    mock_conn.fetch.side_effect = [
        [],
        [
            {
                "document_id": uuid.uuid4(),
                "orden": 1,
                "titulo": "2024-E-001",
                "tipologia": None,
                "fecha_declaracion": None,
                "fecha_archivado": NOW,
                "valor_huella": None,
                "funcion_resumen": "SHA-256",
                "formato": None,
                "tamano_bytes": None,
                "folios": None,
                "orden_documental": 1,
                "nivel_seguridad_snapshot": 1,
                "estado": "vinculado",
                "dependencia_productora_codigo": 100,
                "dependencia_productora_nombre": "Secretaría General",
            },
        ],
    ]
    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["dependencia_productora_codigo"] == 100
    assert item["dependencia_productora_nombre"] == "Secretaría General"


@pytest.mark.asyncio
async def test_get_indice_item_tolerates_missing_rt15_keys(client_with_conn) -> None:
    """Filas de item sin formato/tamano_bytes/folios proyectados (legado) no
    rompen la respuesta — se omiten como None."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        None,
        make_expediente_record(exp_id=exp_id),
        _indice_row(version=1, num=1),
    ]
    mock_conn.fetch.side_effect = [
        [],
        [
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
        ],
    ]
    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["formato"] is None
    assert item["tamano_bytes"] is None
    assert item["folios"] is None
    assert item["dependencia_productora_codigo"] is None
    assert item["dependencia_productora_nombre"] is None
    assert item["estado"] is None
    assert item["nivel_seguridad"] is None


@pytest.mark.asyncio
async def test_get_indice_not_found(client_with_conn) -> None:
    """Expediente existe y es visible, pero aún no tiene índice generado → 404
    'expediente_indice_not_found' — distinto del 404 'expediente_not_found'
    que dispara el guard de no-read-up (ver `test_get_indice_clearance_scoped_returns_404`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [None, make_expediente_record(exp_id=exp_id), None]
    mock_conn.fetch.return_value = []
    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_indice_not_found"


@pytest.mark.asyncio
async def test_get_indice_requires_permission(client_with_conn) -> None:
    """(Alta, cierre RT-15) `GET /indice` ahora exige USUA_PERM_EXPEDIENTE —
    antes cualquier llamante autenticado del tenant, sin el permiso, podía
    leer el índice XML completo de cualquier expediente (incl. CLASIFICADA)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado
    resp = await client.get(f"/api/v1/expedientes/{uuid.uuid4()}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_indice_clearance_scoped_returns_404(client_with_conn) -> None:
    """(Alta, cierre RT-15) No-read-up (RF-SEG-08): aunque el permiso esté
    concedido, un expediente cuyo `nivel_seguridad` supere el clearance del
    llamante no es visible — 404 'expediente_not_found' ANTES de servir nada
    del índice (mismo criterio que `GET /{id}`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso concedido
    exp_id = uuid.uuid4()
    # resolve_caller_clearance: sin fila RBAC -> PUBLICA(1); get_expediente_by_id
    # con nivel_seguridad > clearance no matchea el predicado -> None.
    mock_conn.fetchrow.side_effect = [None, None]
    mock_conn.fetch.return_value = []
    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"


@pytest.mark.asyncio
async def test_indice_xml_requires_permission(client_with_conn) -> None:
    """(Alta, cierre RT-15) `GET /indice.xml` ahora exige USUA_PERM_EXPEDIENTE."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get(
        f"/api/v1/expedientes/{uuid.uuid4()}/indice.xml", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_indice_xml_returns_xml_when_visible(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        None,
        make_expediente_record(exp_id=exp_id),
        _indice_row(version=1, num=0),
    ]
    mock_conn.fetch.return_value = []
    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice.xml", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")


@pytest.mark.asyncio
async def test_indice_versions_requires_permission(client_with_conn) -> None:
    """(Alta, cierre RT-15) `GET /indice/versions` ahora exige USUA_PERM_EXPEDIENTE."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get(
        f"/api/v1/expedientes/{uuid.uuid4()}/indice/versions", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_indice_versions_lists_when_visible(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    # fetchval: check_permission, luego COUNT(*) dentro de list_versions.
    mock_conn.fetchval.side_effect = [True, 1]
    mock_conn.fetchrow.side_effect = [None, make_expediente_record(exp_id=exp_id)]
    mock_conn.fetch.side_effect = [[], [_indice_row(version=1, num=0)]]
    resp = await client.get(
        f"/api/v1/expedientes/{exp_id}/indice/versions", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "1"
    assert len(resp.json()) == 1


def test_item_valor_huella_from_link_hash() -> None:
    """El content_hash del vínculo se usa como valor_huella del item del índice.

    (RT-15, E15 Inc.3) `_items_from` también lee formato/tamano_bytes/folios
    del vínculo — el objeto de dominio debe exponerlos, aunque sea NULL. (E15
    perfil v3) también hereda nivel_seguridad del EXPEDIENTE y el estado del
    vínculo — se usa el dataclass real `ExpedienteRadicado` (no un stub
    dinámico) para que la prueba no quede desalineada de sus campos.
    """
    from app.models.archive import Expediente, ExpedienteRadicado
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
        nivel_seguridad=2,
        radicados=[
            ExpedienteRadicado(
                expediente_id=rad["expediente_id"],
                radicado_id=rad["radicado_id"],
                tracking_number=rad["tracking_number"],
                added_at=NOW,
                content_hash=h,
                formato=None,
                tamano_bytes=None,
                folios=None,
                orden_documental=1,
                estado="vinculado",
            )
        ],
    )
    items = IndexService.__new__(IndexService)._items_from(exp)
    assert items[0].valor_huella == h
    assert items[0].formato is None
    assert items[0].tamano_bytes is None
    assert items[0].folios is None
    assert items[0].nivel_seguridad == 2
    assert items[0].estado == "vinculado"


@pytest.mark.asyncio
async def test_link_to_closed_expediente_rejected(client_with_conn) -> None:
    """No se pueden añadir documentos a un expediente cerrado (inmutabilidad)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="closed")
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted

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
    # fetchrow: resolve_caller_clearance (None -> PUBLICA), get_expediente(exp),
    # get_current(indice); fetch: radicados, items — cierre RT-15: `verify`
    # ahora resuelve clearance + expediente ANTES del índice (no-read-up).
    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [item]]

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
    mock_conn.fetchrow.side_effect = [None, exp, _indice_row(version=2, num=1)]
    mock_conn.fetch.side_effect = [[link], [item]]

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
    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [item]]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["huella_indice_ok"] is False
    assert body["valido"] is False
    # Fixity por documento sigue OK — el fallo es específico de la huella del índice.
    assert body["documentos"][0]["coincide"] is True


async def _expected_rebuild_hash(exp_row: dict, link_rows: list[dict]) -> str:
    """Computa el content_hash (v4) que produciría `IndexService.rebuild_index`
    para este par (expediente, vínculos) — reutiliza las MISMAS piezas del
    servicio bajo prueba (`_items_from`/`_data`/`build_xml`/`content_hash`),
    no una reimplementación manual del canon. v4 hashea el XML COMPLETO
    (cabecera incluida), así que el "expected" ya no puede construirse a mano
    a partir de un solo `IndiceItemData` como en v1-v3 — debe pasar por
    `_data` (nivel_seguridad, productora_presente, pista_acceso_desde,
    contexto TRD) igual que el código real."""
    from app.models.archive import Expediente
    from app.repositories.archive_repository import _row_to_expediente, _row_to_radicado
    from app.services.index_service import IndexService

    exp: Expediente = _row_to_expediente(exp_row)
    exp.radicados = [_row_to_radicado(r) for r in link_rows]
    svc = IndexService.__new__(IndexService)
    items = svc._items_from(exp)
    data = await svc._data(exp, items)
    return content_hash(build_xml(data))


@pytest.mark.asyncio
async def test_rebuild_idempotent_when_unchanged(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    added_at = NOW
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, tracking_number="2024-E-001", added_at=added_at)
    # content_hash del único documento (orden 1, sin huella) que el índice ya
    # tiene — v4: el hash cubre el XML completo (cabecera incluida), así que
    # se computa con las mismas piezas del servicio bajo prueba.
    expected_ch = await _expected_rebuild_hash(exp, [link])
    # get_expediente_by_id: fetchrow(exp) + fetch(radicados); get_current: fetchrow(indice);
    # caller_clearance (M2, RF-SEG-08 — None → PUBLICA(1), suficiente porque el
    # expediente es nivel_seguridad=1 por defecto); get_expediente_by_id de nuevo
    # (redacción de la respuesta en el handler, ver `routers/indice.py::rebuild_indice`)
    mock_conn.fetchrow.side_effect = [exp, _indice_row(version=3, content=expected_ch), None, exp]
    mock_conn.fetch.return_value = [link]

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
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(
        radicado_id=rad_id,
        content_hash=h,
        formato="application/pdf",
        tamano_bytes=1024,
        folios=5,
        added_at=added_at,
    )
    expected_ch = await _expected_rebuild_hash(exp, [link])
    # + caller_clearance (M2 — None → PUBLICA(1)) + get_expediente_by_id de nuevo
    # (redacción de la respuesta, ver `routers/indice.py::rebuild_indice`)
    mock_conn.fetchrow.side_effect = [exp, _indice_row(version=1, content=expected_ch), None, exp]
    mock_conn.fetch.return_value = [link]

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
    exp = make_expediente_record(exp_id=exp_id)
    # El índice vigente (version=3) fue generado ANTES de declarar folios.
    link_sin_folios = make_radicado_link_record(radicado_id=rad_id, content_hash=h, added_at=added_at)
    ch_sin_folios = await _expected_rebuild_hash(exp, [link_sin_folios])
    # get_expediente_by_id, get_current, e insert_version (RETURNING) — en ese orden;
    # + caller_clearance (M2 — None → PUBLICA(1)) + get_expediente_by_id de nuevo
    # (redacción de la respuesta, ver `routers/indice.py::rebuild_indice`)
    mock_conn.fetchrow.side_effect = [
        exp,
        _indice_row(version=3, content=ch_sin_folios),
        _indice_row(version=4),
        None,
        exp,
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
    assert body["xsd_version"] == "v4"

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
    assert '"xsd_version": "v4"' in regen_payload


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
    # regenerate: get_expediente_by_id -> exp_closed (dispara el 409
    # `expediente_not_open`); el handler intercepta esa excepción en su
    # `except` y evalúa `_puede_leer()` para decidir si propagarla o
    # sustituirla por el ack acotado (M2 hallazgo #1) -> caller_clearance
    # (None: sin fila RBAC -> PUBLICA(1)) + get_expediente_by_id de nuevo
    # (exp_closed, nivel_seguridad=1 <= PUBLICA(1) -> SÍ puede leer -> el 409
    # se propaga tal cual, como espera este test).
    mock_conn.fetchrow.side_effect = [exp_closed, None, exp_closed]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_open"


# ---------------------------------------------------------------------------
# M2 (RF-SEG-08, resuelto) — `POST /indice/rebuild` SIEMPRE regenera
# (`IndexService.regenerate` resuelve existencia/estado con
# `CLEARANCE_SIN_RESTRICCION`, sin cambios) pero la RESPUESTA se acota en el
# HANDLER (`routers/indice.py::rebuild_indice`) cuando el llamante no puede
# LEER un expediente CLASIFICADA (3) — ver `RebuildResult`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_redacted_when_caller_cannot_read_classified(client_with_conn) -> None:
    """M2: con clearance PUBLICA (1) sobre un expediente CLASIFICADA (3), el
    rebuild SIGUE regenerando/persistiendo el índice, pero la respuesta acota
    `version`/`xml_sha256`/`num_documentos` a `None` y `unchanged` a `False`
    (valor fijo, no el resultado real). `xsd_version` TAMBIÉN se redacta a
    `None` (hallazgo #1, ver `RebuildResult.xsd_version`): antes se
    conservaba el valor real aquí, mientras que la rama "no-lector + closed/
    transferred" (`test_rebuild_redacted_when_caller_cannot_read_classified_closed`)
    ya devolvía `None` -- esa asimetría era, por sí sola, un oráculo de ciclo
    de vida. Ambos casos deben ser indistinguibles."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    added_at = NOW
    exp = make_expediente_record(exp_id=exp_id, nivel_seguridad=3)
    link = make_radicado_link_record(radicado_id=rad_id, tracking_number="2024-E-001", added_at=added_at)
    expected_ch = await _expected_rebuild_hash(exp, [link])
    # get_expediente_by_id (regenerate) + get_current (índice vigente,
    # unchanged) + caller_clearance (M2 — None → PUBLICA(1), insuficiente
    # para nivel_seguridad=3) + get_expediente_by_id de nuevo (redacción de
    # la respuesta en el handler).
    mock_conn.fetchrow.side_effect = [exp, _indice_row(version=3, content=expected_ch), None, exp]
    mock_conn.fetch.return_value = [link]

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] is None
    assert body["xml_sha256"] is None
    assert body["num_documentos"] is None
    # Redactado a un valor FIJO, no al resultado real de `regenerate` (que
    # aquí habría sido `unchanged=True`) — así lo especifica `RebuildResult`.
    assert body["unchanged"] is False
    # Hallazgo #1: `xsd_version` también se redacta (antes era "v2", el valor
    # real) para ser indistinguible de la rama "no-lector + closed".
    assert body["xsd_version"] is None


@pytest.mark.asyncio
async def test_rebuild_full_response_when_caller_can_read_classified(client_with_conn) -> None:
    """M2 (contraparte): con clearance CLASIFICADA (3) — suficiente para leer
    el mismo expediente — la respuesta trae los valores REALES de `regenerate`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    added_at = NOW
    exp = make_expediente_record(exp_id=exp_id, nivel_seguridad=3)
    link = make_radicado_link_record(radicado_id=rad_id, tracking_number="2024-E-001", added_at=added_at)
    expected_ch = await _expected_rebuild_hash(exp, [link])
    mock_conn.fetchrow.side_effect = [
        exp,
        _indice_row(version=3, content=expected_ch),
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
        exp,
    ]
    mock_conn.fetch.return_value = [link]

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unchanged"] is True
    assert body["version"] == 3
    # `xml_sha256` (unchanged) es el de la fila persistida (`_indice_row`,
    # sobre el XML placeholder almacenado), no `expected_ch` (el content_hash
    # que decide si hubo cambios) — mismo criterio que `RebuildResult` real.
    assert body["xml_sha256"] == xml_sha256("<indice/>")
    assert body["num_documentos"] == 1


@pytest.mark.asyncio
async def test_rebuild_redacted_when_caller_cannot_read_classified_closed(client_with_conn) -> None:
    """M2 hallazgo #1 (auditoría M2, cierre): un no-lector (clearance PUBLICA=1)
    que sondea un expediente CLASIFICADA (3) **closed** por su UUID NO debe
    recibir el 409 `expediente_not_open` (con el estado LITERAL en el
    cuerpo) que `IndexService.regenerate`/`assert_open` lanzan internamente
    -- eso sería un oráculo de ciclo de vida (revelaría "closed" a quien no
    puede leer el expediente). El handler lo intercepta en su `except` y
    responde el MISMO ack acotado que la rama "no-lector + open"
    (`test_rebuild_redacted_when_caller_cannot_read_classified`, arriba):
    ambos cuerpos deben ser byte-a-byte idénticos, incl. `xsd_version`
    (`None` en ambos -- ver `RebuildResult.xsd_version`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    exp_closed_classificada = make_expediente_record(
        exp_id=exp_id, exp_status="closed", nivel_seguridad=3
    )
    # regenerate: get_expediente_by_id -> exp (closed) dispara el 409
    # `expediente_not_open` DENTRO de `assert_open`, antes de tocar el
    # índice (`get_current` nunca se llama). El handler intercepta esa
    # excepción en su `except` y evalúa `_puede_leer()`: caller_clearance
    # (None: sin fila RBAC -> PUBLICA(1)) + get_expediente_by_id de nuevo
    # (mismo exp closed/CLASIFICADA, nivel_seguridad=3 > PUBLICA(1) -> NO
    # puede leer) -> el 409 se sustituye por el ack acotado.
    mock_conn.fetchrow.side_effect = [
        exp_closed_classificada,
        None,
        exp_closed_classificada,
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] is None
    assert body["xml_sha256"] is None
    assert body["num_documentos"] is None
    assert body["unchanged"] is False
    assert body["xsd_version"] is None

    # Nada se persistió: `assert_open` abortó antes de llegar a `get_current`/
    # `insert_version` -- ningún INSERT en `expediente_indice`.
    insert_calls = [
        c.args
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_indice" in c.args[0]
    ]
    assert len(insert_calls) == 0


@pytest.mark.asyncio
async def test_rebuild_open_and_closed_no_lector_acks_are_indistinguishable(client_with_conn) -> None:
    """M2 hallazgo #1: el ack acotado que recibe un no-lector es EXACTAMENTE
    el mismo, sea cual sea el estado real del expediente CLASIFICADA
    (open/closed) -- comparación DIRECTA de los dos cuerpos de respuesta
    HTTP reales (mismo cliente/mock, reconfigurado entre llamadas), en vez
    de solo repetir las mismas aserciones campo a campo en dos tests
    separados: si alguno de los dos caminos volviera a filtrar un campo
    (p.ej. `xsd_version`) este test lo detecta aunque las aserciones
    individuales de `test_rebuild_redacted_when_caller_cannot_read_classified`
    y `test_rebuild_redacted_when_caller_cannot_read_classified_closed` se
    desincronizaran por error."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open", nivel_seguridad=3)
    link = make_radicado_link_record(radicado_id=rad_id, tracking_number="2024-E-001", added_at=NOW)
    expected_ch = await _expected_rebuild_hash(exp_open, [link])

    # 1) no-lector + open (unchanged): mismo montaje que
    # `test_rebuild_redacted_when_caller_cannot_read_classified`.
    mock_conn.fetchrow.side_effect = [
        exp_open,
        _indice_row(version=3, content=expected_ch),
        None,
        exp_open,
    ]
    mock_conn.fetch.return_value = [link]
    resp_open = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp_open.status_code == 200

    # 2) no-lector + closed: mismo expediente/nivel_seguridad, solo cambia el
    # status -- mismo montaje que
    # `test_rebuild_redacted_when_caller_cannot_read_classified_closed`.
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed", nivel_seguridad=3)
    mock_conn.fetchrow.side_effect = [exp_closed, None, exp_closed]
    mock_conn.fetch.return_value = []
    resp_closed = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp_closed.status_code == 200

    assert resp_open.json() == resp_closed.json()


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

    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [_item_row(rad_id, h)]]

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

    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [_item_row(rad_id, h)]]

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

    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [_item_row(rad_id, h)]]

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

    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [_item_row(rad_id, h)]]

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

    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [_item_row(rad_id, h)]]

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

    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [_item_row(rad_id, h)]]

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


# ---------------------------------------------------------------------------
# E15 finalización RT-15 — perfil v3 "CONFORME EXTENDIDO"
# (orden_documental estable + soft-delete + listaControlAcceso + <contexto>)
# ---------------------------------------------------------------------------

_IDX_NS = {"idx": NS}


def _documento_elements(xml: str) -> list:
    """Parsea el XML y devuelve los <documento> en el orden en que quedaron
    serializados (equivalente a como los vería un consumidor externo)."""
    root = ET.fromstring(xml)
    return root.findall("idx:documentos/idx:documento", _IDX_NS)


def test_build_xml_v3_excluded_document_preserves_orden_and_emits_hole() -> None:
    """(a) Vincular A,B,C -> orden 1,2,3; excluir B (soft-delete) NO renumera
    a C (sigue en orden=3) ni comprime el hueco de B — B se emite como
    <documento excluido="true"> conservando su orden_documental original, con
    <fechaExclusion>/<causalExclusion>. El orden de EMISIÓN es por ordinal,
    no por el orden en que llegaron los items a `build_xml` (aquí, a propósito,
    fuera de orden: C, B, A)."""
    fecha_exclusion = NOW
    id_a, id_b, id_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    items = [
        IndiceItemData(document_id=id_c, orden=3, titulo="C"),
        IndiceItemData(
            document_id=id_b,
            orden=2,
            titulo="B",
            estado="excluido",
            fecha_exclusion=fecha_exclusion,
            causal_exclusion="error de captura",
        ),
        IndiceItemData(document_id=id_a, orden=1, titulo="A"),
    ]
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2026-0001",
        name="Contrato",
        serie_id=None,
        fecha_creacion=NOW,
        items=items,
    )
    xml = build_xml(data)
    docs = _documento_elements(xml)
    identificadores = [d.findtext("idx:identificador", namespaces=_IDX_NS) for d in docs]
    assert identificadores == [id_a, id_b, id_c]  # por ordinal, no por orden de entrada
    ordenes = [d.findtext("idx:orden", namespaces=_IDX_NS) for d in docs]
    assert ordenes == ["1", "2", "3"]  # C NO se renumera a 2
    excluido_attrs = [d.get("excluido") for d in docs]
    assert excluido_attrs == [None, "true", None]  # ausente en vigentes, "true" solo en B
    doc_b = docs[1]
    assert doc_b.findtext("idx:causalExclusion", namespaces=_IDX_NS) == "error de captura"
    assert doc_b.findtext("idx:fechaExclusion", namespaces=_IDX_NS) == fecha_exclusion.isoformat()
    # Los vigentes no llevan fechaExclusion/causalExclusion.
    assert docs[0].findtext("idx:fechaExclusion", namespaces=_IDX_NS) is None
    assert count_vigentes(items) == 2  # num_documentos solo cuenta A y C


def test_items_from_preserves_stable_orden_after_exclusion() -> None:
    """(a) `_items_from` usa orden_documental ESTABLE de cada vínculo — no un
    enumerate temporal — así que un documento excluido en medio de la lista
    NO desplaza el orden de los que le siguen."""
    from app.models.archive import Expediente, ExpedienteRadicado
    from app.services.index_service import IndexService

    a_id, b_id, c_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
        nivel_seguridad=1,
        radicados=[
            ExpedienteRadicado(
                expediente_id=uuid.uuid4(),
                radicado_id=a_id,
                tracking_number="A",
                added_at=NOW,
                orden_documental=1,
                estado="vinculado",
            ),
            ExpedienteRadicado(
                expediente_id=uuid.uuid4(),
                radicado_id=b_id,
                tracking_number="B",
                added_at=NOW,
                orden_documental=2,
                estado="excluido",
                excluido_at=NOW,
                excluido_causal="duplicado",
            ),
            ExpedienteRadicado(
                expediente_id=uuid.uuid4(),
                radicado_id=c_id,
                tracking_number="C",
                added_at=NOW,
                orden_documental=3,
                estado="vinculado",
            ),
        ],
    )
    items = IndexService.__new__(IndexService)._items_from(exp)
    by_id = {it.document_id: it for it in items}
    assert by_id[str(a_id)].orden == 1
    assert by_id[str(b_id)].orden == 2
    assert by_id[str(b_id)].estado == "excluido"
    assert by_id[str(b_id)].causal_exclusion == "duplicado"
    assert by_id[str(c_id)].orden == 3  # NO renumerado a 2 tras excluir B
    assert count_vigentes(items) == 2


@pytest.mark.asyncio
async def test_unlink_radicado_repo_soft_deletes_not_deletes() -> None:
    """(b) `ArchiveRepository.unlink_radicado` es un UPDATE (soft-delete) — ya
    NO un DELETE físico — y persiste el causal opcional aportado por quien
    excluye (Acuerdo AGN 001/2024 art. 4.3.2.3)."""
    from app.repositories.archive_repository import ArchiveRepository

    mock_conn = AsyncMock()
    mock_conn.execute.return_value = "UPDATE 1"
    repo = ArchiveRepository(mock_conn, "demo")

    result = await repo.unlink_radicado(uuid.uuid4(), uuid.uuid4(), causal="error de captura")

    assert result is True
    sql, *params = mock_conn.execute.call_args_list[-1].args
    assert "UPDATE expediente_radicados" in sql
    assert "SET estado = 'excluido'" in sql
    assert "DELETE" not in sql
    assert params[-1] == "error de captura"


@pytest.mark.asyncio
async def test_unlink_radicado_repo_already_excluded_is_noop(client_with_conn) -> None:
    """Idempotente: excluir dos veces el mismo vínculo devuelve 0 filas la
    segunda vez (el `WHERE estado = 'vinculado'` no matchea nada) — el
    servicio lo traduce a 404, igual que el DELETE físico anterior."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    mock_conn.execute.return_value = "UPDATE 0"
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetchrow.return_value = exp_record
    mock_conn.fetch.return_value = []

    resp = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "radicado_link_not_found"


@pytest.mark.asyncio
async def test_expediente_response_excludes_soft_deleted_radicados(client_with_conn) -> None:
    """(c) `GET /expedientes/{id}` solo lista los radicados VIGENTES — un
    excluido (soft-delete) no debe aparecer en la vista de conjunto ACTIVO,
    aunque el repositorio traiga ambos para que el índice pueda ver el hueco."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    vinculado_id = uuid.uuid4()
    vinculado = make_radicado_link_record(
        expediente_id=exp_id, radicado_id=vinculado_id, orden_documental=1, estado="vinculado"
    )
    excluido = make_radicado_link_record(
        expediente_id=exp_id,
        radicado_id=uuid.uuid4(),
        orden_documental=2,
        estado="excluido",
        excluido_at=NOW,
        excluido_causal="duplicado",
    )
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=exp_id)
    mock_conn.fetch.return_value = [vinculado, excluido]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["radicados"]) == 1
    assert body["radicados"][0]["radicado_id"] == str(vinculado_id)


@pytest.mark.asyncio
async def test_foliado_excludes_soft_deleted_radicados(client_with_conn) -> None:
    """(c) `GET /expedientes/{id}/foliado` tampoco cuenta folio a un radicado
    excluido — solo los VIGENTES entran en la foliación (E02)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    vinculado_id = uuid.uuid4()
    vinculado = make_radicado_link_record(
        expediente_id=exp_id, radicado_id=vinculado_id, orden_documental=1, estado="vinculado"
    )
    excluido = make_radicado_link_record(
        expediente_id=exp_id, radicado_id=uuid.uuid4(), orden_documental=2, estado="excluido"
    )
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=exp_id)
    mock_conn.fetch.return_value = [vinculado, excluido]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/foliado", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_folios"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["radicado_id"] == str(vinculado_id)
    assert body["items"][0]["folio"] == 1


@pytest.mark.asyncio
async def test_foliado_clearance_scoped_returns_404(client_with_conn) -> None:
    """(Alta, cierre RT-15) No-read-up (RF-SEG-08): `GET /foliado` ya exigía
    USUA_PERM_EXPEDIENTE, pero no contrastaba el clearance del llamante contra
    `nivel_seguridad` — un expediente por encima del clearance debe ser 404,
    igual que `GET /{id}`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso concedido
    exp_id = uuid.uuid4()
    # resolve_caller_clearance (None -> PUBLICA) + get_expediente_by_id (None:
    # nivel_seguridad del expediente supera la clearance del llamante).
    mock_conn.fetchrow.side_effect = [None, None]
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/foliado", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"


@pytest.mark.asyncio
async def test_verify_clearance_scoped_returns_404(client_with_conn) -> None:
    """(Alta, cierre RT-15) No-read-up (RF-SEG-08): `GET /indice/verify` ya
    exigía USUA_PERM_EXPEDIENTE, pero resolvía el expediente sin restricción
    de clearance — una ruta alterna para leer (indirectamente, vía
    huellas/estado de firma) un expediente CLASIFICADA sin la clearance
    debida."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso concedido
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [None, None]
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/expedientes/{exp_id}/indice/verify", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"


def test_build_xml_v4_lista_control_acceso_moves_to_cabecera() -> None:
    """(v4, R4) <listaControlAcceso> se emite UNA sola vez en la CABECERA
    (nivel del EXPEDIENTE) — ya NO por-documento (duplicado N veces sin
    necesidad, v3). `<politicaAcceso>` acompaña con el modelo/permiso/
    clearance mínimo ESTABLE (misma fuente que `nivelSeguridad`, contrato R4:
    no se deriva aparte)."""
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2026-0001",
        name="Contrato",
        serie_id=None,
        fecha_creacion=NOW,
        items=[IndiceItemData(document_id=str(uuid.uuid4()), orden=1, titulo="A")],
        nivel_seguridad=2,
    )
    xml = build_xml(data)
    root = ET.fromstring(xml)
    lca = root.find("idx:cabecera/idx:listaControlAcceso", _IDX_NS)
    assert lca is not None
    nivel_el = lca.find("idx:nivelSeguridad", _IDX_NS)
    assert nivel_el.get("codigo") == "2"
    assert nivel_el.text == "RESERVADA"
    politica_el = lca.find("idx:politicaAcceso", _IDX_NS)
    assert politica_el is not None
    assert politica_el.get("modelo") == "no-read-up"
    assert politica_el.get("requiere") == "USUA_PERM_EXPEDIENTE"
    assert politica_el.get("clearanceMinimo") == "2"  # misma fuente que nivelSeguridad
    # Ya NO se duplica por-documento (v3 -> v4).
    doc = _documento_elements(xml)[0]
    assert doc.find("idx:listaControlAcceso", _IDX_NS) is None


def test_build_xml_v4_pista_auditoria_and_conformidad_markers() -> None:
    """(v4, R3) `<pistaAuditoria>` atesta (referencia estable, sin contenido
    mutable) que el historial de acceso es verificable vía `public.audit_log`
    — NUNCA incrusta timestamps/conteos que romperían la idempotencia del
    hash. `<conformidad>` es el marcador HONESTO per-instancia (RF-FIR-15):
    refleja los valores que le pasó `IndexService._data`, no una constante."""
    from datetime import date

    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2026-0001",
        name="Contrato",
        serie_id=None,
        fecha_creacion=NOW,
        items=[],
        productora_presente=False,
        pista_acceso_desde=date(2026, 1, 1),
    )
    xml = build_xml(data)
    root = ET.fromstring(xml)
    pista_el = root.find("idx:cabecera/idx:pistaAuditoria", _IDX_NS)
    assert pista_el is not None
    assert pista_el.get("disponible") == "true"
    assert pista_el.get("registro") == "public.audit_log"
    assert pista_el.get("objetoRef") == "EXP-2026-0001"
    conformidad_el = root.find("idx:cabecera/idx:conformidad", _IDX_NS)
    assert conformidad_el is not None
    assert conformidad_el.get("perfil") == "v4"
    assert conformidad_el.get("productoraPresente") == "false"
    assert conformidad_el.get("pistaAccesoDesde") == "2026-01-01"


def test_build_xml_v4_dependencia_productora_emitted_only_when_both_present() -> None:
    """(v4, R1) `<dependenciaProductora>` solo se emite cuando AMBOS
    codigo/nombre están declarados — sin centinela para filas legacy con solo
    uno de los dos (que en la práctica no puede ocurrir, CHECK de pareja en
    BD, pero el builder no debe asumirlo)."""
    common = dict(expediente_id=str(uuid.uuid4()), code="EXP-2026-0001", name="Contrato", serie_id=None, fecha_creacion=NOW)
    con_productora = IndiceItemData(
        document_id=str(uuid.uuid4()),
        orden=1,
        dependencia_productora_codigo=100,
        dependencia_productora_nombre="Secretaría General",
    )
    sin_productora = IndiceItemData(document_id=str(uuid.uuid4()), orden=1)

    xml_con = build_xml(IndiceData(**common, items=[con_productora]))
    doc_con = _documento_elements(xml_con)[0]
    dep_el = doc_con.find("idx:dependenciaProductora", _IDX_NS)
    assert dep_el is not None
    assert dep_el.get("codigo") == "100"
    assert dep_el.text == "Secretaría General"

    xml_sin = build_xml(IndiceData(**common, items=[sin_productora]))
    doc_sin = _documento_elements(xml_sin)[0]
    assert doc_sin.find("idx:dependenciaProductora", _IDX_NS) is None


def test_build_xml_v3_contexto_with_serie_subserie_and_fecha_primer_uso() -> None:
    """(e) <contexto> emite serie/subserie por NOMBRE (no el UUID, que sigue
    en <expediente><serie>) y fechaPrimerUso."""
    fecha = NOW
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2026-0001",
        name="Contrato",
        serie_id=str(uuid.uuid4()),
        fecha_creacion=NOW,
        items=[],
        serie_nombre="Contratos",
        subserie_nombre="Prestación de servicios",
        fecha_primer_uso=fecha,
    )
    xml = build_xml(data)
    root = ET.fromstring(xml)
    ctx = root.find("idx:cabecera/idx:contexto", _IDX_NS)
    assert ctx is not None
    assert ctx.findtext("idx:serie", namespaces=_IDX_NS) == "Contratos"
    assert ctx.findtext("idx:subserie", namespaces=_IDX_NS) == "Prestación de servicios"
    assert ctx.findtext("idx:fechaPrimerUso", namespaces=_IDX_NS) == fecha.isoformat()


def test_build_xml_v3_contexto_omitted_when_no_data() -> None:
    """(e) Sin serie TRD asignada ni documentos vinculados aún, <contexto> se
    omite por completo — no sugerir un contexto vacío declarado."""
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2026-0001",
        name="Contrato",
        serie_id=None,
        fecha_creacion=NOW,
        items=[],
    )
    xml = build_xml(data)
    root = ET.fromstring(xml)
    assert root.find("idx:cabecera/idx:contexto", _IDX_NS) is None


@pytest.mark.asyncio
async def test_contexto_series_resolves_parent_as_serie_and_own_as_subserie() -> None:
    """(e) Si la serie TRD del expediente tiene padre (`parent_id`), el
    expediente está clasificado en una SUBSERIE: `serie` = nombre del padre,
    `subserie` = nombre propio."""
    from app.models.archive import TrdSerie
    from app.services.index_service import IndexService

    parent = TrdSerie(
        id=uuid.uuid4(),
        code="100",
        name="Contratos",
        description=None,
        retention_years=5,
        total_retention=10,
        disposition="CT",
        is_active=True,
        created_at=NOW,
        parent_id=None,
    )
    child = TrdSerie(
        id=uuid.uuid4(),
        code="100.1",
        name="Prestación de servicios",
        description=None,
        retention_years=5,
        total_retention=10,
        disposition="CT",
        is_active=True,
        created_at=NOW,
        parent_id=parent.id,
    )
    svc = IndexService.__new__(IndexService)
    svc._archive = AsyncMock()
    svc._archive.get_trd_serie_by_id.side_effect = lambda sid: (
        child if sid == child.id else parent if sid == parent.id else None
    )

    serie_nombre, subserie_nombre = await svc._contexto_series(child.id)
    assert serie_nombre == "Contratos"
    assert subserie_nombre == "Prestación de servicios"


@pytest.mark.asyncio
async def test_contexto_series_top_level_has_no_subserie() -> None:
    """(e) Una serie TRD de primer nivel (sin `parent_id`) solo aporta
    `serie` — `subserie` queda None (se omite del XML)."""
    from app.models.archive import TrdSerie
    from app.services.index_service import IndexService

    serie = TrdSerie(
        id=uuid.uuid4(),
        code="100",
        name="Contratos",
        description=None,
        retention_years=5,
        total_retention=10,
        disposition="CT",
        is_active=True,
        created_at=NOW,
        parent_id=None,
    )
    svc = IndexService.__new__(IndexService)
    svc._archive = AsyncMock()
    svc._archive.get_trd_serie_by_id.return_value = serie

    serie_nombre, subserie_nombre = await svc._contexto_series(serie.id)
    assert serie_nombre == "Contratos"
    assert subserie_nombre is None


@pytest.mark.asyncio
async def test_data_productora_presente_false_sin_documentos_vigentes() -> None:
    """(hallazgo Baja, cierre v4) Un expediente SIN documentos vigentes
    (versión 0 recién creada, o todos excluidos) debe emitir
    `productoraPresente="false"` — `all()` sobre una lista vacía da
    vacuamente `True`, lo que proyectaría conformidad positiva sobre un
    conjunto documental inexistente. Solo debe ser `True` cuando hay AL MENOS
    UN vigente y todos declararon productora."""
    from app.models.archive import Expediente
    from app.services.index_service import IndexService

    exp = Expediente(
        id=uuid.uuid4(),
        code="EXP-2026-0001",
        name="Contrato",
        description=None,
        trd_serie_id=None,
        status="open",
        opened_by=None,
        opened_at=NOW,
        closed_at=None,
        updated_at=NOW,
        radicados=[],
    )
    svc = IndexService.__new__(IndexService)

    data = await svc._data(exp, items=[])
    assert data.productora_presente is False

    xml = build_xml(data)
    root = ET.fromstring(xml)
    conformidad_el = root.find("idx:cabecera/idx:conformidad", _IDX_NS)
    assert conformidad_el.get("productoraPresente") == "false"


@pytest.mark.asyncio
async def test_verify_v2_indice_firmado_sigue_verificando_ok(client_with_conn) -> None:
    """(g) Retrocompat: un índice legado con xsd_version='v2' (creado antes
    del salto a v3, E15 finalización RT-15) y ya firmado sigue verificando OK
    — `verify()` no asume v3 en ningún punto."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    h = "6" * 64
    v2_xml = (
        '<indice xmlns="urn:orpycamcp:indice:v2" xsdVersion="v2">'
        "<cabecera><identificadorSistema>abc</identificadorSistema></cabecera>"
        "<documentos/></indice>"
    )
    indice = _indice_row_with_firma(firma_id)
    indice["xsd_version"] = "v2"
    indice["xml"] = v2_xml
    indice["xml_sha256"] = xml_sha256(v2_xml)
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)

    mock_conn.fetchrow.side_effect = [None, exp, indice]
    mock_conn.fetch.side_effect = [[link], [_item_row(rad_id, h)]]

    from app.clients.signature_client import VerifyResult

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value=v2_xml),
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


# ---------------------------------------------------------------------------
# Pista de acceso (E15 perfil v4, R3-a) — GET /indice y GET /indice.xml dejan
# traza en audit_log SOLO para canales de un usuario real; 'system' (default,
# canal ausente) no audita una consulta.
# ---------------------------------------------------------------------------


def _audit_inserts(mock_conn, action: str) -> list:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == action
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("canal", ["api", "ui", "mcp"])
async def test_get_indice_audits_read_access_for_user_channels(client_with_conn, canal) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 + advisory lock/prev_hash de audit.append
    exp_id = uuid.uuid4()
    exp = make_expediente_record(exp_id=exp_id)
    mock_conn.fetchrow.side_effect = [None, exp, _indice_row(version=1, num=0)]
    mock_conn.fetch.side_effect = [[], []]

    resp = await client.get(
        f"/api/v1/expedientes/{exp_id}/indice",
        headers={**TENANT_HEADERS, "X-Canal": canal},
    )
    assert resp.status_code == 200
    calls = _audit_inserts(mock_conn, "archive.indice_consultado")
    assert len(calls) == 1
    assert calls[0][6] == "expediente_indice"  # object_type
    assert calls[0][7] == exp["code"]  # object_ref


@pytest.mark.asyncio
async def test_get_indice_does_not_audit_system_channel(client_with_conn) -> None:
    """Sin `X-Canal` (o `system` explícito) — regeneración/verificación
    interna — no debe dejar pista de acceso: no es una lectura de un usuario
    real (evita amplificar cada rebuild/verify como si alguien "leyera" el
    índice, ver `IndexService._audit_read_access`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    exp = make_expediente_record(exp_id=exp_id)
    mock_conn.fetchrow.side_effect = [None, exp, _indice_row(version=1, num=0)]
    mock_conn.fetch.side_effect = [[], []]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert _audit_inserts(mock_conn, "archive.indice_consultado") == []


@pytest.mark.asyncio
async def test_get_indice_xml_audits_read_access(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    exp = make_expediente_record(exp_id=exp_id)
    mock_conn.fetchrow.side_effect = [None, exp, _indice_row(version=1, num=0)]
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/expedientes/{exp_id}/indice.xml",
        headers={**TENANT_HEADERS, "X-Canal": "api"},
    )
    assert resp.status_code == 200
    assert len(_audit_inserts(mock_conn, "archive.indice_consultado")) == 1


@pytest.mark.asyncio
async def test_index_service_audit_read_access_does_not_write_without_user_id() -> None:
    """(hallazgo Baja, cierre v4) `IndexService._audit_read_access` con canal
    'api' pero `user_id=None` NO escribe `audit_log` — espejo de
    `ArchiveService._audit_read_access` (R3-a exige un actor identificado, no
    `actor=None`). Se prueba el método directamente: en el router
    (`indice.py`) este estado es inalcanzable vía HTTP porque
    `require_permission("USUA_PERM_EXPEDIENTE")` ya exige un `X-User-Id`
    resoluble (fail-closed en `check_permission`); la guarda debe sostenerse
    igual si `IndexService` se invoca directamente."""
    from app.services.index_service import IndexService

    mock_conn = AsyncMock()
    svc = IndexService(mock_conn, "demo")

    await svc._audit_read_access("EXP-2024-0001", version=1, user_id=None, canal="api")

    mock_conn.execute.assert_not_called()
