# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""XML canónico de la constancia de «firma inobtenible» del acta de entrega y de su revocación
(AREA-07-10, ADR-030 + addendum FIRMA-2).

Es lo que se FIRMA: el sello institucional XAdES-T y, cuando corresponde, la firma personal de
quien actúa cubren exactamente estos bytes. La constancia (v3) ata la constancia al acta que
excusa (huella del XML congelado), a la parte y al motivo, al fundamento por un COMPROMISO CON
SAL por registro (`SHA-256(sal || texto)`: el texto libre puede referir salud o fallecimiento y
no debe viajar dentro de un artefacto que se replica y se conserva para siempre; un SHA-256
plano de un texto corto se ataca por diccionario, y un HMAC con clave de plataforma no lo puede
verificar un tercero —el AGN— sin entregarle la clave de TODA la entidad; con sal por registro,
la entidad revela el texto y la sal de UN registro y cualquiera recalcula), y la hace
AUTOSUFICIENTE: número del radicado de soporte, SHA-256 del anexo, y nombre y cargo de declarante
y parte como instantánea resuelta por el servidor.

La revocación (v3) ata la baja a la huella de la constancia que revoca y lleva su `causa` de
vocabulario cerrado. El `declarada_en` / `revocada_en` del XML es el MISMO instante que se
persiste en la fila: quien compare el artefacto con la base no encuentra dos relojes.

CANONICALIZACIÓN (A2): lo que se firma, se guarda y se huella es la C14N EXCLUSIVA del XML
(`canonicalizar`). La huella (`constancia_sha256` / `revocacion_sha256`) es el SHA-256 de esos
bytes: un tercero que reciba el XML ya firmado, le quite la `ds:Signature` envolvente y lo
canonicalice obtiene la misma huella (`huella_de_firmado`).

PRIVACIDAD (Ley 1581 art. 5): el motivo `incapacidad` es un dato de salud; dentro del artefacto
(y del asiento de auditoría) se escribe como el término neutro `impedimento_sobreviniente`
(`motivo_publico`). La categoría exacta queda en la base.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

from lxml import etree

NS = "urn:orpycamcp:acta-firma-excepcion:v3"
NS_REVOCACION = "urn:orpycamcp:acta-firma-excepcion-revocacion:v3"
NS_SOBRE = "urn:orpycamcp:acta-firma-excepcion-sobre:v1"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"

#: Versión de constancia / revocación que escribe este código (columnas `constancia_version` y
#: `revocacion_version`). La v2 de la constancia (HMAC con clave de plataforma) y la v2 de la
#: revocación quedan como datos LEGADOS: se leen, no se escriben.
CONSTANCIA_VERSION = 3
REVOCACION_VERSION = 3

ALGORITMO_COMPROMISO = "SHA-256-salted"
SAL_BYTES = 32

#: Motivos cuyo nombre exacto NO debe viajar en un artefacto permanente (dato de salud, Ley 1581
#: art. 5): término neutro dentro del XML y del asiento.
_MOTIVO_NEUTRO = {"incapacidad": "impedimento_sobreviniente"}

#: Vocabulario cerrado de la causa de una revocación (A6).
CAUSAS_REVOCACION = ("error_de_declaracion", "parte_puede_firmar", "otro")


@dataclass(frozen=True, slots=True)
class Identidad:
    """Instantánea de una persona tal como la resolvió el servidor al actuar.

    `fuente` dice de dónde sale el nombre: `certificado` (CN del certificado de firma activo),
    `usuario` (username del catálogo) o `no_resuelta` (la identidad no está en el catálogo: se
    declara el hueco, no se inventa un nombre)."""

    id: uuid.UUID
    nombre: str | None
    cargo: str | None
    fuente: str
    certificado_activo: bool = False


@dataclass(frozen=True, slots=True)
class HuellaFundamento:
    """Compromiso con sal del fundamento. `sal` es secreta (solo la base y quien la verifica);
    `compromiso` (hex) viaja en el XML, la columna y el asiento."""

    sal: bytes
    compromiso: str
    algoritmo: str = ALGORITMO_COMPROMISO


def compromiso_fundamento(sal: bytes, texto: str) -> str:
    """`SHA-256(sal || texto_utf8)` en hex. Verificable por un tercero a quien se entreguen el
    texto y la sal de UN registro, sin que eso revele nada de los demás."""
    return hashlib.sha256(sal + texto.encode("utf-8")).hexdigest()


def huella_de_fundamento(texto: str) -> HuellaFundamento:
    """Compromiso nuevo con sal aleatoria de 32 bytes (`secrets`, CSPRNG) por registro."""
    sal = secrets.token_bytes(SAL_BYTES)
    return HuellaFundamento(sal=sal, compromiso=compromiso_fundamento(sal, texto))


