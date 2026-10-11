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
                and getattr(nodo.func, "id", "")
                in ("require_permission", "require_any_permission")
                and nodo.args
            ):
                continue
            # `require_any_permission("A", "B")`: TODOS los literales cuentan, un
            # permiso fantasma en la 2.ª posición dejaría ese brazo inalcanzable.
            literales = (
                nodo.args
                if getattr(nodo.func, "id", "") == "require_any_permission"
                else nodo.args[:1]
            )
            for arg in literales:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    exigidos.setdefault(arg.value, set()).add(servicio)
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


# Permisos SEMBRADOS que hoy no exige nadie, declarados a mano y vigilados abajo.
#
# Un permiso otorgable que no restringe nada es una promesa falsa: la entidad lo
# concede creyendo que acota a un funcionario, y no acota. La migración 018
# corrige su DESCRIPCIÓN para que quien va a concederlo lo lea; esta lista es la
# que permite comprobar que esa corrección sigue estando al día.
#
# `USUA_PERM_ROOT` entra aquí porque tampoco aparece en ningún
# `require_permission` — pero por un motivo legítimo y distinto: es el bypass, se
# comprueba con `is_root`. Se declara junto a los demás para que la comparación
# de abajo sea exacta y no haya que llevar dos listas.
_INERTES_DECLARADOS = {
    "USUA_PERM_ROOT",  # legítimo: bypass, no se comprueba con require_permission
    "PERM_APRO_ANULAR",
    "PERM_BORRAR_ANEXO",
    "PERM_DESCARGAEXP",
    "PERM_NRR",
    "PERM_RADI_ENTRADA",
    "PERM_RADI_INTERNO",
    "PERM_TIPIF_ANEXO",
}


def test_la_lista_de_permisos_inertes_esta_al_dia() -> None:
    """Una lista a mano solo es segura si algo deriva la verdad y la compara.

    Este test cierra el barrido en el sentido INVERSO al de arriba. Aquellos
    persiguen un permiso EXIGIDO que nadie siembra —el gate que nadie puede
    satisfacer—; este persigue un permiso SEMBRADO que nadie exige: el control
    que una entidad concede creyendo que restringe, y no restringe. De los 26
    sembrados había NUEVE así, y solo uno era legítimo.

    Falla en los dos sentidos, y los dos importan:

    * Si alguien **cablea** uno de estos permisos a un gate, hay que sacarlo de
      la lista y corregir su descripción, que dice «RESERVADO — hoy NO restringe
      nada» y pasaría a ser falsa justo cuando empieza a restringir.
    * Si aparece un permiso sembrado **nuevo** que nadie exige, hay que
      declararlo aquí y decirlo en su descripción, en vez de dejar otra promesa
      vacía en el catálogo que ve el administrador.

    El caso que motivó todo esto fue `SGD_PERM_ESTADISTICA`: estaba en este
    grupo, la interfaz lo honraba y el backend no. Ya lo exigen los dos
    endpoints de reportes, así que NO figura en la lista — y si alguien
    revirtiera ese gate, este test lo diría.
    """
    sembrados = permisos_sembrados()
    exigidos = set(permisos_exigidos_por_los_gates()) | set(
        permisos_exigidos_por_los_datos_sembrados()
    )
    inertes_reales = sembrados - exigidos

    sobran = sorted(_INERTES_DECLARADOS - inertes_reales)
    faltan = sorted(inertes_reales - _INERTES_DECLARADOS)

    assert not sobran, (
        "estos permisos YA están cableados a un gate, así que sobran de la lista "
        "de inertes; hay que quitarlos de aquí y corregir su descripción en una "
        f"migración, que sigue diciendo que no restringen nada: {sobran}"
    )
    assert not faltan, (
        "estos permisos se siembran y NO los exige nadie: se pueden conceder "
        "creyendo que restringen algo. Decláralos aquí y di en su descripción "
        f"que no restringen, o cabléalos a su gate: {faltan}"
    )


# ---------------------------------------------------------------------------
# El demo: un grupo que dice «todos los permisos» tiene que concederlos todos
# ---------------------------------------------------------------------------


