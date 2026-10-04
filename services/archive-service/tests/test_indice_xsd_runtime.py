# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-07-01: el índice se valida contra su XSD ANTES de pedir el sello.

Sellar (XAdES-T) y proteger (WORM) un índice que su propio contrato rechaza es
irreversible. Si no valida: `pendiente_firma`, motivo `xsd_invalido`, asiento, y
`signature-service` NUNCA se llama. La reconciliación firma por su cuenta
(`sign_indice_internal`), así que tiene su propio gate: se prueban los dos caminos.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.signature_client import SignOutcome
from app.core.config import settings
from app.services.index_builder import IndiceData, IndiceItemData, build_xml
from app.services.index_service import IndexService
from app.services.index_xsd import MOTIVO_XSD_INVALIDO, validar_indice_xml

TENANT = "demo"


def _xml_valido() -> str:
    items = [
        IndiceItemData(
            document_id="d1",
            orden=1,
            titulo="oficio",
            tipologia="Oficio",
            fecha_archivado=datetime.now(UTC),
            valor_huella="a" * 64,
            formato="application/pdf",
            tamano_bytes=10,
            folios=2,
        )
    ]
    return build_xml(
        IndiceData(
            expediente_id="e1",
            code="EXP-1",
            name="N",
            serie_id="s1",
            fecha_creacion=datetime.now(UTC),
            items=items,
            fecha_primer_uso=datetime.now(UTC),
        )
    )


def _documento(raiz):
    return raiz.find(".//{urn:orpycamcp:indice:v4}documento")


def _sin_tipologia(xml: str) -> str:
    """El elemento OBLIGATORIO `tipologia` ausente (lo que el índice sin tipología deja)."""
    from lxml import etree

    raiz = etree.fromstring(xml.encode())
    doc = _documento(raiz)
    doc.remove(doc.find("{urn:orpycamcp:indice:v4}tipologia"))
    return etree.tostring(raiz, encoding="unicode")


def _fuera_de_orden(xml: str) -> str:
    """`titulo` antes que `identificador`: viola la secuencia del XSD."""
    from lxml import etree

    raiz = etree.fromstring(xml.encode())
    doc = _documento(raiz)
    ident = doc.find("{urn:orpycamcp:indice:v4}identificador")
    doc.remove(ident)
    doc.insert(1, ident)
    return etree.tostring(raiz, encoding="unicode")


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def test_el_builder_real_valida_contra_el_xsd() -> None:
    """Control positivo: lo que produce `build_xml` pasa (si no, el gate bloquearía todo)."""
    assert validar_indice_xml(_xml_valido()) is None


def test_tipologia_ausente_no_valida() -> None:
    xml = _sin_tipologia(_xml_valido())
    assert "tipologia" not in xml  # el helper hizo lo que dice
    assert validar_indice_xml(xml) is not None


def test_elemento_fuera_de_orden_no_valida() -> None:
    xml = _fuera_de_orden(_xml_valido())
    assert xml != _xml_valido()
    assert validar_indice_xml(xml) is not None


def test_xml_mal_formado_no_valida() -> None:
    assert "mal formado" in (validar_indice_xml("<indice") or "")


def test_namespace_sin_xsd_no_se_juzga() -> None:
    """Las filas anteriores a la numeración de perfiles no tienen XSD: no se bloquean."""
    assert validar_indice_xml('<indice xmlns="urn:orpycamcp:indice:v1"/>') is None


@pytest.mark.asyncio
async def test_cierre_con_xml_invalido_no_pide_el_sello_y_queda_pendiente() -> None:
    conn = _mock_conn()
    indice_id = uuid.uuid4()
    conn.fetchrow.return_value = {
        "id": indice_id,
        "estado": "vigente",
        "xml": _sin_tipologia(_xml_valido()),
        "version": 1,
    }
    svc = IndexService(conn, TENANT)

    with patch(
        "app.clients.signature_client.sign_xml_outcome", new=AsyncMock()
    ) as sellar:
        resultado = await svc.firmar_indice_close(
            uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4())
        )

    assert resultado is None
    sellar.assert_not_awaited()
    marca = [c for c in conn.execute.call_args_list if "reconcile_ultimo_error" in str(c.args[0])]
    assert marca and marca[0].args[2] == MOTIVO_XSD_INVALIDO
    asientos = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(asientos) == 1
    assert asientos[0][5] == "archive.indice_firma_pendiente"
    payload = json.loads(asientos[0][8])
    assert payload["motivo"] == MOTIVO_XSD_INVALIDO and payload["detalle"]


@pytest.mark.asyncio
async def test_reintento_manual_con_xml_invalido_tampoco_sella() -> None:
    conn = _mock_conn()
    conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "estado": "pendiente_firma",
        "xml": _fuera_de_orden(_xml_valido()),
        "version": 1,
    }
    svc = IndexService(conn, TENANT)

    with patch("app.clients.signature_client.sign_xml", new=AsyncMock()) as sellar:
        resultado = await svc.firmar_indice(
            uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4())
        )

    assert resultado is None
    sellar.assert_not_awaited()


@pytest.mark.asyncio
async def test_cierre_con_xml_valido_si_pide_el_sello() -> None:
    """Control del control: el mismo flujo con el XML válido llega a signature-service."""
    conn = _mock_conn()
    conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "estado": "vigente",
        "xml": _xml_valido(),
        "version": 1,
    }
    svc = IndexService(conn, TENANT)

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=None, seal_absent=True)),
    ) as sellar:
        await svc.firmar_indice_close(
            uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4())
        )

    sellar.assert_awaited_once()


@pytest.mark.asyncio
async def test_la_reconciliacion_no_firma_un_xml_invalido(monkeypatch) -> None:
    from app.jobs.indice_reconciliation import _reconciliar_indice

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 8)
    conn = _mock_conn()
    row = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": _sin_tipologia(_xml_valido()),
        "reconcile_intentos": 0,
        "expediente_code": "EXP-2026-0001",
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(),
    ) as sellar:
        resultado = await _reconciliar_indice(conn, TENANT, row)

    assert resultado == "fallido"
    sellar.assert_not_awaited()
    bump = [c for c in conn.execute.call_args_list if "reconcile_intentos = reconcile_intentos + 1" in str(c.args[0])]
    assert bump[0].args[3] == MOTIVO_XSD_INVALIDO
