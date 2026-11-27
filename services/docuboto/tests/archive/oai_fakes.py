# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Despachador de `conn.fetch` para los tests del OAI/EAD (C-4/C8).

El EAD hace TRES lecturas con `fetch` (cadena de la serie, productoras de la versión
raíz y ruta viva del organigrama). Un `return_value` único devolvería la cadena a las
tres, así que se responde según de qué tabla pregunta el SQL."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any


def fetch_oai(
    cadena: Sequence[dict[str, Any]],
    *,
    productoras: Sequence[dict[str, Any]] = (),
    ruta_viva: Sequence[dict[str, Any]] = (),
) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def _fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
        if "trd_serie_dependencia_productora" in sql:
            return [dict(p) for p in productoras]
        if "FROM dependencias" in sql:
            return [dict(r) for r in ruta_viva]
        return [dict(c) for c in cadena]

    return _fetch


def productora(codigo: int, ruta: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Fila de `trd_serie_dependencia_productora` (jsonb crudo, como lo entrega asyncpg)."""
    return {
        "dependencia_codigo": codigo,
        "ruta_convalidada": json.dumps(list(ruta)) if ruta is not None else None,
    }
