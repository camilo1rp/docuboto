# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El año del número de radicado se decide en hora local, no universal.

Colombia es UTC-5 todo el año. Radicando el 31 de diciembre después de las 19:00
de Bogotá, el servidor ya estaba en el año siguiente: salía un radicado con fecha
impresa 31/12 y número del año entrante. El número y su acuse se contradicen, y
el consecutivo del año que cierra queda cortado antes de tiempo — sobre un número
que es inmutable por Ley 594.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.document_service import _zona_de_radicacion

#: 31 de diciembre de 2026, 19:30 en Bogotá. En UTC ya es 1 de enero de 2027.
_CIERRE_DE_ANO = datetime(2026, 12, 31, 19, 30, tzinfo=ZoneInfo("America/Bogota"))


def test_la_tarde_del_31_de_diciembre_sigue_siendo_del_ano_que_cierra():
    assert _CIERRE_DE_ANO.astimezone(UTC).year == 2027, "el caso no reproduce el defecto"
    assert _CIERRE_DE_ANO.astimezone(_zona_de_radicacion()).year == 2026


def test_la_zona_por_defecto_es_la_del_pais_y_no_universal():
    assert settings.tz_radicacion == "America/Bogota"
    assert _zona_de_radicacion() is not UTC


def test_una_zona_invalida_no_tumba_la_radicacion(monkeypatch):
    """Es un dato de despliegue, no del documento: degrada a universal con aviso.

    Lo que no puede hacer es fallar en silencio usando una zona equivocada.
    """
    monkeypatch.setattr(settings, "tz_radicacion", "No/Existe")
    assert _zona_de_radicacion() is UTC
