# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Transporte HTTP hacia el proveedor. No decide qué sale: eso es del punto de paso.

Cada petición declara la versión del protocolo. El plazo de una consulta se
pasa por llamada; el envío usa el plazo del cliente.
"""

from __future__ import annotations

from typing import Any

import httpx

#: Versión que esta pasarela habla. El proveedor la exige en `PPI-Version`.
PPI_VERSION = "1.2.0"

_consulta: TransporteHttp | None = None


class TransporteHttp:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "json": json,
            "headers": {"PPI-Version": PPI_VERSION},
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        respuesta = await self._http.request(method, url, **kwargs)
        respuesta.raise_for_status()
        if not respuesta.content:
            return None
        return respuesta.json()

    async def cerrar(self) -> None:
        await self._http.aclose()


def transporte_de_consulta() -> TransporteHttp:
    """Cliente de las consultas de la API. El consumidor crea el suyo."""
    global _consulta
    if _consulta is None:
        _consulta = TransporteHttp()
    return _consulta


async def cerrar_consulta() -> None:
    global _consulta
    if _consulta is not None:
        await _consulta.cerrar()
        _consulta = None
