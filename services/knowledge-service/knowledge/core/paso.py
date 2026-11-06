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

El borrado de un radicado no usa esa misma pregunta: con el interruptor
encendido, un radicado anulado, purgado o de un nivel ya no habilitado ordena
borrar. Con el interruptor apagado esa llamada no sale.

Tres llamadas no transmiten registros y por eso no miran el interruptor: leer
la declaración firmada (el administrador la nombra antes de pasar a ``ppi`` y
otra vez con el envío suspendido), ordenar el borrado de lo ya transmitido
(al deshabilitar un nivel, o al retirar los datos con la entidad en ``local``)
y contar los radicados que el proveedor aún conserva, para la pantalla de
administración. El borrado deja una fila en el libro de envíos. La lectura de
la declaración y el conteo no.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from orpycamcp_common.tenant import slug_valido

from knowledge.clients.ppi import ClientePpi
from knowledge.core import medidor, salida_http
from knowledge.core.clearance import motivo_interruptor, motivo_salida
from knowledge.core.seudonimo import abrir as abrir_seudonimo
from knowledge.core.seudonimo import referenciar

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

    async def consultar(self, cuerpo: dict[str, Any], *, timeout: float) -> Any: ...

    async def inventario(self, *, cursor: str | None, limite: int) -> Any: ...

    async def capacidades(self) -> Any: ...

    async def declarar(self, cuerpo: dict[str, Any]) -> Any: ...

    @property
    def destino_purga(self) -> str: ...

    async def purgar(self, cuerpo: dict[str, Any]) -> Any: ...


class _SinDestino:
    """No hay URL de proveedor. Llamar es un fallo, no un envío silencioso."""

    destino = ""

    async def enviar(self, cuerpo: dict[str, Any]) -> Any:
        raise RuntimeError("PPI_PROVIDER_URL no está definido")

    async def consultar(self, cuerpo: dict[str, Any], *, timeout: float) -> Any:
        raise RuntimeError("PPI_PROVIDER_URL no está definido")

    async def inventario(self, *, cursor: str | None, limite: int) -> Any:
        raise RuntimeError("PPI_PROVIDER_URL no está definido")

    async def capacidades(self) -> Any:
        raise RuntimeError("PPI_PROVIDER_URL no está definido")

    async def declarar(self, cuerpo: dict[str, Any]) -> Any:
        raise RuntimeError("PPI_PROVIDER_URL no está definido")

    destino_purga = ""

    async def purgar(self, cuerpo: dict[str, Any]) -> Any:
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
class Respuesta:
    """Resultado de una consulta. ``cuerpo`` solo viene si el proveedor respondió."""

    sale: bool
    motivo: str
    cuerpo: dict[str, Any] | None


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


def codigo_clearance(usuario: int, habilitados: tuple[int, ...]) -> str | None:
    """El código más alto que esta persona puede ver y la entidad transmite."""
    try:
        tope = int(usuario)
    except (TypeError, ValueError):
        return None
    visibles = [n for n in habilitados if n in _SENSIBILIDAD and n <= tope]
    if not visibles:
        return None
    return _SENSIBILIDAD[max(visibles)]


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
    slug: str,
    cliente: _Salida,
    operation: str,
    record_ref: str,
    level: int | None,
    lote: dict[str, Any],
) -> None:
    try:
        with salida_http.con_entidad(conn, slug):
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
        if motivo in medidor.RAZONES_BARRERA:
            await medidor.anotar(conn, medidor.BARRERA, motivo)
        return Decision(False, motivo)

    sensibilidad = _SENSIBILIDAD[int(nivel)]
    try:
        lote = _estampar(cuerpo, sensibilidad)
    except ValueError:
        return Decision(False, "cuerpo")
    if not politica.metadata:
        lote = _quitar_metadata(lote)
    await _enviar_y_anotar(conn, slug, cliente, "push", str(radicado_id), int(nivel), lote)
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
    await _enviar_y_anotar(conn, slug, cliente, "purge", str(radicado_id), nivel, lote)
    return Decision(True, "ok")


