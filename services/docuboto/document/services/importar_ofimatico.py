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

Paridad con `import_docx_editor.php` del Orfeo legado. Una entidad que migra
llega con sus plantillas en `.docx` o `.odt` y con los marcadores `*NOM_R*`
dentro; reescribirlas a mano es exactamente como una migración se queda a
medias.

── Qué se promete y qué no ──────────────────────────────────────────────────

**Mejor esfuerzo DECLARADO sobre texto y estructura**, igual que el legado. Se
conservan párrafos, negritas, cursivas, listas, encabezados y tablas. NO se
conservan: cajas de texto, campos de Word, encabezados/pies de página,
numeración automática, ni la maquetación exacta.

La distinción importa: el membrete, los márgenes y el pie los pone la plantilla
de página del renderizador (RF-PRO-04), no el documento importado. Traerse el
encabezado del `.docx` duplicaría el membrete en cada oficio.

Una importación que promete fidelidad total y no la da es peor que una que
avisa, así que el resultado viaja con la lista de lo que se perdió.

── Seguridad ────────────────────────────────────────────────────────────────

Un `.docx`/`.odt` es un ZIP que sube un administrador. Se acota el tamaño
comprimido Y el descomprimido: sin lo segundo, un archivo de pocos KB puede
expandirse a gigabytes (zip bomb) y tumbar el proceso. `odfpy` parsea con
`defusedxml`, que cierra XXE; `mammoth` no ejecuta nada y produce un conjunto
acotado de etiquetas, sin `<script>`.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

#: Tamaño máximo del fichero subido. Una plantilla de oficio son unas decenas de
#: KB; 10 MB deja sitio de sobra para logos incrustados sin admitir un volcado.
MAX_BYTES = 10 * 1024 * 1024

#: Tamaño máximo YA DESCOMPRIMIDO. Es la defensa que de verdad importa: el
#: límite de arriba no dice nada del contenido de un ZIP.
MAX_DESCOMPRIMIDO = 60 * 1024 * 1024


class ImportacionError(Exception):
    """El fichero no se pudo convertir. El mensaje va al usuario."""


@dataclass
class Importado:
    cuerpo: str
    #: Qué NO se conservó, para que quien importa lo sepa antes de guardar.
    perdido: list[str] = field(default_factory=list)


def _verificar_zip(datos: bytes) -> zipfile.ZipFile:
    if len(datos) > MAX_BYTES:
        raise ImportacionError(
            f"El archivo pesa {len(datos) // 1024} KB y el limite son "
            f"{MAX_BYTES // 1024 // 1024} MB."
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile as exc:
        raise ImportacionError(
            "El archivo no es un .docx ni un .odt validos (no es un ZIP)."
        ) from exc

    total = sum(info.file_size for info in zf.infolist())
    if total > MAX_DESCOMPRIMIDO:
        # No se abre ni un byte: se decide con la tabla del ZIP.
        raise ImportacionError(
            "El archivo se expande a un tamano desproporcionado y no se procesa."
        )
    return zf


def _perdidas_docx(zf: zipfile.ZipFile) -> list[str]:
    """Qué trae el .docx que la conversión no conserva.

    Se mira la TABLA del ZIP, no el contenido: basta para saber que hay
    encabezados o pies, y no obliga a abrir nada.
    """
    nombres = set(zf.namelist())
    perdido: list[str] = []
    if any(n.startswith("word/header") for n in nombres):
        perdido.append(
            "encabezado de pagina (lo pone la plantilla de pagina del sistema)"
        )
    if any(n.startswith("word/footer") for n in nombres):
        perdido.append("pie de pagina (lo pone la plantilla de pagina del sistema)")
    if "word/numbering.xml" in nombres:
        perdido.append("numeracion automatica de listas")
    return perdido


def importar_docx(datos: bytes) -> Importado:
    zf = _verificar_zip(datos)
    if "word/document.xml" not in zf.namelist():
        raise ImportacionError("El archivo no parece un .docx (falta word/document.xml).")
    perdido = _perdidas_docx(zf)

    import mammoth  # noqa: PLC0415 — import diferido: solo se paga al importar

    resultado = mammoth.convert_to_html(io.BytesIO(datos))
    cuerpo = (resultado.value or "").strip()
    if not cuerpo:
        raise ImportacionError("El documento no tiene texto que importar.")

    # Los avisos de mammoth se traducen a la MISMA lista: quien importa no tiene
    # por qué distinguir «el sistema no lo soporta» de «el documento usaba algo
    # raro»; lo que necesita saber es qué no llegó.
    for mensaje in resultado.messages:
        texto = str(getattr(mensaje, "message", mensaje)).strip()
        if texto and texto not in perdido:
            perdido.append(texto)
    return Importado(cuerpo=cuerpo, perdido=perdido)


def importar_odt(datos: bytes) -> Importado:
    zf = _verificar_zip(datos)
    if "content.xml" not in zf.namelist():
        raise ImportacionError("El archivo no parece un .odt (falta content.xml).")

    from odf import teletype, text  # noqa: PLC0415
    from odf.opendocument import load  # noqa: PLC0415

    try:
        doc = load(io.BytesIO(datos))
    except Exception as exc:  # noqa: BLE001 — odfpy lanza de todo ante ficheros rotos
        raise ImportacionError(f"El .odt no se pudo leer: {exc}") from exc

    parrafos = [
        teletype.extractText(p).strip() for p in doc.getElementsByType(text.P)
    ]
    cuerpo = "".join(f"<p>{_escapar(t)}</p>" for t in parrafos if t)
    if not cuerpo:
        raise ImportacionError("El documento no tiene texto que importar.")

    # Se declara SIEMPRE, y no solo cuando se detecta: la extracción de ODT es
    # de texto plano por párrafos. Prometer que conserva negritas y luego
    # perderlas es peor que decir de entrada que no las conserva.
    return Importado(
        cuerpo=cuerpo,
        perdido=[
            "formato de caracter (negrita, cursiva) y tablas: del .odt se "
            "importa el texto por parrafos"
        ],
    )


def _escapar(texto: str) -> str:
    import html  # noqa: PLC0415

    return html.escape(texto)


#: Extensión → conversor. Cerrado a propósito: admitir `.doc` binario o `.rtf`
#: exigiría LibreOffice, que es la decisión D-1 y va detrás de un flag.
CONVERSORES = {".docx": importar_docx, ".odt": importar_odt}


def importar(nombre: str, datos: bytes) -> Importado:
    """Convierte por EXTENSIÓN, y la extensión se comprueba contra la firma.

    Un `.docx` que no es un ZIP se rechaza en `_verificar_zip`, así que el
    nombre por sí solo no decide nada: es una pista para elegir el conversor,
    no una afirmación en la que confiar.
    """
    minusculas = nombre.lower()
    for extension, conversor in CONVERSORES.items():
        if minusculas.endswith(extension):
            return conversor(datos)
    raise ImportacionError(
        f"Formato no admitido. Se admiten: {', '.join(sorted(CONVERSORES))}."
    )
