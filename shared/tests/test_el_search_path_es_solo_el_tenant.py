# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El `search_path` es SOLO el tenant; todo lo global se califica `public.` (ADR-002, C-7 / SEG #12).

Con `"tenant_x", public` en el path, una tabla de tenant que faltara se resolvía en
silencio contra la homónima de `public` (de hecho había 14 copias huérfanas allí). Sin
`public`, la misma ausencia es `UndefinedTable`: fail-closed. El costo es que lo global
(`public.audit_log`, `public.tenants`...) y las extensiones (`public.vector`,
`OPERATOR(public.<=>)`, `public.gin_trgm_ops`, `public.unaccent`) se nombran
calificados. Este guardián lo hace imposible de olvidar:

1. NINGÚN `search_path` lleva más de un schema (los tests pueden llevar dos TENANTS, pero
   jamás `public`). `= public` a secas (conexión sin tenant) se admite.
2. Las tablas/funciones globales (DERIVADAS de `migrations/global/*.sql`) no se nombran
   sin `public.` en el SQL de ejecución ni en las migraciones de tenant.
3. Las extensiones se nombran calificadas, y `CREATE EXTENSION` dice `WITH SCHEMA public`.
4. Controles positivos: sobre fuentes sintéticas el guardián SÍ acusa.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"
ESTE = Path(__file__).name

_SITIO = re.compile(r"search_path\s*(?:=|TO)\s*", re.IGNORECASE)
_SET_CONFIG = re.compile(r"""set_config\(\s*['"]search_path['"]\s*,\s*(['"])(.*?)\1""", re.IGNORECASE)
_SERVER_SETTINGS = re.compile(r"""['"]search_path['"]\s*:\s*(['"])(.*?)\1""", re.IGNORECASE)


def _es_comentario(linea: str) -> bool:
    return linea.lstrip().startswith(("#", "--"))


def _valor_de_sitio(resto: str) -> str:
    """Texto del valor de un `SET search_path = <valor>` dentro de una cadena Python/SQL.

    Respeta las comillas dobles del identificador y las llaves de un f-string
    (`{tenant_schema(slug)}`), y se detiene en la comilla simple que cierra la sentencia,
    en `)`, `;` o `#` fuera de ellas.
    """
    salida: list[str] = []
    entre_comillas = False
    llaves = 0
    for c in resto:
        if c == "{" and not entre_comillas:
            llaves += 1
        elif c == "}" and not entre_comillas and llaves:
            llaves -= 1
        elif llaves == 0:
            if c == '"':
                entre_comillas = not entre_comillas
            elif not entre_comillas and c in "');#`":
                break
            elif not entre_comillas and c == ")":
                break
        salida.append(c)
    return "".join(salida)


def _elementos(valor: str) -> list[str]:
    """Elementos de la lista del path, separados por comas de nivel superior."""
    elementos: list[str] = []
    actual: list[str] = []
    entre_comillas = False
    llaves = 0
    for c in valor:
        if c == "{" and not entre_comillas:
            llaves += 1
        elif c == "}" and not entre_comillas and llaves:
            llaves -= 1
        if c == '"' and llaves == 0:
            entre_comillas = not entre_comillas
        if c == "," and not entre_comillas and llaves == 0:
            elementos.append("".join(actual).strip())
            actual = []
        else:
            actual.append(c)
    elementos.append("".join(actual).strip())
    return [e for e in elementos if e]


def _sitios(texto: str) -> list[tuple[int, list[str]]]:
    """(línea, elementos del path) de cada asignación de `search_path`, sin comentarios.

    Los docstrings SÍ cuentan: ahí se documentaba la cadena vieja.
    """
    hallazgos: list[tuple[int, list[str]]] = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if _es_comentario(linea):
            continue
        for m in _SITIO.finditer(linea):
            elementos = _elementos(_valor_de_sitio(linea[m.end():]))
            if elementos:
                hallazgos.append((n, elementos))
        for rx in (_SET_CONFIG, _SERVER_SETTINGS):
            for m in rx.finditer(linea):
                hallazgos.append((n, _elementos(m.group(2))))
    return hallazgos


