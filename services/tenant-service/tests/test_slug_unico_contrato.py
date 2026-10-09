# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-02-05: la función de schema de este servicio aplica el validador ÚNICO de
`orpycamcp_common.tenant` (no una copia con otra cota). Atrapa que el servicio siga
importando el validador pero deje de llamarlo, algo que el guardián de `shared/tests`
(que cuenta importaciones) no ve."""

import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema as schema_de


@pytest.mark.parametrize(
    "slug",
    ["-abc", "a_b", "a" * 55, "A", "a;b", 'a"b', "", "a", "ab\n", "a b"],
)
def test_slug_invalido_se_rechaza(slug: str) -> None:
    with pytest.raises((HTTPException, ValueError)):
        schema_de(slug)


def test_slug_valido_se_normaliza_y_el_tope_cabe_en_postgres() -> None:
    assert schema_de("bogota-limpia") == "tenant_bogota_limpia"
    assert len(schema_de("a" * 54).encode()) <= 63


def test_migracion_global_exige_la_misma_forma_que_la_aplicacion() -> None:
    """La base y `SLUG_PATTERN` coinciden (AREA-02-05) y la migración aborta antes de endurecer.

    Si el patrón cambia en common y no en la 003, una entidad aceptada por la base
    sería rechazada por todos los servicios (o al revés): el fondo quedaría inaccesible.
    """
    from pathlib import Path

    from orpycamcp_common.tenant import SLUG_PATTERN

    sql = (Path(__file__).parents[1] / "migrations" / "global" / "003_slug_forma.sql").read_text()
    assert f"slug !~ {SLUG_PATTERN!r}" in sql  # guarda previa
    assert f"CHECK (slug ~ {SLUG_PATTERN!r})" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("ADD CONSTRAINT")
