# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Catálogo cerrado de causales de reserva (C12, ADR-039): la regla, y que no diverja.

El catálogo vive en TRES sitios que deben decir lo mismo: la constante `CAUSALES`, el
CHECK de la migración 050 (y el de la tabla de solicitudes) y el `enum` de la tool MCP.
Este guardián ata los dos primeros (el tercero lo ata
`shared/tests/test_causales_reserva_una_sola_lista.py`, que ve ambos servicios).
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

from app.core import causales_reserva as cr
from tests.conftest import TENANT_HEADERS

_SQL = (
    Path(__file__).parents[1] / "migrations" / "tenant" / "050_reserva_catalogo_y_desclasificacion.sql"
).read_text()


def _codigos(texto: str, prefijo: str) -> set[str]:
    return set(re.findall(rf"'({prefijo}_[a-z_]+)'", texto))


def _bloque(inicio: str, fin: str) -> str:
    i = _SQL.index(inicio)
    return _SQL[i : _SQL.index(fin, i)]


def test_la_constante_y_el_literal_dicen_lo_mismo() -> None:
    assert set(typing.get_args(cr.CausalCodigo)) == set(cr.CAUSALES)
    assert cr.POR_CLASIFICAR not in cr.CAUSALES, "por_clasificar no se asigna por la API"


def test_el_check_de_radicados_lista_exactamente_el_catalogo() -> None:
    check = _bloque("ADD CONSTRAINT radicados_causal_catalogo", "NOT VALID")
    assert _codigos(check, "art18") == {c for c in cr.CAUSALES if c.startswith("art18")}
    assert _codigos(check, "art19") == {c for c in cr.CAUSALES if c.startswith("art19")}
    assert f"'{cr.POR_CLASIFICAR}'" in check


def test_el_backfill_de_la_migracion_usa_el_mismo_catalogo() -> None:
    sentencia = _bloque("SET clasificacion_causal = 'por_clasificar'", "-- 2) CHECK")
    assert _codigos(sentencia, "art18") | _codigos(sentencia, "art19") == set(cr.CAUSALES)


def test_la_tabla_de_solicitudes_admite_solo_causales_art19_como_destino() -> None:
    destino = _bloque("CONSTRAINT rs_causal_destino", "CONSTRAINT rs_resuelta_forma")
    assert _codigos(destino, "art19") == {c for c in cr.CAUSALES if c.startswith("art19")}
    assert not _codigos(destino, "art18"), "bajar a nivel 2 no se motiva con el art. 18"


def test_el_umbral_de_doble_firma_es_el_mismo_en_codigo_tabla_y_trigger() -> None:
    assert cr.NIVEL_DOBLE_FIRMA == 3
    assert f"CHECK (nivel_anterior = {cr.NIVEL_DOBLE_FIRMA})" in _SQL
    assert f"OLD.nivel_seguridad >= {cr.NIVEL_DOBLE_FIRMA}" in _SQL


def test_el_plazo_maximo_del_codigo_es_el_del_check() -> None:
    assert f"BETWEEN 1 AND {cr.PLAZO_MAX_MESES}" in _SQL


def test_cada_causal_es_coherente_con_su_nivel() -> None:
    for c in cr.CAUSALES.values():
        assert c.codigo.startswith("art18" if c.nivel == 3 else "art19")
        assert (c.plazo_max_meses is None) == (c.nivel == 3)


@pytest.mark.parametrize(
    ("nivel", "causal", "plazo", "ok"),
    [
        (1, None, None, True),
        (1, "art18_a_intimidad", None, False),
        (2, None, 12, False),
        (2, "art19_i_salud_publica", 12, True),
        (2, "art19_i_salud_publica", None, False),
        (2, "art19_i_salud_publica", 181, False),
        (2, "art19_i_salud_publica", 0, False),
        (2, "art18_a_intimidad", None, False),
        (3, "art18_a_intimidad", None, True),
        (3, "art18_a_intimidad", 12, False),
        (3, "art19_i_salud_publica", 12, False),
        (3, "inventada", None, False),
        (2, "por_clasificar", None, False),
    ],
)
def test_validar_clasificacion(nivel, causal, plazo, ok) -> None:
    assert (cr.validar_clasificacion(nivel, causal, plazo) is None) is ok


