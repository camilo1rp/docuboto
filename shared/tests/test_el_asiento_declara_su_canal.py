# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-03-07: todo asiento DECLARA su canal; la librería no lo supone.

`audit.append` tenía `canal=None` y caía a `'api'`: ~190 asientos —entre ellos los de
purga, WORM y reconciliación— decían `'api'` aunque nadie hubiera hecho una petición, y
la traza legal mentía sobre el origen. Ahora el parámetro es obligatorio: el que escribe
el asiento elige `CANAL_PETICION` (hereda el `X-Canal` del gateway) o un canal concreto
(`'system'` en un job, consumidor o herramienta de línea de comandos). Los procesos de
fondo fijan `'system'` en su tarea (`request_log.canal_de_fondo`).
"""

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from orpycamcp_common import audit, request_log

_RAIZ = Path(__file__).resolve().parents[2]
_SERVICIOS = _RAIZ / "services"

_necesita_arbol = pytest.mark.skipif(
    not _SERVICIOS.is_dir(), reason="el árbol de servicios no está montado"
)


def _llamadas_a_append() -> list[tuple[str, int, ast.Call]]:
    """(archivo, línea, llamada) de cada `audit.append(...)` / `_audit.append(...)` /
    `audit_core.append(...)` (el envoltorio de tenant-service) en `services/*/app/**`."""
    hallazgos: list[tuple[str, int, ast.Call]] = []
    for archivo in sorted(_SERVICIOS.glob("*/app/**/*.py")):
        texto = archivo.read_text(encoding="utf-8")
        if ".append(" not in texto:
            continue
        for nodo in ast.walk(ast.parse(texto, filename=str(archivo))):
            if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)):
                continue
            if nodo.func.attr != "append":
                continue
            base = ast.unparse(nodo.func.value)
            if base.endswith("audit") or base == "audit_core":
                hallazgos.append((str(archivo.relative_to(_RAIZ)), nodo.lineno, nodo))
    return hallazgos


@_necesita_arbol
def test_el_censo_no_es_vacio() -> None:
    """Anti-vacuidad: sin esto, un glob que no encuentra nada también pasaría."""
    assert len(_llamadas_a_append()) >= 150


@_necesita_arbol
def test_toda_llamada_a_append_declara_su_canal() -> None:
    sin_canal = [
        f"{f}:{n}"
        for f, n, llamada in _llamadas_a_append()
        if not any(k.arg == "canal" for k in llamada.keywords)
    ]
    assert not sin_canal, (
        "Estos asientos no declaran `canal=`; antes caían a 'api' aunque vinieran de un "
        f"job o de la UI: {sin_canal}. Usa `audit.CANAL_PETICION` (acto de una petición) "
        "o `\"system\"` (job, consumidor, CLI)."
    )


@_necesita_arbol
def test_ningun_canal_es_none() -> None:
    nulos = [
        f"{f}:{n}"
        for f, n, llamada in _llamadas_a_append()
        for k in llamada.keywords
        if k.arg == "canal" and isinstance(k.value, ast.Constant) and k.value.value is None
    ]
    assert not nulos, f"`canal=None` es el valor por omisión disfrazado: {nulos}"


@_necesita_arbol
def test_los_trabajos_conocidos_asientan_system() -> None:
    """Los trabajos que la auditoría señaló (purga de disposición y renovaciones del
    cableado WORM) no pueden volver a decir 'api'."""
    esperados = {
        "services/document-service/app/services/purga_service.py": "system",
        "services/storage-service/app/services/storage_service.py": "system",
    }
    vistos: dict[str, set[str]] = {k: set() for k in esperados}
    for f, _, llamada in _llamadas_a_append():
        if f in esperados:
            for k in llamada.keywords:
                if k.arg == "canal" and isinstance(k.value, ast.Constant):
                    vistos[f].add(str(k.value.value))
    for f, canal in esperados.items():
        assert canal in vistos[f], f"{f} ya no asienta '{canal}' en sus trabajos"


# --------------------------------------------------------------------------- comportamiento


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.execute.return_value = None
    conn.transaction = MagicMock(return_value=_Tx())
    return conn


async def _asentar(canal: str | None = None, *, omitir: bool = False) -> str:
    conn = _conn()
    kwargs: dict[str, object] = {}
    if not omitir:
        kwargs["canal"] = canal
    await audit.append(
        conn,
        tenant_slug="icetex",
        service="t",
        action="x.y",
        object_type="x",
        **kwargs,  # type: ignore[arg-type]
    )
    # El canal es el 5.º campo de la fila insertada (ver `append`): se lee del INSERT.
    insert = conn.execute.await_args_list[-1]
    return next(a for a in insert.args if a in audit.VALID_CANALES)


@pytest.mark.asyncio
async def test_append_sin_canal_es_un_error_de_llamada() -> None:
    with pytest.raises(TypeError):
        await _asentar(omitir=True)


@pytest.mark.asyncio
async def test_peticion_sin_marca_del_gateway_asienta_api() -> None:
    assert await _asentar(audit.CANAL_PETICION) == "api"


@pytest.mark.asyncio
async def test_peticion_hereda_el_canal_del_gateway() -> None:
    token = request_log._canal.set("mcp")
    try:
        assert await _asentar(audit.CANAL_PETICION) == "mcp"
    finally:
        request_log._canal.reset(token)


@pytest.mark.asyncio
async def test_un_canal_explicito_pisa_al_de_la_peticion() -> None:
    token = request_log._canal.set("ui")
    try:
        assert await _asentar("system") == "system"
    finally:
        request_log._canal.reset(token)


@pytest.mark.asyncio
async def test_un_proceso_de_fondo_asienta_system_aunque_declare_peticion() -> None:
    async def _tarea() -> str:
        request_log.canal_de_fondo()
        return await _asentar(audit.CANAL_PETICION)

    assert await asyncio.create_task(_tarea()) == "system"
    # y la marca de la tarea NO se filtra al contexto que la lanzó
    assert request_log.canal_actual() == ""


@pytest.mark.asyncio
async def test_canal_peticion_no_es_un_canal_valido_para_guardar() -> None:
    assert audit.CANAL_PETICION not in audit.VALID_CANALES


# --------------------------------------------------------------------------- bucles de fondo


@_necesita_arbol
def test_todo_bucle_de_fondo_fija_el_canal_system() -> None:
    """Un `while True` + `asyncio.sleep` lanzado por `create_task` NO pasa por
    `events._supervisar`: si no llama a `canal_de_fondo()` hereda el contexto del
    lifespan (vacío -> 'api') y sus asientos mienten sobre el origen (el hueco que el
    censo de `canal=` no ve, porque esas llamadas declaran `CANAL_PETICION`)."""
    sin_marca = []
    vistos = 0
    for archivo in sorted(_SERVICIOS.glob("*/app/**/*.py")):
        texto = archivo.read_text(encoding="utf-8")
        if "while True" not in texto:
            continue
        for fn in ast.walk(ast.parse(texto)):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            tiene_bucle = any(
                isinstance(n, ast.While) and isinstance(n.test, ast.Constant) and n.test.value is True
                for n in ast.walk(fn)
            )
            duerme = any(
                isinstance(n, ast.Call) and ast.unparse(n.func) == "asyncio.sleep" for n in ast.walk(fn)
            )
            if not (tiene_bucle and duerme):
                continue
            if "_loop" not in fn.name and not fn.name.startswith("bucle_"):
                continue
            vistos += 1
            llama = any(
                isinstance(n, ast.Call) and ast.unparse(n.func).endswith("canal_de_fondo")
                for n in ast.walk(fn)
            )
            if not llama:
                sin_marca.append(f"{archivo.relative_to(_RAIZ)}::{fn.name}")
    assert vistos >= 5, vistos  # anti-vacuidad: archive(3)+storage+document(2)
    assert not sin_marca, f"bucles de fondo sin canal_de_fondo(): {sin_marca}"