def _es_public(elemento: str) -> bool:
    return elemento.strip().strip('"').lower() == "public"


def _viola_el_path(elementos: list[str], *, es_test: bool) -> bool:
    if len(elementos) <= 1:
        return False
    # Los tests pueden fijar dos TENANTS a propósito (probar que una consulta califica
    # su schema aunque haya otra homónima en el path); `public` nunca va en la lista.
    return any(_es_public(e) for e in elementos) if es_test else True


def _ficheros_de_codigo() -> list[Path]:
    ficheros: list[Path] = []
    for base in (SERVICIOS, RAIZ / "shared", RAIZ / "scripts"):
        for ext in ("*.py", "*.sql", "*.sh"):
            ficheros += [
                f
                for f in base.rglob(ext)
                if f.name != ESTE
                and not {"node_modules", "__pycache__", ".venv", "site-packages"} & set(f.parts)
            ]
    return sorted(ficheros)


def _es_test(f: Path) -> bool:
    return "tests" in f.relative_to(RAIZ).parts


# ---------------------------------------------------------------------------
# 1. Un solo schema
# ---------------------------------------------------------------------------


def _violaciones_de_path() -> tuple[list[str], int]:
    violaciones: list[str] = []
    en_produccion = 0
    for f in _ficheros_de_codigo():
        es_test = _es_test(f)
        for n, elementos in _sitios(f.read_text(encoding="utf-8")):
            if not es_test:
                en_produccion += 1
            if _viola_el_path(elementos, es_test=es_test):
                violaciones.append(f"{f.relative_to(RAIZ)}:{n}: {', '.join(elementos)}")
    return violaciones, en_produccion


def test_ningun_search_path_lleva_mas_de_un_schema():
    violaciones, _ = _violaciones_de_path()
    assert not violaciones, (
        "El `search_path` es SOLO el tenant (ADR-002, addendum C-7): con `public` detrás, "
        "una tabla de tenant ausente se resuelve en silencio contra la homónima de `public`. "
        "Calificar `public.` lo global en vez de ampliar el path: " + " | ".join(violaciones)
    )


def test_el_censo_de_search_path_no_esta_vacio():
    """Anti-vacuidad: sin sitios que mirar, el guardián de arriba pasa sin comprobar nada."""
    _, en_produccion = _violaciones_de_path()
    assert en_produccion >= 60, en_produccion


# ---------------------------------------------------------------------------
# 2. Lo global se califica
# ---------------------------------------------------------------------------

_CREA = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|FUNCTION|VIEW|SEQUENCE)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?",
    re.IGNORECASE,
)
#: Globales que el libro de migraciones crea fuera de `migrations/global/` (shared).
_GLOBALES_EXTRA = {"schema_migrations", "_migrations", "_tenant_migrations"}


def _nombres_creados(patron: str) -> set[str]:
    nombres: set[str] = set()
    for f in SERVICIOS.glob(patron):
        for linea in f.read_text(encoding="utf-8").splitlines():
            if _es_comentario(linea):
                continue
            nombres.update(m.group(1).lower() for m in _CREA.finditer(linea))
    return nombres


def _objetos_globales() -> set[str]:
    """Nombres que SOLO existen en `public`: creados en `migrations/global/` y en ningún
    `migrations/tenant/` (un nombre en ambos sería ambiguo y daría falsos positivos)."""
    globales = _nombres_creados("*/migrations/global/*.sql") | _GLOBALES_EXTRA
    de_tenant = _nombres_creados("*/migrations/tenant/*.sql")
    return {n for n in globales - de_tenant if len(n) >= 4 and n != "public"}


_USO_SQL = (
    r"(?:FROM|JOIN|INTO|UPDATE|TABLE|REFERENCES|PERFORM|EXECUTE\s+FUNCTION|EXECUTE\s+PROCEDURE)"
)


