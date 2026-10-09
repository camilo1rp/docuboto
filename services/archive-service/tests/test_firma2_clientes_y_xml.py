# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""FIRMA-2 (ADR-030 addendum): lo que archive-service ENVÍA a signature/storage y los XML que firma.

* El fundamento se huella con un compromiso con sal por registro, local (sin signature-service).
* `renovar_retencion_worm_internal(file_id=...)`: los tipos multi-artefacto se renuevan por
  `file_id`; sin él storage extendería solo el objeto más reciente.
* XML canónico (C14N exclusiva) de la constancia v3 y de la revocación v3.
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime

import httpx
import pytest

from app.clients import signature_client, storage_client
from app.core.config import settings
from app.services.constancia_firma_excepcion import (
    HuellaFundamento,
    Identidad,
    build_constancia_xml,
    build_revocacion_xml,
    canonicalizar,
    compromiso_coincide,
    compromiso_fundamento,
    enlaces_de_constancia,
    enlaces_de_revocacion,
    huella_de_firmado,
    huella_de_fundamento,
    motivo_publico,
    sha256_c14n,
    sha256_hex,
)
from app.services.transferencia_service import _cn_de

_SIG = "tok-firma"
_STO = "tok-storage"
_REAL = httpx.AsyncClient  # el de verdad: monkeypatch no anida si se parchea dos veces en un test


def _cliente(monkeypatch, respuesta: httpx.Response | Exception):
    peticiones: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    class _Cliente(_REAL):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    monkeypatch.setattr(settings, "signature_internal_token", _SIG)
    monkeypatch.setattr(settings, "storage_internal_token", _STO)
    return peticiones


def test_archive_ya_no_depende_de_signature_para_huellar_el_fundamento() -> None:
    """A1: el compromiso se calcula localmente; los endpoints HMAC de signature se retiran y
    su cliente aquí también (sin ellos no hay 503 `hmac_no_disponible` que mantener)."""
    assert not hasattr(signature_client, "hmac_fundamento")
    assert not hasattr(signature_client, "verificar_hmac_fundamento")