def compromiso_coincide(sal: bytes | None, texto: str | None, compromiso: str | None) -> bool:
    """¿`compromiso` es el compromiso de `texto` con `sal`? Falso ante cualquier dato ausente.
    Comparación en tiempo constante."""
    if sal is None or texto is None or not compromiso:
        return False
    return hmac.compare_digest(compromiso_fundamento(bytes(sal), texto), compromiso.strip().lower())


def motivo_publico(motivo: str) -> str:
    """El motivo tal como puede viajar en el XML y en el asiento (A5)."""
    return _MOTIVO_NEUTRO.get(motivo, motivo)


def canonicalizar(xml: str) -> str:
    """C14N EXCLUSIVA (`http://www.w3.org/2001/10/xml-exc-c14n#`, sin comentarios) del XML.
    Idempotente: `canonicalizar(canonicalizar(x)) == canonicalizar(x)`."""
    raiz = etree.fromstring(xml.encode("utf-8"))
    return etree.tostring(raiz, method="c14n", exclusive=True).decode("utf-8")


def sha256_c14n(xml: str) -> str:
    """Huella de un XML: SHA-256 de su C14N exclusiva."""
    return sha256_hex(canonicalizar(xml))


def huella_de_firmado(xml_firmado: str) -> str | None:
    """Huella del contenido FIRMADO: le quita toda `ds:Signature` (la firma envolvente) y
    canonicaliza. Para un XAdES envolvente sobre el XML de este módulo coincide con
    `sha256_c14n` del original. `None` si el XML no parsea."""
    try:
        raiz = etree.fromstring(xml_firmado.encode("utf-8"))
    except (etree.XMLSyntaxError, ValueError):
        return None
    for sig in list(raiz.iter(f"{{{NS_DSIG}}}Signature")):
        padre = sig.getparent()
        if padre is None:
            return None  # el documento ES la firma: no hay contenido que huellar.
        # Conserva el `tail` (texto tras la firma) al quitarla.
        tail = sig.tail
        if tail:
            previo = sig.getprevious()
            if previo is not None:
                previo.tail = (previo.tail or "") + tail
            else:
                padre.text = (padre.text or "") + tail
        padre.remove(sig)
    return sha256_hex(etree.tostring(raiz, method="c14n", exclusive=True).decode("utf-8"))


