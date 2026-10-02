# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
`REFERENCIAS_DE_ARCHIVO` (storage-service) cubre TODA columna que guarda un `files.id`.

La purga de binarios huérfanos solo borra un objeto si ninguna columna del tenant lo
referencia. Esa lista de columnas se escribió a mano, y una columna nueva con un
`*file_id*` que nadie añada a la lista es una referencia que la purga NUNCA ve: borraría
un objeto que sostiene un documento. Por eso el conjunto se DERIVA de las migraciones de
tenant de todos los servicios (no se mantiene en paralelo) y se compara con la lista.

Una columna que guarda un file_id pero NO debe contar (hoy, la cola de purga, que apunta
al objeto precisamente para borrarlo) se declara en `NO_REFERENCIAN` con su motivo.
"""

from __future__ import annotations

import ast
import os
import re

import pytest

# (tabla, columna) -> por qué una columna `*file_id*` UUID no cuenta como referencia.
NO_REFERENCIAN: dict[tuple[str, str], str] = {
    ("import_binario_huerfano", "file_id"): (
        "cola de purga: apunta al objeto para BORRARLO, no para conservarlo"
    ),
}

_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\w+\.)?(\w+)\s*\((.*?)\n\s*\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_ALTER = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:\w+\.)?(\w+)\s+(.*?);", re.IGNORECASE | re.DOTALL
)
_COL = re.compile(r"(?:ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?)?\b(\w*file_id\w*)\s+UUID\b", re.IGNORECASE)


def _sin_comentarios(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def columnas_file_id(sql: str) -> set[tuple[str, str]]:
    """(tabla, columna) de cada columna `*file_id*` de tipo UUID que declara el SQL."""
    sql = _sin_comentarios(sql)
    hallados: set[tuple[str, str]] = set()
    for tabla, cuerpo in _CREATE.findall(sql):
        for linea in cuerpo.split("\n"):
            m = _COL.search(linea)
            if m and not linea.strip().upper().startswith(("CONSTRAINT", "CHECK", "UNIQUE")):
                hallados.add((tabla.lower(), m.group(1).lower()))
    for tabla, cuerpo in _ALTER.findall(sql):
        for m in re.finditer(
            r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w*file_id\w*)\s+UUID\b", cuerpo, re.IGNORECASE
        ):
            hallados.add((tabla.lower(), m.group(1).lower()))
    return hallados


def _raiz_repo() -> str | None:
    aqui = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(aqui, "services")):
            return aqui
        aqui = os.path.dirname(aqui)
    return None


def _derivadas(raiz: str) -> set[tuple[str, str]]:
    todas: set[tuple[str, str]] = set()
    servicios = os.path.join(raiz, "services")
    for svc in sorted(os.listdir(servicios)):
        d = os.path.join(servicios, svc, "migrations", "tenant")
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".sql"):
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    todas |= columnas_file_id(fh.read())
    return todas


def _declaradas(raiz: str) -> set[tuple[str, str]]:
    ruta = os.path.join(
        raiz, "services", "storage-service", "app", "repositories", "file_repository.py"
    )
    with open(ruta, encoding="utf-8") as fh:
        arbol = ast.parse(fh.read())
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.AnnAssign) and getattr(nodo.target, "id", "") == "REFERENCIAS_DE_ARCHIVO":
            return {(t, c) for t, c in ast.literal_eval(nodo.value)}
    raise AssertionError("REFERENCIAS_DE_ARCHIVO no está en file_repository.py")


def test_el_parser_detecta_las_formas_de_declarar_una_columna() -> None:
    """Control positivo: sin esto, un parser roto daría un conjunto vacío y el guardián
    pasaría por vacuidad."""
    sql = """
    -- nueva_tabla.comentada_file_id UUID  (un comentario no cuenta)
    CREATE TABLE IF NOT EXISTS nueva_tabla (
        id UUID PRIMARY KEY,
        adjunto_file_id UUID NOT NULL,
        otro TEXT,
        CONSTRAINT ux UNIQUE (id, adjunto_file_id)
    );
    ALTER TABLE vieja ADD COLUMN IF NOT EXISTS pdf_file_id UUID;
    ALTER TABLE vieja ADD COLUMN nota TEXT;
    """
    assert columnas_file_id(sql) == {("nueva_tabla", "adjunto_file_id"), ("vieja", "pdf_file_id")}


def test_toda_columna_file_id_esta_en_la_lista_de_referencias() -> None:
    raiz = _raiz_repo()
    if raiz is None:
        pytest.skip("los directorios de servicios no están montados en esta imagen")
    derivadas = _derivadas(raiz)
    declaradas = _declaradas(raiz)

    # Anti-vacuidad: el censo tiene que ver al menos las referencias que SABEMOS que hay.
    for conocida in (("anexos", "file_id"), ("borrador", "documento_file_id")):
        assert conocida in derivadas, f"el censo no encuentra {conocida}: el parser no ve las migraciones"
    assert len(derivadas) >= 6

    # La excepción declarada tiene que existir (si no, es una excusa caducada).
    for par in NO_REFERENCIAN:
        assert par in derivadas, f"{par} ya no existe: quítalo de NO_REFERENCIAN"

    sin_cubrir = derivadas - declaradas - set(NO_REFERENCIAN)
    assert not sin_cubrir, (
        f"columnas con un file_id que la purga de huérfanos NO mira: {sorted(sin_cubrir)}. "
        "Añádelas a REFERENCIAS_DE_ARCHIVO (storage-service) o, si no sostienen el objeto, "
        "a NO_REFERENCIAN con el motivo."
    )
    inexistentes = declaradas - derivadas
    assert not inexistentes, (
        f"REFERENCIAS_DE_ARCHIVO nombra columnas que ninguna migración crea: {sorted(inexistentes)}"
    )
