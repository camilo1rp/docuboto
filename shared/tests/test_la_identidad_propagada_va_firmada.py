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
#: La llamada que pone la firma. Se acepta cualquier forma de importarla.
_FIRMA = re.compile(r"\bfirmar\s*\(")


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
    assert len(_funciones_que_emiten()) >= 5


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
