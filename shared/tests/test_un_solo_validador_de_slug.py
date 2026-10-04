# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-02-05: el slug de tenant tiene UN validador, el de `orpycamcp_common.tenant`.

Estaba copiado en 14 sitios con cinco cotas distintas y el alta admitía slugs que
ningún servicio aceptaba (`-abc`). Una copia futura sin ancla, o con `_`, habría
permitido colisiones de schema o salirse del entrecomillado.
"""

import re
from pathlib import Path

import pytest
from orpycamcp_common.tenant import SLUG_MAX, slug_valido, tenant_schema

_RAIZ = Path(__file__).resolve().parents[2]
_SERVICIOS = _RAIZ / "services"

pytestmark_arbol = pytest.mark.skipif(
    not _SERVICIOS.is_dir(), reason="el árbol de servicios no está montado"
)

#: Una regex local de slug: la definición `X_RE = re.compile(...)` con el patrón de
#: «minúsculas, dígitos y guion» o el patrón mismo.
_COPIA_LOCAL = re.compile(r"""\b_?(?:TENANT_)?SLUG_RE\s*=\s*re\.compile|\[a-z0-9\]\[a-z0-9-\]\{""")


def _fuentes() -> list[Path]:
    return sorted(_SERVICIOS.glob("*/app/**/*.py"))


@pytestmark_arbol
def test_ningun_servicio_define_su_propia_regex_de_slug() -> None:
    copias = [
        f"{p.relative_to(_RAIZ)}:{n}"
        for p in _fuentes()
        for n, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _COPIA_LOCAL.search(linea) and not linea.lstrip().startswith("#")
    ]
    assert not copias, (
        "El slug se valida SOLO con orpycamcp_common.tenant (slug_valido / "
        f"tenant_schema); copias locales: {copias}"
    )


@pytestmark_arbol
def test_el_censo_no_es_vacio_y_ve_a_los_que_importan_el_validador() -> None:
    """Anti-vacuidad: si el glob no encuentra nada, el guardián de arriba pasaría."""
    assert len(_fuentes()) > 200
    importan = [p for p in _fuentes() if "orpycamcp_common.tenant" in p.read_text(encoding="utf-8")]
    assert len(importan) >= 9, [p.name for p in importan]


@pytestmark_arbol
def test_ninguna_derivacion_de_schema_se_hace_a_mano() -> None:
    a_mano = [
        f"{p.relative_to(_RAIZ)}:{n}"
        for p in _fuentes()
        for n, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"""f["']tenant_\{[^}]*replace\(""", linea) and not linea.lstrip().startswith("#")
    ]
    assert not a_mano, f"deriva `tenant_{{slug}}` solo con tenant_schema(): {a_mano}"


@pytest.mark.parametrize(
    "slug",
    ['a";DROP', "a:b", "-a", "A", "a_b", "x" * (SLUG_MAX + 1), "", "a", "a b", "ab\n", "é1"],
)
def test_slug_invalido_se_rechaza(slug: str) -> None:
    assert not slug_valido(slug)
    with pytest.raises(ValueError):
        tenant_schema(slug)


def test_el_tope_cabe_en_el_identificador_de_postgres() -> None:
    assert slug_valido("a" * SLUG_MAX)
    assert len(tenant_schema("a" * SLUG_MAX).encode()) <= 63


def test_slug_con_guion_se_acepta_y_se_normaliza() -> None:
    assert tenant_schema("bogota-limpia") == "tenant_bogota_limpia"
    assert slug_valido("demo")


def test_no_slug_cadena_se_rechaza() -> None:
    assert not slug_valido(None)
    assert not slug_valido(123)


def test_el_patron_esta_anclado_en_ambos_extremos() -> None:
    """Defensa doble: `fullmatch` ya exige el calce completo, pero el patrón mismo
    viaja a otros usos (p. ej. un `pattern=` de Pydantic) donde no hay `fullmatch`."""
    from orpycamcp_common.tenant import SLUG_PATTERN

    assert SLUG_PATTERN.startswith("^") and SLUG_PATTERN.endswith("$")


# --------------------------------------------------------------------------- uso real


def _importa_y_llama(ruta: Path) -> tuple[set[str], set[str]]:
    """(nombres importados de orpycamcp_common.tenant, nombres LLAMADOS en el módulo)."""
    import ast

    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module == "orpycamcp_common.tenant":
            importados |= {a.asname or a.name for a in nodo.names}
    llamados = {
        n.func.id for n in ast.walk(arbol) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    return importados & {"slug_valido", "tenant_schema", "_schema_de"}, llamados


@pytestmark_arbol
def test_quien_importa_el_validador_lo_llama_de_verdad() -> None:
    """Importar y no llamar deja la puerta abierta con el guardián de «importan >= 9» en
    verde (mutación: borrar el `if not slug_valido(...)` de un database.py)."""
    muertos = []
    visto = 0
    for p in _fuentes():
        if "orpycamcp_common.tenant" not in p.read_text(encoding="utf-8"):
            continue
        importados, llamados = _importa_y_llama(p)
        for nombre in importados:
            visto += 1
            if nombre not in llamados:
                muertos.append(f"{p.relative_to(_RAIZ)}:{nombre}")
    assert visto >= 15, visto
    assert not muertos, f"importado pero nunca llamado (validación inerte): {muertos}"


@pytestmark_arbol
def test_cada_database_py_llama_a_slug_valido() -> None:
    """Los 9 `core/database.py` DEBEN llamar a `slug_valido` (no solo a `_schema_de`,
    que ya valida pero lanza ValueError sin el 400 del borde HTTP)."""
    faltan = []
    dbs = sorted(_SERVICIOS.glob("*/app/core/database.py"))
    assert len(dbs) >= 9
    for p in dbs:
        _, llamados = _importa_y_llama(p)
        if "slug_valido" not in llamados:
            faltan.append(str(p.relative_to(_RAIZ)))
    assert not faltan, faltan
