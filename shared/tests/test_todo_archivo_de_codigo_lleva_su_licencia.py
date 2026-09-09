# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Todo archivo de código nace con su encabezado de licencia, sin excepción.

El proyecto es AGPL v3 derivado del Orfeo GPL: la autoría (Orpyca,
aurigadl@gmail.com) y la licencia tienen que constar **en cada archivo**, porque
un archivo viaja solo — se copia a otro repositorio, se pega en un informe, se
adjunta a un correo — y fuera del árbol el `LICENSE` de la raíz no lo acompaña.

**Por qué un guardián y no la disciplina.** El encabezado se añadió a 437
archivos de golpe el 2026-06-22. El 2026-09-08, al censar, faltaba en **56**: no
se abrió de nuevo por una decisión, se abrió porque nadie volvió a mirar. Entre
ellos estaban los 16 de `docs/templates/service-template/`, que es de donde se
copia un servicio nuevo: la plantilla sembraba la omisión en cada servicio que
naciera de ella.

**Qué se comprueba y qué no.** Se exige la línea de atribución, que es la que
identifica el origen; no el texto completo palabra por palabra, para que
reformular un párrafo de la licencia no ponga en rojo a todo el repositorio. Va
en la cabecera del archivo (los primeros 2000 caracteres) y no en cualquier
sitio: un archivo que lo menciona al final no lo lleva.
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

#: La línea que identifica el origen. Se busca ESTA y no el texto completo: es
#: la afirmación que no puede faltar ni reformularse.
MARCA = "Desarrollo original de Orpyca"

#: Cuánto de la cabecera se mira. El encabezado va arriba por definición.
CABECERA = 2000

#: Qué cuenta como código. El sufijo vacío es para los `Dockerfile`, que se
#: reconocen por nombre.
SUFIJOS = {".py", ".sql", ".js", ".cjs", ".mjs", ".ts", ".svelte", ".sh"}

#: Directorios que no son código de este proyecto: dependencias de terceros,
#: artefactos de build y cachés. Sin esto el censo tarda minutos y acusa a
#: `node_modules`.
DIRECTORIOS_AJENOS = {
    ".git",
    ".svelte-kit",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "data",
    "htmlcov",
    "site",
    "documentos",
}

#: Archivos exentos, con su motivo. La regla del proyecto exceptúa la
#: **configuración** (docker-compose, mkdocs, requirements, .env.example) y la
#: documentación publicable; casi toda queda fuera por extensión, y lo que no,
#: se declara aquí. Una lista de excepciones sin motivo escrito es una lista que
#: crece sola.
EXCEPCIONES: dict[str, str] = {
    "frontend/.eslintrc.cjs": (
        "Configuración de herramienta, no código del producto: la regla del "
        "proyecto exceptúa la configuración (docker-compose, mkdocs, "
        "requirements, .env.example) y este archivo es de esa clase. No se "
        "distribuye ni se ejecuta como parte del sistema."
    ),
}


def _archivos_de_codigo() -> list[Path]:
    """Derivado del árbol: un archivo nuevo entra en el censo el día que existe."""
    hallados: list[Path] = []
    for ruta in RAIZ.rglob("*"):
        if not ruta.is_file():
            continue
        if DIRECTORIOS_AJENOS.intersection(ruta.relative_to(RAIZ).parts[:-1]):
            continue
        if ruta.suffix in SUFIJOS or ruta.name.startswith("Dockerfile"):
            hallados.append(ruta)
    return hallados


def test_todo_archivo_de_codigo_declara_su_origen_y_su_licencia() -> None:
    culpables = []
    for ruta in sorted(_archivos_de_codigo()):
        rel = str(ruta.relative_to(RAIZ))
        if rel in EXCEPCIONES:
            continue
        try:
            cabecera = ruta.read_text(encoding="utf-8", errors="replace")[:CABECERA]
        except OSError:  # pragma: no cover - un archivo ilegible ya es otro problema
            continue
        if MARCA not in cabecera:
            culpables.append(rel)

    assert not culpables, (
        "Estos archivos de código no llevan el encabezado de licencia AGPL en su "
        "cabecera. El proyecto es software libre derivado del Orfeo GPL y cada "
        "archivo tiene que decirlo por sí mismo, porque viaja solo. Añádelo al "
        "principio con el prefijo de comentario que corresponda (`# ` en .py, "
        ".sh y Dockerfile; `-- ` en .sql; `// ` en .js/.ts; un bloque `<!-- -->` "
        "en .svelte), después del shebang si lo hay, o declara el archivo en "
        "`EXCEPCIONES` con su motivo:\n  " + "\n  ".join(culpables)
    )


def test_la_plantilla_de_servicio_lo_lleva() -> None:
    """La plantilla no es un archivo más: es de donde nace cada servicio nuevo.

    Sus 16 archivos estaban entre los que faltaban. Un servicio creado desde ahí
    heredaba la omisión entera, y el guardián de arriba lo habría cazado archivo
    por archivo después, en vez de en la fuente.
    """
    plantilla = RAIZ / "docs" / "templates" / "service-template"
    if not plantilla.is_dir():
        return
    archivos = [
        r
        for r in plantilla.rglob("*")
        if r.is_file() and (r.suffix in SUFIJOS or r.name.startswith("Dockerfile"))
    ]
    assert archivos, "La plantilla de servicio no tiene archivos de código: ¿se movió?"
    sin = [
        str(r.relative_to(RAIZ))
        for r in sorted(archivos)
        if MARCA not in r.read_text(encoding="utf-8", errors="replace")[:CABECERA]
    ]
    assert not sin, (
        "La plantilla de la que se copia cada servicio nuevo tiene archivos sin "
        "encabezado: cada servicio que nazca de ella heredará la omisión.\n  "
        + "\n  ".join(sin)
    )


def test_el_censo_encuentra_el_repositorio_entero() -> None:
    """Anti-vacuidad: si el censo se vacía, el guardián deja de guardar.

    Ya pasó en este repositorio con censos que dejaron de casar en silencio. Al
    escribir esto había 1.773 archivos de código; el suelo se pone muy por
    debajo para que borrar un servicio no ponga esto en rojo, pero lo bastante
    alto para que un `rglob` que dejó de recorrer sí lo haga.
    """
    assert len(_archivos_de_codigo()) >= 800, (
        "El censo de archivos de código se quedó corto: el recorrido del árbol "
        "dejó de funcionar y este guardián ya no vigila nada."
    )


def test_la_lista_de_excepciones_no_caduca() -> None:
    """Una excepción sobre un archivo que ya no existe —o que ya lo lleva— miente."""
    sobran = []
    for rel in EXCEPCIONES:
        ruta = RAIZ / rel
        if not ruta.is_file():
            sobran.append(f"{rel} (ya no existe)")
        elif MARCA in ruta.read_text(encoding="utf-8", errors="replace")[:CABECERA]:
            sobran.append(f"{rel} (ya lleva el encabezado)")
    assert not sobran, (
        "Estas excepciones ya no hacen falta: quítalas para que la lista siga "
        "contando la deuda real:\n  " + "\n  ".join(sobran)
    )
