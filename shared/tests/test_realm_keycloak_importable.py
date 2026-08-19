# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Los realms de Keycloak tienen que poder importarse.

Un JSON de realm se deserializa contra `RealmRepresentation` con Jackson, y
**cualquier** propiedad desconocida aborta la importación con
`UnrecognizedPropertyException`. Incluida una clave que empieza por `_` y que
solo pretendía documentar el archivo.

No es un fallo cosmético ni diferido: `--import-realm` corre en el arranque, así
que el contenedor **no levanta**. Y como Keycloak es la puerta de entrada, el
sistema entero queda inaccesible — sin ninguna pista en la interfaz, porque la
interfaz tampoco carga. Ocurrió de verdad: una explicación de doce líneas sobre
la duración de las sesiones, añadida al `realm-dev.json` con la mejor intención,
tumbó el arranque en el siguiente reinicio de la pila.

La prosa vive en `infra/keycloak/README.md`, que es donde no rompe nada.
"""

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
DIR_REALMS = RAIZ / "infra" / "keycloak"
REALMS = sorted(DIR_REALMS.glob("realm-*.json*"))


def _claves(objeto, ruta=""):
    """Todas las claves del documento, con su ruta — a cualquier profundidad."""
    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            yield f"{ruta}/{clave}"
            yield from _claves(valor, f"{ruta}/{clave}")
    elif isinstance(objeto, list):
        for indice, valor in enumerate(objeto):
            yield from _claves(valor, f"{ruta}[{indice}]")


@pytest.mark.parametrize("archivo", REALMS, ids=lambda p: p.name)
def test_el_realm_no_lleva_claves_de_comentario(archivo: Path) -> None:
    documento = json.loads(archivo.read_text(encoding="utf-8"))
    intrusas = [c for c in _claves(documento) if c.rsplit("/", 1)[-1].startswith("_")]
    assert not intrusas, (
        f"{archivo.name} lleva claves que Keycloak no conoce: {intrusas}. "
        "La importación aborta y el contenedor no arranca. "
        "La explicación va en infra/keycloak/README.md."
    )


def test_el_barrido_mira_de_verdad() -> None:
    """Gemelo: un barrido que no encuentra archivos pasaría siempre en verde.

    Y uno que solo mirase la raíz del documento dejaría pasar el mismo comentario
    escrito un nivel más abajo — por eso se comprueba que recorre en profundidad.
    """
    assert len(REALMS) >= 2, f"esperaba los dos realms, encontré {[p.name for p in REALMS]}"

    anidado = {"realm": "x", "clients": [{"clientId": "c", "_nota": "esto rompe el arranque"}]}
    intrusas = [c for c in _claves(anidado) if c.rsplit("/", 1)[-1].startswith("_")]
    assert intrusas == ["/clients[0]/_nota"]


def test_la_explicacion_esta_donde_no_rompe() -> None:
    """Sacar la prosa del JSON solo es media corrección si se pierde el porqué."""
    readme = DIR_REALMS / "README.md"
    assert readme.is_file(), "falta infra/keycloak/README.md"
    texto = readme.read_text(encoding="utf-8")
    assert "ssoSessionIdleTimeout" in texto
    assert "realm-prod.json" in texto
