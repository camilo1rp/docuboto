# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente HTTP de PPI. No decide qué sale: eso es del punto de paso.

Solo ``app.core.paso`` puede importar este módulo. Un transporte se inyecta
para que las pruebas no abran una red, y para que un transporte falso falle
si algo llama igual.
"""

from __future__ import annotations

from typing import Any, Protocol

RUTA_INGEST = "/v1/ingest"


class Transporte(Protocol):
    async def request(self, method: str, url: str, *, json: dict[str, Any]) -> Any:
        """Hace la petición y devuelve la respuesta del transporte."""


class ClientePpi:
    """POST al proveedor. ``base_url`` vacío no es un destino: falla cerrado."""

    def __init__(self, transporte: Transporte, *, base_url: str) -> None:
        limpio = base_url.strip().rstrip("/")
        if not limpio:
            raise RuntimeError("PPI_PROVIDER_URL no está definido")
        self._transporte = transporte
        self._base_url = limpio

    @property
    def destino(self) -> str:
        """URL del ingest. Es lo que anota el libro de envíos."""
        return f"{self._base_url}{RUTA_INGEST}"

    async def enviar(self, cuerpo: dict[str, Any]) -> Any:
        return await self._transporte.request(
            "POST",
            f"{self._base_url}{RUTA_INGEST}",
            json=cuerpo,
        )
