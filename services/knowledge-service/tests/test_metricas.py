# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La señal `intelligence` suma sin identidad y el cero medido no es un fallo."""

from __future__ import annotations

from typing import Any

import pytest
from orpycamcp_common.metricas import MetricaInvalida

from app.core import medidor


def _valor(muestras: list[Any], familia: str, **etiquetas: str) -> float:
    for muestra in muestras:
        if muestra.familia == familia and dict(muestra.etiquetas) == etiquetas:
            return muestra.valor
    raise AssertionError(familia)


def test_sin_filas_el_cero_se_emite() -> None:
    muestras = medidor.muestras_de({}, {}, 0, 0)
    assert len(muestras) == 9
    aceptados = _valor(
        muestras, "orpycamcp_intelligence_sends", operation="push", result="accepted"
    )
    assert aceptados == 0
    assert _valor(muestras, "orpycamcp_intelligence_barrier_denials", reason="nivel") == 0
    assert _valor(muestras, "orpycamcp_intelligence_fallbacks") == 0
    assert _valor(muestras, "orpycamcp_intelligence_suspensions") == 0


def test_un_resultado_ajeno_no_se_publica() -> None:
    with pytest.raises(MetricaInvalida):
        medidor.muestras_de({("push", "otro"): 1}, {}, 0, 0)


def test_anotar_rechaza_un_motivo_fuera_del_registro() -> None:
    with pytest.raises(ValueError):
        medidor._comprobar("barrier", "local")


class _Conn:
    def __init__(self, tablas: dict[str, list[dict[str, Any]]], schemas: list[str]) -> None:
        self.tablas = tablas
        self.schemas = schemas
        self.sql: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql.append(sql)
        if "public.tenants" in sql:
            return [{"slug": s.removeprefix("tenant_")} for s in self.schemas]
        if "intelligence_send_log" in sql:
            return self.tablas["envios"]
        if "intelligence_meter" in sql:
            return self.tablas["contadores"]
        if "intelligence_state" in sql:
            return self.tablas["suspensiones"]
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_suma_entidades_y_califica_el_schema() -> None:
    conn = _Conn(
        {
            "envios": [
                {"operation": "push", "result": "accepted", "n": 2},
                {"operation": "push", "result": "accepted", "n": 1},
                {"operation": "purge", "result": "error", "n": 4},
            ],
            "contadores": [
                {"kind": "barrier", "reason": "nivel", "n": 3},
                {"kind": "fallback", "reason": "local", "n": 5},
            ],
            "suspensiones": [{"n": 1}, {"n": 0}],
        },
        ["tenant_demo", "tenant_otra"],
    )
    muestras = await medidor.medir(conn)  # type: ignore[arg-type]
    aceptados = _valor(
        muestras, "orpycamcp_intelligence_sends", operation="push", result="accepted"
    )
    assert aceptados == 3
    assert _valor(muestras, "orpycamcp_intelligence_sends", operation="purge", result="error") == 4
    assert _valor(muestras, "orpycamcp_intelligence_barrier_denials", reason="nivel") == 3
    assert _valor(muestras, "orpycamcp_intelligence_barrier_denials", reason="ausente") == 0
    assert _valor(muestras, "orpycamcp_intelligence_fallbacks") == 5
    assert _valor(muestras, "orpycamcp_intelligence_suspensions") == 1
    assert all('"tenant_demo"' in sql and '"tenant_otra"' in sql for sql in conn.sql[1:])
    assert "search_path" not in " ".join(conn.sql)


@pytest.mark.asyncio
async def test_un_contador_ajeno_deja_la_medicion_invalida() -> None:
    conn = _Conn(
        {
            "envios": [],
            "contadores": [{"kind": "barrier", "reason": "local", "n": 1}],
            "suspensiones": [],
        },
        ["tenant_demo"],
    )
    with pytest.raises(MetricaInvalida):
        await medidor.medir(conn)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_anotar_no_propaga_un_fallo_de_base() -> None:
    class _Rota:
        async def execute(self, sql: str, *args: Any) -> str:
            raise RuntimeError("tabla ausente")

    await medidor.anotar(_Rota(), "fallback", "local")