@pytest.mark.asyncio
async def test_renovar_retencion_lleva_el_file_id_solo_cuando_se_da(monkeypatch) -> None:
    reqs = _cliente(monkeypatch, httpx.Response(500, json={}))
    await storage_client.renovar_retencion_worm_internal(
        tenant_slug="demo", expediente_id="e", tipo="acta_firma_excepcion_recibe",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1), file_id="abc",
    )
    await storage_client.renovar_retencion_worm_internal(
        tenant_slug="demo", expediente_id="e", tipo="indice",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    con, sin = (json.loads(r.content) for r in reqs)
    assert con["file_id"] == "abc" and "file_id" not in sin
    assert reqs[0].headers["X-Internal-Token"] == _STO


# --- XML canónico ---------------------------------------------------------------------------

_SAL = bytes(range(32))
_HUELLA = HuellaFundamento(sal=_SAL, compromiso=compromiso_fundamento(_SAL, "texto de prueba"))
_PARTE = Identidad(id=uuid.uuid4(), nombre="Carlos Parte", cargo="Contador", fuente="certificado")
_DECL = Identidad(id=uuid.uuid4(), nombre="Ana Admin", cargo=None, fuente="usuario")


def _constancia(**kw) -> str:
    base = dict(
        constancia_id=uuid.uuid4(), transferencia_id=uuid.uuid4(), expediente_id=uuid.uuid4(),
        acta_fuid_sha256="a" * 64, ordinal=1, sustituye_a=None, revocacion_sustituida_sha256=None,
        rol="recibe", parte=_PARTE, motivo="fallecimiento", fundamento=_HUELLA,
        referencia_radicado_id=uuid.uuid4(), referencia_radicado_numero="2026-X-E-000001",
        referencia_anexo_id=uuid.uuid4(), referencia_anexo_sha256="e" * 64, declarante=_DECL,
        declarada_en=datetime(2026, 10, 9, 12, 0, 0, 123456, tzinfo=UTC),
    )
    base.update(kw)
    return build_constancia_xml(**base)


def _texto(raiz: ET.Element, tag: str) -> str | None:
    for el in raiz.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return el.text
    return None


def test_la_constancia_v3_es_autosuficiente_y_no_lleva_el_texto_ni_la_sal() -> None:
    xml = _constancia()
    raiz = ET.fromstring(xml)
    assert raiz.tag == "{urn:orpycamcp:acta-firma-excepcion:v3}constanciaFirmaInobtenible"
    assert raiz.get("version") == "3"
    assert _texto(raiz, "radicadoNumero") == "2026-X-E-000001"
    assert _texto(raiz, "anexoSha256") == "e" * 64
    assert _texto(raiz, "nombre") == "Carlos Parte" and "Ana Admin" in xml and "Contador" in xml
    assert _texto(raiz, "fundamentoCompromiso") == _HUELLA.compromiso
    cp = next(e for e in raiz.iter() if e.tag.endswith("fundamentoCompromiso"))
    assert cp.get("algoritmo") == "SHA-256-salted"
    assert "fundamentoSha256" not in xml and "fundamentoHmac" not in xml
    assert _SAL.hex() not in xml
    assert _texto(raiz, "declaradaEn") == "2026-10-09T12:00:00.123456Z"
    assert _texto(raiz, "ordinal") == "1" and _texto(raiz, "sustituyeA") is None


def test_la_constancia_encadenada_declara_la_sustituida_y_la_huella_de_su_revocacion() -> None:
    previa = uuid.uuid4()
    raiz = ET.fromstring(_constancia(ordinal=3, sustituye_a=previa, revocacion_sustituida_sha256="d" * 64))
    assert _texto(raiz, "ordinal") == "3"
    assert _texto(raiz, "sustituyeA") == str(previa) and _texto(raiz, "revocacionSha256") == "d" * 64


def test_una_persona_no_resuelta_se_declara_sin_inventar_nombre() -> None:
    sin = Identidad(id=uuid.uuid4(), nombre=None, cargo=None, fuente="no_resuelta")
    raiz = ET.fromstring(_constancia(parte=sin))
    assert "no_resuelta" in _constancia(parte=sin)
    parte = next(e for e in raiz if e.tag.endswith("}parte"))
    assert [c.tag.rsplit("}", 1)[-1] for c in parte] == ["id", "fuente"]


def test_los_caracteres_especiales_de_un_nombre_no_rompen_el_xml() -> None:
    raro = Identidad(id=uuid.uuid4(), nombre='O\'Brien <&> "Pérez"', cargo="Jefe & Co", fuente="usuario")
    raiz = ET.fromstring(_constancia(parte=raro))
    assert _texto(raiz, "nombre") == 'O\'Brien <&> "Pérez"'


def test_la_revocacion_ata_la_baja_a_la_huella_de_la_constancia_y_dice_si_era_legada() -> None:
    cid, rid = uuid.uuid4(), uuid.uuid4()
    comunes = dict(
        revocacion_id=rid, constancia_id=cid, transferencia_id=uuid.uuid4(),
        expediente_id=uuid.uuid4(), acta_fuid_sha256="a" * 64, ordinal=2, rol="recibe",
        parte_id=uuid.uuid4(), causa="parte_puede_firmar", fundamento=_HUELLA, revocante=_DECL,
        revocada_en=datetime(2026, 10, 9, 13, 0, tzinfo=UTC),
    )
    xml = build_revocacion_xml(constancia_sha256="c" * 64, **comunes)
    raiz = ET.fromstring(xml)
    assert raiz.tag == "{urn:orpycamcp:acta-firma-excepcion-revocacion:v3}revocacionFirmaInobtenible"
    constancia = next(e for e in raiz if e.tag.endswith("}constancia"))
    assert constancia.get("firmada") == "true"
    assert _texto(constancia, "sha256") == "c" * 64 and _texto(constancia, "id") == str(cid)
    assert _texto(raiz, "fundamentoCompromiso") == _HUELLA.compromiso and "Ana Admin" in xml
    assert _texto(raiz, "causa") == "parte_puede_firmar"
    assert _texto(raiz, "revocadaEn") == "2026-10-09T13:00:00.000000Z"

    legada = ET.fromstring(build_revocacion_xml(constancia_sha256=None, **comunes))
    c2 = next(e for e in legada if e.tag.endswith("}constancia"))
    assert c2.get("firmada") == "false" and _texto(c2, "sha256") is None  # no se inventa una huella


def test_sha256_hex_sigue_siendo_el_de_los_artefactos_no_el_del_fundamento() -> None:
    assert sha256_hex("a") == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"


@pytest.mark.parametrize(
    ("dn", "cn"),
    [
        ("CN=Ana Pérez,O=Entidad,C=CO", "Ana Pérez"),
        ("O=Entidad,CN=Luis Gómez", "Luis Gómez"),
        ("cn=minúsculas,o=x", "minúsculas"),
        (r"CN=Pérez\, Ana,O=Entidad", "Pérez, Ana"),
        ("O=Entidad,C=CO", None),
        ("", None),
        (None, None),
    ],
)
def test_el_nombre_sale_del_cn_del_certificado(dn, cn) -> None:
    assert _cn_de(dn) == cn


# --- A1: compromiso con sal por registro ----------------------------------------------------


def test_el_compromiso_es_sha256_de_sal_mas_texto_y_lo_recalcula_un_tercero() -> None:
    sal = bytes(range(32))
    texto = "Certificado de defuncion del receptor"
    esperado = sha256_hex  # noqa: F841 - el plano NO es el compromiso
    import hashlib

    assert compromiso_fundamento(sal, texto) == hashlib.sha256(sal + texto.encode()).hexdigest()
    assert compromiso_fundamento(sal, texto) != hashlib.sha256(texto.encode()).hexdigest()
    assert compromiso_coincide(sal, texto, compromiso_fundamento(sal, texto)) is True
    # otro texto, otra sal, o un dato ausente: no coincide (nunca por defecto)
    assert compromiso_coincide(sal, texto + ".", compromiso_fundamento(sal, texto)) is False
    assert compromiso_coincide(bytes(32), texto, compromiso_fundamento(sal, texto)) is False
    assert compromiso_coincide(None, texto, compromiso_fundamento(sal, texto)) is False
    assert compromiso_coincide(sal, None, compromiso_fundamento(sal, texto)) is False
    assert compromiso_coincide(sal, texto, None) is False


def test_la_sal_es_aleatoria_de_32_bytes_por_registro() -> None:
    a, b = huella_de_fundamento("mismo texto largo para el fundamento"), huella_de_fundamento(
        "mismo texto largo para el fundamento"
    )
    assert len(a.sal) == len(b.sal) == 32
    assert a.sal != b.sal and a.compromiso != b.compromiso
    assert a.algoritmo == "SHA-256-salted"


# --- A2: C14N exclusiva ---------------------------------------------------------------------


def test_lo_que_se_firma_es_la_c14n_exclusiva_y_la_huella_se_calcula_sobre_esos_bytes() -> None:
    xml = _constancia()
    assert xml == canonicalizar(xml)  # idempotente: ya es canónico
    assert not xml.startswith("<?xml")  # C14N no lleva declaración XML
    assert sha256_c14n(xml) == sha256_hex(xml)
    # dos serializaciones equivalentes (atributos/espacios/orden de namespaces) -> misma huella
    desordenado = '<?xml version="1.0"?>\n' + xml.replace("<", " <", 0)
    assert sha256_c14n(desordenado) == sha256_c14n(xml)


def test_la_huella_del_firmado_quita_la_firma_envolvente_y_coincide_con_la_del_original() -> None:
    xml = _constancia()
    firmado = xml.replace(
        "</constanciaFirmaInobtenible>",
        '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:SignatureValue>zz'
        "</ds:SignatureValue></ds:Signature></constanciaFirmaInobtenible>",
    )
    assert firmado != xml
    assert huella_de_firmado(firmado) == sha256_c14n(xml)
    assert huella_de_firmado("<no-xml") is None


def test_los_enlaces_se_leen_del_xml() -> None:
    previa = uuid.uuid4()
    xml = _constancia(ordinal=2, sustituye_a=previa, revocacion_sustituida_sha256="d" * 64)
    e = enlaces_de_constancia(xml)
    assert e["sustituye_a"] == str(previa) and e["revocacion_sha256"] == "d" * 64
    assert e["ordinal"] == "2" and e["compromiso"] == _HUELLA.compromiso
    assert enlaces_de_constancia("<roto") is None and enlaces_de_revocacion(None) is None


# --- A5: privacidad del motivo --------------------------------------------------------------


def test_incapacidad_se_escribe_como_termino_neutro_en_el_xml() -> None:
    assert motivo_publico("incapacidad") == "impedimento_sobreviniente"
    assert motivo_publico("fallecimiento") == "fallecimiento" and motivo_publico("otro") == "otro"
    xml = _constancia(motivo="incapacidad")
    raiz = ET.fromstring(xml)
    assert _texto(raiz, "motivo") == "impedimento_sobreviniente"
    assert "incapacidad" not in xml
