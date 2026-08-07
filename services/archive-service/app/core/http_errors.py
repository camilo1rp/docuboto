# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Extracción del motivo de una ``HTTPException`` para el asiento de auditoría."""

from __future__ import annotations

from fastapi import HTTPException


def motivo_de(exc: HTTPException) -> str:
    """El código de error de ``exc``, o su representación textual.

    Los routers de este servicio lanzan ``HTTPException`` con un ``detail``
    que es un ``dict`` (``{"error": ..., "detail": ..., "status": ...}``, la
    convención de error de la API), pero Starlette declara ese campo como
    ``str``. Esa discrepancia hacía que ``exc.detail.get("error")`` fuera,
    para el verificador de tipos, un acceso imposible — repetido en siete
    puntos que auditan un fallo.

    Se centraliza aquí en vez de anotar cada sitio: el ensanchamiento a
    ``object`` es lo que permite que la comprobación de ``dict`` signifique
    algo, y así hay UN solo lugar donde consta por qué.
    """
    detail: object = exc.detail
    if isinstance(detail, dict):
        error = detail.get("error")
        return str(error) if error is not None else str(detail)
    return str(detail)
