# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Guardián: `PARAMS_ADMITIDOS` de `PUT /config/params/{key}` == claves sembradas.

La allowlist (tenant-service/app/routers/config.py) impide que la tabla
`sgd_param_admin` —de lectura abierta a todo el tenant— aloje claves libres. Pero
una lista escrita a mano envejece: una migración que siembra un parámetro nuevo sin
añadirlo aquí lo deja imposible de configurar (el caso del membrete, ver
`test_params_entidad_configurables`), y una clave retirada de la siembra que sigue
admitida reabre el buzón. Se compara en las dos direcciones, leyendo el fuente.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
MIGRACIONES = RAIZ / "services" / "tenant-service" / "migrations" / "tenant"
ROUTER = RAIZ / "services" / "tenant-service" / "app" / "routers" / "config.py"

_necesita_arbol = pytest.mark.skipif(
    not MIGRACIONES.is_dir(), reason="el árbol de servicios no está montado"
)


def _lista(nombre: str) -> set[str]:
    for nodo in ast.parse(ROUTER.read_text(encoding="utf-8")).body:
        if isinstance(nodo, ast.AnnAssign) and getattr(nodo.target, "id", "") == nombre:
            return {
                c.value
                for c in ast.walk(nodo.value)  # type: ignore[arg-type]
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            }
    raise AssertionError(f"{nombre} no encontrado en config.py")


def _admitidas() -> set[str]:
    return _lista("PARAMS_ADMITIDOS")


def _inertes() -> set[str]:
    return _lista("PARAMS_INERTES")


def _sembradas() -> set[str]:
    claves: set[str] = set()
    for f in sorted(MIGRACIONES.glob("*.sql")):
        sql = f.read_text(encoding="utf-8")
        for bloque in re.finditer(
            r"INSERT INTO sgd_param_admin[^;]*?;", sql, re.IGNORECASE | re.DOTALL
        ):
            claves.update(re.findall(r"\(\s*'([a-z][a-z0-9_]*)'\s*,", bloque.group(0)))
    return claves


@_necesita_arbol
def test_toda_clave_sembrada_es_configurable_o_inerte_declarada() -> None:
    """sembradas = admitidas ∪ inertes declaradas: ninguna queda sin decidir."""
    sin_decidir = _sembradas() - _admitidas() - _inertes()
    assert not sin_decidir, (
        "sembradas, rechazadas por el PUT (422) y sin declarar inertes: "
        f"{sorted(sin_decidir)}"
    )


@_necesita_arbol
def test_una_clave_no_es_a_la_vez_admitida_e_inerte() -> None:
    assert not _admitidas() & _inertes(), sorted(_admitidas() & _inertes())


@_necesita_arbol
def test_ninguna_inerte_carece_de_siembra() -> None:
    """La lista de inertes tampoco envejece: una retirada de la siembra se quita."""
    assert not _inertes() - _sembradas(), sorted(_inertes() - _sembradas())


@_necesita_arbol
def test_una_inerte_no_la_lee_ningun_servicio() -> None:
    """Qué las hace inertes: ningún `app/` de servicio las menciona salvo la lista
    de config.py. Si alguien las cablea, el test obliga a moverlas a admitidas."""
    for archivo in sorted((RAIZ / "services").glob("*/app/**/*.py")):
        if archivo == ROUTER:
            continue
        texto = archivo.read_text(encoding="utf-8")
        leidas = sorted(k for k in _inertes() if re.search(rf"['\"]{k}['\"]", texto))
        assert not leidas, f"{archivo.relative_to(RAIZ)} lee claves declaradas inertes: {leidas}"


@_necesita_arbol
def test_ninguna_clave_admitida_carece_de_siembra() -> None:
    de_mas = _admitidas() - _sembradas()
    assert not de_mas, (
        f"admitidas por el PUT y no sembradas por ninguna migración: {sorted(de_mas)}"
    )


@_necesita_arbol
def test_el_censo_no_esta_vacio() -> None:
    """Anti-vacuidad: dos conjuntos vacíos serían 'iguales'."""
    assert len(_sembradas()) >= 20
    assert len(_admitidas()) >= 10
    assert len(_inertes()) == 10


@_necesita_arbol
def test_las_claves_que_lee_el_codigo_estan_admitidas() -> None:
    leidas = {"firma_personal_require_xades"}
    fuente = (
        RAIZ / "services" / "document-service" / "app" / "services" / "contexto_combinacion.py"
    ).read_text(encoding="utf-8")
    leidas.update(re.findall(r'"(entidad_[a-z_]+)"', fuente))
    assert not leidas - _admitidas(), sorted(leidas - _admitidas())
