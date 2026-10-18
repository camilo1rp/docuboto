# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Quien PROPAGA una identidad de persona a otro servicio, la firma.

`test_la_identidad_viene_firmada.py` ya prueba que `firmar` y `verificar` se
entienden. Prueba la PIEZA, y por eso no vio el defecto: el middleware que
verifica se dispara por la sola presencia de `X-User-Id`, sin mirar si la
petición entra por el gateway o la hace otro servicio de la red — y siete
clientes internos mandaban esa cabecera en texto plano.

Su propio docstring declaraba la premisa que lo dejó pasar: «las llamadas
servicio-a-servicio no traen identidad de persona y tienen su propio gate». Es
falsa, y falsa a propósito en el peor sitio: el sellado del índice electrónico
EXIGE la identidad de quien cierra el expediente (RF-FIR-11/12/14) — se atribuye
a una persona, no al sistema. Con la firma activada, `POST /sign-xml` devolvía
401 y **todo expediente que se cerrara quedaba con su índice sin sellar**, y por
tanto intransferible (Ac. AGN 001/2024 art. 4.3.2.4). Novena aparición del
comentario falso como vector: [[comentario-falso-como-vector]].

El censo se DERIVA del árbol y va por FUNCIÓN, no por archivo: un cliente que
firma en un sitio y no en el de al lado es [[arreglar-un-gemelo-y-dejar-el-otro]],
y aquí había tres emisiones en un mismo fichero.

Solo se miran las EMISIONES —meter `X-User-Id` en un mapa de cabeceras que sale
por HTTP—, nunca las lecturas (`request.headers.get(...)`, `Header(alias=...)`),
que son el lado receptor y no tienen nada que firmar.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

#: `X-User-Id` puesto DENTRO de un literal de diccionario (`{"X-User-Id": x}`)
#: o asignado a una clave (`headers["X-User-Id"] = x`). Las dos formas que usan
#: hoy los clientes internos.
_EMITE = re.compile(
    r'(["\']X-User-Id["\']\s*:)|(\[\s*["\']X-User-Id["\']\s*\]\s*=)'
)
#: La llamada que pone la firma. Se acepta cualquier forma de importarla, pero
#: tiene que atarla al método y a la ruta (ADR-036): `firmar(..., metodo=, ruta=)`.
_FIRMA = re.compile(r"\bfirmar\s*\((?=[^#]*?\bmetodo\s*=)(?=[^#]*?\bruta\s*=)", re.DOTALL)
_VERBOS = {"get", "post", "put", "patch", "delete", "head"}


def _funciones_que_emiten() -> list[tuple[Path, str, str]]:
    """(archivo, función, código) de cada función que mete `X-User-Id` en unas
    cabeceras salientes."""
    hallazgos: list[tuple[Path, str, str]] = []
    for archivo in sorted(SERVICIOS.glob("*/app/**/*.py")):
        texto = archivo.read_text(encoding="utf-8")
        if "X-User-Id" not in texto:
            continue
        arbol = ast.parse(texto, filename=str(archivo))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            codigo = ast.get_source_segment(texto, nodo) or ""
            # Sin los comentarios: una emisión citada en una explicación no es
            # una emisión — es su lápida (mismo criterio que el guardián del
            # historial de acceso).
            sin_comentarios = "\n".join(
                linea for linea in codigo.splitlines() if not linea.lstrip().startswith("#")
            )
            if _EMITE.search(sin_comentarios):
                hallazgos.append((archivo, nodo.name, sin_comentarios))
    return hallazgos


def test_el_censo_encuentra_emisores():
    """Anti-vacuidad: si el censo sale vacío, los demás pasan sin mirar nada."""
    assert len(_funciones_que_emiten()) >= 9


def test_toda_identidad_propagada_se_firma():
    sin_firmar = [
        f"{archivo.relative_to(RAIZ)}::{funcion}"
        for archivo, funcion, codigo in _funciones_que_emiten()
        if not _FIRMA.search(codigo)
    ]
    assert not sin_firmar, (
        "Estas funciones mandan `X-User-Id` a otro servicio sin firmarlo. El "
        "middleware `IdentidadFirmadaMiddleware` lo rechaza con 401 en el "
        "destino, y el fallo aparece lejos de aquí: "
        + ", ".join(sin_firmar)
    )


def test_el_gateway_tambien_esta_en_el_censo():
    """El gateway es el emisor original y entra por la misma puerta: si un día
    dejara de firmar, este guardián tiene que verlo igual que a los demás."""
    emisores = {archivo.name for archivo, _, _ in _funciones_que_emiten()}
    assert "auth.py" in emisores


