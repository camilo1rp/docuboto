# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La huella de un DOCUMENTO del índice electrónico (E15 T-08).

Lo que se prueba aquí es la REGLA, no la aritmética de SHA-256: qué se afirma
cuando un documento tiene cero, uno o varios ficheros. Es la definición que
comparten quien calcula la huella al vincular y quien la comprueba después; si
divergen, la verificación acusa alteraciones que no ocurrieron.
"""

from __future__ import annotations

from app.core.huella_documento import huella_de_documento

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64


def test_sin_anexos_no_hay_huella_y_se_DICE() -> None:
    """Un radicado sin contenido binario —lo normal en ventanilla— no tiene
    huella que resumir. Devolver el hash del vacío sería afirmar que se
    verificó algo; `None` es lo que el índice sabe declarar como «sin huella»,
    que no es lo mismo que una discrepancia de integridad."""
    assert huella_de_documento([]) is None
    assert huella_de_documento(["", "   "]) is None


def test_un_anexo_es_su_propia_huella() -> None:
    """El caso común no añade indirección: la huella del documento ES la del
    fichero, y quien abra el índice puede cotejarla contra storage sin
    conocer ninguna regla de composición."""
    assert huella_de_documento([_A]) == _A


def test_el_hexadecimal_se_normaliza_a_minusculas() -> None:
    """El SHA-256 es el mismo valor en cualquier caja. Sin normalizar, dos
    escrituras del mismo hash producirían documentos distintos."""
    assert huella_de_documento([_A.upper()]) == _A


def test_varios_anexos_dan_UNA_huella_del_conjunto() -> None:
    huella = huella_de_documento([_A, _B])
    assert huella is not None
    assert len(huella) == 64
    # No es la de ninguno de sus ficheros: afirma el conjunto, no una parte.
    assert huella not in {_A, _B}


def test_el_ORDEN_de_los_anexos_no_cambia_la_huella() -> None:
    """La garantía que justifica ordenar. Sin ella, la huella dependería del
    orden en que la base devolvió los anexos: la misma evidencia daría dos
    valores y la verificación acusaría una alteración inexistente."""
    assert huella_de_documento([_A, _B, _C]) == huella_de_documento([_C, _A, _B])


def test_un_anexo_repetido_no_es_un_anexo_mas() -> None:
    """El mismo fichero listado dos veces es el mismo contenido: la huella
    afirma qué compone el documento, no cuántas filas lo enumeran."""
    assert huella_de_documento([_A, _A]) == _A


def test_cambiar_UN_anexo_cambia_la_huella_del_documento() -> None:
    """Lo que hace útil la construcción: alterar un fichero cambia su SHA-256
    y con él el del conjunto. Sin esto, la huella no detectaría nada."""
    assert huella_de_documento([_A, _B]) != huella_de_documento([_A, _C])
