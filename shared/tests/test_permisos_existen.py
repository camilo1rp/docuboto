# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Todo permiso que el sistema EXIGE tiene que ser un permiso que el sistema SIEMBRA.

**Este control ya existía y llevaba desde su primer commit sin comprobar nada.**
Vivía en `mcp-server/tests/test_catalog_invariants.py`, y esa imagen no monta el
repositorio: no encontraba las migraciones de auth-service, así que hacía
`pytest.skip` en cada corrida. Un `skip` es indistinguible de un `pass` en el
resumen —«11 passed, 1 skipped»— y por eso nadie lo miró. El guardián escrito
para impedir el `RADI_CREAR` fantasma estaba, él mismo, apagado.

**Lo que ocultaba, medido con un tenant real levantado**: `auth_permissions`
traía 20 filas y el código exigía SEIS permisos que no estaban —`PERM_TRAMITAR`,
`PERM_VOBO`, `PERM_ANULAR`, `PERM_CERRAR_EXP`, `PERM_INFORMAR`,
`PERM_AGENDAR`—, todos de trámite. Un permiso que no está en `auth_permissions`
no se puede conceder a ningún grupo, así que el gate niega a todo el mundo salvo
a ROOT: los 12 tipos de transacción sembrados y las cinco escrituras de flujo
eran ROOT-only. Falla cerrado, que es el lado correcto, y aun así es capacidad
que existe en el código y que ninguna entidad podía repartir.

**Por qué vive aquí y por qué se amplió.** Aquí, porque `shared/tests/` es el
único carril de CI que ve el repositorio entero: el `skip` deja de ser posible, y
un `skip` que oculta un defecto real es otro mecanismo que reporta éxito sin
verificar nada. Y ampliado porque el control anterior solo miraba el catálogo
MCP: los seis que faltaban no los declara el catálogo, los declara el gate de un
router y la columna `requires_permission` de una tabla sembrada. Mirar solo una
de las tres fuentes es cómo el defecto sobrevivió a un guardián que llevaba su
nombre.

Las tres fuentes se derivan del código. No hay ninguna lista escrita a mano.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_SERVICIOS = _RAIZ / "services"
_SEED = _SERVICIOS / "auth-service" / "migrations" / "tenant"

# Un nombre de permiso: mayúsculas, dígitos y guión bajo. Se exige el prefijo
# real del sistema para no confundir un permiso con cualquier otra constante en
# mayúsculas que aparezca en un SQL.
_PERMISO = re.compile(r"\b((?:PERM|USUA_PERM|SGD_PERM)_[A-Z0-9_]+)\b")


def permisos_sembrados() -> set[str]:
    """Los que existen de verdad, leídos de los `INSERT INTO auth_permissions`."""
    nombres: set[str] = set()
    for fichero in sorted(_SEED.glob("*.sql")):
        sql = fichero.read_text(encoding="utf-8")
        if "INSERT INTO auth_permissions" not in sql:
            continue
        bloque = sql[sql.index("INSERT INTO auth_permissions") :]
        # Solo las tuplas de valores: `('PERM_X', 'descripción')`.
        nombres.update(m.group(1) for m in re.finditer(r"\('([A-Z][A-Z0-9_]+)'", bloque))
    return nombres


def permisos_exigidos_por_los_gates() -> dict[str, set[str]]:
    """`require_permission("X")` en cualquier router, por AST. permiso → servicios."""
    exigidos: dict[str, set[str]] = {}
    for fichero in sorted(_SERVICIOS.glob("*/app/**/*.py")):
        try:
            arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — lo caza el lint del servicio
            continue
        servicio = fichero.relative_to(_SERVICIOS).parts[0]
        for nodo in ast.walk(arbol):
            if not (
                isinstance(nodo, ast.Call)
                and getattr(nodo.func, "id", "") == "require_permission"
                and nodo.args
                and isinstance(nodo.args[0], ast.Constant)
                and isinstance(nodo.args[0].value, str)
            ):
                continue
            exigidos.setdefault(nodo.args[0].value, set()).add(servicio)
    return exigidos


def permisos_exigidos_por_los_datos_sembrados() -> dict[str, set[str]]:
    """Los que declaran las FILAS sembradas, no el código.

    `transaction_types.requires_permission` es el caso real: doce filas que
    nombran un permiso cada una. Es una exigencia tan efectiva como un decorador
    —el servicio la lee y autoriza contra ella— y no aparece en ningún `.py`.
    """
    exigidos: dict[str, set[str]] = {}
    for fichero in sorted(_SERVICIOS.glob("*/migrations/**/*.sql")):
        # El propio seed de permisos los DEFINE, no los exige.
        if fichero.parent == _SEED:
            continue
        servicio = fichero.relative_to(_SERVICIOS).parts[0]
        for m in _PERMISO.finditer(fichero.read_text(encoding="utf-8")):
            exigidos.setdefault(m.group(1), set()).add(servicio)
    return exigidos


def test_todo_permiso_exigido_por_un_gate_existe_en_el_sistema() -> None:
    """El caso `RADI_CREAR`: un gate contra un permiso que nadie puede tener."""
    sembrados = permisos_sembrados()
    assert sembrados, "no se leyó ningún permiso sembrado: el parseo se rompió"

    fantasma = {
        p: sorted(s) for p, s in permisos_exigidos_por_los_gates().items() if p not in sembrados
    }
    assert not fantasma, (
        "estos permisos los exige un gate y NO los siembra ninguna migración de "
        "auth-service: no se pueden conceder a ningún grupo, así que el endpoint "
        f"queda inalcanzable salvo para ROOT: {fantasma}"
    )


def test_todo_permiso_exigido_por_una_fila_sembrada_existe_en_el_sistema() -> None:
    """La fuente que el control anterior no miraba, y por donde entraron los seis.

    `transaction_types.requires_permission` autoriza en tiempo de ejecución. Una
    fila que nombre un permiso inexistente deja ese tipo de transacción
    ejecutable solo por ROOT, y no hay ningún decorador donde verlo.
    """
    sembrados = permisos_sembrados()
    fantasma = {
        p: sorted(s)
        for p, s in permisos_exigidos_por_los_datos_sembrados().items()
        if p not in sembrados
    }
    assert not fantasma, (
        "estas filas sembradas exigen un permiso que no existe en "
        f"`auth_permissions`: solo ROOT podrá ejecutarlas: {fantasma}"
    )


def test_el_control_ve_de_verdad_las_tres_fuentes() -> None:
    """Control de cobertura, porque el modo de fallo de este test fue SALTARSE.

    Su versión anterior hacía `pytest.skip` cuando no encontraba las
    migraciones, y un `skip` es indistinguible de un `pass` en el resumen. Aquí
    no hay `skip` posible —`shared/` ve el repositorio entero— pero un parseo
    que dejara de encontrar cosas produciría conjuntos vacíos y los dos tests de
    arriba quedarían verdes para siempre. Se mide que las tres fuentes traen
    material.
    """
    assert len(permisos_sembrados()) >= 20
    assert len(permisos_exigidos_por_los_gates()) >= 10, (
        "casi ningún gate se está leyendo: el AST dejó de encontrar "
        "`require_permission`"
    )
    assert permisos_exigidos_por_los_datos_sembrados(), (
        "ninguna fila sembrada declara permiso: el barrido de migraciones se rompió"
    )
