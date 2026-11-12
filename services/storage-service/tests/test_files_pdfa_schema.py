# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Chequeo ESTÁTICO del esquema de `files.pdfa_status`/`pdfa_profile`/
`pdfa_checked_at` (E10 Increment C2b follow-up) en la cadena única de
migraciones (ADR-053, `services/docuboto/migrations/tenant/300_files.sql`)
-- sin abrir una conexión real (el carril unit corre con `--no-deps`).

La verdadera fuerza del CHECK constraint (INSERT/UPDATE con un valor inválido
DEBE fallar) se prueba además contra Postgres REAL en
`tests/integration/test_pdfa_ingest_worker_real_sql.py` -- un chequeo
estático de texto no puede demostrar que el motor de verdad rechaza un valor.
"""

from __future__ import annotations

import re
from pathlib import Path

_FILES_SQL_PATH = (
    Path(__file__).resolve().parents[2] / "docuboto" / "migrations" / "tenant" / "300_files.sql"
)


def _files_table_sql() -> str:
    sql = _FILES_SQL_PATH.read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE files \((.*?)\n\);", sql, re.DOTALL)
    assert m, "no se encontró CREATE TABLE files en 300_files.sql"
    return m.group(1)


def test_files_has_the_three_pdfa_columns() -> None:
    table = _files_table_sql()
    assert re.search(r"^\s*pdfa_status text,$", table, re.MULTILINE)
    assert re.search(r"^\s*pdfa_profile text,$", table, re.MULTILINE)
    assert re.search(r"^\s*pdfa_checked_at timestamp with time zone,$", table, re.MULTILINE)


def test_files_pdfa_status_check_admits_exactly_the_documented_states() -> None:
    check_match = re.search(
        r"CONSTRAINT files_pdfa_status_check CHECK \(\(\(pdfa_status IS NULL\) OR "
        r"\(pdfa_status = ANY \(ARRAY\[([^\]]+)\]\)\)\)\)",
        _files_table_sql(),
    )
    assert check_match, "no se encontró el CHECK constraint de pdfa_status en 300_files.sql"
    allowed_values = set(re.findall(r"'([^']+)'::text", check_match.group(1)))
    assert allowed_values == {"conformant", "non_conformant", "not_evaluated", "pendiente"}
