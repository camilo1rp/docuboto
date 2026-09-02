# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Importación de plantillas ofimáticas (E23 Inc.5, RF-PRO-07).

Los ficheros se CONSTRUYEN aquí, no se traen como binarios de prueba: un `.docx`
es un ZIP con XML dentro, y construirlo deja a la vista qué está ejercitando
cada caso. Un binario opaco en el repositorio no se puede revisar.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services.importar_ofimatico import (
    MAX_BYTES,
    ImportacionError,
    importar,
    importar_docx,
    importar_odt,
)

_DOC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{texto}</w:t></w:r></w:p>
    <w:p><w:r><w:rPr><w:b/></w:rPr><w:r></w:r><w:t>En negrita</w:t></w:r></w:p>
  </w:body>
</w:document>"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>"""


def _docx(texto: str = "Senor *NOM_R*", extra: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", _DOC_XML.format(texto=texto))
        for nombre, contenido in (extra or {}).items():
            zf.writestr(nombre, contenido)
    return buf.getvalue()


def _odt(parrafos: list[str]) -> bytes:
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    doc = OpenDocumentText()
    for t in parrafos:
        doc.text.addElement(P(text=t))
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()


class TestDocx:
    def test_importa_el_texto_conservando_los_marcadores(self) -> None:
        # Es el caso que motiva la funcionalidad: una entidad llega con sus
        # plantillas del Orfeo legado y sus `*NOM_R*` dentro. Reescribirlas a
        # mano es como una migración se queda a medias.
        r = importar_docx(_docx("Senor *NOM_R*, sobre *RAD_E*"))
        assert "*NOM_R*" in r.cuerpo
        assert "*RAD_E*" in r.cuerpo
        assert "<p>" in r.cuerpo

    def test_declara_lo_que_NO_conserva(self) -> None:
        """Una importación que promete fidelidad total y no la da es peor.

        El encabezado y el pie los pone la plantilla de página del sistema
        (RF-PRO-04): traérselos del `.docx` duplicaría el membrete en cada
        oficio. Se dice, no se calla.
        """
        r = importar_docx(
            _docx(extra={"word/header1.xml": "<hdr/>", "word/footer1.xml": "<ftr/>"})
        )
        texto = " ".join(r.perdido)
        assert "encabezado" in texto
        assert "pie de pagina" in texto

    def test_un_documento_vacio_se_rechaza(self) -> None:
        # Importar la nada y guardarla como plantilla dejaría un oficio en blanco
        # esperando a que alguien lo descubra al radicar.
        #
        # El cuerpo se construye a mano y no con `_docx("")`: ese fixture lleva
        # un segundo párrafo con texto, así que «vacío» no lo era — el test
        # pasaba por el motivo equivocado hasta que falló.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("_rels/.rels", _RELS)
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8"?><w:document '
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body/></w:document>",
            )
        with pytest.raises(ImportacionError, match="no tiene texto"):
            importar_docx(buf.getvalue())

    def test_un_docx_sin_document_xml_no_es_un_docx(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("cualquier.txt", "hola")
        with pytest.raises(ImportacionError, match="word/document.xml"):
            importar_docx(buf.getvalue())


class TestOdt:
    def test_importa_los_parrafos(self) -> None:
        r = importar_odt(_odt(["Senor *NOM_P*", "Atentamente"]))
        assert "*NOM_P*" in r.cuerpo
        assert r.cuerpo.count("<p>") == 2

    def test_escapa_el_texto(self) -> None:
        # Del ODT se extrae TEXTO: un `<` en el documento es un carácter, no una
        # etiqueta, y meterlo crudo en el cuerpo lo convertiría en marcado.
        r = importar_odt(_odt(["Menor <que> y & ampersand"]))
        assert "&lt;que&gt;" in r.cuerpo
        assert "<que>" not in r.cuerpo

    def test_declara_SIEMPRE_que_pierde_el_formato(self) -> None:
        # Y no solo cuando lo detecta: prometer que conserva negritas y luego
        # perderlas es peor que decir de entrada que no las conserva.
        r = importar_odt(_odt(["Texto"]))
        assert any("formato de caracter" in p for p in r.perdido)


class TestSeguridad:
    def test_un_fichero_enorme_se_rechaza_por_tamano(self) -> None:
        with pytest.raises(ImportacionError, match="limite"):
            importar_docx(b"x" * (MAX_BYTES + 1))

    def test_una_bomba_zip_se_rechaza_SIN_descomprimir(self) -> None:
        """La defensa que de verdad importa.

        El límite de tamaño del fichero no dice nada de su contenido: unos
        pocos KB comprimidos pueden expandirse a gigabytes y tumbar el proceso.
        Se decide con la TABLA del ZIP, sin abrir un byte.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", b"\\0" * (200 * 1024 * 1024))
        datos = buf.getvalue()
        assert len(datos) < MAX_BYTES, "el fichero comprimido pasa el primer limite"
        with pytest.raises(ImportacionError, match="desproporcionado"):
            importar_docx(datos)

    def test_lo_que_no_es_un_zip_se_rechaza(self) -> None:
        with pytest.raises(ImportacionError, match="no es un ZIP|validos"):
            importar_docx(b"esto es texto plano")


class TestDespacho:
    def test_elige_el_conversor_por_extension(self) -> None:
        assert importar("plantilla.docx", _docx()).cuerpo
        assert importar("plantilla.ODT", _odt(["Hola"])).cuerpo

    def test_un_formato_no_admitido_lo_dice(self) -> None:
        # `.doc` binario y `.rtf` exigirían LibreOffice, que es otra decisión.
        with pytest.raises(ImportacionError, match="Formato no admitido"):
            importar("plantilla.doc", b"loquesea")

    def test_la_extension_es_una_pista_no_una_afirmacion(self) -> None:
        # Un `.docx` que no es un ZIP se rechaza igual: el nombre del fichero lo
        # elige quien sube.
        with pytest.raises(ImportacionError):
            importar("mentira.docx", b"no soy un zip")
