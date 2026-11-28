# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Los eventos de dominio llevan contenido y tienen que caducar (ADR-049).

Llevan el asunto y el nombre del ciudadano de cada radicación. Sin caducidad
serían un almacén paralelo de datos personales fuera de la Tabla de Retención
Documental que gobierna todo lo demás del sistema.

Lo que se comprueba aquí es el CONTRATO, sin base: que la purga corta por
antigüedad y no por número de filas, y que el barrido de cada entidad la llama
con la retención configurada. Su comportamiento contra la tabla de verdad (no
borra lo que un grupo tiene sin confirmar, se lleva sus entregas) lo prueba
`services/docuboto/tests/document/integration/test_bus_eventos_integration.py`.
"""

import inspect
from pathlib import Path

from orpycamcp_common import events

RAIZ = Path(__file__).resolve().parents[2]
BARRIDO = RAIZ / "services" / "docuboto" / "document" / "services" / "barrido.py"


def test_la_purga_corta_por_ANTIGUEDAD_y_no_por_numero_de_filas():
    """Con un tope de filas la ventana dependería del volumen del despliegue:
    una entidad con mucho tráfico conservaría minutos y una tranquila, meses.
    Por tiempo la promesa es la misma para todas."""
    fuente = inspect.getsource(events.purgar)
    assert "publicado_en < now() - make_interval(days => $1)" in fuente
    assert "LIMIT" not in fuente.upper().replace("LIMITE", "")


def test_la_purga_respeta_lo_que_un_grupo_tiene_sin_confirmar():
    fuente = inspect.getsource(events.purgar)
    assert "confirmado_en IS NULL" in fuente


def test_el_barrido_de_cada_entidad_purga_con_la_retencion_configurada():
    fuente = BARRIDO.read_text(encoding="utf-8")
    assert "events.purgar(" in fuente
    assert "dias=settings.eventos_retencion_dias" in fuente
