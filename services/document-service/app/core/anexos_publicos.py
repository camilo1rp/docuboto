# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Qué se admite como anexo por la VENTANILLA PÚBLICA, y por qué tan poco.

Una tutela o un derecho de petición sin sus soportes es inservible, y el Acuerdo
AGN 060/2001 exige registrar folios y anexos: por eso la ventanilla los admite.
Pero quien sube aquí **no está autenticado**, así que cada límite es una
decisión de exposición, no una preferencia.

**Se comprueba el CONTENIDO, no el tipo declarado.** El `Content-Type` de una
subida lo escribe el cliente: un desconocido puede declarar `application/pdf` y
enviar cualquier cosa. Se leen los primeros bytes y se exige que coincidan con
una firma conocida. No es antivirus —y **no hay antivirus**, queda declarado—:
es evitar que la lista de formatos sea una cortesía que cualquiera esquiva
cambiando una cabecera.

La lista es **corta a propósito**: PDF y las imágenes de digitalización cubren
lo que de verdad presenta un ciudadano en una ventanilla. Los ofimáticos que sí
admite un funcionario (`.docx`, `.xlsx`) quedan fuera aquí: son contenedores con
macros y su superficie no la justifica el caso de uso.
"""

from __future__ import annotations

#: Número máximo de anexos por radicación. Una tutela con sus soportes cabe de
#: sobra; una subida masiva no.
MAX_ANEXOS = 5

#: Tope por fichero. Coincide con `max_public_upload_bytes` de storage-service,
#: que es quien manda: aquí se rechaza antes para no pagar el traslado.
MAX_BYTES_POR_ANEXO = 10 * 1024 * 1024

#: Tope del conjunto. Sin él, cinco ficheros al límite serían 50 MB por
#: radicación anónima.
MAX_BYTES_TOTAL = 25 * 1024 * 1024

#: Firmas de contenido admitidas → MIME canónico. El MIME que se guarda sale de
#: AQUÍ, no de lo que declare el cliente.
_FIRMAS: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

#: Cuántos bytes hacen falta para decidir. La firma más larga son 8.
CABECERA = 8


class AnexoRechazado(ValueError):
    """El anexo no se admite. El mensaje va dirigido a quien lo subió."""


def tipo_por_contenido(datos: bytes) -> str:
    """MIME deducido de los primeros bytes. Lanza `AnexoRechazado` si no encaja.

    Devolver el tipo en vez de un booleano es deliberado: el llamante guarda
    ESTE valor y no el declarado, así que un fichero no puede quedar registrado
    con un tipo que no es el suyo.
    """
    cabecera = datos[:CABECERA]
    for firma, mime in _FIRMAS:
        if cabecera.startswith(firma):
            return mime
    raise AnexoRechazado(
        "Solo se admiten archivos PDF, JPG, PNG o TIFF. El archivo enviado no "
        "corresponde a ninguno de esos formatos (se comprueba su contenido, no "
        "su extensión ni el tipo declarado)."
    )


def validar_conjunto(tamanos: list[int]) -> None:
    """Comprueba número y tamaño ANTES de leer o trasladar nada."""
    if len(tamanos) > MAX_ANEXOS:
        raise AnexoRechazado(
            f"Se admiten como máximo {MAX_ANEXOS} archivos por radicación; "
            f"se enviaron {len(tamanos)}."
        )
    for tam in tamanos:
        if tam > MAX_BYTES_POR_ANEXO:
            raise AnexoRechazado(
                f"Cada archivo puede pesar como máximo "
                f"{MAX_BYTES_POR_ANEXO // (1024 * 1024)} MB."
            )
    if sum(tamanos) > MAX_BYTES_TOTAL:
        raise AnexoRechazado(
            f"El conjunto de archivos supera el máximo de "
            f"{MAX_BYTES_TOTAL // (1024 * 1024)} MB por radicación."
        )
