# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La huella de un DOCUMENTO del índice electrónico (E15 T-08, RT-15 art. 4.3.2.3).

El índice lleva una huella por `<documento>`, y en este modelo un documento es
un **radicado**, no un fichero: un radicado puede traer varios anexos. Hay que
decir entonces qué significa «la huella de ese documento», y decirlo en UN solo
sitio para que quien la calcula y quien la comprueba no diverjan.

**La regla, declarada:**

* **Sin anexos** → no hay huella. Es lo normal en ventanilla (un registro de
  metadatos no tiene contenido binario que resumir) y el índice ya sabe
  declararlo (`sin_huella_declarada`), que es distinto de una discrepancia de
  integridad. No se inventa un hash del vacío.
* **Un anexo** → su SHA-256, tal cual. Es el caso común y no añade indirección.
* **Varios anexos** → SHA-256 del conjunto de sus huellas, ordenadas
  ascendentemente y unidas por salto de línea, con salto final.

**Por qué ordenadas.** La huella del documento no puede depender del orden en
que la base devolvió los anexos: sin ordenar, la misma evidencia daría huellas
distintas en dos consultas y la verificación acusaría una alteración que no
existió. Ordenar la hace función del CONJUNTO, que es lo que se quiere afirmar:
«estos son los ficheros que componen este documento».

**Por qué las huellas y no los bytes.** Resumir el contenido exigiría descargar
cada anexo en cada vinculación. Encadenar las huellas ya publicadas por
storage-service da la misma garantía —alterar un anexo cambia su SHA-256 y con
él el del conjunto— sin mover un byte. Es la construcción de un árbol de
Merkle de un nivel.

**Lo que esta huella NO es.** No es una firma ni prueba de existencia en el
tiempo: dice qué se vinculó, no cuándo ni ante quién. Eso lo aporta la firma
XAdES del índice (E06).
"""

from __future__ import annotations

import hashlib

_SEP = "\n"


def huella_de_documento(huellas_de_anexos: list[str]) -> str | None:
    """La huella del documento a partir de las de sus anexos.

    Devuelve ``None`` cuando no hay ninguna — sin huella se DECLARA, no se
    finge. Las entradas se normalizan a minúsculas: el SHA-256 hexadecimal es
    el mismo valor en cualquier caja, y compararlo como texto sin normalizar
    haría que dos escrituras del mismo hash parecieran documentos distintos.
    """
    limpias = sorted({h.strip().lower() for h in huellas_de_anexos if h and h.strip()})
    if not limpias:
        return None
    if len(limpias) == 1:
        return limpias[0]
    material = _SEP.join(limpias) + _SEP
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
