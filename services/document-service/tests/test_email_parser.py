# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lectura de un mensaje de correo para la bandeja (E19 Increment 2)."""

from email.message import EmailMessage

from app.clients.email_parser import leer

RESPALDO = "orpyca-local:cuenta/INBOX/1/7"


def _correo(**kw) -> bytes:
    m = EmailMessage()
    m["From"] = kw.get("de", "Juan Pérez <juan@ciudadano.co>")
    m["To"] = "pqrs@e.gov.co"
    m["Subject"] = kw.get("asunto", "Solicitud de información")
    if "message_id" in kw:
        m["Message-ID"] = kw["message_id"]
    m.set_content(kw.get("cuerpo", "Solicito copia del expediente."))
    for nombre, datos in kw.get("adjuntos", []):
        m.add_attachment(datos, maintype="application", subtype="pdf", filename=nombre)
    return m.as_bytes()


def test_extrae_lo_que_la_bandeja_necesita() -> None:
    correo = leer(_correo(message_id="<abc@ciudadano.co>"), respaldo_id=RESPALDO)
    assert correo.message_id == "<abc@ciudadano.co>"
    assert correo.remitente_email == "juan@ciudadano.co"
    assert correo.remitente_nombre == "Juan Pérez"
    assert correo.asunto == "Solicitud de información"
    assert "expediente" in correo.cuerpo


def test_los_adjuntos_llegan_con_sus_BYTES() -> None:
    """El defecto que esto corrige: la implementación anterior guardaba solo el
    NOMBRE del adjunto y tiraba el contenido, así que se radicaba la petición y
    se perdía su soporte. El Acuerdo 060/2001 art. 2 incluye «Anexos» entre los
    datos mínimos del registro."""
    crudo = _correo(adjuntos=[("cedula.pdf", b"%PDF-1.4 cedula")])
    correo = leer(crudo, respaldo_id=RESPALDO)
    assert len(correo.adjuntos) == 1
    assert correo.adjuntos[0].nombre == "cedula.pdf"
    assert correo.adjuntos[0].contenido == b"%PDF-1.4 cedula"
    # El checksum sale del contenido real, no de un valor declarado.
    assert correo.adjuntos[0].sha256 == (
        __import__("hashlib").sha256(b"%PDF-1.4 cedula").hexdigest()
    )


def test_un_correo_sin_Message_ID_recibe_el_respaldo() -> None:
    """No puede quedar vacío: es la clave que impide que el mismo correo entre
    dos veces en la bandeja."""
    correo = leer(_correo(), respaldo_id=RESPALDO)
    assert correo.message_id == RESPALDO


def test_un_nombre_de_adjunto_con_ruta_no_viaja_tal_cual() -> None:
    """Un nombre con barras o `..` compondría una ruta al guardarlo.

    Se SUSTITUYE en vez de recortarse: un nombre que casi funciona es más difícil
    de detectar que uno evidentemente cambiado.
    """
    crudo = _correo(adjuntos=[("../../etc/passwd", b"x")])
    nombre = leer(crudo, respaldo_id=RESPALDO).adjuntos[0].nombre
    assert "/" not in nombre and ".." not in nombre
    # Sustituido entero, no maquillado: `.._.._etc_passwd` no compone una ruta
    # pero conserva la forma del original, y eso es más difícil de reconocer
    # como manipulado que un nombre evidentemente generado.
    assert nombre.startswith("anexo-")

    # Un nombre normal SÍ se conserva — control positivo de que la sustitución
    # no se aplica a todo.
    normal = leer(_correo(adjuntos=[("cédula ciudadanía.pdf", b"x")]), respaldo_id=RESPALDO)
    assert normal.adjuntos[0].nombre == "cédula ciudadanía.pdf"


def test_una_imagen_incrustada_no_es_un_anexo() -> None:
    """Un logotipo de firma en un correo HTML es parte del cuerpo.

    Sin esta distinción, cada correo con firma corporativa metería su logotipo
    en el expediente como si fuera un soporte documental.
    """
    m = EmailMessage()
    m["From"] = "a@b.co"
    m["Subject"] = "Con firma"
    m.set_content("Cuerpo")
    m.add_related(b"\x89PNG logo", maintype="image", subtype="png", cid="<logo@firma>")
    m.add_attachment(b"%PDF real", maintype="application", subtype="pdf", filename="anexo.pdf")

    correo = leer(m.as_bytes(), respaldo_id=RESPALDO)
    nombres = [a.nombre for a in correo.adjuntos]
    assert nombres == ["anexo.pdf"], f"se colaron partes del cuerpo: {nombres}"


def test_el_asunto_codificado_se_lee_en_espanol() -> None:
    correo = leer(_correo(asunto="Petición de información — nº 3"), respaldo_id=RESPALDO)
    assert correo.asunto == "Petición de información — nº 3"


def test_un_correo_solo_HTML_no_pierde_el_cuerpo() -> None:
    m = EmailMessage()
    m["From"] = "a@b.co"
    m["Subject"] = "HTML"
    m.set_content("<p>Solicito <b>copia</b> del acto.</p>", subtype="html")
    correo = leer(m.as_bytes(), respaldo_id=RESPALDO)
    assert "copia" in correo.cuerpo
