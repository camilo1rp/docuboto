# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Guardián: ningún servicio alcanzable por el gateway conecta como PROPIETARIO.

── El defecto ───────────────────────────────────────────────────────────────

La migración 008 de auth-service declaraba una excepción: «tenant-service sigue
conectando como propietario: está sellado con token interno y no enrutado por el
gateway». La premisa era falsa —`/dependencias`, `/catalogos`, `/config`,
`/business-days` y `/pinar` van a tenant-service y los alcanza cualquier JWT— y
el servicio corría con el rol superusuario: el solo-anexar de `audit_log`, que
descansa en privilegios que ese rol omite, no protegía nada frente a él.

── Cómo se decide ───────────────────────────────────────────────────────────

Los servicios se derivan de `proxy.py` (los `app_state.<x>_service_url` de su
tabla de ruteo), no de una lista a mano: un servicio nuevo que el gateway
enrute queda cubierto sin acordarse de nada. De cada uno se lee el
`DATABASE_URL` en los DOS compose (dev y producción) y se exige el rol de
aplicación (`DB_APP_USER`/`orpycamcp_app`), nunca el propietario.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROXY = REPO / "services" / "api-gateway" / "app" / "routers" / "proxy.py"
COMPOSES = [REPO / "docker-compose.yml", REPO / "infra" / "docker-compose.prod.yml"]

_URL = re.compile(r"app_state\.([a-z_]+)_service_url")
_SERVICIO = re.compile(r"^  ([a-z0-9-]+):\s*$")
_DB = re.compile(r"^\s*(?:-\s*)?DATABASE_URL[=:]\s*(.+?)\s*$")


def _enrutados() -> set[str]:
    """Nombres de servicio (`tenant-service`…) que el gateway enruta."""
    codigo = "\n".join(
        linea for linea in PROXY.read_text(encoding="utf-8").splitlines()
        if not linea.lstrip().startswith("#")
    )
    return {f"{n.replace('_', '-')}-service" for n in _URL.findall(codigo)}


def _database_url(compose: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    actual: str | None = None
    for linea in compose.read_text(encoding="utf-8").splitlines():
        m = _SERVICIO.match(linea)
        if m:
            actual = m.group(1)
            continue
        d = _DB.match(linea)
        if d and actual:
            out[actual] = d.group(1)
    return out


def test_el_gateway_enruta_a_tenant_service() -> None:
    # Control positivo: si el parseo no ve el servicio que motivó el guardián,
    # un «0 incumplimientos» no significaría nada.
    assert "tenant-service" in _enrutados()


@pytest.mark.parametrize("compose", COMPOSES, ids=lambda p: p.name)
def test_los_enrutados_usan_el_rol_de_aplicacion(compose: Path) -> None:
    urls = _database_url(compose)
    incumplen = {
        svc: urls[svc]
        for svc in sorted(_enrutados())
        if svc in urls and "DB_APP_USER" not in urls[svc]
    }
    assert not incumplen, (
        f"{compose.name}: servicios alcanzables por el gateway que conectan como "
        f"propietario (usar DB_APP_USER y dejar el DDL a MIGRATIONS_DATABASE_URL): {incumplen}"
    )
    assert "tenant-service" in urls, f"{compose.name}: no se encontró el DATABASE_URL de tenant-service"


_MIG = re.compile(r"^\s*(?:-\s*)?MIGRATIONS_DATABASE_URL[=:]\s*(.+?)\s*$")


def _migrations_url(compose: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    actual: str | None = None
    for linea in compose.read_text(encoding="utf-8").splitlines():
        m = _SERVICIO.match(linea)
        if m:
            actual = m.group(1)
            continue
        d = _MIG.match(linea)
        if d and actual:
            out[actual] = d.group(1)
    return out


def _bloque_de_servicio(compose: Path, svc: str) -> str:
    """Texto del bloque `  <svc>:` del compose (hasta el siguiente servicio)."""
    out: list[str] = []
    dentro = False
    for linea in compose.read_text(encoding="utf-8").splitlines():
        m = _SERVICIO.match(linea)
        if m:
            dentro = m.group(1) == svc
            continue
        if dentro:
            out.append(linea)
    return "\n".join(out)


def test_el_runtime_de_produccion_no_lleva_el_propietario_en_el_entorno() -> None:
    """Hallazgo T14: `MIGRATIONS_DATABASE_URL` (propietario) seguía en el entorno de
    los servicios EN EJECUCIÓN. Un RCE o un volcado del entorno de cualquiera que el
    gateway alcance entregaba el rol que puede `ALTER TABLE public.audit_log DISABLE
    TRIGGER`. Solo los contenedores `migrate-<svc>` (un solo uso) la llevan."""
    compose = REPO / "infra" / "docker-compose.prod.yml"
    mig = _migrates_prod(compose)
    assert len(mig) >= 9, f"solo {sorted(mig)}: guardián ciego (¿cambió el parseo?)"
    runtime = {s: u for s, u in mig.items() if not s.startswith("migrate-")}
    assert not runtime, (
        "servicios que SIRVEN con el propietario en el entorno (quitar "
        f"MIGRATIONS_DATABASE_URL, déjala solo en migrate-<svc>): {sorted(runtime)}"
    )
    # Control positivo: el migrador SÍ la lleva (si no, el runner no podría hacer DDL).
    assert "migrate-tenant" in mig and "DB_APP_USER" not in mig["migrate-tenant"]
    # El propio tenant-service (enrutado por el gateway) no la lleva.
    assert "MIGRATIONS_DATABASE_URL" not in _bloque_de_servicio(compose, "tenant-service")


def _migrates_prod(compose: Path) -> dict[str, str]:
    return _migrations_url(compose)


def test_el_dev_conserva_el_propietario_para_el_runner_de_tenant() -> None:
    """En desarrollo la variable sigue viviendo en el servicio (decisión de dev, no de prod).

    Desde C19 (ADR-041) dev SÍ tiene `migrate-<svc>`, así que ya no es por el runner: la
    razón vigente es que `scripts/init-tenant.sh` hace `docker compose exec` dentro del
    servicio y los conftest de integración la leen. Quitarla de dev los rompe."""
    mig = _migrations_url(REPO / "docker-compose.yml")
    assert "tenant-service" in mig and "DB_APP_USER" not in mig["tenant-service"]