def _sin_calificar(texto: str, nombres: set[str]) -> list[tuple[int, str]]:
    if not nombres:
        return []
    rx = re.compile(
        r"\b" + _USO_SQL + r"\s+(?:ONLY\s+)?(?<![.\"])\b(" + "|".join(sorted(nombres)) + r")\b(?!\.)"
    )
    hallazgos: list[tuple[int, str]] = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if _es_comentario(linea):
            continue
        hallazgos += [(n, m.group(1)) for m in rx.finditer(linea)]
    return hallazgos


def _ficheros_de_ejecucion() -> list[Path]:
    """SQL que corre con el path del tenant: servicios, shared, scripts y migraciones de
    tenant. Las `migrations/global/` corren con `public` y quedan fuera; los tests, también
    (sus fixtures usan fuentes sintéticas)."""
    ficheros: list[Path] = []
    ficheros += list(SERVICIOS.glob("*/app/**/*.py"))
    ficheros += list((RAIZ / "shared" / "orpycamcp_common").glob("*.py"))
    ficheros += list((RAIZ / "scripts").glob("*.py"))
    ficheros += list(SERVICIOS.glob("*/migrations/tenant/*.sql"))
    return sorted(f for f in ficheros if f.name != ESTE)


def test_lo_global_no_se_nombra_sin_public():
    nombres = _objetos_globales()
    sin_calificar = [
        f"{f.relative_to(RAIZ)}:{n}: {nombre}"
        for f in _ficheros_de_ejecucion()
        for n, nombre in _sin_calificar(f.read_text(encoding="utf-8"), nombres)
    ]
    assert not sin_calificar, (
        "Estos objetos viven en `public` y el `search_path` ya no lo incluye: "
        "calificar `public.<nombre>` — " + " | ".join(sin_calificar)
    )


def test_el_censo_de_objetos_globales_no_esta_vacio():
    nombres = _objetos_globales()
    assert {"audit_log", "tenants", "schema_migrations", "divipola_municipio"} <= nombres, nombres


# ---------------------------------------------------------------------------
# 3. Las extensiones se nombran calificadas
# ---------------------------------------------------------------------------

_EXTENSION = re.compile(
    r"(?<![\w.])(?:unaccent|digest|crypt|gen_salt|similarity|word_similarity|show_trgm)\s*\("
    r"|::\s*vector\b"
    r"|(?<!OPERATOR\(public\.)(?:<=>|<#>|embedding\s*<->)"
    r"|(?<![\w.])(?:gin_trgm_ops|gist_trgm_ops|vector_cosine_ops|vector_l2_ops|vector_ip_ops)\b"
    r"|(?<![\w.])vector\s*\(\s*\d"
    r"|'spanish_unaccent'",
    re.IGNORECASE,
)


def _extensiones_sin_calificar(texto: str) -> list[tuple[int, str]]:
    hallazgos: list[tuple[int, str]] = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if _es_comentario(linea):
            continue
        # La búsqueda de la CONFIGURACIÓN en el catálogo no es un uso: `cfgname = '...'`.
        if "cfgname" in linea:
            continue
        hallazgos += [(n, m.group(0)) for m in _EXTENSION.finditer(linea)]
    return hallazgos


def test_las_extensiones_se_nombran_calificadas():
    malos = [
        f"{f.relative_to(RAIZ)}:{n}: {uso}"
        for f in _ficheros_de_ejecucion()
        for n, uso in _extensiones_sin_calificar(f.read_text(encoding="utf-8"))
    ]
    assert not malos, (
        "Las extensiones viven en `public` y el path ya no lo incluye: "
        "`public.vector`, `OPERATOR(public.<=>)`, `public.gin_trgm_ops`, `public.unaccent`, "
        "`'public.spanish_unaccent'::regconfig` — " + " | ".join(malos)
    )


_CREATE_EXTENSION = re.compile(r"CREATE\s+EXTENSION\b[^;]*", re.IGNORECASE)


