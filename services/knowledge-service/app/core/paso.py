# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Único punto por el que un registro sale hacia el proveedor (ADR-045 §3).

Relee la bandera, el proveedor, la suspensión y el nivel vivo en el momento de
enviar. El nivel que traía el evento no se usa. Si no puede salir, no hay
llamada. La sensibilidad del cuerpo que sí sale es la del nivel releído, no la
que haya puesto quien llama.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from orpycamcp_common.tenant import slug_valido

from app.clients.ppi import ClientePpi
from app.core.clearance import motivo_salida

logger = logging.getLogger(__name__)

# Códigos que la conexión PPI declara para los niveles 1, 2 y 3.
_SENSIBILIDAD = {1: "public", 2: "reserved", 3: "classified"}

_POLITICA = """
SELECT asistente_habilitado, intelligence_provider, intelligence_levels
  FROM public.tenants
 WHERE slug = $1
"""

_SUSPENSION = """
SELECT send_suspended
  FROM intelligence_state
 WHERE id = 1
"""

# El nivel del evento no sirve: entre el evento y el envío el radicado puede
# haberse reclasificado o anulado. `anulado` no sale.
_NIVEL_VIVO = """
SELECT nivel_seguridad
  FROM radicados
 WHERE id = $1
   AND status <> 'anulado'
"""


@dataclass(frozen=True)
class Decision:
    sale: bool
    motivo: str


def _estampar(cuerpo: dict[str, Any], sensibilidad: str) -> dict[str, Any]:
    """Copia el lote y fija la sensibilidad de cada ítem con el nivel releído."""
    items = cuerpo.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("sin items")
    nuevos: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("item")
        copia = dict(item)
        estampados = 0
        for clave in ("record", "attachment"):
            objeto = copia.get(clave)
            if isinstance(objeto, dict):
                copia[clave] = {**objeto, "sensitivity": sensibilidad}
                estampados += 1
        if estampados == 0:
            raise ValueError("item sin objeto")
        nuevos.append(copia)
    return {**cuerpo, "items": nuevos}


async def _leer(conn: Any, sql: str, *args: Any) -> Any:
    try:
        return await conn.fetchrow(sql, *args)
    except AssertionError:
        raise
    except Exception:
        logger.warning("no se pudo leer la barrera de salida", exc_info=True)
        return None


async def enviar(
    conn: Any,
    slug: str,
    radicado_id: uuid.UUID,
    cliente: ClientePpi,
    cuerpo: dict[str, Any],
) -> Decision:
    """Envía el lote si el registro puede salir. Si no, no llama al cliente."""
    if not slug_valido(slug):
        return Decision(False, "registro")

    politica = await _leer(conn, _POLITICA, slug)
    if politica is None:
        return Decision(False, "registro")
    estado = await _leer(conn, _SUSPENSION)
    if estado is None:
        return Decision(False, "suspendido")

    bandera = bool(politica["asistente_habilitado"])
    proveedor = str(politica["intelligence_provider"])
    niveles = politica["intelligence_levels"] or []
    suspendido = bool(estado["send_suspended"])

    fila = await _leer(conn, _NIVEL_VIVO, radicado_id)
    nivel = None if fila is None else fila["nivel_seguridad"]
    motivo = motivo_salida(
        bandera=bandera,
        proveedor=proveedor,
        niveles_habilitados=niveles,
        suspendido=suspendido,
        nivel_vivo=nivel,
    )
    if motivo is not None:
        return Decision(False, motivo)

    sensibilidad = _SENSIBILIDAD[int(nivel)]
    try:
        lote = _estampar(cuerpo, sensibilidad)
    except ValueError:
        return Decision(False, "cuerpo")
    await cliente.enviar(lote)
    return Decision(True, "ok")
