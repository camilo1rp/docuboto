# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reconciliación periódica (ADR-045 §2).

Dos lecturas. El libro: un push aceptado, sin purga posterior, cuyo radicado
ya no está, está anulado o salió de los niveles habilitados. El inventario
del proveedor: una página de lo que conserva. Lo que no debe seguir ahí se
borra por el punto de paso y deja traza. Con el interruptor apagado no hay
ninguna llamada.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.core import paso

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit

    _AUDIT = True
except ImportError:  # pragma: no cover
    audit = None  # type: ignore[assignment]
    _AUDIT = False

_TOPE_LIBRO = 20
_TOPE_INVENTARIO = 100
_SERVICIO = "knowledge-service"
_REF = re.compile(r"^[A-Za-z0-9._:~-]{1,200}$")
_CODIGO = {"public": 1, "reserved": 2, "classified": 3}
_MALA = object()

_LIBRO = """
-- reconciliacion:libro
SELECT record_ref
  FROM (
    SELECT DISTINCT ON (record_ref) record_ref, operation
      FROM intelligence_send_log
     WHERE result = 'accepted'
     ORDER BY record_ref, at DESC
  ) ultimo
  LEFT JOIN radicados r
    ON r.id::text = ultimo.record_ref
 WHERE ultimo.operation = 'push'
   AND (
        r.id IS NULL
        OR r.status = 'anulado'
        OR r.nivel_seguridad IS NULL
        OR NOT (r.nivel_seguridad = ANY($1::smallint[]))
   )
 ORDER BY record_ref
 LIMIT $2
"""

_CURSOR = """
-- reconciliacion:cursor
SELECT inventory_cursor
  FROM intelligence_state
 WHERE id = 1
"""

_GUARDA = """
UPDATE intelligence_state
   SET inventory_cursor = $1, updated_at = now()
 WHERE id = 1
"""

_ANEXO = """
-- reconciliacion:anexo
SELECT radicado_id
  FROM anexos
 WHERE id = $1
"""


@dataclass(frozen=True)
class Barrido:
    borrados: int
    revisados: int


async def reconciliar(conn: Any, slug: str, cliente: Any) -> Barrido:
    """Una página del libro y una del inventario. Cero llamadas si no hay envío."""
    politica = await paso.leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return Barrido(0, 0)
    hechos: set[str] = set()
    borrados = await _libro(conn, slug, cliente, politica.niveles, hechos)
    cursor_fila = await conn.fetchrow(_CURSOR)
    cursor_actual = None if cursor_fila is None else cursor_fila["inventory_cursor"]
    pagina = await paso.inventario(
        conn, slug, cliente, cursor=cursor_actual, limite=_TOPE_INVENTARIO
    )
    if not pagina.sale or not isinstance(pagina.cuerpo, dict):
        return Barrido(borrados, 0)
    cursor = _cursor(pagina.cuerpo)
    items = pagina.cuerpo.get("items")
    if cursor is _MALA or not isinstance(items, list):
        return Barrido(borrados, 0)
    extra, limpio = await _inventario(
        conn, slug, cliente, items, politica.niveles, hechos
    )
    if limpio:
        await conn.execute(_GUARDA, cursor)
    return Barrido(borrados + extra, len(items))


async def _libro(
    conn: Any,
    slug: str,
    cliente: Any,
    niveles: tuple[int, ...],
    hechos: set[str],
) -> int:
    filas = await conn.fetch(_LIBRO, list(niveles), _TOPE_LIBRO)
    borrados = 0
    for fila in filas:
        ref = str(fila["record_ref"])
        try:
            if await _borrar_radicado(conn, slug, cliente, ref, hechos, "libro"):
                borrados += 1
        except Exception:
            logger.warning("no se pudo reconciliar una fila del libro")
    return borrados


async def _inventario(
    conn: Any,
    slug: str,
    cliente: Any,
    items: list[Any],
    niveles: tuple[int, ...],
    hechos: set[str],
) -> tuple[int, bool]:
    habilitados = {n for n in niveles if n in (1, 2, 3)}
    borrados = 0
    limpio = True
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            if await _item(conn, slug, cliente, item, habilitados, hechos):
                borrados += 1
        except Exception:
            limpio = False
            logger.warning("no se pudo reconciliar una fila del inventario")
    return borrados, limpio


async def _item(
    conn: Any,
    slug: str,
    cliente: Any,
    item: dict[str, Any],
    habilitados: set[int],
    hechos: set[str],
) -> bool:
    ref = str(item.get("external_ref") or "")
    if not _REF.fullmatch(ref) or ref in hechos:
        return False
    ilegal = _CODIGO.get(item.get("sensitivity")) not in habilitados
    tipo = item.get("type")
    ident = _uuid(ref)
    if tipo == "record" and ident is not None:
        return await _borrar_radicado(
            conn, slug, cliente, ref, hechos, "inventario", ilegal=ilegal
        )
    if tipo == "attachment" and ident is not None:
        padre = await conn.fetchrow(_ANEXO, ident)
        if padre is not None:
            padre_ref = str(padre["radicado_id"])
            if await _borrar_radicado(conn, slug, cliente, padre_ref, hechos, "inventario"):
                return True
        if ilegal:
            return await _expulsar(conn, slug, cliente, ref, "reclassified", hechos, "inventario")
        return False
    if ilegal:
        return await _expulsar(conn, slug, cliente, ref, "reclassified", hechos, "inventario")
    return False


async def _borrar_radicado(
    conn: Any,
    slug: str,
    cliente: Any,
    ref: str,
    hechos: set[str],
    origen: str,
    *,
    ilegal: bool = False,
) -> bool:
    """Borra por el nivel vivo. Si el inventario trae un nivel no habilitado
    y el registro vivo sí puede seguir, igual se expulsa esa copia."""
    if ref in hechos:
        return False
    ident = _uuid(ref)
    if ident is None:
        return False
    decision = await paso.borrar(conn, slug, ident, cliente)
    if decision.sale:
        hechos.add(ref)
        await _alerta(conn, slug, ref, "borrado", origen)
        return True
    if ilegal:
        return await _expulsar(conn, slug, cliente, ref, "reclassified", hechos, origen)
    return False


async def _expulsar(
    conn: Any,
    slug: str,
    cliente: Any,
    ref: str,
    razon: str,
    hechos: set[str],
    origen: str,
) -> bool:
    if ref in hechos:
        return False
    decision = await paso.expulsar(conn, slug, ref, cliente, razon=razon)
    if not decision.sale:
        return False
    hechos.add(ref)
    await _alerta(conn, slug, ref, razon, origen)
    return True


async def _alerta(conn: Any, slug: str, ref: str, motivo: str, origen: str) -> None:
    logger.info("reconciliacion borra motivo=%s origen=%s", motivo, origen)
    if not _AUDIT or audit is None:
        return
    try:
        await audit.append(
            conn,
            tenant_slug=slug,
            service=_SERVICIO,
            action="intelligence.reconciliacion",
            object_type="radicado",
            object_ref=ref,
            actor="system",
            canal="system",
            payload={"motivo": motivo, "origen": origen},
        )
    except Exception:
        logger.warning("no se pudo dejar la traza de la reconciliación")


def _cursor(pagina: dict[str, Any]) -> Any:
    if "items" not in pagina or "next_cursor" not in pagina:
        return _MALA
    crudo = pagina["next_cursor"]
    if crudo is None or crudo == "":
        return None
    if not isinstance(crudo, str) or not 1 <= len(crudo) <= 512:
        return _MALA
    return crudo


def _uuid(ref: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(ref)
    except ValueError:
        return None
