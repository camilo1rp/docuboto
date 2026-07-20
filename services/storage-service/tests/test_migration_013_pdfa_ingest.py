# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Chequeo ESTÁTICO de la migración 013 (`files.pdfa_status`/`pdfa_profile`/
`pdfa_checked_at`, E10 Increment C2b follow-up) -- sin abrir una conexión
real (el carril unit corre con `--no-deps`). Mismo patrón que
`test_aip_pdfa.py::test_migration_008_check_constraint_admits_no_evaluado` y
`test_preservacion_worm_renovacion.py::test_migration_011_is_additive_and_idempotent_by_construction`.

La idempotencia real contra Postgres (doble aplicación de la migración) y la
verdadera fuerza del CHECK constraint (INSERT/UPDATE con un valor inválido
DEBE fallar) se prueban además contra Postgres REAL en
`tests/integration/test_pdfa_ingest_worker_real_sql.py` -- un chequeo
estático de texto no puede demostrar que el motor de verdad rechaza un valor.
"""

from __future__ import annotations

import re
from pathlib import Path

_MIGRATION_013_PATH = (
    Path(__file__).resolve().parent.parent / "migrations" / "tenant" / "013_files_pdfa_ingest.sql"
)


def _sql_text() -> str:
    return _MIGRATION_013_PATH.read_text(encoding="utf-8")


def test_migration_013_adds_three_columns_additively() -> None:
    sql = _sql_text()
    assert "ADD COLUMN IF NOT EXISTS pdfa_status" in sql
    assert "ADD COLUMN IF NOT EXISTS pdfa_profile" in sql
    assert "ADD COLUMN IF NOT EXISTS pdfa_checked_at" in sql
    assert "ALTER TABLE files" in sql


def test_migration_013_check_constraint_is_drop_then_add_idempotent_by_construction() -> None:
    """`DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` (mismo patrón que la
    008/011): re-ejecutar la migración sobre un schema ya migrado no debe
    fallar por "constraint ya existe"."""
    sql = _sql_text()
    assert "DROP CONSTRAINT IF EXISTS files_pdfa_status_check" in sql
    assert re.search(r"ADD CONSTRAINT\s+files_pdfa_status_check", sql)


def test_migration_013_check_constraint_admits_exactly_the_documented_states() -> None:
    sql = _sql_text()
    check_match = re.search(
        r"CHECK\s*\(pdfa_status IS NULL OR pdfa_status IN \(([^)]+)\)\)", sql, re.DOTALL
    )
    assert check_match, "no se encontró el CHECK constraint de pdfa_status en la migración 013"
    allowed_values = {
        v.strip().strip("'").strip("\n").strip()
        for v in check_match.group(1).replace("\n", " ").split(",")
    }
    assert allowed_values == {"conformant", "non_conformant", "not_evaluated", "pendiente"}


def test_migration_013_rollback_documented_but_not_automated() -> None:
    """El rollback es MANUAL (comentado, no ejecutado por el runner) -- mismo
    criterio que el resto de migraciones aditivas de este servicio."""
    sql = _sql_text()
    assert "-- Rollback (manual, no automatizado por el runner)" in sql
    assert "-- ALTER TABLE files DROP COLUMN IF EXISTS pdfa_status" in sql
