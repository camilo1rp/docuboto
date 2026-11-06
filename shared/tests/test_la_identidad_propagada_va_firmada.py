# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El llamante sale del JWT. Nadie propaga `X-User-Id` entre módulos de dominio.

Antes el gateway firmaba esas cabeceras y los clientes internos las volvían a
firmar. En un solo proceso eso es un salto de más: `UsuarioActualMiddleware`
valida el Bearer y deja un `Usuario` en `request.state`. Un `X-User-Id` de
cliente no cuenta. Este censo falla si un módulo de dominio vuelve a emitir esa
cabecera o a llamar `identidad.firmar` para una persona.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests._compose import DIR_PAQUETE, RAIZ

_EMITE = re.compile(
    r'(["\']X-User-Id["\']\s*:)|(\[\s*["\']X-User-Id["\']\s*\]\s*=)'
)
_FIRMA_PERSONA = re.compile(r"\bidentidad\.firmar\s*\(")
_DOMINIO = tuple(
    nombre for nombre, paquete in DIR_PAQUETE.items() if paquete != "app"
)


def _fuentes_dominio() -> list[Path]:
    archivos: list[Path] = []
    for nombre in _DOMINIO:
        raiz = RAIZ / "services" / nombre / DIR_PAQUETE[nombre]
        archivos.extend(
            p
            for p in sorted(raiz.rglob("*.py"))
            if "tests" not in p.parts
        )
    return archivos


def _sin_comentarios(texto: str) -> str:
    return "\n".join(
        linea for linea in texto.splitlines() if not linea.lstrip().startswith("#")
    )


def test_el_censo_ve_los_modulos_de_dominio() -> None:
    archivos = _fuentes_dominio()
    assert len(archivos) >= 80, f"censo ciego: solo {len(archivos)} archivos"
    nombres = {p.parts[-3] for p in archivos}
    for servicio in _DOMINIO:
        assert servicio in nombres or (RAIZ / "services" / servicio).is_dir()


def test_el_dominio_no_emite_x_user_id() -> None:
    mal: list[str] = []
    for archivo in _fuentes_dominio():
        texto = _sin_comentarios(archivo.read_text(encoding="utf-8"))
        if not _EMITE.search(texto):
            continue
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            codigo = ast.get_source_segment(archivo.read_text(encoding="utf-8"), nodo) or ""
            cuerpo = "\n".join(
                linea for linea in codigo.splitlines() if not linea.lstrip().startswith("#")
            )
            if _EMITE.search(cuerpo):
                mal.append(f"{archivo.relative_to(RAIZ)}::{nodo.name}")
    assert not mal, (
        "Estas funciones meten `X-User-Id` en cabeceras salientes. El llamante "
        "sale del JWT (`cabeceras_internas` reenvía Authorization). "
        + ", ".join(mal)
    )


def test_el_dominio_no_firma_identidad_hmac() -> None:
    mal: list[str] = []
    for archivo in _fuentes_dominio():
        for n, linea in enumerate(
            _sin_comentarios(archivo.read_text(encoding="utf-8")).splitlines(), 1
        ):
            if _FIRMA_PERSONA.search(linea):
                mal.append(f"{archivo.relative_to(RAIZ)}:{n}")
    assert not mal, (
        "El dominio no firma identidad HMAC: el Bearer es la credencial. "
        + ", ".join(mal)
    )


def test_cada_proceso_monta_usuario_actual() -> None:
    faltan: list[str] = []
    for nombre in (*_DOMINIO, "docuboto"):
        if nombre == "docuboto":
            main = RAIZ / "services" / "docuboto" / "docuboto" / "main.py"
        else:
            main = RAIZ / "services" / nombre / DIR_PAQUETE[nombre] / "main.py"
        texto = main.read_text(encoding="utf-8")
        if "UsuarioActualMiddleware" not in texto:
            faltan.append(str(main.relative_to(RAIZ)))
        if "IdentidadFirmadaMiddleware" in texto:
            faltan.append(f"{main.relative_to(RAIZ)} aún monta IdentidadFirmadaMiddleware")
    assert not faltan, faltan
