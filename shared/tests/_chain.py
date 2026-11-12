# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reading the migration chain (ADR-053) from the architecture tests.

Support module (the `_` prefix keeps pytest from collecting it). The chain is
`services/docuboto/migrations/{global,tenant}/NNN_name.sql`; files are named
after tables, so tests ask for a table or a seed instead of a file name.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAIN = ROOT / "services" / "docuboto" / "migrations"
SCOPES = ("global", "tenant")


def chain_files(*scopes: str) -> list[Path]:
    """The `.sql` files of the given scopes (both by default), in order."""
    return [p for scope in (scopes or SCOPES) for p in sorted((CHAIN / scope).glob("*.sql"))]


def chain_text(*scopes: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in chain_files(*scopes))


def create_table(table: str, scope: str = "tenant") -> str:
    """The `CREATE TABLE <table> (...);` statement. Fails if it is missing or repeated."""
    found = re.findall(
        rf"^CREATE TABLE (?:public\.)?{re.escape(table)} \(.*?^\);", chain_text(scope), re.M | re.S
    )
    assert len(found) == 1, f"CREATE TABLE {table}: {len(found)} in {scope}/"
    return found[0]


def seed_rows(table: str, scope: str = "tenant") -> str:
    """Text of every `INSERT INTO <table> ...;` of the chain, concatenated."""
    return "\n".join(
        re.findall(rf"^INSERT INTO (?:public\.)?{re.escape(table)} \(.*?\);$", chain_text(scope), re.M | re.S)
    )
