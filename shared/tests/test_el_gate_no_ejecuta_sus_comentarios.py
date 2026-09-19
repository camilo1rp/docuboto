# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El guion del gate no ejecuta el texto de sus propios comentarios.

`scripts/lint.sh` pasa al contenedor un guion largo dentro de `sh -c "..."`, y esa
cadena va entre COMILLAS DOBLES: ahí dentro los acentos graves siguen siendo
sustitución de comandos, comentario o no. El `#` no protege nada, porque el host
expande la cadena ANTES de que exista ningún shell que reconozca comentarios.

Ya ocurrió, y se descubrió por el ruido y no por el daño: dos comentarios que
citaban `` `set +e` `` y `` `sh -c` `` se ejecutaban **en el host** en cada
invocación del gate. El segundo imprimía `sh: 0: -c requires an argument`, un
mensaje que parece del contenedor y no lo es. El primero es el que importa:
anulaba el `set -e` del propio `scripts/lint.sh` a mitad de ejecución.

Es la variante ejecutable del comentario falso como vector: aquí el comentario no
miente sobre el código — ES código.

El guardián mira **solo dentro de la cadena** que se pasa a `sh -c`, y no el guion
entero: fuera de ella los acentos graves de un comentario de bash son inofensivos
—ahí el `#` sí protege— y hay diez, todos legítimos. Un guardián que los marcara
sería ruido, y un guardián ruidoso se acaba desactivando.

Dice en voz alta cuál es la línea, porque el síntoma no señala al culpable: el
mensaje del host se lee como si viniera del contenedor.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Los guiones que pasan un programa a otro shell dentro de comillas dobles. Se
# nombran uno a uno a propósito: un censo del árbol entero metería aquí guiones
# que usan acentos graves de forma legítima, y el guardián acabaría
# desactivándose por ruidoso, que es como mueren los guardianes.
GUIONES = ("scripts/lint.sh",)

# Un acento grave que NO viene precedido de barra invertida.
_SIN_ESCAPAR = re.compile(r"(?<!\\)`")

# Apertura y cierre de la cadena que viaja al otro shell. El cierre se reconoce
# por una comilla doble que abre la línea (`"; then`, `"` sola): dentro del
# programa las comillas van siempre escapadas o en mitad de la línea.
_ABRE_CADENA = re.compile(r"""\bsh\s+-c\s+\"\s*$""")
_CIERRA_CADENA = re.compile(r"""^\s*\"""")


def _lineas_con_acento_suelto(texto: str, *, solo_en_la_cadena: bool = True) -> list[tuple[int, str]]:
    """Líneas con un acento grave sin escapar, dentro de la cadena de `sh -c`.

    Con `solo_en_la_cadena=False` mira el texto entero — así lo usa el control
    positivo, que no arma ninguna cadena.
    """
    dentro = not solo_en_la_cadena
    hallazgos: list[tuple[int, str]] = []
    for n, linea in enumerate(texto.splitlines(), start=1):
        if solo_en_la_cadena and not dentro:
            dentro = bool(_ABRE_CADENA.search(linea))
            continue
        if solo_en_la_cadena and _CIERRA_CADENA.match(linea):
            dentro = False
            continue
        if _SIN_ESCAPAR.search(linea):
            hallazgos.append((n, linea.strip()))
    return hallazgos


def test_ningun_acento_grave_sin_escapar_en_los_guiones_del_gate() -> None:
    hallazgos: list[str] = []
    for relativa in GUIONES:
        guion = REPO / relativa
        assert guion.is_file(), f"{relativa} no existe: el guardián apunta a un guion que se movió"
        for numero, linea in _lineas_con_acento_suelto(guion.read_text(encoding="utf-8")):
            hallazgos.append(f"{relativa}:{numero}  {linea}")

    assert not hallazgos, (
        "Acento grave sin escapar dentro del guion del gate. El host lo ejecuta como "
        "sustitución de comandos aunque esté en un comentario — un `set +e` así anula "
        "el modo estricto del propio guion. Escríbelo como \\` :\n  " + "\n  ".join(hallazgos)
    )


def test_el_guardian_cae_si_aparece_uno(tmp_path: Path) -> None:
    """Control positivo: sin esto, el `assert not hallazgos` pasaría vacío para siempre.

    Es la lección de la ausencia trivial: una comprobación que solo sabe decir
    «no encontré nada» hay que verla encontrar algo al menos una vez.
    """
    sospechoso = tmp_path / "falso.sh"
    sospechoso.write_text("echo uno\n# `set +e` citado sin escapar\necho dos\n", encoding="utf-8")

    hallazgos = _lineas_con_acento_suelto(
        sospechoso.read_text(encoding="utf-8"), solo_en_la_cadena=False
    )

    assert [n for n, _ in hallazgos] == [2]