def _constantes_del_seed() -> dict[str, object]:
    """Lee `scripts/seed_demo.py` por su AST, sin importarlo.

    Importarlo arrastraría `asyncpg` y `httpx` a un test que solo quiere mirar
    tres tuplas de texto, y ataría este guardián a que el seed sea importable
    —que es justo lo que no se puede dar por supuesto en el carril de CI.
    """
    arbol = ast.parse((_RAIZ / "scripts" / "seed_demo.py").read_text(encoding="utf-8"))
    valores: dict[str, object] = {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and isinstance(nodo.targets[0], ast.Name):
            try:
                valores[nodo.targets[0].id] = ast.literal_eval(nodo.value)
            except ValueError:
                continue
    return valores


# ROOT no es «un permiso más»: salta comprobaciones en vez de habilitar una
# función. Un demo que enseñara el sistema desde ROOT no enseñaría el sistema,
# enseñaría el sistema sin sus controles. Se excluye por escrito, que es la
# diferencia entre una decisión y un olvido.
_FUERA_DEL_DEMO = {"USUA_PERM_ROOT"}


def test_el_demo_concede_todos_los_permisos_que_su_nombre_promete() -> None:
    """El grupo del demo se llama «Demostración (todos los permisos)».

    Durante toda la vida del script concedió **8 de 26**, y la diferencia no se
    veía por ninguna parte: `/envios`, `/admin/colas`, `/anulaciones`, la
    publicación de oficio y el índice de clasificados aparecían **vacíos**, que
    es indistinguible de una función que no existe. Quien estrenaba el sistema
    concluía que esas partes no estaban hechas.

    Este control es sobre todo para el FUTURO: el modo natural de que vuelva a
    pasar es que alguien añada un permiso a las migraciones y nadie se acuerde
    del demo. Entonces el guardián se pone rojo y obliga a elegir — concederlo,
    o declararlo excluido en `_FUERA_DEL_DEMO` con su motivo.
    """
    reales = permisos_sembrados()
    concedidos = set(_constantes_del_seed()["PERMISOS_DEMO"])  # type: ignore[arg-type]

    olvidados = reales - concedidos - _FUERA_DEL_DEMO
    assert not olvidados, (
        f"El grupo «todos los permisos» del demo NO concede: {sorted(olvidados)}. "
        "Añádelos a PERMISOS_DEMO en scripts/seed_demo.py, o si hay una razón para "
        "dejarlos fuera, escríbela en _FUERA_DEL_DEMO. Un demo silenciosamente "
        "parcial enseña menos sistema del que hay."
    )

    inventados = concedidos - reales
    assert not inventados, (
        f"PERMISOS_DEMO nombra permisos que no existen: {sorted(inventados)}. "
        "El seed abortaría a media ejecución."
    )


def test_el_usuario_pobre_del_demo_sigue_siendo_pobre() -> None:
    """Control negativo, y no es simetría gratuita.

    `consulta` existe para que se pueda VER que los controles funcionan: sin un
    usuario al que le falten permisos, «el botón no aparece» y «no hay control»
    son la misma pantalla. Si alguien le concediera de más —por ejemplo
    aplicándole `PERMISOS_DEMO` por comodidad— el barrido negativo entero
    seguiría pasando y ya no probaría nada.
    """
    seed = _constantes_del_seed()
    pobres = set(seed["PERMISOS_CONSULTA"])  # type: ignore[arg-type]
    ricos = set(seed["PERMISOS_DEMO"])  # type: ignore[arg-type]

    assert pobres < ricos, "el usuario de consulta debe tener un subconjunto ESTRICTO"
    assert seed["CRUD_CONSULTA"] == 1, "con CRUD 3 no se puede ver desaparecer ningún botón"
    assert seed["CLEARANCE_CONSULTA"] == 1, "con clearance alto no se puede probar el no-read-up"
    assert seed["CLEARANCE_DEMO"] > seed["CLEARANCE_CONSULTA"], (  # type: ignore[operator]
        "los dos clearances iguales dejan el eje de reserva sin probar"
    )