def test_por_clasificar_solo_lo_admite_el_contexto_interno() -> None:
    assert cr.validar_clasificacion(3, "por_clasificar", None, permitir_por_clasificar=True) is None
    assert cr.validar_clasificacion(3, "por_clasificar", None) is not None


@pytest.mark.parametrize(
    ("nivel", "causal", "plazo", "esperado"),
    [
        (1, "art18_a_intimidad", 5, (None, None)),
        (3, "Ley 1712 art. 18", None, ("por_clasificar", None)),
        (3, None, None, ("por_clasificar", None)),
        (3, "art18_a_intimidad", None, ("art18_a_intimidad", None)),
        (2, "art19_i_salud_publica", 12, ("art19_i_salud_publica", 12)),
        (2, "art19_i_salud_publica", 600, ("por_clasificar", 600)),
        (2, "art19_i_salud_publica", None, ("por_clasificar", None)),
        (3, "art19_i_salud_publica", 12, ("por_clasificar", 12)),
    ],
)
def test_importar_normaliza_lo_que_no_cumple(nivel, causal, plazo, esperado) -> None:
    assert cr.normalizar_para_importar(nivel, causal, plazo) == esperado


@pytest.mark.asyncio
async def test_endpoint_del_catalogo_expone_la_ley(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.get("/api/v1/documents/causales-reserva", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["nivel_doble_firma"] == cr.NIVEL_DOBLE_FIRMA
    assert {c["codigo"] for c in cuerpo["causales"]} == set(cr.CAUSALES)
    art18 = [c for c in cuerpo["causales"] if c["categoria"] == "clasificada"]
    assert art18 and all(c["plazo_max_meses"] is None and c["nivel"] == 3 for c in art18)


# -- cierre del lote C: tope desde la generación y cita normativa ----------------------------------


def test_el_tope_se_mide_desde_la_generacion_no_desde_la_clasificacion() -> None:
    from app.core.causales_reserva import PLAZO_EXCEDE_TOPE, RESERVA_AGOTADA, tope_reserva

    assert tope_reserva(2, 60, 120) is None
    assert tope_reserva(2, 61, 120)[0] == PLAZO_EXCEDE_TOPE
    assert tope_reserva(2, 1, 180)[0] == RESERVA_AGOTADA
    assert tope_reserva(2, 1, 400)[0] == RESERVA_AGOTADA
    assert tope_reserva(3, None, 400) is None, "la clasificada (art. 18) no tiene plazo"
    assert tope_reserva(2, None, 400) is None


def test_validar_clasificacion_aplica_el_tope_cuando_se_le_da_la_edad() -> None:
    assert cr.validar_clasificacion(2, "art19_i_salud_publica", 61, meses_transcurridos=120) is not None
    assert cr.validar_clasificacion(2, "art19_i_salud_publica", 60, meses_transcurridos=120) is None


@pytest.mark.parametrize(
    "cita",
    ["Ley 1581 de 2012, art. 5", "Decreto 1081 de 2015", "Constitución Política art. 15", "Código Penal art. 418"],
)
def test_una_cita_normativa_valida_pasa(cita: str) -> None:
    from app.core.causales_reserva import validar_cita_norma

    assert validar_cita_norma(cita) is None


@pytest.mark.parametrize(
    "cita",
    ["el expediente de Juan Pérez", "Ley sin número", "Ley 1581\nsegunda línea", "x" * 200, "Ley 1; DROP TABLE"],
)
def test_una_frase_libre_no_es_una_cita_normativa(cita: str) -> None:
    from app.core.causales_reserva import validar_cita_norma

    assert validar_cita_norma(cita) is not None


def test_la_cita_del_codigo_y_la_del_check_son_la_misma() -> None:
    import pathlib

    from app.core.causales_reserva import CITA_NORMA_PATRON

    sql = (pathlib.Path(__file__).parents[1] / "migrations/tenant/051_reserva_tope_y_norma.sql").read_text()
    assert f"'{CITA_NORMA_PATRON}'" in sql
