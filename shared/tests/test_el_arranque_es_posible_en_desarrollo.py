# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Todo secreto EXIGIDO al arrancar tiene que estar declarado en el compose de dev.

El defecto que trajo este guardián: al extender la comprobación de secretos a los
tokens internos, `tenant-service` pasó a exigir `TENANT_INTERNAL_TOKEN` para
arrancar y el `docker-compose.yml` no lo define. El servicio dejó de levantar —y
el síntoma no se leyó como lo que era: `/health/ready` decía `degraded` con
`tenant-service: unreachable`, que es exactamente lo que se ve cuando el servicio
está sano pero la red falla.

El fallo es de la clase «gate que se dispara siempre»: una comprobación que nadie
puede satisfacer no protege, suprime. Y ninguna suite lo cazaba, porque los tests
de cada servicio fijan sus propios valores en `conftest.py` — el arranque real
solo ocurre en Docker.

Este guardián cruza las dos mitades: lo que el `lifespan` exige contra lo que el
manifiesto de desarrollo entrega. Ojo con la asimetría deliberada:
`reject_published_values` NO se mira, porque admite la ausencia a propósito (ese
es su motivo de existir).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"
SERVICIOS = REPO / "services"

_SERVICIO_COMPOSE = re.compile(r"^  ([a-z0-9-]+):\s*$")
_ENV = re.compile(r"^\s*-\s*([A-Z_][A-Z0-9_]*)=")
#: Una variable del compose puede llamarse distinto DENTRO del contenedor que
#: FUERA: `document-service` recibe `INTERNAL_SERVICE_TOKEN=${DOCUMENT_INTERNAL_TOKEN:-…}`,
#: y lo que `require_secrets` nombra es el segundo —el que teclea quien despliega—
#: porque es el que hay que definir. Cuentan los dos lados de la sustitución, o el
#: guardián acusaría de no arrancar a servicios que arrancan.
_SUSTITUCION = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)[:\-}]")
# `require_secrets(` … `)` — el bloque entero, para sacar de él las claves.
_LLAMADA = re.compile(r"require_secrets\((.*?)\)\s*$", re.DOTALL | re.MULTILINE)
_CLAVE = re.compile(r'"([A-Z_][A-Z0-9_]*)"\s*:')


def _variables_declaradas() -> dict[str, set[str]]:
    """`{servicio del compose: {VARIABLES que su bloque entrega, por cualquiera de sus dos nombres}}`."""
    declaradas: dict[str, set[str]] = {}
    actual: str | None = None
    for linea in COMPOSE.read_text(encoding="utf-8").splitlines():
        m = _SERVICIO_COMPOSE.match(linea)
        if m:
            actual = m.group(1)
            declaradas.setdefault(actual, set())
            continue
        if actual is None:
            continue
        m = _ENV.match(linea)
        if m:
            declaradas[actual].add(m.group(1))
        declaradas[actual].update(_SUSTITUCION.findall(linea))
    return declaradas


def _secretos_exigidos() -> dict[str, set[str]]:
    """`{servicio: {VARIABLES que su lifespan exige presentes}}`."""
    exigidos: dict[str, set[str]] = {}
    for main in sorted(SERVICIOS.glob("*/app/main.py")):
        servicio = main.parents[1].name
        codigo = main.read_text(encoding="utf-8")
        claves: set[str] = set()
        for bloque in _LLAMADA.findall(codigo):
            claves.update(_CLAVE.findall(bloque))
        if claves:
            exigidos[servicio] = claves
    return exigidos


def test_el_censo_encuentra_las_dos_mitades() -> None:
    """Anti-vacuidad: sin esto, un parser roto dejaría el guardián verde y mudo."""
    exigidos = _secretos_exigidos()
    declaradas = _variables_declaradas()
    assert len(exigidos) >= 5, f"apenas se reconocieron {len(exigidos)} servicios con guardián"
    assert "SIGNATURE_SECRET" in exigidos.get("signature-service", set())
    assert declaradas.get("document-service"), "no se leyó el entorno del compose"


def test_ningun_servicio_exige_un_secreto_que_el_compose_no_entrega() -> None:
    declaradas = _variables_declaradas()
    huecos: dict[str, set[str]] = {}
    for servicio, claves in _secretos_exigidos().items():
        faltan = claves - declaradas.get(servicio, set())
        if faltan:
            huecos[servicio] = faltan
    assert not huecos, (
        "Estos servicios NO arrancan con `docker compose up`: su lifespan exige "
        "una variable que el manifiesto de desarrollo no define. O se añade al "
        "compose, o —si su ausencia ya es la postura segura, como el gate D-02 "
        f"de /api/v1/tenants— se comprueba con `reject_published_values`. {huecos}"
    )
