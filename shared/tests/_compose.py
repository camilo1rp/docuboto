# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lectura de los manifiestos de despliegue para los guardianes de infraestructura.

Módulo de apoyo (el prefijo `_` evita que pytest lo recoja). Necesita PyYAML,
que `test-unit-shared` instala a propósito: un guardián de manifiestos que se
salta por falta de librería deja de guardar sin que nadie lo vea.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE_DEV = RAIZ / "docker-compose.yml"
COMPOSE_E2E = RAIZ / "docker-compose.e2e.yml"
COMPOSE_PROD = RAIZ / "infra" / "docker-compose.prod.yml"
WORKFLOWS = RAIZ / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci.yml"
RELEASE_WORKFLOW = WORKFLOWS / "release.yml"
AUDIT_WORKFLOW = WORKFLOWS / "dependency-audit.yml"


def cargar(ruta: Path) -> dict[str, Any]:
    data = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{ruta} no es un mapa YAML"
    return data


def job(ruta: Path, nombre: str) -> dict[str, Any]:
    """Un trabajo de un workflow de GitHub Actions."""
    jobs = cargar(ruta).get("jobs") or {}
    assert nombre in jobs, f"{ruta.name} no tiene el trabajo {nombre!r}"
    return dict(jobs[nombre])


def disparadores(ruta: Path) -> dict[str, Any]:
    """La clave `on:` de un workflow. PyYAML (YAML 1.1) la lee como `True`."""
    data = cargar(ruta)
    on = data.get("on", data.get(True))
    return {on: None} if isinstance(on, str) else dict(on or {})


def guion(trabajo: dict[str, Any]) -> str:
    """El texto de todos los `run:` de un trabajo, en orden."""
    return "\n".join(str(p.get("run", "")) for p in trabajo.get("steps") or [])


def servicios(ruta: Path) -> dict[str, dict[str, Any]]:
    return dict(cargar(ruta).get("services") or {})


def servicios_propios() -> list[str]:
    """Directorios `services/*/` con Dockerfile (los servicios de la pila)."""
    return sorted(p.parent.name for p in (RAIZ / "services").glob("*/Dockerfile"))


# Paquete Python de cada servicio. Los de dominio usan el nombre de la carpeta
# (document, workflow, …). api-gateway y mcp-server siguen en `app`.
DIR_PAQUETE = {
    "api-gateway": "app",
    "mcp-server": "app",
    "document-service": "document",
    "workflow-service": "workflow",
    "archive-service": "archive",
    "storage-service": "storage",
    "auth-service": "auth",
    "tenant-service": "tenant",
    "notification-service": "notification",
    "signature-service": "signature",
    "knowledge-service": "knowledge",
}
PAQUETES = tuple(sorted(set(DIR_PAQUETE.values())))


def dir_paquete(nombre: str) -> str:
    """Nombre del directorio-paquete de un servicio (`document-service` → `document`)."""
    if nombre in DIR_PAQUETE:
        return DIR_PAQUETE[nombre]
    if nombre.endswith("-service"):
        corto = nombre[: -len("-service")]
        if corto in DIR_PAQUETE.values():
            return corto
    return "app"


def ruta_src(nombre: str, *partes: str) -> Path:
    """Fuente de un servicio: `ruta_src("document-service", "main.py")`."""
    return (RAIZ / "services" / nombre / dir_paquete(nombre)).joinpath(*partes)


def paquete_de(servicio: Path | str) -> Path | None:
    """Ruta al paquete Python de un servicio, o None si no existe."""
    raiz = servicio if isinstance(servicio, Path) else RAIZ / "services" / servicio
    dest = raiz / dir_paquete(raiz.name)
    return dest if dest.is_dir() else None


def glob_src(raiz: Path, patron: str) -> list[Path]:
    """Expande el segmento de paquete `app` a cada nombre real.

    Los guardianes escribían `services/*/app/**`. Tras el rename, nueve
    servicios viven en `document/`, `workflow/`, … y dos siguen en `app/`.
    """
    if "/app/" not in patron:
        return sorted(raiz.glob(patron))
    encontrados: set[Path] = set()
    for paquete in PAQUETES:
        encontrados.update(raiz.glob(patron.replace("/app/", f"/{paquete}/")))
    return sorted(encontrados)
