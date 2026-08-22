# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Censo: todo `nivel_conformidad` que el código pueda escribir debe admitirlo el CHECK.

POR QUÉ EXISTE. La firma personal por-usuario (E17) estuvo rota desde que se
implementó: `personal_signing.py` fija `nivel_conformidad='xades_personal_no_acreditado'`
y ninguna migración añadió ese valor a `firma_nivel_conformidad_check`. El INSERT
final moría con CheckViolationError DESPUÉS de haber firmado, sellado con TSA y
subido el XML a MinIO, y el usuario veía «signature-service no está disponible».

Nueve tests usaban ese literal sin cazarlo, porque todos conducen el repositorio
con la base SIMULADA: un mock acepta cualquier cadena. Una restricción de
integridad no se prueba con mocks. Este censo la prueba sin necesitar Postgres:
compara las dos listas que el defecto dejó separarse.

CÓMO DECIDE CUÁL ES EL CHECK VIGENTE. No sirve "la migración de número más alto
que lo mencione": la 015 lo re-crea con la lista ANTIGUA, pero guardada tras un
`IF NOT EXISTS`, así que sobre un tenant migrado no hace nada. La que fija el
estado final es la última que lo DROPea incondicionalmente y lo vuelve a crear
(013, hoy 016) — y eso es justo lo que se busca aquí.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SERVICE_ROOT / "app"
TENANT_MIGRATIONS = SERVICE_ROOT / "migrations" / "tenant"

CAMPO = "nivel_conformidad"
CONSTRAINT = "firma_nivel_conformidad_check"

_COMENTARIO_SQL = re.compile(r"--[^\n]*")
_DROP = re.compile(rf"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+{CONSTRAINT}\b", re.I)
_ADD = re.compile(
    rf"ADD\s+CONSTRAINT\s+{CONSTRAINT}\s+CHECK\s*\((?P<cuerpo>.*?)\)\s*\)\s*;",
    re.I | re.S,
)
_LITERAL_SQL = re.compile(r"'([^']*)'")


def _literales_del_codigo() -> tuple[set[str], list[str]]:
    """Devuelve `(literales, dinamicos)` de todo `nivel_conformidad=` en `app/`.

    `dinamicos` son los puntos donde el valor es una expresión (una variable,
    una llamada) y por tanto este censo no puede juzgarlos — se listan para que
    su existencia sea visible en vez de silenciosa.
    """
    literales: set[str] = set()
    dinamicos: list[str] = []

    for archivo in sorted(APP_DIR.rglob("*.py")):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
        rel = archivo.relative_to(SERVICE_ROOT)

        # `rel` se ata por valor por defecto y no por cierre: capturarla del
        # bucle (B023) funciona hoy porque solo se llama dentro de la misma
        # iteración, pero deja una trampa para quien mueva la llamada.
        def registrar(valor: ast.expr, linea: int, rel: Path = rel) -> None:
            if isinstance(valor, ast.Constant) and isinstance(valor.value, str):
                literales.add(valor.value)
            else:
                dinamicos.append(f"{rel}:{linea}")

        for nodo in ast.walk(arbol):
            # nivel_conformidad="..." como argumento con nombre
            if isinstance(nodo, ast.Call):
                for kw in nodo.keywords:
                    if kw.arg == CAMPO:
                        registrar(kw.value, nodo.lineno)
            # {"nivel_conformidad": "..."}
            elif isinstance(nodo, ast.Dict):
                for clave, valor in zip(nodo.keys, nodo.values, strict=True):
                    if (
                        isinstance(clave, ast.Constant)
                        and clave.value == CAMPO
                        and valor is not None
                    ):
                        registrar(valor, nodo.lineno)
            # nivel_conformidad = "..."
            elif isinstance(nodo, ast.Assign):
                for destino in nodo.targets:
                    if isinstance(destino, ast.Name) and destino.id == CAMPO:
                        registrar(nodo.value, nodo.lineno)

    return literales, dinamicos


def _admitidos_por_el_sql() -> tuple[set[str], str]:
    """Lista del último `DROP`+`ADD` incondicional del CHECK, y el archivo que lo fija."""
    vigente: set[str] | None = None
    origen = ""

    for sql in sorted(TENANT_MIGRATIONS.glob("*.sql")):
        texto = _COMENTARIO_SQL.sub("", sql.read_text(encoding="utf-8"))
        if not _DROP.search(texto):
            continue  # guardado por IF NOT EXISTS: repone, no define
        m = _ADD.search(texto)
        if m is None:
            continue
        vigente = set(_LITERAL_SQL.findall(m.group("cuerpo")))
        origen = sql.name

    assert vigente, (
        "Ninguna migración de tenant DROPea y re-crea "
        f"{CONSTRAINT}: el censo no tendría contra qué comparar."
    )
    return vigente, origen


def test_todo_nivel_conformidad_escrito_esta_admitido_por_el_check() -> None:
    literales, dinamicos = _literales_del_codigo()
    admitidos, origen = _admitidos_por_el_sql()

    # Anti-vacuidad: si la extracción se rompe (un refactor renombra el campo,
    # un cambio de AST), el censo pasaría comparando el conjunto vacío contra
    # todo. Un test que no puede fallar no es una protección.
    assert literales, (
        f"El censo no encontró NINGÚN literal de {CAMPO} en app/ — la extracción "
        "está rota, no es que el código haya dejado de escribir el campo."
    )

    huerfanos = sorted(literales - admitidos)
    assert not huerfanos, (
        f"El código escribe {CAMPO} con valores que {CONSTRAINT} NO admite: "
        f"{huerfanos}. El INSERT morirá con CheckViolationError DESPUÉS de firmar, "
        f"sellar y subir el XML. Añádelos en una migración nueva que DROPee y "
        f"re-cree el CHECK (la vigente la fija {origen}), no en un backfill guardado. "
        f"Puntos con valor dinámico, fuera del alcance de este censo: {dinamicos or 'ninguno'}."
    )