def test_create_extension_dice_with_schema_public():
    sin_esquema: list[str] = []
    ficheros = list(SERVICIOS.glob("*/migrations/**/*.sql")) + [RAIZ / "scripts" / "init-db.sql"]
    visto = 0
    for f in ficheros:
        if not f.exists():
            continue
        for n, linea in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _es_comentario(linea):
                continue
            for m in _CREATE_EXTENSION.finditer(linea):
                visto += 1
                if not re.search(r"WITH\s+SCHEMA\s+public\b", m.group(0), re.IGNORECASE):
                    sin_esquema.append(f"{f.relative_to(RAIZ)}:{n}")
    assert visto >= 3, visto
    assert not sin_esquema, (
        "`CREATE EXTENSION` sin `WITH SCHEMA public`: la extensión caería donde apunte el "
        "path de quien migre — " + ", ".join(sin_esquema)
    )


# ---------------------------------------------------------------------------
# 4. Controles positivos: sobre fuentes sintéticas el guardián SÍ acusa
# ---------------------------------------------------------------------------


def test_el_guardian_acusa_un_path_con_public():
    texto = "await conn.execute(f'SET search_path = \"{schema}\", public')\n"
    ((_, elementos),) = _sitios(texto)
    assert elementos == ['"{schema}"', "public"]
    assert _viola_el_path(elementos, es_test=False)
    assert _viola_el_path(elementos, es_test=True)


def test_el_guardian_acusa_la_forma_sin_comillas_y_to():
    for fuente in (
        "SET search_path TO tenant_x, public",
        "await c.execute(f\"SET search_path = {schema}, public\")",
        "await c.execute(f'SET search_path TO \"{tenant_schema(slug)}\", public')",
        "SELECT set_config('search_path', 'tenant_x, public', true)",
        "server_settings={'search_path': 'tenant_x,public'}",
    ):
        sitios = _sitios(fuente)
        assert sitios and _viola_el_path(sitios[0][1], es_test=False), fuente


def test_el_guardian_admite_lo_legitimo():
    assert not _viola_el_path(_elementos("public"), es_test=False)
    assert not _viola_el_path(_elementos('"{tenant_schema(slug)}"'), es_test=False)
    # Dos tenants a propósito en un test: permitido; con `public`, no.
    assert not _viola_el_path(['"{a}"', '"{b}"'], es_test=True)
    assert _viola_el_path(['"{a}"', '"{b}"', "public"], es_test=True)
    assert not _sitios("# SET search_path = a, public\n-- SET search_path = a, public\n")


def test_el_guardian_acusa_una_tabla_global_sin_calificar():
    nombres = {"tenants", "audit_log"}
    assert _sin_calificar('"SELECT code FROM tenants WHERE slug = $1"', nombres) == [(1, "tenants")]
    assert _sin_calificar("INSERT INTO audit_log (a) VALUES ($1)", nombres) == [(1, "audit_log")]
    assert not _sin_calificar("INSERT INTO public.audit_log (a) VALUES ($1)", nombres)


def test_el_guardian_acusa_una_extension_sin_calificar():
    for fuente in (
        "VALUES ($1, $2::vector)",
        "ORDER BY k.embedding <=> $1::public.vector",
        "lower(unaccent(nombre))",
        "CREATE INDEX i ON t USING GIN (c gin_trgm_ops);",
        "ALTER COLUMN embedding TYPE vector(384);",
        "websearch_to_tsquery('spanish_unaccent', $1)",
        "USING hnsw (embedding vector_cosine_ops)",
    ):
        assert _extensiones_sin_calificar(fuente), fuente
    for fuente in (
        "VALUES ($1, $2::public.vector)",
        "ORDER BY k.embedding OPERATOR(public.<=>) $1::public.vector",
        "lower(public.unaccent(nombre))",
        "USING GIN (c public.gin_trgm_ops);",
        "websearch_to_tsquery('public.spanish_unaccent'::regconfig, $1)",
        "WHERE c.cfgname = 'spanish_unaccent' AND n.nspname = 'public'",
        "-- embedding <=> $1::vector",
    ):
        assert not _extensiones_sin_calificar(fuente), fuente


def test_el_guardian_acusa_un_create_extension_sin_esquema():
    assert not re.search(
        r"WITH\s+SCHEMA\s+public\b", _CREATE_EXTENSION.search("CREATE EXTENSION IF NOT EXISTS vector;").group(0)
    )
