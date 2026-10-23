# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El cerrojo de migraciones, sin base: reintento, plazo, mensaje y liberación (C19).

El comportamiento contra Postgres real (dos sesiones) vive en
`services/auth-service/tests/integration/test_cerrojo_migraciones.py`.

Mutaciones que deben caer (descritas, no ejecutadas): no liberar en el `finally`;
ignorar el plazo (esperar para siempre); aceptar `MIGRATIONS_LOCK_TIMEOUT_S` basura.
"""

from __future__ import annotations

from typing import Any

import pytest

from orpycamcp_common import migrations as ledger


class _Conn:
    """`fetchval` devuelve, por turno, lo que pg_try_advisory_lock respondería."""

    def __init__(self, respuestas: list[bool]) -> None:
        self.respuestas = list(respuestas)
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.intentos = 0

    async def fetchval(self, sql: str, *a: Any) -> Any:
        if "pg_try_advisory_lock" in sql:
            self.intentos += 1
            return self.respuestas.pop(0) if self.respuestas else False
        return 4321  # pid del que lo tiene

    async def execute(self, sql: str, *a: Any) -> str:
        self.executes.append((sql, a))
        return "SELECT 1"


async def test_toma_el_cerrojo_y_lo_suelta_al_salir() -> None:
    c = _Conn([True])
    async with ledger.cerrojo(c, timeout_s=1):  # type: ignore[arg-type]
        assert c.executes == []
    assert [e[0] for e in c.executes] == ["SELECT pg_advisory_unlock($1)"]
    assert c.executes[0][1] == (ledger.CERROJO_MIGRACIONES,)


async def test_lo_suelta_aunque_el_cuerpo_falle() -> None:
    c = _Conn([True])
    with pytest.raises(ZeroDivisionError):
        async with ledger.cerrojo(c, timeout_s=1):  # type: ignore[arg-type]
            1 / 0  # noqa: B018
    assert len(c.executes) == 1


async def test_reintenta_hasta_obtenerlo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, "_ESPERA_ENTRE_INTENTOS_S", 0.001)
    c = _Conn([False, False, True])
    async with ledger.cerrojo(c, timeout_s=5):  # type: ignore[arg-type]
        pass
    assert c.intentos == 3


async def test_vencido_el_plazo_falla_con_mensaje_legible_y_sin_soltar_lo_ajeno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ledger, "_ESPERA_ENTRE_INTENTOS_S", 0.001)
    c = _Conn([])  # siempre ocupado
    with pytest.raises(RuntimeError) as exc:
        async with ledger.cerrojo(c, timeout_s=0.05):  # type: ignore[arg-type]
            pytest.fail("no debió entrar")  # pragma: no cover
    msg = str(exc.value)
    assert "otra migración tiene el cerrojo" in msg and "pid 4321" in msg
    assert "MIGRATIONS_LOCK_TIMEOUT_S" in msg
    assert c.executes == []  # nunca lo tuvo: no hace unlock de un cerrojo ajeno


def test_el_plazo_sale_del_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIGRATIONS_LOCK_TIMEOUT_S", raising=False)
    assert ledger.plazo_cerrojo_s() == 600.0
    monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT_S", "45")
    assert ledger.plazo_cerrojo_s() == 45.0
    for malo in ("abc", "0", "-3"):
        monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT_S", malo)
        with pytest.raises(RuntimeError):
            ledger.plazo_cerrojo_s()


def test_la_constante_es_un_bigint_positivo_y_estable() -> None:
    assert 0 < ledger.CERROJO_MIGRACIONES < 2**63
    assert ledger.CERROJO_MIGRACIONES == 0x4F52505943414D31  # «ORPYCAM1»; cambiarla rompe la exclusión mutua entre versiones
