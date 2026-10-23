# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La métrica de WORM agotado cubre TODA tabla con un ciclo de reintento WORM (C18).

Una lista de admisión que crece aparte del sistema que vigila cubre lo que alguien
recordó, no lo que hay. Aquí se recalcula desde las migraciones de tenant de archive:
toda tabla con columnas `worm_reconcile_agotado` / `worm_renovacion_agotado` (y el
estado `'agotado'` del empaquetado del AIP) tiene su combinación en
`COMBINACIONES`, y viceversa.

Mutación que debe caer: añadir una migración con `worm_renovacion_agotado` en una
tabla nueva sin añadirla a `metricas_repository.COMBINACIONES`.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MIGRACIONES = RAIZ / "services" / "archive-service" / "migrations" / "tenant"
REPO = RAIZ / "services" / "archive-service" / "app" / "repositories" / "metricas_repository.py"

_FASES = {"worm_reconcile_agotado": "reconcile", "worm_renovacion_agotado": "renewal"}


def _combinaciones_del_repositorio() -> set[tuple[str, str]]:
    """(tabla, fase) leídos del módulo SIN importar el paquete `app` del servicio."""
    spec = importlib.util.spec_from_file_location("metricas_repository_censo", REPO)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {(tabla, fase) for _a, tabla, fase, _p in mod.COMBINACIONES}


def _combinaciones_de_las_migraciones() -> set[tuple[str, str]]:
    """Recorre los .sql: la columna se atribuye a la tabla del `ALTER TABLE`/`CREATE TABLE` vigente."""
    halladas: set[tuple[str, str]] = set()
    tabla: str | None = None
    for sql in sorted(MIGRACIONES.glob("*.sql")):
        for linea in sql.read_text(encoding="utf-8").splitlines():
            if linea.lstrip().startswith("--"):
                continue
            m = re.match(r"\s*(?:ALTER TABLE(?: IF EXISTS)?|CREATE TABLE(?: IF NOT EXISTS)?)\s+(\w+)", linea, re.I)
            if m:
                tabla = m.group(1)
            for columna, fase in _FASES.items():
                if re.search(rf"\b{columna}\s+BOOLEAN\b", linea, re.I) and tabla:
                    halladas.add((tabla, fase))
            if re.match(r"\s*CREATE\s+(UNIQUE\s+)?INDEX|\s*COMMENT\s+ON|\s*CREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|TRIGGER)", linea, re.I):
                tabla = None
    return halladas


def test_control_positivo_el_censo_encuentra_las_tablas_conocidas() -> None:
    halladas = _combinaciones_de_las_migraciones()
    assert len(halladas) >= 10, f"censo de migraciones ciego: {sorted(halladas)}"
    assert ("expediente_indice", "reconcile") in halladas
    assert ("transferencia_acta_firma_excepcion_revocacion_worm", "renewal") in halladas


def test_toda_tabla_worm_tiene_su_metrica_y_viceversa() -> None:
    repo = _combinaciones_del_repositorio()
    # el empaquetado del AIP es un estado, no una columna: se declara aparte
    repo_columnas = {c for c in repo if c[1] != "packaging"}
    assert ("expediente_aip", "packaging") in repo
    de_migraciones = _combinaciones_de_las_migraciones()
    assert de_migraciones == repo_columnas, (
        f"sin métrica: {sorted(de_migraciones - repo_columnas)}; "
        f"métrica sin columna: {sorted(repo_columnas - de_migraciones)}"
    )


def test_el_estado_agotado_del_aip_existe_en_su_check() -> None:
    sql = (MIGRACIONES / "033_expediente_aip.sql").read_text(encoding="utf-8")
    assert re.search(r"estado\s+IN\s*\([^)]*'agotado'", sql)
