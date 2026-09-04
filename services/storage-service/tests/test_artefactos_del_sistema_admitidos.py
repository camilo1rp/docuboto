# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La lista de artefactos del sistema tiene que cubrir lo que el sistema produce.

Es la tercera vez que se queda corta por el mismo motivo. Nació para el XML del
índice electrónico —que se firmaba entero, con su sello de tiempo, y se perdía
al guardarlo con `415 Formato no admitido: application/xml`—, se amplió para el
`.eml` del correo conservado, y cuando la producción documental (E23) empezó a
generar el PDF/A del oficio de salida nadie volvió a mirarla: **radicar un
borrador con documento no funcionaba**, con el mismo 415 y el mismo síntoma —el
trabajo hecho entero y tirado en el último paso.

El patrón que hay detrás merece el test: **una lista de admisión y un productor
que crece por separado**. Cada vez que el sistema aprende a generar un formato
nuevo, alguien tiene que acordarse de esta variable, y la única señal de que no
se acordó es un 415 en producción. Aquí se ata al revés: los formatos que el
sistema produce se declaran, y si uno falta el test cae — no el usuario.
"""

from __future__ import annotations

from app.core.config import settings

#: Formatos que el sistema PRODUCE y necesita guardar por el endpoint interno.
#: Cada entrada dice quién lo genera: si mañana alguien retira un productor,
#: esta lista es donde se ve que la entrada sobra.
PRODUCIDOS_POR_EL_SISTEMA = {
    # signature-service, al sellar el índice electrónico (E15/E06).
    "application/xml": "índice electrónico firmado (XAdES)",
    # document-service, al radicar una comunicación recibida por buzón (E19).
    # Ley 527/1999 art. 12: el original se conserva «en el formato en que se
    # haya generado, enviado o recibido».
    "message/rfc822": "mensaje de correo original conservado",
    # document-service, al radicar un borrador (E23): el PDF/A del oficio que
    # se anexa como `documento_principal`.
    "application/pdf": "PDF/A del documento de salida producido",
}


def test_todos_los_formatos_que_el_sistema_produce_estan_admitidos() -> None:
    faltan = {
        mime: quien
        for mime, quien in PRODUCIDOS_POR_EL_SISTEMA.items()
        if mime not in settings.allowed_system_mime_set
    }
    assert not faltan, (
        "El sistema produce estos formatos y storage-service los rechazaría "
        "con 415 al guardarlos (el trabajo se hace entero y se tira en el "
        f"último paso): {faltan}"
    )


def test_la_lista_de_artefactos_no_es_la_de_anexos() -> None:
    """Son dos listas a propósito, y tienen que seguir siéndolo.

    La de anexos acota lo que entra desde fuera (RF-DIG-04); ésta describe lo
    que el propio sistema genera. Fundirlas «para simplificar» dejaría subir
    XML y correos completos —con sus adjuntos incrustados— por la puerta de
    usuario, que es exactamente lo que se decidió no hacer cuando apareció el
    caso del índice.
    """
    solo_del_sistema = settings.allowed_system_mime_set - settings.allowed_mime_set
    assert "application/xml" in solo_del_sistema
    assert "message/rfc822" in solo_del_sistema


def test_el_pdf_esta_en_las_dos_listas_y_eso_es_correcto() -> None:
    """`application/pdf` no abre superficie nueva.

    Ya estaba admitido como anexo, así que cualquiera podía subir un PDF por la
    puerta de usuario. Añadirlo aquí solo reconoce que el sistema también los
    produce — a diferencia del XML y el `.eml`, que solo se admiten porque los
    genera el propio sistema.
    """
    assert "application/pdf" in settings.allowed_system_mime_set
    assert "application/pdf" in settings.allowed_mime_set