async def consultar(
    conn: Any,
    slug: str,
    cliente: _Salida,
    *,
    modo: str,
    texto: str,
    actor: uuid.UUID,
    clearance: int,
    limite: int,
    plazo: float,
    alcance: uuid.UUID | None = None,
) -> Respuesta:
    """Pregunta al proveedor si el interruptor está encendido.

    El seudónimo se calcula aquí. Quien llama no puede pasar el id crudo.
    Un fallo de transporte, un plazo vencido o una clave ausente no lanzan:
    la pasarela responde con el índice local.
    """
    politica = await leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return Respuesta(False, politica.motivo or "registro", None)
    if not getattr(cliente, "destino", ""):
        return Respuesta(False, "destino", None)
    if modo not in ("search", "ask"):
        return Respuesta(False, "modo", None)
    limpio = texto.strip()
    if not limpio or not (1 <= limite <= 50) or plazo <= 0:
        return Respuesta(False, "texto", None)
    codigo = codigo_clearance(clearance, politica.niveles)
    if codigo is None:
        return Respuesta(False, "clearance", None)
    clave = await abrir_seudonimo(conn, slug)
    if clave is None:
        return Respuesta(False, "seudonimo", None)

    cuerpo: dict[str, Any] = {
        "mode": modo,
        "text": limpio[:4000],
        "on_behalf_of": referenciar(clave, actor),
        "clearance": codigo,
        "limit": limite,
        "language": "es",
    }
    if alcance is not None:
        cuerpo["scope"] = {"record_ref": str(alcance), "include_children": True}
    try:
        with salida_http.con_entidad(conn, slug):
            recibido = await cliente.consultar(cuerpo, timeout=plazo)
    except Exception:
        logger.warning("la consulta al proveedor falló")
        return Respuesta(False, "proveedor", None)
    if not isinstance(recibido, dict):
        return Respuesta(False, "proveedor", None)
    return Respuesta(True, "ok", recibido)


_RAZONES = frozenset({"reclassified", "annulled", "purged", "withdrawn", "host_request"})


async def inventario(
    conn: Any,
    slug: str,
    cliente: _Salida,
    *,
    cursor: str | None,
    limite: int,
) -> Respuesta:
    """Una página del inventario. Con el interruptor apagado no llama."""
    politica = await leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return Respuesta(False, politica.motivo or "registro", None)
    if not getattr(cliente, "destino", ""):
        return Respuesta(False, "destino", None)
    if not 1 <= limite <= 1000:
        return Respuesta(False, "limite", None)
    try:
        with salida_http.con_entidad(conn, slug):
            recibido = await cliente.inventario(cursor=cursor, limite=limite)
    except Exception:
        logger.warning("el inventario no respondió")
        return Respuesta(False, "proveedor", None)
    if not isinstance(recibido, dict):
        return Respuesta(False, "proveedor", None)
    return Respuesta(True, "ok", recibido)


_PAGINAS_INVENTARIO = 20
_LIMITE_INVENTARIO = 1000
_TIPOS_INVENTARIO = frozenset({"record", "attachment"})


