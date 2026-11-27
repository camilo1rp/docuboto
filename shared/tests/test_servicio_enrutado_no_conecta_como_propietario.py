# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Guardián: ningún proceso que sirve peticiones conecta como PROPIETARIO.

── El defecto ───────────────────────────────────────────────────────────────

La migración 008 de auth-service declaraba una excepción: «tenant-service sigue
conectando como propietario: está sellado con token interno y no enrutado por el
gateway». La premisa era falsa —`/dependencias`, `/catalogos`, `/config`,
`/business-days` y `/pinar` van a tenant-service y los alcanza cualquier JWT— y
el servicio corría con el rol superusuario: el solo-anexar de `audit_log`, que
descansa en privilegios que ese rol omite, no protegía nada frente a él.

── Cómo se decide ───────────────────────────────────────────────────────────

Desde ADR-047 hay un proceso que sirve (`app`) y ningún gateway. Se deriva de
los manifiestos —todo servicio con `DATABASE_URL` que no sea un migrador— y de
cada uno se exige, en los DOS compose (dev y producción), el rol de aplicación
(`DB_APP_USER`/`orpycamcp_app`), nunca el propietario.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSES = [REPO / "docker-compose.yml", REPO / "infra" / "docker-compose.prod.yml"]

_SERVICIO = re.compile(r"^  ([a-z0-9-]+):\s*$")
_DB = re.compile(r"^\s*(?:-\s*)?DATABASE_URL[=:]\s*(.+?)\s*$")


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


def _enrutados(compose: Path) -> set[str]:
    """Servicios que sirven y conectan a la base (no los migradores de un solo uso)."""
    return {s for s in _database_url(compose) if not s.startswith("migrate-")}


@pytest.mark.parametrize("compose", COMPOSES, ids=lambda p: p.name)
def test_el_censo_ve_la_app(compose: Path) -> None:
    # Control positivo: si el parseo no ve la app, «0 incumplimientos» no dice nada.
    assert _enrutados(compose) == {"app"}


@pytest.mark.parametrize("compose", COMPOSES, ids=lambda p: p.name)
def test_los_enrutados_usan_el_rol_de_aplicacion(compose: Path) -> None:
    urls = _database_url(compose)
    incumplen = {
        svc: urls[svc]
        for svc in sorted(_enrutados(compose))
        if "DB_APP_USER" not in urls[svc]
    }
    assert not incumplen, (
        f"{compose.name}: procesos que sirven y conectan como propietario "
        f"(usar DB_APP_USER y dejar el DDL a MIGRATIONS_DATABASE_URL): {incumplen}"
    )


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
    proceso que sirve entregaba el rol que puede `ALTER TABLE public.audit_log DISABLE
    TRIGGER`. Solo el contenedor `migrate-docuboto` (un solo uso, ADR-053) la lleva."""
    compose = REPO / "infra" / "docker-compose.prod.yml"
    mig = _migrates_prod(compose)
    runtime = {s: u for s, u in mig.items() if not s.startswith("migrate-")}
    assert not runtime, (
        "servicios que SIRVEN con el propietario en el entorno (quitar "
        f"MIGRATIONS_DATABASE_URL, déjala solo en migrate-docuboto): {sorted(runtime)}"
    )
    # Control positivo: el migrador SÍ la lleva (si no, el runner no podría hacer DDL).
    # Es también el control contra un parseo ciego: sin él, `runtime` vacío no probaría nada.
    assert sorted(mig) == ["migrate-docuboto"], sorted(mig)
    assert "DB_APP_USER" not in mig["migrate-docuboto"]
    assert "MIGRATIONS_DATABASE_URL" not in _bloque_de_servicio(compose, "app")


def _migrates_prod(compose: Path) -> dict[str, str]:
    return _migrations_url(compose)
