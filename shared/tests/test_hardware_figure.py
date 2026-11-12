# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One hardware figure, checked against the manifest (ADR-053 §6).

The documentation used to promise three different figures, none of them checked.
Now `docs/{es,en}/deployment.md` publish one (`**N vCPU / N GB / N GB SSD**`),
and the sum of `deploy.resources.limits` of the services that run by default in
`infra/docker-compose.prod.yml` must fit in it. Services behind a `profiles:`
entry do not run by default; one-shot jobs (`restart: "no"`, the migrator) do not
run alongside the others. Disk cannot be read from the manifest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._compose import COMPOSE_PROD, RAIZ, servicios

FIGURE = re.compile(r"\*\*(\d+(?:[.,]\d+)?) vCPU / (\d+(?:[.,]\d+)?) GB / (\d+) GB SSD\*\*")
UNITS = {"k": 1 / 1024**2, "m": 1 / 1024, "g": 1.0}


def _published() -> tuple[float, float]:
    found = {
        lang: FIGURE.findall((RAIZ / "docs" / lang / "deployment.md").read_text(encoding="utf-8"))
        for lang in ("es", "en")
    }
    assert len(found["es"]) == 1, f"docs/es/deployment.md must publish the figure once: {found['es']}"
    assert found["en"] == found["es"], f"es and en publish different figures: {found}"
    cpu, mem, _disk = found["es"][0]
    return float(cpu.replace(",", ".")), float(mem.replace(",", "."))


def _gigabytes(value: str) -> float:
    v = str(value).strip().lower().removesuffix("b")
    return float(v[:-1]) * UNITS[v[-1]] if v[-1] in UNITS else float(v) / 1024**3


def _always_on() -> dict[str, dict]:
    return {
        name: svc
        for name, svc in servicios(COMPOSE_PROD).items()
        if not svc.get("profiles") and str(svc.get("restart", "")) != "no"
    }


def _limits(svc: dict) -> dict:
    return ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}


def test_every_service_declares_its_limits() -> None:
    """A service without limits makes the sum meaningless."""
    missing = sorted(
        n for n, s in _always_on().items() if "cpus" not in _limits(s) or "memory" not in _limits(s)
    )
    assert missing == []


def test_published_once() -> None:
    _published()
    for lang in ("es", "en"):
        text = (RAIZ / "docs" / lang / "getting-started.md").read_text(encoding="utf-8")
        assert not re.search(r"\d+ ?GB", text), f"docs/{lang}/getting-started.md states its own figure"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "2026-11: the default profile adds up to 13.5 CPU / 20.75 GB of limits (Keycloak, Redis, "
        "MinIO, app, worker...). The figure is the target of ADR-047/048/049; remove this mark "
        "when the manifest fits."
    ),
)
def test_manifest_fits_the_published_figure() -> None:
    cpu, mem = _published()
    services = _always_on()
    total_cpu = sum(float(_limits(s).get("cpus", 0)) for s in services.values())
    total_mem = sum(_gigabytes(_limits(s).get("memory", "0")) for s in services.values())
    assert total_cpu <= cpu and total_mem <= mem, (
        f"limits add up to {total_cpu:g} CPU / {total_mem:g} GB; published {cpu:g} vCPU / {mem:g} GB"
    )
