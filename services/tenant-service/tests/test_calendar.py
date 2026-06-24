# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del calendario de días hábiles (E14, festivos colombianos). Lógica pura."""

from datetime import date

from app.core.calendar import (
    add_business_days,
    colombian_holidays,
    easter_sunday,
    is_business_day,
)


def test_easter_2025() -> None:
    assert easter_sunday(2025) == date(2025, 4, 20)


def test_fixed_holidays_2025() -> None:
    h = colombian_holidays(2025)
    assert h[date(2025, 1, 1)] == "Año Nuevo"
    assert h[date(2025, 7, 20)] == "Día de la Independencia"
    assert h[date(2025, 12, 25)] == "Navidad"


def test_emiliani_moves_to_monday() -> None:
    h = colombian_holidays(2025)
    # San José (19 mar 2025, miércoles) se traslada al lunes 24 mar.
    assert date(2025, 3, 24) in h
    assert h[date(2025, 3, 24)] == "San José"
    # Reyes (6 ene 2025) ya es lunes → no se mueve.
    assert date(2025, 1, 6) in h


def test_easter_relative_holidays_2025() -> None:
    h = colombian_holidays(2025)
    assert h[date(2025, 4, 17)] == "Jueves Santo"
    assert h[date(2025, 4, 18)] == "Viernes Santo"
    # Ascensión: Pascua+39 (29 may) → lunes 2 jun.
    assert h[date(2025, 6, 2)] == "Ascensión del Señor"
    # Corpus Christi: Pascua+60 → lunes 23 jun.
    assert h[date(2025, 6, 23)] == "Corpus Christi"
    # Sagrado Corazón: Pascua+68 → lunes 30 jun.
    assert h[date(2025, 6, 30)] == "Sagrado Corazón"


def test_colombia_holiday_count() -> None:
    # 2025 tiene una coincidencia legal (San Pedro y San Pablo y Sagrado Corazón
    # caen ambos el lunes 30-jun) → 17 días no hábiles distintos, no 18.
    assert len(colombian_holidays(2025)) == 17
    # 2026 no tiene esa coincidencia → los 18 festivos son fechas distintas.
    assert len(colombian_holidays(2026)) == 18


def test_is_business_day_weekend() -> None:
    # 21 jun 2025 es sábado.
    assert is_business_day(date(2025, 6, 21), set()) is False
    # 20 jun 2025 es viernes hábil.
    assert is_business_day(date(2025, 6, 20), set()) is True


def test_add_business_days_skips_weekend() -> None:
    # Viernes 20 jun 2025 + 1 día hábil = lunes 23 jun (salta fin de semana).
    # (con festivos vacíos para aislar el efecto del fin de semana)
    assert add_business_days(date(2025, 6, 20), 1, set()) == date(2025, 6, 23)


def test_add_business_days_skips_holidays() -> None:
    festivos = set(colombian_holidays(2025))
    # Desde el martes 15 jul 2025, 5 días hábiles. El 20 jul (domingo) es festivo
    # pero cae en fin de semana; verificamos el conteo correcto saltando sáb/dom.
    result = add_business_days(date(2025, 7, 15), 5, festivos)
    assert result == date(2025, 7, 22)  # mié,jue,vie, (sáb/dom), lun(21),mar(22)


def test_add_business_days_zero() -> None:
    assert add_business_days(date(2025, 6, 20), 0, set()) == date(2025, 6, 20)
