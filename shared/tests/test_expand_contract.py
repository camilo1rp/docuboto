# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Expand, deploy, contract (ADR-053 §4).

Migrations have no `down`. Rolling a release back means pointing at the previous
version's images, and that only works if the schema left behind still serves
the previous code. So within one release a migration may only *expand*:

* no `DROP TABLE` / `DROP COLUMN` of something created in the same release;
* no `ALTER COLUMN … SET NOT NULL` on a column created in the same release;
* no `ADD COLUMN … NOT NULL` without `DEFAULT` on a table that already existed
  (the expand rule itself: the previous code does not write that column).

Contracting (dropping what nobody reads any more) happens in the *next* release.

**What a release is.** A release is a git tag `X.Y.Z` (the first one will be
`1.0.0`). The files of the release being built are the chain files that the
latest tag does not have; "created in the same release" means created by one of
those files. With no tag yet, the whole chain is the release in progress. We
chose tags over an in-tree list of shipped files because a tag is already the
release (ADR-053 §3 builds images from it) and needs no extra step to cut one;
the price is that this test needs `git` and the tags. Without git it skips
locally and **fails under CI** (`CI` set): a guard that silently skips does not
guard. Only the tag's tree is read (`git ls-tree`), not its ancestry, so a
shallow clone is enough as long as tags are fetched.

**A shipped file does not change.** The runner records files by name in
`public.schema_migrations` and skips them afterwards; editing a file the latest
tag already shipped would never reach an installation, and would let a `DROP`
slip past this guard. So files present in the tag must be byte-identical.

What this cannot see: whether the previous release's *code* still reads a column
that this release drops. That is the contract rule (drop only what the code of
the release before stopped reading) and stays a review item.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHAIN = ROOT / "services" / "docuboto" / "migrations"
CHAIN_REL = CHAIN.relative_to(ROOT).as_posix()
VERSION_TAG = re.compile(r"^\d+\.\d+\.\d+$")

# ── Parsing ──────────────────────────────────────────────────────────────────

_IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
_QNAME = rf"(?:{_IDENT}\.)?({_IDENT})"


def _name(raw: str) -> str:
    return raw.strip('"').lower()


