# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Calendario de días hábiles y festivos colombianos (E14, Ley 51/1983).

Módulo PURO (sin BD): facilita los festivos de un año y el cálculo de fechas de
vencimiento en días hábiles que consume E01 (RF-RAD-04).

Festivos:
- Fijos: Año Nuevo, Trabajo, Independencia (20 jul), Batalla de Boyacá (7 ago),
  Inmaculada (8 dic), Navidad.
- "Emiliani" (se trasladan al lunes siguiente si no caen en lunes): Reyes, San José,
  San Pedro y San Pablo, Asunción, Día de la Raza, Todos los Santos, Independencia
  de Cartagena.
- Móviles relativos a Pascua: Jueves y Viernes Santo (sin traslado); Ascensión,
  Corpus Christi y Sagrado Corazón (trasladados a lunes).
"""

from __future__ import annotations

from datetime import date, timedelta


def easter_sunday(year: int) -> date:
    """Domingo de Pascua (algoritmo anónimo gregoriano / Meeus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _next_monday(d: date) -> date:
    """El propio día si es lunes; si no, el lunes siguiente (regla Emiliani)."""
    return d + timedelta(days=(0 - d.weekday()) % 7)


def colombian_holidays(year: int) -> dict[date, str]:
    """Festivos nacionales de Colombia para ``year`` → {fecha: descripción}."""
    holidays: dict[date, str] = {}

    # Fijos (no se trasladan).
    for month, day, name in [
        (1, 1, "Año Nuevo"),
        (5, 1, "Día del Trabajo"),
        (7, 20, "Día de la Independencia"),
        (8, 7, "Batalla de Boyacá"),
        (12, 8, "Inmaculada Concepción"),
        (12, 25, "Navidad"),
    ]:
        holidays[date(year, month, day)] = name

    # Emiliani (se trasladan al lunes siguiente).
    #
    # Nota: en años donde dos festivos trasladados caen en el mismo lunes (p. ej.
    # 2025: San Pedro y San Pablo y Sagrado Corazón coinciden el 30-jun), el dict
    # los fusiona en un único día no hábil — que es el efecto legal correcto
    # (Ley 51/1983 no duplica el descanso). El conteo anual puede ser 17 o 18.
    for month, day, name in [
        (1, 6, "Reyes Magos"),
        (3, 19, "San José"),
        (6, 29, "San Pedro y San Pablo"),
        (8, 15, "Asunción de la Virgen"),
        (10, 12, "Día de la Raza"),
        (11, 1, "Todos los Santos"),
        (11, 11, "Independencia de Cartagena"),
    ]:
        holidays[_next_monday(date(year, month, day))] = name

    # Móviles relativos a Pascua.
    easter = easter_sunday(year)
    holidays[easter - timedelta(days=3)] = "Jueves Santo"
    holidays[easter - timedelta(days=2)] = "Viernes Santo"
    holidays[_next_monday(easter + timedelta(days=39))] = "Ascensión del Señor"
    holidays[_next_monday(easter + timedelta(days=60))] = "Corpus Christi"
    holidays[_next_monday(easter + timedelta(days=68))] = "Sagrado Corazón"

    return holidays


def is_business_day(d: date, non_business: set[date]) -> bool:
    """Lunes-viernes que no sea festivo/no hábil."""
    return d.weekday() < 5 and d not in non_business


def add_business_days(start: date, days: int, non_business: set[date]) -> date:
    """Fecha resultante de sumar ``days`` días hábiles a ``start`` (exclusivo).

    ``days`` debe ser >= 0. Cuenta solo días hábiles posteriores a ``start``.
    """
    if days < 0:
        raise ValueError("days must be >= 0")
    current = start
    counted = 0
    while counted < days:
        current += timedelta(days=1)
        if is_business_day(current, non_business):
            counted += 1
    return current
