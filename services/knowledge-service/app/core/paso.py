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
llamada. La sensibilidad del cuerpo que sí sale es la del nivel releído.

El borrado no usa esa misma pregunta: con el interruptor encendido, un
radicado anulado, purgado o de un nivel ya no habilitado ordena borrar. Con
el interruptor apagado no hay ninguna llamada. Cada llamada que ocurre deja
una fila en el libro de envíos.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from orpycamcp_common.tenant import slug_valido

from app.clients.ppi import ClientePpi
from app.core.clearance import motivo_interruptor, motivo_salida

logger = logging.getLogger(__name__)

# Códigos que la conexión PPI declara para los niveles 1, 2 y 3.
_SENSIBILIDAD = {1: "public", 2: "reserved", 3: "classified"}

_POLITICA = """
SELECT asistente_habilitado, intelligence_provider, intelligence_levels,
       intelligence_send_metadata
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
-- paso:nivel
SELECT nivel_seguridad
  FROM radicados
 WHERE id = $1
   AND status <> 'anulado'
"""

# El borrado sí lee el anulado: es una de las razones para ordenarlo.
_ESTADO = """
-- paso:estado
SELECT nivel_seguridad, status
  FROM radicados
 WHERE id = $1
"""

_LIBRO = """
INSERT INTO intelligence_send_log
    (operation, record_ref, level, body_sha256, destination, result)
VALUES ($1, $2, $3, $4, $5, $6)
"""


class _Salida(Protocol):
    @property
    def destino(self) -> str: ...

    async def enviar(self, cuerpo: dict[str, Any]) -> Any: ...


class _SinDestino:
    """No hay URL de proveedor. Llamar es un fallo, no un envío silencioso."""

    destino = ""

    async def enviar(self, cuerpo: dict[str, Any]) -> Any:
        raise RuntimeError("PPI_PROVIDER_URL no está definido")


def destino_de(base_url: str, transporte: Any) -> _Salida:
    """El cliente si hay URL. Si no, un destino que falla al usarse."""
    if not base_url.strip():
        return _SinDestino()
    return ClientePpi(transporte, base_url=base_url)


@dataclass(frozen=True)
class Decision:
    sale: bool
    motivo: str


@dataclass(frozen=True)
class Politica:
    """Lectura del interruptor. ``completa`` es False si faltó la entidad o el estado."""

    motivo: str | None
    niveles: tuple[int, ...]
    metadata: bool
    bandera: bool
    proveedor: str
    suspendido: bool
    completa: bool


def huella(cuerpo: dict[str, Any]) -> str:
    """SHA-256 hex del JSON canónico (claves ordenadas, sin espacios)."""
    canonico = json.dumps(cuerpo, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode()).hexdigest()


def _niveles(raw: Any) -> tuple[int, ...]:
    if not raw:
        return ()
    salida: list[int] = []
    for valor in raw:
        try:
            salida.append(int(valor))
        except (TypeError, ValueError):
            continue
    return tuple(salida)


def _con_metadata(fila: Any) -> bool:
    try:
        return bool(fila["intelligence_send_metadata"])
    except (KeyError, TypeError):
        return False


def _nivel_libro(nivel: Any) -> int | None:
    try:
        valor = int(nivel)
    except (TypeError, ValueError):
        return None
    return valor if valor in (1, 2, 3) else None


async def leer_politica(conn: Any, slug: str) -> Politica:
    """Relee bandera, proveedor, niveles, metadatos y suspensión."""
    vacia = Politica("registro", (), False, False, "", False, False)
    if not slug_valido(slug):
        return vacia
    fila = await conn.fetchrow(_POLITICA, slug)
    if fila is None:
        return vacia
    estado = await conn.fetchrow(_SUSPENSION)
    if estado is None:
        return Politica("suspendido", (), False, False, "", True, False)
    bandera = bool(fila["asistente_habilitado"])
    proveedor = str(fila["intelligence_provider"])
    suspendido = bool(estado["send_suspended"])
    return Politica(
        motivo_interruptor(bandera=bandera, proveedor=proveedor, suspendido=suspendido),
        _niveles(fila["intelligence_levels"]),
        _con_metadata(fila),
        bandera,
        proveedor,
        suspendido,
        True,
    )


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