async def contar_radicados(
    conn: Any,
    slug: str,
    cliente: _Salida,
    *,
    tope_paginas: int = _PAGINAS_INVENTARIO,
) -> Respuesta:
    """Cuántos radicados conserva el proveedor. No mira el interruptor.

    La pantalla de administración lo muestra con la entidad en ``local``,
    para que el retiro no se olvide. No transmite registros y no escribe el
    libro. Recorre el inventario de a mil y se detiene a las 20 páginas: si
    queda cursor, ``complete`` es falso y ``records`` es un mínimo.
    """
    if not getattr(cliente, "destino", ""):
        return Respuesta(False, "destino", None)
    if tope_paginas < 1:
        return Respuesta(False, "limite", None)
    cursor: str | None = None
    vistos: set[str] = set()
    total = 0
    for _ in range(tope_paginas):
        try:
            with salida_http.con_entidad(conn, slug):
                recibido = await cliente.inventario(
                    cursor=cursor, limite=_LIMITE_INVENTARIO
                )
        except Exception:
            logger.warning("no se pudo contar el inventario")
            return Respuesta(False, "proveedor", None)
        pagina = _pagina_inventario(recibido)
        if pagina is None:
            return Respuesta(False, "proveedor", None)
        items, siguiente = pagina
        total += sum(1 for item in items if item.get("type") == "record")
        if siguiente is None:
            return Respuesta(True, "ok", {"records": total, "complete": True})
        if siguiente in vistos:
            return Respuesta(False, "proveedor", None)
        vistos.add(siguiente)
        cursor = siguiente
    return Respuesta(True, "ok", {"records": total, "complete": False})


def _pagina_inventario(recibido: Any) -> tuple[list[dict[str, Any]], str | None] | None:
    if not isinstance(recibido, dict) or "items" not in recibido or "next_cursor" not in recibido:
        return None
    items = recibido["items"]
    cursor = recibido["next_cursor"]
    if not isinstance(items, list):
        return None
    if cursor is not None and not isinstance(cursor, str):
        return None
    if isinstance(cursor, str) and (not cursor or len(cursor) > 512):
        return None
    filas: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in _TIPOS_INVENTARIO:
            return None
        filas.append(item)
    return filas, cursor


async def expulsar(
    conn: Any,
    slug: str,
    external_ref: str,
    cliente: _Salida,
    *,
    razon: str,
) -> Decision:
    """Borra una referencia que ya no debe estar, sin releer el radicado.

    La usa la reconciliación cuando el inventario trae un nivel que la
    entidad no transmite. Con el interruptor apagado no hay llamada.
    """
    politica = await leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return Decision(False, politica.motivo or "registro")
    if razon not in _RAZONES or not external_ref or len(external_ref) > 200:
        return Decision(False, "ref")
    lote = {
        "items": [
            {
                "type": "deletion",
                "deletion": {
                    "external_ref": external_ref,
                    "reason": razon,
                    "include_children": True,
                },
            }
        ]
    }
    await _enviar_y_anotar(conn, slug, cliente, "purge", external_ref, None, lote)
    return Decision(True, "ok")


async def _salida_abierta(conn: Any, slug: str, cliente: _Salida) -> str | None:
    """None si se puede llamar. Si no, el motivo, sin tocar al proveedor."""
    politica = await leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return politica.motivo or "registro"
    if not getattr(cliente, "destino", ""):
        return "destino"
    return None


async def capacidades(conn: Any, slug: str, cliente: _Salida) -> Respuesta:
    """La declaración firmada. Con el interruptor apagado no llama."""
    motivo = await _salida_abierta(conn, slug, cliente)
    if motivo is not None:
        return Respuesta(False, motivo, None)
    try:
        with salida_http.con_entidad(conn, slug):
            recibido = await cliente.capacidades()
    except Exception:
        logger.warning("la declaración no respondió")
        return Respuesta(False, "proveedor", None)
    if not isinstance(recibido, dict):
        return Respuesta(False, "proveedor", None)
    return Respuesta(True, "ok", recibido)


async def declarar(conn: Any, slug: str, cliente: _Salida, cuerpo: dict[str, Any]) -> Respuesta:
    """Guarda la conexión. Con el interruptor apagado no llama."""
    motivo = await _salida_abierta(conn, slug, cliente)
    if motivo is not None:
        return Respuesta(False, motivo, None)
    try:
        with salida_http.con_entidad(conn, slug):
            recibido = await cliente.declarar(cuerpo)
    except Exception:
        logger.warning("la conexión no quedó declarada")
        return Respuesta(False, "proveedor", None)
    if not isinstance(recibido, dict):
        return Respuesta(False, "proveedor", None)
    return Respuesta(True, "ok", recibido)


