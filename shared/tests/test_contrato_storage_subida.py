# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que document-service lee de una subida tiene que existir en storage.

Defecto real, y doble: el importador (E11 INT-05) y la ventanilla pública leían
`result["file_id"]` de la respuesta de storage. Esa clave **nunca existió**: el
contrato es `FileResponse`, cuyo campo es `id`. Importar un paquete con anexos
binarios reventaba con `KeyError`.

**Los dos tests estaban en verde** porque sus mocks devolvían `{"file_id": …}`:
comprobaban que el código coincide con la creencia de quien lo escribió, no con
el servicio del otro lado. Un mock del colaborador es una afirmación sobre el
colaborador, y hay que contrastarla con él.

Este censo cruza servicios —por eso vive en `shared/`, que es lo que corre en
CI— y lee el esquema REAL de storage en vez de repetir sus nombres aquí: una
copia a mano volvería a poder mentir.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ESQUEMA_STORAGE = RAIZ / "services" / "storage-service" / "app" / "schemas" / "storage.py"
CLIENTE = RAIZ / "services" / "document-service" / "app" / "clients" / "storage_client.py"


def _campos_de(ruta: Path, clase: str) -> set[str]:
    """Nombres de campo declarados en una clase Pydantic/dataclass."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ClassDef) and nodo.name == clase:
            return {
                n.target.id
                for n in nodo.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }
    raise AssertionError(f"no se halló la clase {clase} en {ruta}")


def _claves_leidas_del_cuerpo(ruta: Path) -> set[str]:
    """Claves literales que el cliente saca del JSON de storage.

    Busca `cuerpo["x"]` y `cuerpo.get("x")` dentro del traductor, que es el
    único sitio que debe conocer el formato de cable.

    Solo **lecturas**: un `cuerpo["x"] = …` es una clave que el cliente ESCRIBE
    en el cuerpo de una petición, no una que lea de la respuesta, y compararla
    con `FileResponse` es comparar dos contratos distintos. El censo daba un
    falso positivo sobre el cuerpo de la petición de render, que no viaja hacia
    `FileResponse` ni de lejos. Un guardián que acusa donde no hay defecto se
    acaba silenciando, y entonces deja de avisar donde sí lo hay.
    """
    fuente = ruta.read_text(encoding="utf-8")
    arbol = ast.parse(fuente, filename=str(ruta))
    claves: set[str] = set()
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Subscript)
            and isinstance(nodo.value, ast.Name)
            and nodo.value.id == "cuerpo"
            and isinstance(nodo.slice, ast.Constant)
            and isinstance(nodo.slice.value, str)
            and isinstance(nodo.ctx, ast.Load)
        ):
            claves.add(nodo.slice.value)
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "get"
            and isinstance(nodo.func.value, ast.Name)
            and nodo.func.value.id == "cuerpo"
            and nodo.args
            and isinstance(nodo.args[0], ast.Constant)
        ):
            claves.add(nodo.args[0].value)
    return claves


def test_el_censo_ve_algo() -> None:
    """Anti-vacuidad: si los extractores se rompen, cae aquí y no en el de abajo."""
    assert len(_campos_de(ESQUEMA_STORAGE, "FileResponse")) >= 5
    assert len(_claves_leidas_del_cuerpo(CLIENTE)) >= 2


def test_toda_clave_que_lee_el_cliente_existe_en_el_contrato_de_storage() -> None:
    contrato = _campos_de(ESQUEMA_STORAGE, "FileResponse")
    leidas = _claves_leidas_del_cuerpo(CLIENTE)
    inventadas = sorted(leidas - contrato)
    assert not inventadas, (
        "el cliente de storage lee claves que `FileResponse` no expone: "
        f"{inventadas}. Contrato real: {sorted(contrato)}. "
        "Un mock que devuelva la clave inventada deja el camino roto en verde."
    )


def test_el_id_del_fichero_se_traduce_y_no_se_adivina() -> None:
    """El nombre del campo del contrato NO se repite a mano en los llamantes.

    Después del defecto, ningún consumidor debe volver a indexar el JSON de
    storage: el cliente devuelve `ArchivoSubido` y los llamantes usan atributos.
    """
    documentos = RAIZ / "services" / "document-service" / "app"
    culpables = []
    for ruta in sorted(documentos.rglob("*.py")):
        if ruta == CLIENTE:
            continue  # el traductor SÍ puede, es su trabajo
        fuente = ruta.read_text(encoding="utf-8")
        for nodo in ast.walk(ast.parse(fuente, filename=str(ruta))):
            if (
                isinstance(nodo, ast.Subscript)
                and isinstance(nodo.slice, ast.Constant)
                and nodo.slice.value == "file_id"
                and isinstance(nodo.value, ast.Name)
                and nodo.value.id in {"result", "registro", "respuesta"}
            ):
                culpables.append(f"{ruta.relative_to(RAIZ)}:{nodo.lineno}")
    assert not culpables, (
        f"vuelve a adivinarse la clave del fichero en la respuesta de storage: {culpables}"
    )


# --- Contrato del render de documentos (E23, RF-PRO-03) ----------------------
#
# Mismo cruce, otro endpoint: `render_documento` lee la respuesta de
# `POST /render/documento-internal`, cuyo contrato es `RenderDocumentoResponse` en
# storage-service. La respuesta se lee de `datos`, no de `cuerpo`, así que el
# censo de arriba no la miraba: un campo renombrado en storage habría vuelto a
# romperse en tiempo de ejecución con los tests en verde, que es exactamente el
# defecto que este archivo existe para impedir.

ESQUEMA_RENDER = RAIZ / "services" / "storage-service" / "app" / "schemas" / "render.py"


def _claves_leidas_de_datos(ruta: Path) -> set[str]:
    """Claves que el cliente lee de `datos` (la respuesta ya deserializada)."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    claves: set[str] = set()
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Subscript)
            and isinstance(nodo.value, ast.Name)
            and nodo.value.id == "datos"
            and isinstance(nodo.slice, ast.Constant)
            and isinstance(nodo.slice.value, str)
            and isinstance(nodo.ctx, ast.Load)
        ):
            claves.add(nodo.slice.value)
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "get"
            and isinstance(nodo.func.value, ast.Name)
            and nodo.func.value.id == "datos"
            and nodo.args
            and isinstance(nodo.args[0], ast.Constant)
        ):
            claves.add(nodo.args[0].value)
    return claves


def test_el_censo_del_render_ve_algo() -> None:
    assert len(_campos_de(ESQUEMA_RENDER, "RenderDocumentoResponse")) >= 3
    assert len(_claves_leidas_de_datos(CLIENTE)) >= 3


def test_toda_clave_del_render_existe_en_el_contrato() -> None:
    contrato = _campos_de(ESQUEMA_RENDER, "RenderDocumentoResponse")
    inventadas = sorted(_claves_leidas_de_datos(CLIENTE) - contrato)
    assert not inventadas, (
        "el cliente lee del render claves que `RenderDocumentoResponse` no expone: "
        f"{inventadas}. Contrato real: {sorted(contrato)}."
    )
