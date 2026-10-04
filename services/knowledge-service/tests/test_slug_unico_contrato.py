"""AREA-02-05: la función de schema de este servicio aplica el validador ÚNICO de
`orpycamcp_common.tenant` (no una copia con otra cota). Atrapa que el servicio siga
importando el validador pero deje de llamarlo, algo que el guardián de `shared/tests`
(que cuenta importaciones) no ve."""

import pytest
from fastapi import HTTPException

from app.core.database import _schema as schema_de


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
