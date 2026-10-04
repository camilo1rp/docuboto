# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Las reglas 1, 3 y 7 de CLAUDE.md, ejecutables (AREA-13-03).

1. **Sin ORM** (ADR-003): ni SQLAlchemy ni Alembic en ningún servicio.
3. **El repo nunca se monta en escritura en un contenedor**: todo bind mount con
   origen del árbol (`./` o `../`) lleva `:ro`. Docker escribe como root y el
   fallo es silencioso.
7. **JWT con PyJWT**, nunca `python-jose`; y los suelos de versión
   (`PyJWT[crypto]>=2.10.0`, `python-multipart>=0.0.18`, `mcp>=1.12.0`).

Hasta ahora lo bloqueaba solo `.claude/hooks/guard-bash.sh`, que vive fuera del
repo y no corre en CI: nada impedía que un `requirements.lock` regenerado
reintrodujera `python-jose`. Los tres guardianes corren en `test-unit-shared`,
es decir en cada push.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from tests._compose import COMPOSE_DEV, COMPOSE_E2E, COMPOSE_PROD, RAIZ, cargar

SERVICIOS = RAIZ / "services"

PROHIBIDOS = frozenset({"sqlalchemy", "alembic", "python-jose", "jose"})
_MODULOS_PROHIBIDOS = frozenset({"sqlalchemy", "alembic", "jose"})
SUELOS = {
    "pyjwt": Version("2.10.0"),
    "python-multipart": Version("0.0.18"),
    "mcp": Version("1.12.0"),
}


def _normaliza(nombre: str) -> str:
    return re.sub(r"[-_.]+", "-", nombre).lower()


def _requisitos(archivo: Path) -> list[Requirement]:
    reqs: list[Requirement] = []
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if not linea or linea.startswith(("-", "--")):
            continue
        reqs.append(Requirement(linea))
    return reqs


def _archivos_de_requisitos() -> list[Path]:
    return sorted(SERVICIOS.glob("*/requirements*.txt")) + sorted(SERVICIOS.glob("*/requirements*.lock"))


def test_se_leyeron_los_lockfiles() -> None:
    """Anti-vacuidad: un glob roto pasaría todo en verde sobre cero ficheros."""
    assert len(list(SERVICIOS.glob("*/requirements.lock"))) >= 11
    assert len(list(SERVICIOS.glob("*/requirements.txt"))) >= 11


def test_ningun_requisito_trae_orm_ni_python_jose() -> None:
    hallados = [
        f"{a.relative_to(RAIZ)}: {r.name}"
        for a in _archivos_de_requisitos()
        for r in _requisitos(a)
        if _normaliza(r.name) in PROHIBIDOS
    ]
    assert not hallados, (
        "ADR-003 (sin ORM/Alembic) y regla 7 (PyJWT, nunca python-jose):\n  " + "\n  ".join(hallados)
    )


def _imports(texto: str) -> set[str]:
    modulos: set[str] = set()
    for n in ast.walk(ast.parse(texto)):
        if isinstance(n, ast.Import):
            modulos |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            modulos.add(n.module.split(".")[0])
    return modulos


def test_ningun_servicio_importa_orm_ni_jose() -> None:
    fuentes = sorted(SERVICIOS.glob("*/app/**/*.py")) + sorted(SERVICIOS.glob("*/migrations/**/*.py"))
    assert len(fuentes) > 200, "el censo de fuentes se quedó corto"
    hallados = [
        f"{f.relative_to(RAIZ)}: {m}"
        for f in fuentes
        for m in sorted(_imports(f.read_text(encoding="utf-8")) & _MODULOS_PROHIBIDOS)
    ]
    assert not hallados, "import de un módulo prohibido:\n  " + "\n  ".join(hallados)


def test_los_suelos_de_version() -> None:
    """`pyjwt>=2.10.0`, `python-multipart>=0.0.18`, `mcp>=1.12.0` donde aparezcan.

    En un `.txt` el suelo es el límite inferior declarado (`>=`); en un `.lock`
    la versión fijada (`==`). Un `.txt` con un suelo menor que el de la regla
    también rompe: el suelo es PARTE del contrato.
    """
    fallos: list[str] = []
    vistos = 0
    for a in _archivos_de_requisitos():
        for r in _requisitos(a):
            suelo = SUELOS.get(_normaliza(r.name))
            if suelo is None:
                continue
            vistos += 1
            versiones = [Version(s.version) for s in r.specifier if s.operator in (">=", "==", "~=", ">")]
            if not versiones:
                fallos.append(f"{a.relative_to(RAIZ)}: {r} no declara un suelo")
            elif max(versiones) < suelo:
                fallos.append(f"{a.relative_to(RAIZ)}: {r} < {suelo}")
    assert vistos >= 6, f"solo se evaluaron {vistos} requisitos con suelo: el guardián está ciego"
    assert not fallos, "\n  ".join(fallos)


def _montajes_del_arbol(ruta: Path) -> list[tuple[str, str]]:
    """(servicio, volumen) de cada bind mount con origen `./` o `../`."""
    hallados: list[tuple[str, str]] = []
    for nombre, svc in (cargar(ruta).get("services") or {}).items():
        for v in svc.get("volumes") or []:
            if isinstance(v, str):
                if v.startswith(("./", "../")):
                    hallados.append((nombre, v))
            elif isinstance(v, dict) and v.get("type") == "bind":
                src = str(v.get("source", ""))
                if src.startswith(("./", "../")):
                    hallados.append((nombre, f"{src}:{v.get('target')}:{'ro' if v.get('read_only') else 'rw'}"))
    return hallados


def test_el_repo_se_monta_solo_de_lectura() -> None:
    sin_ro: list[str] = []
    total = 0
    for ruta in (COMPOSE_DEV, COMPOSE_E2E, COMPOSE_PROD):
        for nombre, v in _montajes_del_arbol(ruta):
            total += 1
            modo = v.split(":")[2] if v.count(":") >= 2 else ""
            if modo.split(",")[0] != "ro" and not v.endswith(":ro"):
                sin_ro.append(f"{ruta.name}::{nombre}: {v}")
    assert total >= 5, "no se encontró ningún bind mount del árbol: el guardián está ciego"
    assert not sin_ro, (
        "Un bind mount del repo sin `:ro` hace que Docker escriba como root:root en el "
        "árbol (regla 3):\n  " + "\n  ".join(sin_ro)
    )


def test_el_comparador_de_suelos_ve_una_version_baja() -> None:
    """Control positivo del propio guardián."""
    r = Requirement("pyjwt==2.9.0")
    assert max(Version(s.version) for s in r.specifier) < SUELOS["pyjwt"]
    assert _normaliza("python_jose") in {"python-jose"}
