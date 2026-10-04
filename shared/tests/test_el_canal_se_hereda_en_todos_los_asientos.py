# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.



"""AREA-11-04: el canal 'mcp' llega al asiento en TODOS los servicios que asientan.

Dos formas de que lo que el asistente hace en nombre de una persona se asentara
'api' pese a la marca firmada del gateway:

1. Un `canal="api"` escrito a mano en la llamada a `audit.append` (o un envoltorio
   con `canal: str = "api"` por defecto) PISA el canal heredado de la petición. Lo
   tenían los tres actos del admin de la DLQ de notification-service (que el catálogo
   del MCP expone: reprocesar o descartar un evento desde el asistente quedaba
   'api') y el envoltorio de tenant-service.
2. Un servicio que asienta pero no instala `RequestLogMiddleware` nunca puebla
   `canal_actual()`: hereda siempre el vacío y cae a 'api'.

Única excepción legítima: el asiento del acceso PÚBLICO anónimo (`public_audit.py`),
que por definición no pasa por el gateway autenticado.
"""

import re
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parents[2] / "services"

pytestmark = pytest.mark.skipif(
    not _SERVICES.is_dir(), reason="el árbol de servicios no está montado"
)

_CANAL_FIJO = re.compile(r"""\bcanal\s*(?::\s*str\s*)?=\s*["']api["']""")


def test_ningun_asiento_fija_canal_api_a_mano() -> None:
    ofensores = [
        f"{p.relative_to(_SERVICES)}:{n}"
        for p in _SERVICES.glob("*/app/**/*.py")
        if p.name != "public_audit.py"
        for n, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1)
        if _CANAL_FIJO.search(linea) and not linea.lstrip().startswith("#")
    ]
    assert not ofensores, (
        "Un `canal=\"api\"` fijo pisa el canal que el gateway derivó (p. ej. 'mcp'): "
        f"{ofensores}. Declara `canal=audit.CANAL_PETICION`: `audit.append` hereda "
        "entonces el de la petición (y `'system'` en un proceso de fondo)."
    )


def _asientan(main: Path) -> bool:
    return any(
        re.search(r"\b_?audit\.(append|append_denial)\(", p.read_text(encoding="utf-8"))
        for p in (main.parent).rglob("*.py")
    )


def test_todo_servicio_que_asienta_instala_el_middleware_de_traza() -> None:
    sin_middleware = [
        main.parent.parent.name
        for main in sorted(_SERVICES.glob("*/app/main.py"))
        if _asientan(main) and "RequestLogMiddleware" not in main.read_text(encoding="utf-8")
    ]
    assert not sin_middleware, (
        f"{sin_middleware} asientan en audit_log pero no instalan RequestLogMiddleware: "
        "`canal_actual()` queda vacío y todo acto se asienta 'api'."
    )


def test_el_censo_no_es_vacio() -> None:
    """Control positivo: sin él, un glob que no encuentra nada también pasaría."""
    mains = list(_SERVICES.glob("*/app/main.py"))
    assert len([m for m in mains if _asientan(m)]) >= 8
