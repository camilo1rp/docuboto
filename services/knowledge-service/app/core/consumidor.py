# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Decide qué hace cada evento y avanza la carga inicial.

Empuja altas y cambios. Ordena borrar cuando el evento es anulación o purga,
o cuando una reclasificación deja el nivel fuera de los habilitados. El
cuerpo sale de la fila vigente, no del sobre: el punto de paso vuelve a
leer el nivel antes de llamar.

La carga inicial recorre los radicados de niveles habilitados por
``(updated_at, id)`` y guarda el cursor en ``intelligence_state``. Un
anexo que se agrega después no mueve ``updated_at`` del radicado: llega
como ``document.anexo.added`` y se vuelve a leer la fila con sus ficheros.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core import paso, traduccion
from app.core.paso import Decision

logger = logging.getLogger(__name__)

EMPUJE = frozenset(
    {
        "document.radicado.created",
        "document.radicado.updated",
        "document.anexo.added",
        "workflow.step.created",
        "workflow.step.completed",
        "workflow.radicado.estado_cambiado",
    }
)
BORRADO = frozenset(
    {
        "document.radicado.annulled",
        "document.radicado.purged",
    }
)
RECLASIFICADO = "document.radicado.reclassified"
CONOCIDOS = EMPUJE | BORRADO | {RECLASIFICADO}

# Con el interruptor apagado no se avanza el cursor: al encenderlo, esa
# página sigue pendiente. Un registro que no puede salir (nivel, ausente,
# cuerpo) sí se salta, para no trabar el resto.
_PARAR = frozenset({"bandera", "local", "suspendido", "registro"})

_FILA = """
-- fila-oficial
SELECT id, tracking_number, doc_type, subject, doc_class, dest_dept_code,
       status, registered_at, due_date, sender_name, sender_entity,
       metadata, updated_at
  FROM radicados
 WHERE id = $1
"""

_ANEXOS = """
-- anexos-del-radicado
SELECT a.id, a.filename, a.mime_type, a.file_size, a.es_principal, a.folios,
       f.sha256
  FROM anexos a
  JOIN files f ON f.id = a.file_id
 WHERE a.radicado_id = $1
"""

_CURSOR = """
-- cursor-carga
SELECT backfill_cursor
  FROM intelligence_state
 WHERE id = 1
"""

_PAGINA = """
-- carga-inicial
SELECT id, tracking_number, doc_type, subject, doc_class, dest_dept_code,
       status, registered_at, due_date, sender_name, sender_entity,
       metadata, updated_at
  FROM radicados
 WHERE status <> 'anulado'
   AND nivel_seguridad = ANY($1::smallint[])
   AND (updated_at, id) > ($2::timestamptz, $3::uuid)
 ORDER BY updated_at, id
 LIMIT $4
"""

_AVANZA = """
UPDATE intelligence_state
   SET backfill_cursor = $1, updated_at = now()
 WHERE id = 1
"""

_EPOCA = datetime(1970, 1, 1, tzinfo=UTC)
_NULO = uuid.UUID(int=0)


def _radicado_id(payload: dict[str, Any]) -> uuid.UUID | None:
    raw = payload.get("id") or payload.get("radicado_id")
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _instante(valor: Any) -> datetime:
    if isinstance(valor, datetime):
        return valor if valor.tzinfo is not None else valor.replace(tzinfo=UTC)
    raise ValueError("updated_at")


def _texto_cursor(instante: datetime, ident: Any) -> str:
    momento = _instante(instante)
    return f"{momento.isoformat()}|{ident}"


def _leer_cursor(texto: str | None) -> tuple[datetime, uuid.UUID]:
    if not texto or "|" not in texto:
        return _EPOCA, _NULO
    marca, _, ident = texto.partition("|")
    try:
        instante = datetime.fromisoformat(marca)
        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=UTC)
        return instante, uuid.UUID(ident)
    except ValueError:
        return _EPOCA, _NULO


async def _cuerpo(conn: Any, radicado_id: uuid.UUID, fila: Any | None = None) -> dict[str, Any]:
    if fila is None:
        fila = await conn.fetchrow(_FILA, radicado_id)
    if fila is None:
        raise ValueError("ausente")
    anexos = await conn.fetch(_ANEXOS, fila["id"])
    return traduccion.lote(fila, anexos)


async def _empujar(
    conn: Any,
    slug: str,
    radicado_id: uuid.UUID,
    cliente: Any,
    fila: Any | None = None,
) -> Decision:
    try:
        cuerpo = await _cuerpo(conn, radicado_id, fila)
    except ValueError as exc:
        if str(exc) == "ausente":
            return Decision(False, "ausente")
        return Decision(False, "cuerpo")
    ultima: Decision | None = None
    for trozo in traduccion.trozos(cuerpo):
        ultima = await paso.enviar(conn, slug, radicado_id, cliente, trozo)
        if not ultima.sale:
            return ultima
    return ultima if ultima is not None else Decision(False, "cuerpo")


async def atender(conn: Any, slug: str, envelope: dict[str, Any], cliente: Any) -> Decision:
    """Aplica un sobre. Una decisión cerrada no es un fallo: el mensaje se confirma.

    Una excepción de transporte o de base sí sube, para que el mensaje
    quede en la PEL y se reintente.
    """
    tipo = str(envelope.get("event_type") or "")
    if tipo not in CONOCIDOS:
        return Decision(False, "ajeno")
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        return Decision(False, "ajeno")
    radicado_id = _radicado_id(payload)
    if radicado_id is None:
        return Decision(False, "ajeno")

    if tipo in BORRADO:
        return await paso.borrar(conn, slug, radicado_id, cliente)

    if tipo == RECLASIFICADO:
        decision = await _empujar(conn, slug, radicado_id, cliente)
        if decision.motivo in {"nivel", "ausente"}:
            return await paso.borrar(conn, slug, radicado_id, cliente)
        if decision.motivo == "cuerpo":
            borrado = await paso.borrar(conn, slug, radicado_id, cliente)
            if borrado.motivo == "sigue":
                return decision
            return borrado
        return decision

    return await _empujar(conn, slug, radicado_id, cliente)


async def cargar_pagina(conn: Any, slug: str, cliente: Any, *, limite: int = 20) -> int:
    """Empuja una página de la carga inicial y avanza el cursor.

    Devuelve cuántos registros quedaron detrás del cursor. Cero con el
    interruptor apagado, sin mover el cursor y sin llamar al proveedor.
    """
    politica = await paso.leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return 0

    cursor = await conn.fetchrow(_CURSOR)
    texto = None if cursor is None else cursor["backfill_cursor"]
    desde, ident = _leer_cursor(texto)
    filas = await conn.fetch(_PAGINA, list(politica.niveles), desde, ident, limite)
    hechos = 0
    for fila in filas:
        decision = await _empujar(conn, slug, fila["id"], cliente, fila)
        if decision.motivo in _PARAR:
            return hechos
        await conn.execute(_AVANZA, _texto_cursor(fila["updated_at"], fila["id"]))
        hechos += 1
    return hechos
