# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Qué publica `GET /metrics` de la pasarela (ADR-041, ADR-045).

Cuatro gauges, sumados sobre todas las entidades y sin etiqueta de identidad:

* envíos: filas del libro, por operación y resultado;
* rechazos de barrera: el envío estaba encendido y el registro no salió;
* respaldos: el índice local respondió porque el proveedor no pudo;
* suspensiones: entidades con el envío suspendido ahora.

Un valor fuera del conjunto cerrado invalida la medición entera. Con cero
entidades el cero es verdad y se emite.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence

import asyncpg
from orpycamcp_common.metricas import MetricaInvalida, Muestra, esquemas_a_medir

logger = logging.getLogger(__name__)

BARRERA = "barrier"
RESPALDO = "fallback"
RAZON_RESPALDO = "local"
RAZONES_BARRERA = frozenset({"suspendido", "ausente", "nivel"})

FAMILIAS = frozenset(
    {
        "orpycamcp_intelligence_sends",
        "orpycamcp_intelligence_barrier_denials",
        "orpycamcp_intelligence_fallbacks",
        "orpycamcp_intelligence_suspensions",
    }
)

_OPERACIONES = ("push", "purge")
_RESULTADOS = ("accepted", "error")
_ENVIOS_OK = frozenset((op, resultado) for op in _OPERACIONES for resultado in _RESULTADOS)
_SCHEMA = re.compile(r"tenant_[a-z0-9_]+")

_SUMAR = """
INSERT INTO intelligence_meter (kind, reason, n)
VALUES ($1, $2, 1)
ON CONFLICT (kind, reason) DO UPDATE
    SET n = intelligence_meter.n + 1
"""

_ENVIOS = (
    "SELECT operation, result, count(*)::bigint AS n "
    'FROM "{schema}".intelligence_send_log GROUP BY operation, result'
)
_CONTADOR = 'SELECT kind, reason, n FROM "{schema}".intelligence_meter'
_SUSPENSIONES = (
    "SELECT count(*)::bigint AS n "
    'FROM "{schema}".intelligence_state WHERE send_suspended'
)


def _comprobar(kind: str, reason: str) -> None:
    if kind == BARRERA and reason in RAZONES_BARRERA:
        return
    if kind == RESPALDO and reason == RAZON_RESPALDO:
        return
    raise ValueError(f"métrica fuera del registro: {kind}/{reason}")


async def anotar(conn: object, kind: str, reason: str) -> None:
    """Suma uno. Un fallo de base no tumba el envío ni la consulta."""
    _comprobar(kind, reason)
    try:
        await conn.execute(_SUMAR, kind, reason)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("no se anotó %s/%s", kind, reason, exc_info=True)


def _ramas(schemas: Sequence[str], plantilla: str) -> str:
    partes: list[str] = []
    for schema in schemas:
        if not _SCHEMA.fullmatch(schema):
            raise ValueError(f"schema de tenant inválido: {schema!r}")
        partes.append(plantilla.format(schema=schema))
    return " UNION ALL ".join(partes)


def muestras_de(
    envios: Mapping[tuple[str, str], int],
    barreras: Mapping[str, int],
    respaldos: int,
    suspensiones: int,
) -> list[Muestra]:
    """Una muestra por combinación admitida, también en cero."""
    for clave in envios:
        if clave not in _ENVIOS_OK:
            raise MetricaInvalida(f"envio fuera del registro: {clave!r}")
    for razon in barreras:
        if razon not in RAZONES_BARRERA:
            raise MetricaInvalida(f"rechazo fuera del registro: {razon!r}")
    if respaldos < 0 or suspensiones < 0:
        raise MetricaInvalida("contador negativo")
    salida = [
        Muestra(
            "orpycamcp_intelligence_sends",
            envios.get((operacion, resultado), 0),
            {"operation": operacion, "result": resultado},
        )
        for operacion in _OPERACIONES
        for resultado in _RESULTADOS
    ]
    salida.extend(
        Muestra(
            "orpycamcp_intelligence_barrier_denials",
            barreras.get(razon, 0),
            {"reason": razon},
        )
        for razon in ("suspendido", "ausente", "nivel")
    )
    salida.append(Muestra("orpycamcp_intelligence_fallbacks", respaldos))
    salida.append(Muestra("orpycamcp_intelligence_suspensions", suspensiones))
    return salida


def _sumar_entero(acumulado: int, valor: object) -> int:
    try:
        numero = int(valor)  # type: ignore[call-overload]
    except (TypeError, ValueError) as exc:
        raise MetricaInvalida(f"valor no entero: {valor!r}") from exc
    if numero < 0:
        raise MetricaInvalida("contador negativo")
    return acumulado + numero


async def medir(conn: asyncpg.Connection) -> list[Muestra]:
    """Lee las cuatro señales. Una fila fuera del registro invalida la medición."""
    schemas = await esquemas_a_medir(conn)
    if not schemas:
        return muestras_de({}, {}, 0, 0)

    envios: dict[tuple[str, str], int] = {}
    for fila in await conn.fetch(_ramas(schemas, _ENVIOS)):
        clave = (str(fila["operation"]), str(fila["result"]))
        envios[clave] = _sumar_entero(envios.get(clave, 0), fila["n"])

    barreras: dict[str, int] = {}
    respaldos = 0
    for fila in await conn.fetch(_ramas(schemas, _CONTADOR)):
        kind = str(fila["kind"])
        reason = str(fila["reason"])
        if kind == BARRERA:
            barreras[reason] = _sumar_entero(barreras.get(reason, 0), fila["n"])
        elif kind == RESPALDO and reason == RAZON_RESPALDO:
            respaldos = _sumar_entero(respaldos, fila["n"])
        else:
            raise MetricaInvalida(f"contador fuera del registro: {kind}/{reason}")

    suspensiones = 0
    for fila in await conn.fetch(_ramas(schemas, _SUSPENSIONES)):
        suspensiones = _sumar_entero(suspensiones, fila["n"])
    return muestras_de(envios, barreras, respaldos, suspensiones)
