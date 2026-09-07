# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Motor de evaluación de reglas (E05).

Evalúa condiciones con operadores sobre el contexto de un radicado. Las reglas
nuevas usan una lista de condiciones (`conditions`) combinadas por `match_mode`
('all' = AND, 'any' = OR). Las reglas legadas (sin condiciones) se evalúan con
las 4 columnas fijas (doc_type/doc_class/dest_dept/subject_contains, AND).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import regex

# Campos del radicado disponibles para condiciones.
CONTEXT_FIELDS = (
    "doc_type",
    "doc_class",
    "dest_dept",
    "origin_dept",
    "subject",
    "sender_name",
    "sender_entity",
    "pages",
)

OPERATORS = (
    "eq",
    "ne",
    "contains",
    "not_contains",
    "in",
    "gt",
    "lt",
    "gte",
    "lte",
    "regex",
)


def _to_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    # admite "a,b,c"
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def eval_condition(cond: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evalúa una condición {field, op, value} contra el contexto. False si la
    condición está mal formada o el operador no aplica al dato."""
    field = cond.get("field")
    op = cond.get("op")
    expected = cond.get("value")
    if field not in CONTEXT_FIELDS or op not in OPERATORS:
        return False

    actual = context.get(field)

    if op == "eq":
        return actual is not None and str(actual) == str(expected)
    if op == "ne":
        return str(actual) != str(expected)
    if op == "contains":
        return actual is not None and str(expected).lower() in str(actual).lower()
    if op == "not_contains":
        return actual is None or str(expected).lower() not in str(actual).lower()
    if op == "in":
        return actual is not None and str(actual) in _to_list(expected)
    if op in ("gt", "lt", "gte", "lte"):
        a, b = _to_float(actual), _to_float(expected)
        if a is None or b is None:
            return False
        if op == "gt":
            return a > b
        if op == "lt":
            return a < b
        if op == "gte":
            return a >= b
        return a <= b
    if op == "regex":
        if actual is None:
            return False
        try:
            return _busca_acotada(str(expected), str(actual))
        except regex.error:
            return False
    return False


logger = logging.getLogger(__name__)


#: Plazo máximo, en segundos, para evaluar UNA condición de expresión regular.
#:
#: Acotar la LONGITUD del texto no sirve, y se comprobó midiendo: la expresión
#: `(a+)+$` sobre 28 caracteres tarda 43 segundos con el motor de la biblioteca
#: estándar, y sobre 24 tarda 2,6. La explosión ocurre con textos diminutos, así
#: que recortar el texto habría sido teatro — parecía una defensa y no lo era.
#:
#: Lo único que acota un retroceso desmedido es un plazo, y por eso este módulo
#: usa `regex` en vez del `re` de la biblioteca estándar: es el único de la
#: familia que lo ofrece. El texto lo trae quien radica —el asunto de un
#: documento entra aquí sin que nadie lo elija— y las reglas se evalúan al
#: procesar cada evento, de modo que sin plazo una sola radicación detenía el
#: reparto de trabajo de toda la entidad.
_PLAZO_REGEX_S = 0.5

#: Tope de longitud del patrón. Uno enorme gasta tiempo de compilación por sí
#: mismo, y ninguna regla legítima lo necesita.
_MAX_PATRON_REGEX = 512


@lru_cache(maxsize=256)
def _compilar(patron: str) -> Any:
    """Compila con caché: la misma regla se evalúa en cada evento del sistema."""
    return regex.compile(patron)


def _busca_acotada(patron: str, texto: str) -> bool:
    """Evalúa la expresión con plazo. Sin resultado a tiempo, la condición NO se cumple.

    Devolver `False` es deliberado en los tres casos de fallo —patrón desmesurado,
    plazo agotado, expresión inválida—: una regla de distribución que no se puede
    evaluar no debe darse por cumplida. Enrutar de más por no poder comprobar
    sería el lado inseguro.
    """
    if len(patron) > _MAX_PATRON_REGEX:
        logger.warning(
            "Regla con expresión de %d caracteres: se ignora la condición.", len(patron)
        )
        return False
    try:
        return _compilar(patron).search(texto, timeout=_PLAZO_REGEX_S) is not None
    except TimeoutError:
        logger.warning(
            "Regla con expresión %r: excedió %.1fs sobre el texto recibido; "
            "la condición se da por NO cumplida.",
            patron[:80],
            _PLAZO_REGEX_S,
        )
        return False


def matches_conditions(
    conditions: list[dict[str, Any]], match_mode: str, context: dict[str, Any]
) -> bool:
    """Combina las condiciones por match_mode ('any' = OR, resto = AND)."""
    if not conditions:
        return False
    results = [eval_condition(c, context) for c in conditions]
    if match_mode == "any":
        return any(results)
    return all(results)


def matches_legacy(rule: dict[str, Any], context: dict[str, Any]) -> bool:
    """Lógica legada de 4 columnas fijas (AND, igualdad + substring)."""
    if rule.get("doc_type") and rule["doc_type"] != context.get("doc_type"):
        return False
    if rule.get("doc_class") and rule["doc_class"] != context.get("doc_class"):
        return False
    if rule.get("dest_dept") and rule["dest_dept"] != context.get("dest_dept"):
        return False
    if rule.get("subject_contains"):
        subject = str(context.get("subject") or "").lower()
        if rule["subject_contains"].lower() not in subject:
            return False
    return True


def rule_matches(
    rule: dict[str, Any],
    conditions: list[dict[str, Any]],
    context: dict[str, Any],
) -> bool:
    """Una regla casa por sus condiciones nuevas si las tiene; si no, por las
    4 columnas legadas."""
    if conditions:
        return matches_conditions(conditions, rule.get("match_mode") or "all", context)
    return matches_legacy(rule, context)
