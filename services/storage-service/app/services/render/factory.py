# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Selección del renderizador de documentos (E23, RF-PRO-03).

Gemela de `migracion/factory.py`, y por el mismo motivo: sin herramienta se
devuelve `None` y quien llama responde `503`. **No hay stub** — un renderizador
falso tendría que inventarse el documento.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.services.render.types import DocumentRenderer

logger = logging.getLogger(__name__)


class _SettingsLike(Protocol):
    render_engine: str


def get_document_renderer(settings: _SettingsLike) -> DocumentRenderer | None:
    """El renderizador a usar, o `None` si este despliegue no tiene con qué."""
    nombre = getattr(settings, "render_engine", "none")

    if nombre == "weasyprint":
        # El import se hace AQUÍ, no arriba: WeasyPrint carga Pango y Cairo por
        # `ctypes` al importarse, y en un despliegue sin esas librerías el fallo
        # es un `OSError` en tiempo de import. Cazarlo aquí convierte «falta una
        # librería del sistema» en «este despliegue no genera documentos», que
        # es la verdad y no tumba el servicio entero.
        try:
            import weasyprint  # noqa: F401, PLC0415

            from app.services.render.weasyprint_renderer import WeasyPrintRenderer
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RENDER_ENGINE=weasyprint solicitado, pero WeasyPrint no se pudo cargar "
                "(%s) -- NO hay generacion de documentos en este despliegue (no se "
                "degrada a un stub: un renderizador falso tendria que inventarse el "
                "documento).",
                exc,
            )
            return None
        return WeasyPrintRenderer(
            timeout_seconds=getattr(settings, "render_timeout_seconds", 60),
            max_concurrency=getattr(settings, "render_max_concurrency", 2),
        )

    if nombre != "none":
        logger.warning("RENDER_ENGINE=%r desconocido -- sin generacion de documentos.", nombre)
    return None
