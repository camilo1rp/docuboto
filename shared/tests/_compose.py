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
    """Servicios de los manifiestos cuya imagen sale de este repositorio (ADR-047).

    El frontend se nombra aparte en cada guardián, como antes.
    """
    return ["app"]


#: El proceso único (ADR-047): los paquetes de dominio son directorios hermanos.
APP = RAIZ / "services" / "docuboto"

# Paquete Python de cada antiguo servicio. Los guardianes siguen nombrando el
# dominio por su servicio de antes (`document-service`) o por el paquete
# (`document`); los dos llevan al mismo directorio.
DIR_PAQUETE = {
    "document-service": "document",
    "workflow-service": "workflow",
    "archive-service": "archive",
    "storage-service": "storage",
    "auth-service": "auth",
    "tenant-service": "tenant",
    "notification-service": "notification",
    "signature-service": "signature",
    "knowledge-service": "knowledge",
    "docuboto": "docuboto",
}
PAQUETES = tuple(sorted(set(DIR_PAQUETE.values())))


def dir_paquete(nombre: str) -> str:
    """Nombre del directorio-paquete de un dominio (`document-service` → `document`)."""
    if nombre in DIR_PAQUETE:
        return DIR_PAQUETE[nombre]
    if nombre in PAQUETES:
        return nombre
    raise KeyError(f"dominio desconocido: {nombre}")


def ruta_src(nombre: str, *partes: str) -> Path:
    """Fuente de un dominio: `ruta_src("document-service", "main.py")`."""
    return (APP / dir_paquete(nombre)).joinpath(*partes)


def ruta_tests(nombre: str, *partes: str) -> Path:
    """Pruebas de un dominio: `ruta_tests("storage", "test_x.py")`."""
    return (APP / "tests" / dir_paquete(nombre)).joinpath(*partes)


def paquete_de(servicio: Path | str) -> Path | None:
    """Ruta al paquete Python de un dominio, o None si no existe."""
    nombre = servicio.name if isinstance(servicio, Path) else servicio
    dest = APP / dir_paquete(nombre)
    return dest if dest.is_dir() else None


def glob_src(raiz: Path, patron: str) -> list[Path]:
    """Expande el segmento de paquete `app` a cada nombre real.

    Los guardianes escribían `services/*/app/**`. Los paquetes viven ahora en
    `services/docuboto/<dominio>/` y el comodín de servicio los sigue cubriendo.
    """
    if "/app/" not in patron:
        return sorted(raiz.glob(patron))
    encontrados: set[Path] = set()
    for paquete in PAQUETES:
        encontrados.update(raiz.glob(patron.replace("/app/", f"/{paquete}/")))
    return sorted(encontrados)