def instante_iso(ts: datetime) -> str:
    """ISO 8601 UTC con microsegundos, sin ambigüedad de zona."""
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_hex(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def referencia_canonica(radicado_id: uuid.UUID, anexo_id: uuid.UUID | None) -> str:
    """Forma canónica del soporte documental: `radicado:<uuid>` o `radicado:<uuid>#anexo:<uuid>`."""
    base = f"radicado:{radicado_id}"
    return f"{base}#anexo:{anexo_id}" if anexo_id is not None else base


def _hijo(padre: ET.Element, ns: str, tag: str, valor: str) -> ET.Element:
    el = ET.SubElement(padre, f"{{{ns}}}{tag}")
    el.text = valor
    return el


def _persona(padre: ET.Element, ns: str, tag: str, persona: Identidad) -> None:
    el = ET.SubElement(padre, f"{{{ns}}}{tag}")
    _hijo(el, ns, "id", str(persona.id))
    if persona.nombre:
        _hijo(el, ns, "nombre", persona.nombre)
    if persona.cargo:
        _hijo(el, ns, "cargo", persona.cargo)
    _hijo(el, ns, "fuente", persona.fuente)


def _huella(padre: ET.Element, ns: str, huella: HuellaFundamento) -> None:
    el = _hijo(padre, ns, "fundamentoCompromiso", huella.compromiso)
    el.set("algoritmo", huella.algoritmo)


def _serializar(raiz: ET.Element) -> str:
    """Serializa y devuelve la C14N exclusiva: ESO es lo que se firma, se guarda y se huella."""
    return canonicalizar(ET.tostring(raiz, encoding="unicode"))


def build_constancia_xml(
    *,
    constancia_id: uuid.UUID,
    transferencia_id: uuid.UUID,
    expediente_id: uuid.UUID,
    acta_fuid_sha256: str,
    ordinal: int,
    sustituye_a: uuid.UUID | None,
    revocacion_sustituida_sha256: str | None,
    rol: str,
    parte: Identidad,
    motivo: str,
    fundamento: HuellaFundamento,
    referencia_radicado_id: uuid.UUID,
    referencia_radicado_numero: str,
    referencia_anexo_id: uuid.UUID,
    referencia_anexo_sha256: str,
    declarante: Identidad,
    declarada_en: datetime,
) -> str:
    """Serializa la constancia v3 (C14N exclusiva). Sin texto libre del usuario: identificadores, catálogos
    cerrados, huellas e instantes; los nombres vienen del catálogo, no del cliente."""
    ET.register_namespace("", NS)
    raiz = ET.Element(f"{{{NS}}}constanciaFirmaInobtenible", {"version": "3"})
    _hijo(raiz, NS, "constanciaId", str(constancia_id))
    _hijo(raiz, NS, "transferenciaId", str(transferencia_id))
    _hijo(raiz, NS, "expedienteId", str(expediente_id))
    acta = ET.SubElement(raiz, f"{{{NS}}}acta")
    _hijo(acta, NS, "sha256", acta_fuid_sha256)
    cadena = ET.SubElement(raiz, f"{{{NS}}}cadena")
    _hijo(cadena, NS, "ordinal", str(ordinal))
    if sustituye_a is not None:
        _hijo(cadena, NS, "sustituyeA", str(sustituye_a))
        if revocacion_sustituida_sha256:
            _hijo(cadena, NS, "revocacionSha256", revocacion_sustituida_sha256)
    _hijo(raiz, NS, "rol", rol)
    _persona(raiz, NS, "parte", parte)
    _hijo(raiz, NS, "motivo", motivo_publico(motivo))
    _huella(raiz, NS, fundamento)
    ref = ET.SubElement(raiz, f"{{{NS}}}referencia")
    _hijo(ref, NS, "radicadoId", str(referencia_radicado_id))
    _hijo(ref, NS, "radicadoNumero", referencia_radicado_numero)
    _hijo(ref, NS, "anexoId", str(referencia_anexo_id))
    _hijo(ref, NS, "anexoSha256", referencia_anexo_sha256)
    _persona(raiz, NS, "declarante", declarante)
    _hijo(raiz, NS, "declaradaEn", instante_iso(declarada_en))
    return _serializar(raiz)


def build_revocacion_xml(
    *,
    revocacion_id: uuid.UUID,
    constancia_id: uuid.UUID,
    constancia_sha256: str | None,
    transferencia_id: uuid.UUID,
    expediente_id: uuid.UUID,
    acta_fuid_sha256: str,
    ordinal: int,
    rol: str,
    parte_id: uuid.UUID,
    causa: str,
    fundamento: HuellaFundamento,
    revocante: Identidad,
    revocada_en: datetime,
) -> str:
    """Serializa la revocación v3 (C14N exclusiva), ligada a la huella de la constancia que deja sin efecto.
    `constancia_sha256` es `None` solo para una constancia LEGADA sin firma (anterior a la 061):
    se declara con `firmada="false"` en vez de inventar una huella."""
    ET.register_namespace("", NS_REVOCACION)
    raiz = ET.Element(f"{{{NS_REVOCACION}}}revocacionFirmaInobtenible", {"version": "3"})
    _hijo(raiz, NS_REVOCACION, "revocacionId", str(revocacion_id))
    constancia = ET.SubElement(
        raiz,
        f"{{{NS_REVOCACION}}}constancia",
        {"firmada": "true" if constancia_sha256 else "false"},
    )
    _hijo(constancia, NS_REVOCACION, "id", str(constancia_id))
    if constancia_sha256:
        _hijo(constancia, NS_REVOCACION, "sha256", constancia_sha256)
    _hijo(constancia, NS_REVOCACION, "ordinal", str(ordinal))
    _hijo(raiz, NS_REVOCACION, "transferenciaId", str(transferencia_id))
    _hijo(raiz, NS_REVOCACION, "expedienteId", str(expediente_id))
    acta = ET.SubElement(raiz, f"{{{NS_REVOCACION}}}acta")
    _hijo(acta, NS_REVOCACION, "sha256", acta_fuid_sha256)
    _hijo(raiz, NS_REVOCACION, "rol", rol)
    _hijo(raiz, NS_REVOCACION, "parteId", str(parte_id))
    _hijo(raiz, NS_REVOCACION, "causa", causa)
    _huella(raiz, NS_REVOCACION, fundamento)
    _persona(raiz, NS_REVOCACION, "revocante", revocante)
    _hijo(raiz, NS_REVOCACION, "revocadaEn", instante_iso(revocada_en))
    return _serializar(raiz)


def build_sobre_xml(
    *,
    acto_id: uuid.UUID,
    acto: str,
    texto: str,
    huella: HuellaFundamento,
) -> str:
    """Sobre de revelación del fundamento (FIRMA-2 ronda 3): `{id, acto, texto, sal_hex,
    compromiso, algoritmo}` en C14N exclusiva. `acto` es `constancia` o `revocacion`; `id` el de
    ese acto. Es el ÚNICO artefacto que lleva el texto en claro junto a su sal: se sella con el
    sello institucional, se protege con Object-Lock y se sirve solo mediado (administración
    nivel 3, con asiento de lectura). Quien reciba el sobre recalcula `SHA-256(sal || texto)` y lo
    coteja con el compromiso del XML principal."""
    if acto not in ("constancia", "revocacion"):
        raise ValueError(f"acto de sobre desconocido: {acto!r}")
    # lxml (no ElementTree): conserva un `\r` interno del texto como `&#xD;` en la C14N; un parser
    # lo normalizaría a `\n` y el compromiso (que cubre los bytes exactos del texto) dejaría de
    # cuadrar. Un carácter no admitido por XML 1.0 levanta `ValueError` (lo traduce el servicio).
    raiz = etree.Element(f"{{{NS_SOBRE}}}sobreFundamento", {"version": "1"}, nsmap={None: NS_SOBRE})
    for tag, valor in (
        ("id", str(acto_id)),
        ("acto", acto),
        ("texto", texto),
        ("salHex", huella.sal.hex()),
        ("compromiso", huella.compromiso),
        ("algoritmo", huella.algoritmo),
    ):
        etree.SubElement(raiz, f"{{{NS_SOBRE}}}{tag}").text = valor
    return etree.tostring(raiz, method="c14n", exclusive=True).decode("utf-8")


def leer_sobre(xml: str | None) -> dict[str, str | None] | None:
    """Campos del sobre (nombres locales), tolerando la `ds:Signature` envolvente. `None` si el
    XML no parsea o no es un sobre."""
    raiz = leer_xml(xml)
    if raiz is None or etree.QName(raiz).localname != "sobreFundamento":
        return None
    texto = next(
        (h for h in raiz if isinstance(h.tag, str) and etree.QName(h).localname == "texto"), None
    )
    return {
        "id": _texto_de(raiz, "id"),
        "acto": _texto_de(raiz, "acto"),
        # Sin `strip`: el compromiso cubre los bytes exactos del texto.
        "texto": texto.text if texto is not None else None,
        "sal_hex": _texto_de(raiz, "salHex"),
        "compromiso": _texto_de(raiz, "compromiso"),
        "algoritmo": _texto_de(raiz, "algoritmo"),
    }


def _texto_de(raiz: etree._Element, *camino: str) -> str | None:
    """Texto del primer descendiente que sigue `camino` (nombres locales, sin espacio de
    nombres); `None` si falta alguno."""
    actual = raiz
    for nombre in camino:
        siguiente = next(
            (h for h in actual if isinstance(h.tag, str) and etree.QName(h).localname == nombre),
            None,
        )
        if siguiente is None:
            return None
        actual = siguiente
    return (actual.text or "").strip() or None


def leer_xml(xml: str | None) -> etree._Element | None:
    if not xml:
        return None
    try:
        return etree.fromstring(xml.encode("utf-8"))
    except (etree.XMLSyntaxError, ValueError):
        return None


def enlaces_de_constancia(xml: str | None) -> dict[str, str | None] | None:
    """Lo que la constancia AFIRMA sobre su lugar en la cadena y su fundamento (nombres
    locales). `None` si el XML no parsea."""
    raiz = leer_xml(xml)
    if raiz is None:
        return None
    return {
        "constancia_id": _texto_de(raiz, "constanciaId"),
        "acta_sha256": _texto_de(raiz, "acta", "sha256"),
        "ordinal": _texto_de(raiz, "cadena", "ordinal"),
        "sustituye_a": _texto_de(raiz, "cadena", "sustituyeA"),
        "revocacion_sha256": _texto_de(raiz, "cadena", "revocacionSha256"),
        "motivo": _texto_de(raiz, "motivo"),
        "compromiso": _texto_de(raiz, "fundamentoCompromiso"),
    }


def enlaces_de_revocacion(xml: str | None) -> dict[str, str | None] | None:
    """Lo que la revocación AFIRMA sobre la constancia que revoca. `None` si no parsea."""
    raiz = leer_xml(xml)
    if raiz is None:
        return None
    return {
        "revocacion_id": _texto_de(raiz, "revocacionId"),
        "constancia_id": _texto_de(raiz, "constancia", "id"),
        "constancia_sha256": _texto_de(raiz, "constancia", "sha256"),
        "ordinal": _texto_de(raiz, "constancia", "ordinal"),
        "causa": _texto_de(raiz, "causa"),
        "compromiso": _texto_de(raiz, "fundamentoCompromiso"),
    }
