# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Invariantes del catálogo de tools.

Por qué existe este módulo: la única tool de escritura del MCP declaraba el
permiso `RADI_CREAR`, que **no existe en el sistema** — no está en ninguna de las
migraciones que siembran `auth_permissions`. `visible_tools` comparaba contra un
nombre que jamás aparece en el conjunto devuelto por `/auth/me`, así que para todo
usuario no-ROOT la tool era invisible, y su invocación devolvía 403. La capacidad
llevaba apagada desde el primer commit sin que nada chillara.

Los tests del propio servicio no lo detectaron porque **fabricaban el permiso a
mano** (`X-User-Permissions: RADI_CREAR`, `monkeypatch` de `mcp_user_permissions`):
se auto-consistenciaban con el defecto. Un test que inventa su propio universo no
mide nada del sistema real.

De ahí el criterio de este módulo: **cruzar el catálogo contra la fuente de verdad
del sistema**, que son las migraciones, no una lista escrita a mano aquí. Mismo
patrón que `shared/tests/test_migraciones_ambiguas.py`, que recalcula el conjunto
de nombres ambiguos desde el repositorio: una lista que nadie revalida caduca en
silencio, y su caducidad tiene exactamente la forma del defecto que evita.
"""

from __future__ import annotations

from pathlib import Path

from app.tools.catalog import CATALOG


def _raiz_con_shared() -> Path | None:
    """Raíz del repositorio cuando SÍ está montado (carril de `shared/`).

    No se usa para comprobar permisos —eso vive allí— sino para localizar su
    guardián y comprobar que sigue existiendo.
    """
    aqui = Path(__file__).resolve()
    for candidato in (Path("/repo-ro"), *aqui.parents):
        if (candidato / "shared" / "tests").is_dir():
            return candidato
    return None


_RAIZ_SHARED = _raiz_con_shared()

# `INSERT INTO auth_permissions (nombre, descripcion) VALUES ('PERM_X', '...')`
# Escala real de `auth_group_permissions.crud`
# (auth-service/migrations/tenant/001_rbac.sql):
# 0=Ninguno 1=Leer 2=Editar 3=Crear/Borrar 4=Nivel_1 5=Nivel_2
_CRUD_MIN, _CRUD_MAX = 0, 5


def test_el_control_de_permisos_reales_vive_en_shared_y_no_aqui() -> None:
    """Este test SE SALTABA en todas las corridas, y por eso ya no vive aquí.

    Comprobaba que ningún permiso del catálogo fuera inventado —el control
    escrito para impedir el `RADI_CREAR` fantasma— leyendo las migraciones de
    auth-service. Pero esta imagen **no monta el repositorio**: no las
    encontraba y hacía `pytest.skip`. En el resumen, «11 passed, 1 skipped» se
    lee igual de bien que «12 passed», así que nadie lo miró: el guardián
    llevaba apagado desde su primer commit.

    Lo que ocultaba se destapó al levantar el stack con un tenant real: el
    código exigía SEIS permisos de trámite que ninguna migración sembraba
    (`PERM_TRAMITAR`, `PERM_VOBO`, `PERM_ANULAR`, `PERM_CERRAR_EXP`,
    `PERM_INFORMAR`, `PERM_AGENDAR`), de modo que las cinco escrituras de flujo
    y los doce tipos de transacción sembrados eran ROOT-only.

    Se movió a `shared/tests/test_permisos_existen.py`, el único carril de CI
    que ve el repositorio entero —donde el `skip` deja de ser posible— y allí se
    amplió a las tres fuentes de exigencia: el catálogo, los `require_permission`
    de cualquier router y la columna `requires_permission` de las filas
    sembradas. Los seis que faltaban no los declaraba el catálogo: mirar una
    sola fuente es cómo sobrevivieron a un guardián que llevaba su nombre.

    Queda esta comprobación como señal, para que nadie reintroduzca aquí un
    control que esta imagen no puede ejercer.
    """
    guardian = (
        _RAIZ_SHARED / "shared" / "tests" / "test_permisos_existen.py" if _RAIZ_SHARED else None
    )
    if guardian is None or not guardian.exists():
        # Sin repositorio montado no se puede afirmar nada — y eso es
        # exactamente lo que hay que NO volver a hacer pasar por un `pass`.
        return
    texto = guardian.read_text(encoding="utf-8")
    assert "def test_todo_permiso_exigido_por_un_gate_existe_en_el_sistema" in texto
    assert "def test_todo_permiso_exigido_por_una_fila_sembrada_existe_en_el_sistema" in texto


def test_el_permiso_de_radicacion_es_el_que_gatea_el_endpoint() -> None:
    """Control positivo: sin él, el test anterior pasaría con el catálogo vacío."""
    radicar = next((t for t in CATALOG if t.name == "radicar_documento"), None)
    assert radicar is not None, "la tool de radicación desapareció del catálogo"
    assert radicar.permission == "PERM_RADI", (
        "POST /api/v1/documents está gateado con PERM_RADI "
        "(document-service/app/routers/documents.py)"
    )


def test_el_nivel_crud_declarado_esta_en_la_escala_real() -> None:
    """`min_crud` fuera de 0..5 no lo satisface nadie: la tool queda muerta."""
    for t in CATALOG:
        assert _CRUD_MIN <= t.min_crud <= _CRUD_MAX, (
            f"{t.name} declara min_crud={t.min_crud}, fuera de la escala real 0..5 "
            "de auth_group_permissions.crud"
        )


def test_una_tool_sin_permiso_no_declara_nivel_de_escritura() -> None:
    """Coherencia interna: exigir nivel sin permiso al que aplicarlo no gatea nada.

    `puede_usar` solo compara el nivel cuando hay permiso declarado, así que un
    `min_crud=3` con `permission=None` sería una restricción aparente que no
    restringe — la misma clase de control decorativo que este módulo persigue.
    """
    for t in CATALOG:
        if t.permission is None:
            assert t.min_crud == 1, (
                f"{t.name} no declara permiso pero pide min_crud={t.min_crud}: "
                "ese nivel no se comprueba contra nada"
            )


# ---------------------------------------------------------------------------
# El nivel CRUD gobierna la visibilidad, no solo la presencia del permiso
# ---------------------------------------------------------------------------


def test_el_nivel_insuficiente_esconde_la_tool() -> None:
    """Tener `PERM_RADI` para LEER no habilita la tool que exige crear.

    Sin esto, la tool se ofrece y el backend responde 403 al ejecutarla: el
    defecto que ya se corrigió en la interfaz web (`/auth/me` devolvía la lista
    plana y la UI ofrecía escrituras que el servidor niega).
    """
    from app.tools.catalog import Tool, puede_usar

    escritura = Tool(
        name="x", description="", method="POST", path="/x", permission="PERM_X", min_crud=3
    )
    assert not puede_usar(escritura, {"PERM_X"}, levels={"PERM_X": 1})
    assert puede_usar(escritura, {"PERM_X"}, levels={"PERM_X": 3})
    # Permiso ausente del mapa de niveles = 0 = fail-closed.
    assert not puede_usar(escritura, {"PERM_X"}, levels={})
    # ROOT pasa por encima, como en `require_permission` del backend.
    assert puede_usar(escritura, set(), is_root=True, levels={})


def test_sin_mapa_de_niveles_no_se_degrada_a_negar() -> None:
    """Un llamante que solo tiene la lista plana conserva el comportamiento previo.

    Negar por defecto aquí dejaría sin tools a quien sí las tiene, que es el mismo
    error al revés: exigir de más también falla en silencio.
    """
    from app.tools.catalog import Tool, puede_usar

    t = Tool(name="x", description="", method="POST", path="/x", permission="PERM_X", min_crud=3)
    assert puede_usar(t, {"PERM_X"}, levels=None)


# ---------------------------------------------------------------------------
# Invariantes de la declaración de parámetros
#
# Cierran la trampa que dejó `_TOOL_ARGS`: una tool sin parámetros declarados
# obtenía `properties: {}` y, como la whitelist intersecta con esas propiedades,
# se despachaba SIN ARGUMENTOS. Un fallo silencioso que solo aparecía como un 422
# incomprensible del backend — o como una consulta sin filtros.
# ---------------------------------------------------------------------------


def test_todo_parametro_de_ruta_esta_declarado() -> None:
    """Cada `{x}` de la plantilla tiene su `Param(location="path")`, y al revés.

    Sin esto, el dispatcher no encuentra el valor y falla con `falta_parametro`
    en tiempo de ejecución, cuando el catálogo podría haberlo dicho al arrancar.
    """
    import re as _re

    for t in CATALOG:
        en_plantilla = set(_re.findall(r"\{([a-z_]+)\}", t.path))
        declarados = {p.name for p in t.params if p.location == "path"}
        assert en_plantilla == declarados, (
            f"{t.name}: la ruta usa {sorted(en_plantilla)} y se declaran "
            f"{sorted(declarados)} como parámetros de ruta"
        )
        # `path_params` es el campo histórico que consume el dispatcher; debe
        # coincidir mientras exista, o habrá dos verdades sobre la misma ruta.
        assert set(t.path_params) == en_plantilla, (
            f"{t.name}: path_params={t.path_params} no coincide con la plantilla"
        )


def test_ninguna_tool_se_queda_sin_argumentos_por_olvido() -> None:
    """Una tool con parámetros de ruta DEBE declararlos: si no, se despacha vacía."""
    for t in CATALOG:
        if t.path_params:
            assert t.params, (
                f"{t.name} tiene parámetros de ruta pero no declara ninguno: "
                "se despacharía sin argumentos"
            )


def test_toda_tool_declara_su_operacion_de_dominio() -> None:
    """El marco de alcance habla el idioma del trabajo, no el del catálogo.

    Sin `operacion`, el marco tendría que enseñar nombres de tool a la persona
    —`radicar_documento`— y dejaría de cumplir su función: que nadie descubra un
    límite fallando.
    """
    for t in CATALOG:
        assert t.operacion, f"{t.name} no declara su operación de dominio"


def test_las_etiquetas_de_la_tarjeta_son_legibles() -> None:
    """Todo parámetro que se pinta al confirmar tiene etiqueta en castellano.

    Una tarjeta que dice `dest_dept_code: 120` no permite a nadie confirmar con
    conocimiento de causa, que es justo para lo que existe.
    """
    for t in CATALOG:
        if not t.write:
            continue
        for p in t.params:
            if p.show_in_card:
                assert p.label, f"{t.name}.{p.name} se pinta en la tarjeta sin etiqueta"


def test_toda_escritura_declara_si_es_irreversible() -> None:
    """`destructive` se declara, no se hereda de un default.

    El campo vale `None` cuando nadie lo ha declarado, y este test es la razón:
    con default `False`, olvidarlo habría equivalido a afirmar «este acto se
    deshace» sin que nadie lo hubiera comprobado, y un cliente MCP externo
    habría bajado su fricción confiando en esa afirmación fantasma.

    Con ~40 tools por delante, este invariante es lo que impide que el catálogo
    se vuelva mentira a fuerza de entradas añadidas con prisa — que es
    exactamente cómo entró `RADI_CREAR`.
    """
    for t in CATALOG:
        if t.write:
            assert t.destructive is not None, (
                f"{t.name} es de escritura y no declara `destructive`: "
                "decláralo True o False explícitamente"
            )
        else:
            assert t.destructive is None, (
                f"{t.name} no es de escritura; `destructive` no aplica y confunde"
            )


def test_los_grupos_excluyentes_nombran_parametros_reales_y_opcionales() -> None:
    """`exactly_one_of` es una declaración, y una declaración se puede corromper.

    Dos formas de romperla, las dos silenciosas:

    - **Nombrar un parámetro que no existe** (una `k` de más, un renombrado a
      medias): el grupo nunca encuentra a nadie presente y `coerce_args` rechaza
      TODA llamada a esa tool, con un mensaje que habla de un argumento que el
      modelo no puede enviar. La tool queda muerta sin que nada chille.
    - **Meter en el grupo un parámetro `required`**: se exige enviarlo siempre
      y a la vez se prohíbe enviarlo junto al otro. No hay llamada válida.

    Ninguna de las dos la caza el test de comportamiento de `coerce_args`, que
    solo prueba los grupos tal y como están declarados hoy.
    """
    for t in CATALOG:
        declarados = t.params_by_name
        for grupo in t.exactly_one_of:
            assert len(grupo) >= 2, f"{t.name}: un grupo excluyente de menos de dos no excluye nada"
            for nombre in grupo:
                assert nombre in declarados, (
                    f"{t.name}: el grupo excluyente nombra '{nombre}', que no es un "
                    "parámetro declarado — la tool quedaría imposible de llamar"
                )
                assert not declarados[nombre].required, (
                    f"{t.name}: '{nombre}' es obligatorio Y excluyente a la vez; "
                    "no existe ninguna llamada que satisfaga las dos cosas"
                )
