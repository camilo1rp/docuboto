# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""CI prueba la app, y la lista de dominios sale del árbol (ADR-047, AREA-13-02).

Antes había una matriz por servicio escrita a mano, y dejó fuera suites enteras.
Ahora `test-unit` y `test-integration` llaman a `scripts/test-app.sh`, que deriva
los dominios de `services/docuboto/tests/*/`. Este guardián vigila:

* que los dos trabajos llaman al script y no a una lista propia;
* que lo que el script declara fuera de integración existe y sigue teniendo
  `tests/integration/` (una excepción que caduca se quita);
* que el humo de seguridad corre siempre (sin `if:`) y sus ficheros existen.
"""

from __future__ import annotations

import re

from tests._compose import APP, CI_WORKFLOW, RAIZ, guion, job

SCRIPT = RAIZ / "scripts" / "test-app.sh"


def _dominios_con_tests() -> set[str]:
    return {p.name for p in (APP / "tests").iterdir() if p.is_dir() and p.name != "__pycache__"}


def _fuera_de_integracion() -> set[str]:
    m = re.search(r"^FUERA_DE_INTEGRACION=\(([^)]*)\)", SCRIPT.read_text(encoding="utf-8"), re.M)
    assert m, "scripts/test-app.sh ya no declara FUERA_DE_INTEGRACION"
    return set(m.group(1).split())


def test_el_censo_de_dominios_no_esta_vacio() -> None:
    assert len(_dominios_con_tests()) >= 9, "el censo de dominios con tests se quedó corto"


def test_unit_e_integracion_llaman_al_script() -> None:
    for nombre, modo in (("test-unit", "unit"), ("test-integration", "integration")):
        texto = guion(job(CI_WORKFLOW, nombre))
        assert f"scripts/test-app.sh {modo}" in texto, f"{nombre} no llama a test-app.sh {modo}"
        assert "matrix" not in job(CI_WORKFLOW, nombre).get("strategy", {}), (
            f"{nombre} volvió a tener una matriz escrita a mano"
        )


def test_lo_declarado_fuera_de_integracion_no_caduca() -> None:
    for dominio in _fuera_de_integracion():
        assert dominio in _dominios_con_tests(), f"{dominio}: ya no existe, quítalo"
        assert list((APP / "tests" / dominio / "integration").glob("test_*.py")), (
            f"{dominio}: ya no tiene tests/integration, quítalo de FUERA_DE_INTEGRACION"
        )


def test_el_humo_de_seguridad_corre_solo_y_sus_ficheros_existen() -> None:
    trabajo = job(CI_WORKFLOW, "test-security-smoke")
    assert "if" not in trabajo, "el humo de seguridad no puede depender de un clic"
    texto = guion(trabajo)
    rutas = re.findall(r"\btests/[\w/]+(?:\.py)?", texto)
    assert rutas, "el humo de seguridad no corre ningún fichero"
    for ruta in rutas:
        assert (APP / ruta).exists(), f"{ruta} no existe"
