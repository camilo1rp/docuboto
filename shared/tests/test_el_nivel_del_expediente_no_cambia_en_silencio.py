# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El nivel de un expediente NO cambia tras crearse — y si cambia, hay que propagarlo.

AREA-11-11 pedía que knowledge-service consumiera la «reclasificación de
expediente» y re-aplicara el nivel a los chunks de sus radicados. Verificado contra
el código: esa operación NO existe. `ExpedienteUpdate` solo admite `name`,
`description` y `trd_serie_id` (con `extra="forbid"`), ningún `UPDATE expedientes`
toca `nivel_seguridad`, archive-service no publica ningún evento, y el nivel
efectivo de un radicado es el suyo propio (no se deriva del expediente).

Construir un consumidor de un stream que nadie emite sería la clase de defecto que
este proyecto ya conoce: una pieza correcta que protege contra algo inexistente
mientras parece cerrar un riesgo. Este guardián deja la premisa FALSABLE: el día en
que alguien añada la reclasificación de expedientes, falla y obliga a hacer la
propagación al índice derivado (evento del archivo + consumidor con la misma
monotonía GREATEST que ya tiene `reclassified`).
"""

import re
from pathlib import Path

import pytest

_ARCHIVE_APP = Path(__file__).resolve().parents[2] / "services" / "archive-service" / "app"

pytestmark = pytest.mark.skipif(
    not _ARCHIVE_APP.is_dir(), reason="el árbol de archive-service no está montado"
)


def test_ningun_update_de_expedientes_cambia_el_nivel() -> None:
    patron = re.compile(
        r"UPDATE\s+expedientes\b(?:(?!;|\"\"\")[\s\S]){0,2000}?\bSET\b"
        r"(?:(?!;|\"\"\")[\s\S]){0,1500}?\bnivel_seguridad\s*=",
        re.IGNORECASE,
    )
    ofensores = [
        str(p.relative_to(_ARCHIVE_APP))
        for p in _ARCHIVE_APP.rglob("*.py")
        if patron.search(p.read_text(encoding="utf-8"))
    ]
    assert not ofensores, (
        "Un UPDATE de `expedientes` cambia `nivel_seguridad` en "
        f"{ofensores}: ya existe la reclasificación de expediente. Hay que "
        "propagarla al índice derivado de knowledge-service (AREA-11-11): "
        "publicar el evento y consumirlo con monotonía GREATEST sobre los chunks de "
        "sus radicados, y actualizar este guardián."
    )


def test_el_esquema_de_actualizacion_no_admite_nivel() -> None:
    texto = (_ARCHIVE_APP / "schemas" / "archive.py").read_text(encoding="utf-8")
    inicio = texto.index("class ExpedienteUpdate")
    fin = texto.index("\nclass ", inicio + 1)
    assert "nivel_seguridad" not in texto[inicio:fin], (
        "ExpedienteUpdate admite `nivel_seguridad`: ver el docstring de este módulo."
    )
