# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Delegación de buscar y preguntar (ADR-045 §5).

Con el interruptor apagado no hay llamada. Con el proveedor encendido, cada
cita se rechequea contra el registro. Si el proveedor no responde, se pasa
del plazo o el envío está suspendido, responde el índice local.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from orpycamcp_common.rbac import resolve_caller_clearance

from knowledge.core import medidor, paso
from knowledge.core.rechequeo import (
    Afirmacion,
    Descarte,
    Hallazgo,
    Registro,
    filtrar_busqueda,
    filtrar_pregunta,
    refs_a_borrar,
)

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit

    _AUDIT = True
except ImportError:  # pragma: no cover
    audit = None  # type: ignore[assignment]
    _AUDIT = False

#: Buscar y antecedentes: plazo 3 s. Preguntar: 30 s (ADR-045 §5).
PLAZO_BUSCAR = 3.0
PLAZO_PREGUNTAR = 30.0

NO_DISPONIBLE = "Respuesta no disponible, inténtelo de nuevo"
RESPONDIDA = (
    "Cada afirmación tiene cita. Verifique el texto contra el radicado fuente "
    "antes de usarlo."
)
INSUFICIENTE = "No hay evidencia suficiente para responder."

_SERVICIO = "knowledge-service"
_VIVOS = """
SELECT id, tracking_number, status, nivel_seguridad
  FROM radicados
 WHERE id = ANY($1::uuid[])
"""


@dataclass(frozen=True)
class Busqueda:
    """``delegada`` es False cuando debe responder el índice local."""

    delegada: bool
    hallazgos: tuple[Hallazgo, ...] = ()


@dataclass(frozen=True)
class Pregunta:
    delegada: bool
    fallo: bool
    status: Literal["answered", "insufficient_evidence", "unavailable"]
    answer: str | None
    citas: tuple[Hallazgo, ...]
    advisory: str


_PREGUNTA_LOCAL = Pregunta(False, False, "unavailable", None, (), "")
_PREGUNTA_FALLO = Pregunta(True, True, "unavailable", None, (), NO_DISPONIBLE)


@dataclass(frozen=True)
class _Intento:
    delegar: bool
    cuerpo: dict[str, Any] | None
    clearance: int
    habilitados: tuple[int, ...]
    respaldo: bool = False


async def buscar(
    conn: Any,
    slug: str,
    cliente: Any,
    *,
    texto: str,
    limite: int,
    actor: uuid.UUID,
    alcance: uuid.UUID | None = None,
) -> Busqueda:
    intento = await _intentar(
        conn,
        slug,
        cliente,
        modo="search",
        texto=texto,
        limite=limite,
        actor=actor,
        alcance=alcance,
        plazo=PLAZO_BUSCAR,
    )
    if not intento.delegar or intento.cuerpo is None:
        if intento.respaldo:
            await _anotar_respaldo(conn)
        return Busqueda(False)
    hits = intento.cuerpo.get("hits")
    if not isinstance(hits, list):
        await _anotar_respaldo(conn)
        return Busqueda(False)
    try:
        vivos = await leer_vivos(conn, _ids(hits, "citation"))
    except Exception:
        logger.warning("no se pudo rechequear la búsqueda; responde el índice local")
        await _anotar_respaldo(conn)
        return Busqueda(False)
    utiles, descartes = filtrar_busqueda(
        hits,
        vivos,
        habilitados=intento.habilitados,
        clearance=intento.clearance,
    )
    await _cerrar(conn, slug, cliente, actor, descartes)
    return Busqueda(True, tuple(utiles))


async def preguntar(
    conn: Any,
    slug: str,
    cliente: Any,
    *,
    texto: str,
    limite: int,
    actor: uuid.UUID,
    alcance: uuid.UUID | None = None,
) -> Pregunta:
    intento = await _intentar(
        conn,
        slug,
        cliente,
        modo="ask",
        texto=texto,
        limite=limite,
        actor=actor,
        alcance=alcance,
        plazo=PLAZO_PREGUNTAR,
    )
    if not intento.delegar:
        return _PREGUNTA_LOCAL
    if intento.cuerpo is None:
        await _anotar_respaldo(conn)
        return _PREGUNTA_FALLO
    estado = intento.cuerpo.get("status")
    if estado not in ("answered", "insufficient_evidence"):
        await _anotar_respaldo(conn)
        return _PREGUNTA_FALLO
    claims = intento.cuerpo.get("claims")
    if not isinstance(claims, list):
        claims = [] if estado == "insufficient_evidence" else None
    if claims is None:
        await _anotar_respaldo(conn)
        return _PREGUNTA_FALLO
    try:
        vivos = await leer_vivos(conn, _ids_de_afirmaciones(claims))
    except Exception:
        logger.warning("no se pudo rechequear la respuesta; responde el índice local")
        await _anotar_respaldo(conn)
        return _PREGUNTA_FALLO
    afirmaciones, descartes = filtrar_pregunta(
        claims,
        vivos,
        habilitados=intento.habilitados,
        clearance=intento.clearance,
    )
    await _cerrar(conn, slug, cliente, actor, descartes)
    return _respuesta(afirmaciones)