# ---------------------------------------------------------------------------
# La ruta FIRMADA es la ruta LLAMADA (ADR-036)
# ---------------------------------------------------------------------------
#
# Firmar con la ruta de una operación y llamar a otra no falla en ningún test
# unitario: la firma "existe". Falla en producción, con un 401 que nadie
# relaciona con una constante copiada a medias. Por eso se mira por AST que, en
# cada emisor, el argumento `ruta=` de `firmar` es el MISMO nombre o literal que
# el primer argumento de la llamada HTTP, y que `metodo=` es el verbo de esa llamada.


def _llamada_firmar(nodo: ast.AST) -> ast.Call | None:
    for n in ast.walk(nodo):
        if isinstance(n, ast.Call):
            f = n.func
            nombre = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nombre == "firmar":
                return n
    return None


def _kw(llamada: ast.Call, nombre: str) -> ast.expr | None:
    return next((k.value for k in llamada.keywords if k.arg == nombre), None)


def _llamadas_http(nodo: ast.AST) -> list[tuple[str, ast.expr]]:
    """(verbo, expresión de la ruta) de cada `<cliente>.<verbo>(ruta, ...)`."""
    hallazgos: list[tuple[str, ast.expr]] = []
    for n in ast.walk(nodo):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        verbo = n.func.attr
        # Una llamada HTTP lleva siempre `headers=` (la identidad va ahí): así
        # `data.get("k")` de un dict no se confunde con un `client.get(ruta)`.
        if not any(k.arg == "headers" for k in n.keywords):
            continue
        if verbo in _VERBOS and n.args:
            hallazgos.append((verbo.upper(), n.args[0]))
        elif verbo in ("request", "stream") and len(n.args) >= 2:
            if isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                hallazgos.append((n.args[0].value.upper(), n.args[1]))
    return hallazgos


def _incoherencias() -> list[str]:
    problemas: list[str] = []
    for archivo, funcion, _ in _funciones_que_emiten():
        texto = archivo.read_text(encoding="utf-8")
        nodo = next(
            n
            for n in ast.walk(ast.parse(texto))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == funcion
        )
        donde = f"{archivo.relative_to(RAIZ)}::{funcion}"
        firma = _llamada_firmar(nodo)
        metodo, ruta = (_kw(firma, "metodo"), _kw(firma, "ruta")) if firma else (None, None)
        if metodo is None or ruta is None:
            problemas.append(f"{donde}: `firmar` sin metodo= y ruta=")
            continue
        if archivo.name == "auth.py" and "api-gateway" in archivo.parts:
            # El gateway firma la petición que RECIBE y el proxy reenvía esa misma.
            if ast.unparse(metodo) != "request.method" or ast.unparse(ruta) != "request.scope['path']":
                problemas.append(f"{donde}: el gateway debe firmar request.method y request.scope['path']")
            continue
        llamadas = _llamadas_http(nodo)
        if not llamadas:
            problemas.append(f"{donde}: no hay llamada HTTP de la que comparar la ruta")
        for verbo, ruta_llamada in llamadas:
            if ast.dump(ruta_llamada) != ast.dump(ruta):
                problemas.append(
                    f"{donde}: firma {ast.unparse(ruta)} y llama {ast.unparse(ruta_llamada)}"
                )
            if not (isinstance(metodo, ast.Constant) and str(metodo.value).upper() == verbo):
                problemas.append(f"{donde}: firma metodo={ast.unparse(metodo)} y llama {verbo}")
    return problemas


def test_la_ruta_firmada_es_la_ruta_llamada():
    problemas = _incoherencias()
    assert not problemas, (
        "La identidad se ata al método y a la ruta de la petición (ADR-036): firmar "
        "una operación y llamar a otra da 401 en el destino. " + " | ".join(problemas)
    )


def test_el_guardian_ve_una_ruta_firmada_distinta_de_la_llamada():
    """Control positivo: sobre un emisor falso, el guardián SÍ acusa la divergencia."""
    fuente = (
        "async def f(client, actor):\n"
        "    h = identidad.firmar({'X-User-Id': actor}, metodo='POST', ruta=_A)\n"
        "    await client.post(_B, headers=h)\n"
    )
    nodo = ast.parse(fuente).body[0]
    firma = _llamada_firmar(nodo)
    (verbo, llamada), = _llamadas_http(nodo)
    assert ast.dump(_kw(firma, "ruta")) != ast.dump(llamada)
    assert verbo == "POST"
