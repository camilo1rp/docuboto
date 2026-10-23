# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Guard de la taxonomía de `tipo` de `preservacion_worm_objeto` (E10, remedio del
gate de disposición). Convierte en fallo de CI la ÚNICA vía de deriva
peligrosa que los auditores de seguridad y conformidad señalaron: dos
allowlists sincronizadas a mano (el CHECK de la migración 010 y las constantes
de `app.core.preservacion_tipos`).

Si alguien añade un `tipo` nuevo al CHECK sin clasificarlo como CONTENIDO o
INSTRUMENTO DE CONTROL, estos tests fallan → obliga a decidir explícitamente
(en vez de que un tipo de contenido caiga por accidente en el set de
exclusión, o un tipo nuevo quede sin política de gate).
"""

from __future__ import annotations

import pathlib
import re

from app.core.preservacion_tipos import (
    ALL_WORM_TIPOS,
    CONTENT_TIPOS,
    CONTROL_INSTRUMENT_TIPOS,
)

_MIGRACIONES = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "tenant"


def _tipos_from_check() -> frozenset[str]:
    """Extrae los valores del CHECK `tipo IN (...)` **vigente**.

    Recorre TODAS las migraciones en orden y se queda con la ÚLTIMA definición,
    que es la que manda en la base. Antes leía la migración 010 por su nombre, y
    ese guardián tenía un punto ciego que se estrenó con este mismo cambio: la
    migración 014 amplía el CHECK con `acta_eliminacion` y el guard habría
    seguido leyendo la 010, dando por buena una taxonomía incompleta **sin
    fallar**. Un guardián que apunta a un archivo fijo caduca el día que alguien
    escribe el siguiente.

    Ignora las líneas de rollback comentadas para no confundir un CHECK
    derogado con el vigente.
    """
    activos: list[str] = []
    for archivo in sorted(_MIGRACIONES.glob("*.sql")):
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            if linea.lstrip().startswith("--"):
                continue
            m = re.search(r"(?<![A-Za-z_])tipo\s+IN\s*\(([^)]*)\)", linea, re.IGNORECASE)
            if m:
                # Cada CHECK nuevo REEMPLAZA al anterior (`DROP CONSTRAINT` +
                # `ADD CONSTRAINT`), así que se sustituye, no se acumula.
                activos = re.findall(r"'([^']+)'", m.group(1))
    return frozenset(activos)


def test_content_y_control_son_disjuntos():
    """Ningún tipo puede ser a la vez contenido e instrumento de control."""
    assert CONTENT_TIPOS.isdisjoint(CONTROL_INSTRUMENT_TIPOS)


def test_indice_y_aip_nunca_se_excluyen_del_gate():
    """El contenido documental JAMÁS debe estar en el set de exclusión del gate
    (excluirlo abriría un borrado prematuro del contenido)."""
    assert "indice" not in CONTROL_INSTRUMENT_TIPOS
    assert "aip" not in CONTROL_INSTRUMENT_TIPOS


def test_all_worm_tipos_coincide_con_el_check_de_la_migracion():
    """`ALL_WORM_TIPOS` debe reflejar EXACTAMENTE el CHECK vigente — un `tipo`
    nuevo en el CHECK sin clasificar (o una constante que el CHECK no admita)
    rompe aquí."""
    assert _tipos_from_check() == ALL_WORM_TIPOS


def test_todo_tipo_del_check_esta_clasificado_exhaustivamente():
    """Cada `tipo` válido debe caer en CONTENIDO o en CONTROL, sin huérfanos."""
    for tipo in _tipos_from_check():
        assert tipo in CONTENT_TIPOS or tipo in CONTROL_INSTRUMENT_TIPOS, (
            f"tipo '{tipo}' del CHECK sin clasificar como contenido/control"
        )