def _statements(sql: str) -> list[str]:
    """Statements without comments. Good enough for DDL: `;` inside `$$` bodies is kept."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    out, buf, in_dollar = [], [], False
    for part in re.split(r"(\$[A-Za-z_]*\$|;)", sql):
        if re.fullmatch(r"\$[A-Za-z_]*\$", part):
            in_dollar = not in_dollar
        if part == ";" and not in_dollar:
            out.append(" ".join("".join(buf).split()))
            buf = []
        else:
            buf.append(part)
    tail = " ".join("".join(buf).split())
    return [s for s in [*out, tail] if s]


def _split_top(text: str) -> list[str]:
    """Split on commas not nested in parentheses."""
    parts, depth, cur = [], 0, []
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


_CREATE_TABLE = re.compile(
    rf"^CREATE (?:UNLOGGED |TEMP |TEMPORARY )?TABLE (?:IF NOT EXISTS )?{_QNAME}\s*\((.*)\)", re.I | re.S
)
_ALTER_TABLE = re.compile(rf"^ALTER TABLE (?:IF EXISTS )?(?:ONLY )?{_QNAME}\s+(.*)$", re.I | re.S)
_DROP_TABLE = re.compile(r"^DROP TABLE (?:IF EXISTS )?(.*?)(?: CASCADE| RESTRICT)?$", re.I | re.S)
_ADD_COLUMN = re.compile(rf"^ADD (?:COLUMN )?(?:IF NOT EXISTS )?({_IDENT})\s+(.*)$", re.I | re.S)
_DROP_COLUMN = re.compile(rf"^DROP (?:COLUMN )?(?:IF EXISTS )?({_IDENT})", re.I)
_SET_NOT_NULL = re.compile(rf"^ALTER (?:COLUMN )?({_IDENT}) SET NOT NULL$", re.I)
_CONSTRAINT_WORDS = {"constraint", "primary", "unique", "check", "foreign", "exclude", "like"}


def violations(release: dict[str, str]) -> list[str]:
    """Expand/contract breaches in the files of one release (`{file name: SQL}`)."""
    tables: set[str] = set()
    columns: set[tuple[str, str]] = set()
    found: list[str] = []
    for fname, sql in release.items():
        for stmt in _statements(sql):
            if m := _CREATE_TABLE.match(stmt):
                table = _name(m[1])
                tables.add(table)
                for col in _split_top(m[2]):
                    first = col.split()[0]
                    if first.lower() not in _CONSTRAINT_WORDS:
                        columns.add((table, _name(first)))
            elif m := _ALTER_TABLE.match(stmt):
                table = _name(m[1])
                for action in _split_top(m[2]):
                    if (a := _ADD_COLUMN.match(action)) and a[1].lower() not in _CONSTRAINT_WORDS:
                        col, spec = _name(a[1]), a[2]
                        if (
                            table not in tables
                            and re.search(r"\bNOT NULL\b", spec, re.I)
                            and not re.search(r"\bDEFAULT\b", spec, re.I)
                        ):
                            found.append(f"{fname}: ADD COLUMN {table}.{col} NOT NULL without DEFAULT")
                        columns.add((table, col))
                    elif (d := _DROP_COLUMN.match(action)) and d[1].lower() != "constraint":
                        col = _name(d[1])
                        if table in tables or (table, col) in columns:
                            found.append(f"{fname}: DROP COLUMN {table}.{col} created in this release")
                    elif n := _SET_NOT_NULL.match(action):
                        col = _name(n[1])
                        if (table, col) in columns:
                            found.append(f"{fname}: SET NOT NULL on {table}.{col} created in this release")
            elif m := _DROP_TABLE.match(stmt):
                for raw in _split_top(m[1]):
                    table = _name(raw.split(".")[-1])
                    if table in tables:
                        found.append(f"{fname}: DROP TABLE {table} created in this release")
    return found


# ── The guard on the parser ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE a (id int); ALTER TABLE a DROP COLUMN id;",
        "CREATE TABLE a (id int); DROP TABLE IF EXISTS public.a;",
        "ALTER TABLE old ADD COLUMN x text; ALTER TABLE old ALTER COLUMN x SET NOT NULL;",
        "ALTER TABLE old ADD COLUMN x text; ALTER TABLE old DROP COLUMN IF EXISTS x;",
        "ALTER TABLE old ADD COLUMN x int NOT NULL;",
        'CREATE TABLE "A" (id int, n text); ALTER TABLE ONLY a ALTER COLUMN n SET NOT NULL;',
    ],
)
def test_breaches_are_seen(sql: str) -> None:
    assert violations({"900_x.sql": sql})


@pytest.mark.parametrize(
    "sql",
    [
        # expanding is fine
        "CREATE TABLE a (id int NOT NULL, CONSTRAINT a_pk PRIMARY KEY (id));",
        "ALTER TABLE old ADD COLUMN x int NOT NULL DEFAULT 0;",
        "ALTER TABLE old ADD COLUMN x text;",
        "CREATE TABLE a (id int NOT NULL); ALTER TABLE a ADD COLUMN y int NOT NULL;",
        # contracting what an earlier release created is fine (here: `old`)
        "ALTER TABLE old DROP COLUMN x; DROP TABLE old2; ALTER TABLE old ALTER COLUMN y SET NOT NULL;",
        "CREATE TABLE a (id int); ALTER TABLE a DROP CONSTRAINT a_pk;",
        # comments and function bodies are not statements
        "-- DROP TABLE a;\nCREATE TABLE a (id int);\n/* ALTER TABLE a DROP COLUMN id; */",
        "CREATE TABLE a (id int); CREATE FUNCTION f() RETURNS void AS $$ BEGIN DROP TABLE a; END $$ LANGUAGE plpgsql;",
    ],
)
def test_expanding_passes(sql: str) -> None:
    assert violations({"900_x.sql": sql}) == []


# ── The guard on the chain ───────────────────────────────────────────────────


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
    ).stdout


def _latest_release() -> str | None:
    """Highest `X.Y.Z` tag that is not HEAD itself (a tagged HEAD *is* the release being built)."""
    head = _git("rev-parse", "HEAD").strip()
    for tag in _git("tag", "--list", "--sort=-v:refname").split():
        if VERSION_TAG.match(tag) and _git("rev-list", "-n1", tag).strip() != head:
            return tag
    return None


def _shipped(tag: str | None) -> dict[str, str]:
    """`{path: blob sha}` of the chain files in the tag."""
    if tag is None:
        return {}
    rows = _git("ls-tree", "-r", tag, "--", CHAIN_REL).splitlines()
    return {
        path: sha
        for meta, path in (row.split("\t", 1) for row in rows)
        for sha in [meta.split()[2]]
        if path.endswith(".sql")
    }


def _current() -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix() for scope in ("global", "tenant") for p in (CHAIN / scope).glob("*.sql")
    )


@pytest.fixture(scope="module")
def release_state() -> tuple[str | None, dict[str, str]]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        if os.environ.get("CI"):
            pytest.fail("the expand/contract guard needs git and the tags in CI")
        pytest.skip("no git checkout: cannot tell which release a migration belongs to")
    tag = _latest_release()
    return tag, _shipped(tag)


def test_release_only_expands(release_state: tuple[str | None, dict[str, str]]) -> None:
    _, shipped = release_state
    new = {Path(p).name: (ROOT / p).read_text(encoding="utf-8") for p in _current() if p not in shipped}
    assert violations(new) == []


def test_shipped_migrations_do_not_change(release_state: tuple[str | None, dict[str, str]]) -> None:
    tag, shipped = release_state
    if not shipped:
        return  # nothing released yet: the chain is still the 1.0.0 draft
    current = set(_current())
    missing = sorted(p for p in shipped if p not in current)
    assert missing == [], f"removed after {tag} shipped them: {missing}"
    paths = sorted(shipped)
    now = dict(zip(paths, _git("hash-object", "--", *paths).split(), strict=True))
    changed = [p for p in paths if now[p] != shipped[p]]
    assert changed == [], f"edited after {tag} shipped them (add a new file instead): {changed}"
