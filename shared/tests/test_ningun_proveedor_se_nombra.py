# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ningún proveedor de inteligencia se nombra en el código (ADR-045 §10).

La lista prohibida son huellas SHA-256 de los nombres ya normalizados. Este
archivo no los escribe: un fallo muestra la huella y la ruta, no el nombre.
Quedan fuera `docs/` (ahí puede contarse la historia) y los nombres de los
ajustes de modelos que ADR-006 ya admite, que no están en esta lista.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

#: Huellas de nombres normalizados. Añadir una es añadir una huella, no el nombre.
PROHIBIDOS = frozenset(
    {
        "5bd6bf8ad9c4297b72c5cb379e828640a74fa3ae4d1f1e8d63b6b40384948d7f",
    }
)

_RAICES = (
    RAIZ / "services",
    RAIZ / "frontend" / "src",
    RAIZ / "shared",
    RAIZ / "scripts",
    RAIZ / "infra",
)
_SUELTOS = (RAIZ / "docker-compose.yml",)
_SUFIJOS = {
    ".py", ".js", ".mjs", ".cjs", ".svelte", ".sql", ".yml", ".yaml", ".sh", ".json", ".toml",
}
_AJENOS = {"node_modules", "__pycache__", ".venv", ".git", "dist", "build", ".svelte-kit"}
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def _huella(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _tokens(texto: str) -> set[str]:
    bajo = texto.lower()
    hallados: set[str] = set()
    for crudo in _TOKEN.findall(bajo):
        hallados.add(crudo)
        if "-" in crudo or "_" in crudo:
            hallados.add(re.sub(r"[-_]", "", crudo))
            hallados.update(re.findall(r"[a-z0-9]+", crudo))
    return hallados


def _coincidencias(texto: str, huellas: frozenset[str]) -> list[str]:
    return sorted(h for token in _tokens(texto) if (h := _huella(token)) in huellas)


def _archivos() -> list[Path]:
    hallados: list[Path] = []
    for raiz in _RAICES:
        for ruta in raiz.rglob("*"):
            if not ruta.is_file() or ruta.suffix not in _SUFIJOS:
                continue
            if _AJENOS.intersection(ruta.relative_to(RAIZ).parts):
                continue
            hallados.append(ruta)
    hallados.extend(ruta for ruta in _SUELTOS if ruta.is_file())
    return hallados


def test_el_arbol_no_nombra_un_proveedor() -> None:
    archivos = _archivos()
    assert len(archivos) >= 200, "el censo se vació"
    culpables = [
        f"{ruta.relative_to(RAIZ)}: {huella}"
        for ruta in archivos
        for huella in _coincidencias(ruta.read_text(encoding="utf-8", errors="replace"), PROHIBIDOS)
    ]
    assert culpables == [], "huella prohibida en:\n  " + "\n  ".join(culpables)


def test_el_control_positivo_acusa_la_huella() -> None:
    huella = _huella("zzprovider")
    assert _coincidencias("el modulo habla con zzprovider.", frozenset({huella})) == [huella]
    assert _coincidencias("zz-provider", frozenset({huella})) == [huella]
    assert _coincidencias("publico", frozenset({huella})) == []