def _quitar_metadata(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """Saca ``metadata`` de cada record. La bandera apagada no lo deja salir."""
    items: list[dict[str, Any]] = []
    for item in cuerpo["items"]:
        record = item.get("record")
        if isinstance(record, dict) and "metadata" in record:
            limpio = {clave: valor for clave, valor in record.items() if clave != "metadata"}
            item = {**item, "record": limpio}
        items.append(item)
    return {**cuerpo, "items": items}


async def _anotar(
    conn: Any,
    *,
    operation: str,
    record_ref: str,
    level: int | None,
    cuerpo: dict[str, Any],
    destino: str,
    result: str,
) -> None:
    if not destino or len(destino) > 500:
        raise RuntimeError("destino del libro")
    await conn.execute(
        _LIBRO, operation, record_ref[:200], level, huella(cuerpo), destino, result
    )


async def _enviar_y_anotar(
    conn: Any,
    cliente: _Salida,
    operation: str,
    record_ref: str,
    level: int | None,
    lote: dict[str, Any],
) -> None:
    try:
        await cliente.enviar(lote)
    except Exception:
        try:
            await _anotar(
                conn,
                operation=operation,
                record_ref=record_ref,
                level=level,
                cuerpo=lote,
                destino=cliente.destino,
                result="error",
            )
        except Exception:
            logger.warning("el libro no registró el fallo de envío", exc_info=True)
        raise
    await _anotar(
        conn,
        operation=operation,
        record_ref=record_ref,
        level=level,
        cuerpo=lote,
        destino=cliente.destino,
        result="accepted",
    )


async def enviar(
    conn: Any,
    slug: str,
    radicado_id: uuid.UUID,
    cliente: _Salida,
    cuerpo: dict[str, Any],
) -> Decision:
    """Envía el lote si el registro puede salir. Si no, no llama al cliente."""
    politica = await leer_politica(conn, slug)
    if not politica.completa:
        return Decision(False, politica.motivo or "registro")

    fila = await conn.fetchrow(_NIVEL_VIVO, radicado_id)
    nivel = None if fila is None else fila["nivel_seguridad"]
    motivo = motivo_salida(
        bandera=politica.bandera,
        proveedor=politica.proveedor,
        niveles_habilitados=politica.niveles,
        suspendido=politica.suspendido,
        nivel_vivo=nivel,
    )
    if motivo is not None:
        return Decision(False, motivo)

    sensibilidad = _SENSIBILIDAD[int(nivel)]
    try:
        lote = _estampar(cuerpo, sensibilidad)
    except ValueError:
        return Decision(False, "cuerpo")
    if not politica.metadata:
        lote = _quitar_metadata(lote)
    await _enviar_y_anotar(conn, cliente, "push", str(radicado_id), int(nivel), lote)
    return Decision(True, "ok")


async def borrar(
    conn: Any,
    slug: str,
    radicado_id: uuid.UUID,
    cliente: _Salida,
) -> Decision:
    """Ordena borrar el registro si ya no debe seguir en el proveedor.

    Con el interruptor apagado no hay llamada. Si el nivel vivo sigue
    habilitado y el radicado no está anulado, tampoco: el contenido se
    empuja, no se borra.
    """
    politica = await leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return Decision(False, politica.motivo or "registro")

    fila = await conn.fetchrow(_ESTADO, radicado_id)
    if fila is None:
        razon = "purged"
        nivel = None
    elif str(fila["status"]) == "anulado":
        razon = "annulled"
        nivel = _nivel_libro(fila["nivel_seguridad"])
    elif motivo_salida(
        bandera=politica.bandera,
        proveedor=politica.proveedor,
        niveles_habilitados=politica.niveles,
        suspendido=politica.suspendido,
        nivel_vivo=fila["nivel_seguridad"],
    ) is None:
        return Decision(False, "sigue")
    else:
        razon = "reclassified"
        nivel = _nivel_libro(fila["nivel_seguridad"])

    lote = {
        "items": [
            {
                "type": "deletion",
                "deletion": {
                    "external_ref": str(radicado_id),
                    "reason": razon,
                    "include_children": True,
                },
            }
        ]
    }
    await _enviar_y_anotar(conn, cliente, "purge", str(radicado_id), nivel, lote)
    return Decision(True, "ok")