async def leer_declaracion(conn: Any, slug: str, cliente: _Salida) -> Respuesta:
    """La declaración firmada, también con el interruptor apagado.

    No transmite registros. La pide el acto de configuración, antes de pasar
    a ``ppi`` y otra vez cuando el envío está suspendido. Sin URL no llama.
    """
    if not getattr(cliente, "destino", ""):
        return Respuesta(False, "destino", None)
    try:
        with salida_http.con_entidad(conn, slug):
            recibido = await cliente.capacidades()
    except Exception:
        logger.warning("la declaración no respondió")
        return Respuesta(False, "proveedor", None)
    if not isinstance(recibido, dict):
        return Respuesta(False, "proveedor", None)
    return Respuesta(True, "ok", recibido)


_CODIGO = {1: "public", 2: "reserved", 3: "classified"}


async def purgar(
    conn: Any,
    slug: str,
    cliente: _Salida,
    *,
    razon: str,
    actor: uuid.UUID,
    niveles: tuple[int, ...] | None,
) -> Respuesta:
    """Ordena el borrado de lo ya transmitido. No mira el interruptor.

    Sin ``niveles`` borra todo lo de la entidad (``withdrawn``). Con niveles,
    solo esos (``host_request``). El seudónimo del administrador va en
    ``requested_by``. Sin URL, sin clave o con el proveedor caído no lanza:
    quien llama decide si el acto quedó pendiente.
    """
    if not getattr(cliente, "destino", ""):
        return Respuesta(False, "destino", None)
    destino = getattr(cliente, "destino_purga", "")
    if not isinstance(destino, str) or not destino:
        return Respuesta(False, "destino", None)
    filas = _filas_purga(razon, niveles)
    if filas is None:
        return Respuesta(False, "nivel", None)
    clave = await abrir_seudonimo(conn, slug)
    if clave is None:
        return Respuesta(False, "seudonimo", None)
    cuerpo: dict[str, Any] = {"reason": razon, "requested_by": referenciar(clave, actor)}
    if niveles:
        cuerpo["levels"] = [_CODIGO[nivel] for nivel, _ref in filas]
    try:
        with salida_http.con_entidad(conn, slug):
            recibido = await cliente.purgar(cuerpo)
    except Exception:
        logger.warning("el borrado en el proveedor no quedó aceptado")
        await _anotar_purga(conn, filas, cuerpo, destino, "error")
        return Respuesta(False, "proveedor", None)
    if not isinstance(recibido, dict) or not isinstance(recibido.get("purge_ref"), str):
        await _anotar_purga(conn, filas, cuerpo, destino, "error")
        return Respuesta(False, "proveedor", None)
    if not await _anotar_purga(conn, filas, cuerpo, destino, "accepted"):
        return Respuesta(False, "libro", None)
    return Respuesta(True, "ok", {"purge_ref": recibido["purge_ref"]})


def _filas_purga(razon: str, niveles: tuple[int, ...] | None) -> list[tuple[int, str]] | None:
    if niveles is None:
        if razon != "withdrawn":
            return None
        return [(1, "tenant")]
    if razon != "host_request" or not niveles:
        return None
    vistos: list[int] = []
    filas: list[tuple[int, str]] = []
    for nivel in niveles:
        if nivel not in _CODIGO or nivel in vistos:
            return None
        vistos.append(nivel)
        filas.append((nivel, f"level:{_CODIGO[nivel]}"))
    return filas


async def _anotar_purga(
    conn: Any,
    filas: list[tuple[int, str]],
    cuerpo: dict[str, Any],
    destino: str,
    result: str,
) -> bool:
    for nivel, ref in filas:
        try:
            await _anotar(
                conn,
                operation="purge",
                record_ref=ref,
                level=nivel,
                cuerpo=cuerpo,
                destino=destino,
                result=result,
            )
        except Exception:
            logger.warning("el libro no registró el borrado", exc_info=True)
            return False
    return True
