# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Validación XSD del índice electrónico antes de sellarlo (AREA-07-01, RT-15).

El XML lo produce `index_builder.build_xml` y el XSD (`schemas/indice-v*.xsd`) existía
solo como documentación: nada en `app/` lo aplicaba, así que un índice con un hijo
fuera de orden o un campo obligatorio ausente se sellaba XAdES-T y se protegía bajo
WORM (irreversible) con un contenido que su propio contrato rechaza.

Los esquemas se cargan UNA vez, al importar, y se eligen por el espacio de nombres del
elemento raíz: una fila anterior al perfil v4 (v2/v3) se valida contra su XSD, no
contra el vigente. Un espacio de nombres sin XSD (las primeras versiones, anteriores a
la numeración de perfiles) no se puede juzgar y se deja pasar: no hay contrato con el
que compararlo, y exigirlo dejaría esos índices sin poder sellarse nunca.

Quien lo llame DEBE degradar a `pendiente_firma` con el motivo `xsd_invalido` (y
asiento) en vez de firmar: ver `IndexService._current_signable_or_none` y la
reconciliación (`jobs/indice_reconciliation.py`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)

MOTIVO_XSD_INVALIDO = "xsd_invalido"

_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def _cargar() -> dict[str, etree.XMLSchema]:
    esquemas: dict[str, etree.XMLSchema] = {}
    for xsd in sorted(_SCHEMAS_DIR.glob("indice-v*.xsd")):
        doc = etree.parse(str(xsd))
        ns = doc.getroot().get("targetNamespace")
        if ns:
            esquemas[ns] = etree.XMLSchema(doc)
    if not esquemas:
        # Sin esquemas no hay gate: fallar al arrancar, no sellar sin validar en silencio.
        raise RuntimeError(f"No hay XSD del índice en {_SCHEMAS_DIR}: el gate de sellado no puede operar")
    return esquemas


_ESQUEMAS = _cargar()


def validar_indice_xml(xml: str) -> str | None:
    """`None` si el XML es válido (o no hay XSD para su espacio de nombres); si no, un
    texto corto con el primer error, apto para el log y el asiento (sin contenido del
    expediente: solo la ruta y el motivo del esquema)."""
    try:
        raiz = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        return f"XML mal formado: {exc}"
    ns = etree.QName(raiz).namespace
    esquema = _ESQUEMAS.get(ns or "")
    if esquema is None:
        logger.warning("Índice con espacio de nombres sin XSD (%r): no se valida", ns)
        return None
    # `assertValid` es la llamada que el test de mutación quita.
    try:
        esquema.assertValid(raiz)
    except etree.DocumentInvalid as exc:
        return str(exc.error_log.last_error) if exc.error_log else str(exc)
    return None
