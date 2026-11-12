# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One migration chain and one runner (ADR-053).

The schema used to live in three places: 289 numbered files spread over nine
services, a generated baseline, and ten copies of the runner. These checks keep
it in one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHAIN = ROOT / "services" / "docuboto" / "migrations"
NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def test_there_is_one_runner() -> None:
    runners = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob("services/*/migrations/runner.py"))
    assert runners == ["services/docuboto/migrations/runner.py"]


def test_no_service_keeps_its_own_chain() -> None:
    own = sorted(
        p.relative_to(ROOT).as_posix()
        for p in ROOT.glob("services/*/migrations/*/*.sql")
        if p.parent.name in ("global", "tenant") and CHAIN not in p.parents
    )
    assert own == []


def test_no_generated_baseline() -> None:
    assert not (ROOT / "scripts" / "generar_baseline.py").exists()
    assert [p.name for p in CHAIN.rglob("*.sql") if "baseline" in p.name] == []


@pytest.mark.parametrize("scope", ["global", "tenant"])
def test_files_are_named_and_numbered_once(scope: str) -> None:
    files = sorted(p.name for p in (CHAIN / scope).glob("*.sql"))
    assert files, f"{scope}/ is empty"
    bad = [f for f in files if not NAME.match(f)]
    assert bad == [], f"expected NNN_name.sql: {bad}"
    numbers = [NAME.match(f)[1] for f in files]
    repeated = sorted({n for n in numbers if numbers.count(n) > 1})
    assert repeated == [], f"number used twice in {scope}/: {repeated}"


def test_tenant_files_name_no_tenant_schema() -> None:
    """A tenant file runs once per schema: a literal `tenant_x.` would pin it to one."""
    pinned = [
        p.name
        for p in sorted((CHAIN / "tenant").glob("*.sql"))
        if re.search(r"\btenant_[a-z0-9_]+\.", p.read_text(encoding="utf-8"))
    ]
    assert pinned == []