async def leer_vivos(conn: Any, ids: list[uuid.UUID]) -> dict[uuid.UUID, Registro]:
    if not ids:
        return {}
    filas = await conn.fetch(_VIVOS, ids)
    salida: dict[uuid.UUID, Registro] = {}
    for fila in filas:
        try:
            ident = fila["id"]
            if not isinstance(ident, uuid.UUID):
                ident = uuid.UUID(str(ident))
            nivel = int(fila["nivel_seguridad"])
        except (TypeError, ValueError, KeyError):
            continue
        salida[ident] = Registro(
            tracking=str(fila["tracking_number"] or ""),
            status=str(fila["status"] or ""),
            nivel=nivel,
        )
    return salida


async def _intentar(
    conn: Any,
    slug: str,
    cliente: Any,
    *,
    modo: str,
    texto: str,
    limite: int,
    actor: uuid.UUID,
    alcance: uuid.UUID | None,
    plazo: float,
) -> _Intento:
    vacio = _Intento(False, None, 1, ())
    try:
        politica = await paso.leer_politica(conn, slug)
    except Exception:
        logger.warning("no se pudo leer la política; responde el índice local")
        return vacio
    if not politica.completa or politica.motivo is not None:
        return _Intento(
            False,
            None,
            1,
            politica.niveles if politica.completa else (),
            politica.completa and politica.motivo == "suspendido",
        )
    try:
        clearance = await resolve_caller_clearance(conn, actor)
    except Exception:
        logger.warning("no se pudo leer el clearance; responde el índice local")
        return _Intento(True, None, 1, politica.niveles, True)
    respuesta = await paso.consultar(
        conn,
        slug,
        cliente,
        modo=modo,
        texto=texto,
        actor=actor,
        clearance=clearance,
        limite=limite,
        plazo=plazo,
        alcance=alcance,
    )
    if not respuesta.sale or not isinstance(respuesta.cuerpo, dict):
        return _Intento(True, None, clearance, politica.niveles, True)
    if respuesta.cuerpo.get("mode") != modo:
        return _Intento(True, None, clearance, politica.niveles, True)
    return _Intento(True, respuesta.cuerpo, clearance, politica.niveles)


async def _anotar_respaldo(conn: Any) -> None:
    await medidor.anotar(conn, medidor.RESPALDO, medidor.RAZON_RESPALDO)


def _respuesta(afirmaciones: list[Afirmacion]) -> Pregunta:
    if not afirmaciones:
        return Pregunta(True, False, "insufficient_evidence", None, (), INSUFICIENTE)
    citas = tuple(cita for afirmacion in afirmaciones for cita in afirmacion.citas)
    texto = "\n\n".join(afirmacion.texto for afirmacion in afirmaciones)
    return Pregunta(True, False, "answered", texto, citas, RESPONDIDA)


async def _cerrar(
    conn: Any,
    slug: str,
    cliente: Any,
    actor: uuid.UUID,
    descartes: list[Descarte],
) -> None:
    for descarte in descartes:
        await _trazar(conn, slug, actor, descarte)
    for ident in refs_a_borrar(descartes):
        try:
            await paso.borrar(conn, slug, ident, cliente)
        except Exception:
            logger.warning("no se pudo ordenar el borrado de una cita descartada")


async def _trazar(conn: Any, slug: str, actor: uuid.UUID, descarte: Descarte) -> None:
    logger.info("cita descartada motivo=%s", descarte.motivo)
    if not _AUDIT or audit is None:
        return
    try:
        await audit.append(
            conn,
            tenant_slug=slug,
            service=_SERVICIO,
            action="intelligence.cita_descartada",
            object_type="radicado",
            object_ref=str(descarte.ref),
            actor=str(actor),
            canal="system",
            payload={"motivo": descarte.motivo},
        )
    except Exception:
        logger.warning("no se pudo dejar la traza de la cita descartada")


def _ids(hits: list[Any], clave: str) -> list[uuid.UUID]:
    vistos: list[uuid.UUID] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        ident = _uuid(hit.get(clave))
        if ident is not None and ident not in vistos:
            vistos.append(ident)
    return vistos


def _ids_de_afirmaciones(claims: list[Any]) -> list[uuid.UUID]:
    vistos: list[uuid.UUID] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        citas = claim.get("citations")
        if not isinstance(citas, list):
            continue
        for cita in citas:
            ident = _uuid(cita)
            if ident is not None and ident not in vistos:
                vistos.append(ident)
    return vistos


def _uuid(cita: Any) -> uuid.UUID | None:
    if not isinstance(cita, dict):
        return None
    try:
        return uuid.UUID(str(cita.get("record_ref")))
    except (TypeError, ValueError):
        return None
