# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un registro donde el mismo objeto se archiva con dos nombres no se puede leer.

`audit_log.object_ref` es la referencia por la que se busca un asiento: la
pantalla de auditoría la ofrece como filtro y es la columna que cruza el
registro con el resto del sistema. Para un expediente esa referencia es su
CÓDIGO —`EXP-2026-0015`—, que es lo que el sistema imprime y lo que la persona
reconoce.

Para `object_type="expediente_indice"` se escribía de TRES formas según el
sitio: el código en cuatro llamadas, el identificador del expediente en seis y
el identificador del propio índice —otra entidad distinta— en dos. Lo destapó
B12, que es el bloque cuyo trabajo es comprobar el instrumento que verifica
todo lo demás.

**El daño, medido**: filtrar la auditoría por `EXP-2026-0015` devolvía siete
asientos y dejaba fuera `archive.indice_firmado` —el sellado del índice
electrónico, el más probatorio de todos—, archivado bajo el UUID. Una respuesta
incompleta que no se anuncia como incompleta, que es la peor clase de respuesta
en un registro legal (Ley 594/2000).

El censo va por FORMA y se deriva del árbol: se prohíbe `object_ref=str(<algo>)`
en un asiento sobre un expediente, porque `str(...)` de una variable es
justamente como se cuela un identificador donde va un código. Resolver el
código y caer al identificador solo si no existe es la forma correcta, y esa
pasa por un ayudante con nombre.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

#: Los tipos de objeto cuya referencia es un CÓDIGO de expediente.
_TIPOS_CON_CODIGO = ("expediente", "expediente_indice", "expediente_busqueda")

#: Una llamada a `audit.append` completa, hasta su paréntesis de cierre. Basta
#: con capturar un bloque generoso: lo que se busca vive en las líneas
#: inmediatamente contiguas a `object_type=`.
_LLAMADA = re.compile(
    r"object_type\s*=\s*\"(?P<tipo>[a-z_]+)\"[^)]*?object_ref\s*=\s*(?P<ref>[^,\n]+)",
    re.S,
)
#: La forma que mete un identificador donde va un código.
_ES_STR_DE_ALGO = re.compile(r"^str\(")


def _referencias_de_expediente() -> list[tuple[str, str, str]]:
    """(archivo, tipo, expresión) de cada asiento sobre un expediente."""
    hallazgos: list[tuple[str, str, str]] = []
    for archivo in sorted(SERVICIOS.glob("*/app/**/*.py")):
        texto = archivo.read_text(encoding="utf-8")
        if "object_type=" not in texto:
            continue
        for m in _LLAMADA.finditer(texto):
            if m.group("tipo") not in _TIPOS_CON_CODIGO:
                continue
            hallazgos.append(
                (str(archivo.relative_to(RAIZ)), m.group("tipo"), m.group("ref").strip())
            )
    return hallazgos


def test_el_censo_encuentra_asientos_de_expediente() -> None:
    """Anti-vacuidad: si la expresión deja de casar, el guardián de abajo
    aprueba sin haber mirado nada."""
    assert len(_referencias_de_expediente()) >= 10


def test_ningun_asiento_de_expediente_se_archiva_por_su_identificador() -> None:
    culpables = [
        f"{archivo} → object_type=\"{tipo}\", object_ref={ref}"
        for archivo, tipo, ref in _referencias_de_expediente()
        if _ES_STR_DE_ALGO.match(ref)
    ]
    assert not culpables, (
        "Estos asientos archivan un expediente por su identificador interno en "
        "vez de por su código, así que no aparecen al filtrar la auditoría por "
        "la referencia que la persona conoce. Resuelve el código (y cae al "
        "identificador solo si no existe):\n  " + "\n  ".join(sorted(culpables))
    )
