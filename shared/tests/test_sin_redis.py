# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Redis no vuelve (ADR-049 §3).

Los eventos, las colas, los cerrojos y el antirreplay viven en PostgreSQL. Un
segundo almacén es un contenedor más que respaldar, versionar y vigilar, y su
contenido no entra en el `pg_dump`. Este guardián falla si el nombre reaparece
como palabra completa en el código, los manifiestos, la CI o las dependencias.

Por palabra completa y no por subcadena: «rediseño» no es Redis. Quedan fuera
`docs/` (ahí se cuenta la historia) y los archivos que DEBEN nombrarlo: la lista
de hosts internos a los que un correo o un webhook no pueden apuntar.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
_NOMBRE = "re" + "dis"  # sin escribirlo entero, para que este archivo no se acuse a sí mismo

_RAICES = (
    RAIZ / "services",
    RAIZ / "frontend" / "src",
    RAIZ / "shared",
    RAIZ / "scripts",
    RAIZ / "infra",
    RAIZ / ".github",
)
_SUELTOS = (
    RAIZ / "docker-compose.yml",
    RAIZ / "docker-compose.intelligence.yml",
)
_SUFIJOS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".svelte", ".sql", ".yml", ".yaml", ".sh",
    ".json", ".toml", ".txt", ".lock", ".template", ".example", ".conf",
}
_AJENOS = {"node_modules", "__pycache__", ".venv", ".git", "dist", "build", ".svelte-kit"}
_AJENOS_SUFIJO = (".egg-info",)
_TOKEN = re.compile(r"[a-z0-9]+")

#: Los que lo nombran a propósito: hosts internos prohibidos como destino.
PERMITIDOS = frozenset(
    {
        "shared/orpycamcp_common/red_interna.py",
        "services/docuboto/document/schemas/email_cuenta.py",
        "services/docuboto/notification/core/webhook_security.py",
        "services/docuboto/tests/document/test_correo_no_apunta_hacia_dentro.py",
    }
)


def _archivos() -> list[Path]:
    hallados: list[Path] = []
    for raiz in _RAICES:
        for ruta in raiz.rglob("*"):
            if not ruta.is_file():
                continue
            if ruta.suffix not in _SUFIJOS and ruta.name not in {"Dockerfile", ".env.example"}:
                continue
            partes = ruta.relative_to(RAIZ).parts
            if _AJENOS.intersection(partes) or any(p.endswith(_AJENOS_SUFIJO) for p in partes):
                continue
            if ruta == Path(__file__).resolve():
                continue  # este guardián explica lo que prohíbe
            hallados.append(ruta)
    hallados.extend(ruta for ruta in _SUELTOS if ruta.is_file())
    return hallados


def test_el_censo_ve_el_arbol() -> None:
    rutas = {str(r.relative_to(RAIZ)) for r in _archivos()}
    assert "docker-compose.yml" in rutas
    assert "services/docuboto/requirements.txt" in rutas
    assert ".github/workflows/ci.yml" in rutas
    assert PERMITIDOS <= rutas, f"un permitido ya no existe: quítalo -> {sorted(PERMITIDOS - rutas)}"


def test_nadie_nombra_el_almacen_retirado() -> None:
    culpables = sorted(
        rel
        for ruta in _archivos()
        if (rel := str(ruta.relative_to(RAIZ))) not in PERMITIDOS
        and _NOMBRE in _TOKEN.findall(ruta.read_text(encoding="utf-8", errors="replace").lower())
    )
    assert not culpables, (
        f"Vuelve a aparecer «{_NOMBRE}» (ADR-049: el bus vive en PostgreSQL): {culpables}"
    )


def test_los_permitidos_lo_siguen_necesitando() -> None:
    """Un permitido que ya no lo nombra se quita de la lista: la excepción no se hereda."""
    sobrantes = sorted(
        p
        for p in PERMITIDOS
        if _NOMBRE not in _TOKEN.findall((RAIZ / p).read_text(encoding="utf-8").lower())
    )
    assert not sobrantes, sobrantes
