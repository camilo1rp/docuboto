# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Rechequeo en vivo de cada cita (ADR-045 §5).

Una cita se queda si el radicado existe, no está anulado, su nivel vivo está
habilitado y no pasa del clearance de quien pregunta. Si el registro no debe
seguir en el proveedor (no está, está anulado, o el nivel ya no está
habilitado), se ordena borrarlo. Que esta persona no alcance el nivel no lo
borra: otra persona con más clearance puede verlo.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_TOPE_CITA = 2000


@dataclass(frozen=True)
class Registro:
    tracking: str
    status: str
    nivel: int


@dataclass(frozen=True)
class Hallazgo:
    id: uuid.UUID
    tracking: str
    texto: str
    score: float
    page: int | None
    attachment_ref: str | None


@dataclass(frozen=True)
class Afirmacion:
    texto: str
    citas: tuple[Hallazgo, ...]


@dataclass(frozen=True)
class Descarte:
    ref: uuid.UUID
    borrar: bool
    motivo: str


def filtrar_busqueda(
    hits: list[Any],
    vivos: dict[uuid.UUID, Registro],
    *,
    habilitados: Iterable[int],
    clearance: int,
) -> tuple[list[Hallazgo], list[Descarte]]:
    niveles = _niveles(habilitados)
    utiles: list[Hallazgo] = []
    descartes: list[Descarte] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        hallazgo, descarte = _cita(
            hit.get("citation"),
            vivos,
            niveles=niveles,
            clearance=clearance,
            score=_puntuacion(hit.get("score")),
        )
        if descarte is not None:
            descartes.append(descarte)
        if hallazgo is not None:
            utiles.append(hallazgo)
    return utiles, descartes


def filtrar_pregunta(
    claims: list[Any],
    vivos: dict[uuid.UUID, Registro],
    *,
    habilitados: Iterable[int],
    clearance: int,
) -> tuple[list[Afirmacion], list[Descarte]]:
    """Una afirmación sin citas que sobrevivan no se muestra."""
    niveles = _niveles(habilitados)
    afirmaciones: list[Afirmacion] = []
    descartes: list[Descarte] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        texto = str(claim.get("text") or "").strip()
        citas_in = claim.get("citations")
        if not isinstance(citas_in, list):
            citas_in = []
        citas: list[Hallazgo] = []
        for cita in citas_in:
            hallazgo, descarte = _cita(
                cita, vivos, niveles=niveles, clearance=clearance, score=0.0
            )
            if descarte is not None:
                descartes.append(descarte)
            if hallazgo is not None:
                citas.append(hallazgo)
        if texto and citas:
            afirmaciones.append(Afirmacion(texto=texto, citas=tuple(citas)))
    return afirmaciones, descartes


def refs_a_borrar(descartes: list[Descarte]) -> list[uuid.UUID]:
    vistos: list[uuid.UUID] = []
    for descarte in descartes:
        if descarte.borrar and descarte.ref not in vistos:
            vistos.append(descarte.ref)
    return vistos


def _niveles(habilitados: Iterable[int]) -> set[int]:
    salida: set[int] = set()
    for valor in habilitados:
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            continue
        if numero in (1, 2, 3):
            salida.add(numero)
    return salida


def _puntuacion(valor: Any) -> float:
    try:
        numero = float(valor or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if numero != numero:
        return 0.0
    return numero


def _cita(
    cita: Any,
    vivos: dict[uuid.UUID, Registro],
    *,
    niveles: set[int],
    clearance: int,
    score: float,
) -> tuple[Hallazgo | None, Descarte | None]:
    if not isinstance(cita, dict):
        return None, None
    try:
        ident = uuid.UUID(str(cita.get("record_ref")))
    except (TypeError, ValueError):
        return None, None
    fila = vivos.get(ident)
    if fila is None:
        return None, Descarte(ident, True, "ausente")
    if fila.status == "anulado":
        return None, Descarte(ident, True, "anulado")
    if fila.nivel not in niveles:
        return None, Descarte(ident, True, "nivel")
    if fila.nivel > clearance:
        return None, Descarte(ident, False, "clearance")
    if not fila.tracking:
        return None, Descarte(ident, False, "tracking")
    texto = str(cita.get("quote") or "").strip()
    if not texto:
        return None, Descarte(ident, False, "cita")
    pagina = cita.get("page")
    anexo = cita.get("attachment_ref")
    return (
        Hallazgo(
            id=ident,
            tracking=fila.tracking,
            texto=texto[:_TOPE_CITA],
            score=score,
            page=pagina if isinstance(pagina, int) else None,
            attachment_ref=anexo if isinstance(anexo, str) and anexo else None,
        ),
        None,
    )
