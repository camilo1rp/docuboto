# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`verify_audit_chains` anota el resultado de cada corrida (C18, ADR-041).

Mutaciones que deben caer (descritas, no ejecutadas):

* anotar `completo` aunque se acote con `--slug`/`--excluir-prefijo` →
  `test_una_corrida_acotada_se_anota_parcial`;
* que un fallo del INSERT cambie el código de salida del respaldo →
  `test_un_fallo_al_anotar_no_cambia_el_codigo_de_salida`;
* no contar las roturas / retrocesos / anclas alteradas →
  `test_las_estadisticas_cuentan_roturas_retrocesos_y_anclas`;
* anotar una salida temprana (objetivo vacío) como si hubiera verificado →
  `test_sin_estadisticas_no_se_anota`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from orpycamcp_common.audit import ResultadoCadena, RoturaCadena

from app.ops import verify_audit_chains as vac

_ESTAD = {
    "tenants": 2,
    "asientos": 40,
    "roturas_nuevas": 0,
    "roturas_declaradas": 1,
    "retrocesos": 0,
    "anclas_alteradas": 0,
}


async def test_registrar_inserta_una_fila_con_la_forma_de_la_tabla() -> None:
    conn = AsyncMock()
    inicio = vac.datetime.now(vac.UTC)
    ok = await vac.registrar_corrida(
        conn, inicio=inicio, alcance="completo", origen="backup", estadisticas=_ESTAD, codigo_salida=0
    )
    assert ok is True
    sql, *args = conn.execute.await_args.args
    assert "INSERT INTO public.audit_chain_runs" in sql
    assert args[0] == inicio and args[2] == "completo" and args[3] == "backup"
    assert args[4:10] == [2, 40, 0, 1, 0, 0] and args[10] == 0


async def test_sin_estadisticas_no_se_anota() -> None:
    conn = AsyncMock()
    ok = await vac.registrar_corrida(
        conn, inicio=vac.datetime.now(vac.UTC), alcance="completo", origen="manual", estadisticas={}, codigo_salida=2
    )
    assert ok is False
    conn.execute.assert_not_awaited()


async def test_un_fallo_al_anotar_no_lanza_y_avisa(capsys: pytest.CaptureFixture[str]) -> None:
    conn = AsyncMock()
    conn.execute.side_effect = RuntimeError("permission denied")
    ok = await vac.registrar_corrida(
        conn, inicio=vac.datetime.now(vac.UTC), alcance="completo", origen="cron", estadisticas=_ESTAD, codigo_salida=0
    )
    assert ok is False
    assert "audit_chain_runs" in capsys.readouterr().err


class _FakeConn:
    def __init__(self, falla: bool = False) -> None:
        self.inserts: list[tuple[Any, ...]] = []
        self.falla = falla

    async def execute(self, sql: str, *args: Any) -> str:
        if self.falla:
            raise RuntimeError("tabla ausente")
        self.inserts.append(args)
        return "INSERT 0 1"

    async def close(self) -> None:
        return None


def _parchear(monkeypatch: pytest.MonkeyPatch, conn: _FakeConn, codigo: int, stats: dict[str, int]) -> None:
    async def conectar(*a: Any, **k: Any) -> _FakeConn:
        return conn

    async def verificar(c: Any, **kw: Any) -> tuple[int, list[str], list[str]]:
        kw["estadisticas"].update(stats)
        return codigo, [], []

    monkeypatch.setattr(vac.asyncpg, "connect", conectar)
    monkeypatch.setattr(vac, "verificar", verificar)


@pytest.mark.parametrize(
    "argv,alcance",
    [
        ([], "completo"),
        (["--slug", "demo"], "parcial"),
        (["--excluir-prefijo", "itest"], "parcial"),
        (["--slug", "a", "--excluir-prefijo", "itest"], "parcial"),
    ],
)
async def test_una_corrida_acotada_se_anota_parcial(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], alcance: str
) -> None:
    conn = _FakeConn()
    _parchear(monkeypatch, conn, 0, dict(_ESTAD))
    codigo = await vac._main(vac._argumentos(["--anclas", "", *argv, "--origen", "restore"]))
    assert codigo == 0
    [fila] = conn.inserts
    assert fila[2] == alcance and fila[3] == "restore" and fila[10] == 0


async def test_el_origen_por_defecto_es_manual() -> None:
    assert vac._argumentos([]).origen == "manual"
    with pytest.raises(SystemExit):
        vac._argumentos(["--origen", "inventado"])


async def test_un_fallo_al_anotar_no_cambia_el_codigo_de_salida(monkeypatch: pytest.MonkeyPatch) -> None:
    _parchear(monkeypatch, _FakeConn(falla=True), 1, dict(_ESTAD))
    assert await vac._main(vac._argumentos([])) == 1  # el 1 de la verificación, no otro
    _parchear(monkeypatch, _FakeConn(falla=True), 0, dict(_ESTAD))
    assert await vac._main(vac._argumentos([])) == 0  # y un 0 sigue siendo 0


async def test_una_salida_temprana_no_deja_fila(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn()
    _parchear(monkeypatch, conn, 2, {})  # objetivo vacío: sin estadísticas
    assert await vac._main(vac._argumentos([])) == 2
    assert conn.inserts == []


# ── verificar() rellena las estadísticas con lo que pasó de verdad ───────────────


def _cadena(roturas: tuple[RoturaCadena, ...] = (), omitidas: int = 0) -> ResultadoCadena:
    return ResultadoCadena(total=10, roturas=roturas, roturas_omitidas=omitidas)


class _Db:
    """fetchrow/fetchval con la forma real de _extremo y del ancla."""

    def __init__(self, hash_anclado: str | None = "h1", asientos: int = 10) -> None:
        self.hash_anclado = hash_anclado
        self.asientos = asientos

    async def fetchrow(self, sql: str, *a: Any) -> dict[str, Any]:
        return {"n": self.asientos, "ultimo_id": 9}

    async def fetchval(self, sql: str, *a: Any) -> Any:
        return self.hash_anclado


async def test_las_estadisticas_cuentan_roturas_retrocesos_y_anclas(monkeypatch: pytest.MonkeyPatch) -> None:
    rotura = RoturaCadena(id=5, motivo="hash")
    resultados = {
        "limpio": _cadena(),
        "declarada": _cadena((rotura,)),
        "nueva": _cadena((RoturaCadena(id=7, motivo="hash"),), omitidas=2),
        "retro": _cadena(),
        "ancla": _cadena(),
    }

    async def verify_chain(conn: Any, slug: str) -> ResultadoCadena:
        return resultados[slug]

    monkeypatch.setattr(vac, "_audit", SimpleNamespace(verify_chain=verify_chain))
    anclas = {
        "retro": vac.Ancla("2026-01-01", "retro", 999, 9, "h1"),  # el conteo bajó
        "ancla": vac.Ancla("2026-01-01", "ancla", 1, 9, "OTRO"),  # el hash anclado cambió
    }
    est: dict[str, int] = {}
    codigo, _msgs, nuevas = await vac.verificar(
        _Db(),  # type: ignore[arg-type]
        anclas=anclas,
        slugs=["limpio", "declarada", "nueva", "retro", "ancla"],
        roturas_declaradas={"declarada": frozenset({5})},
        estadisticas=est,
    )
    assert codigo == 1 and nuevas == []
    assert est["tenants"] == 5
    assert est["roturas_nuevas"] == 3  # 1 enumerada + 2 omitidas
    assert est["roturas_declaradas"] == 1
    assert est["retrocesos"] == 1 and est["anclas_alteradas"] == 1
    assert est["asientos"] == 40  # limpio, declarada, retro y ancla (la rota por hash no llega a contarse)


async def test_una_cadena_integra_deja_las_estadisticas_en_cero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def verify_chain(conn: Any, slug: str) -> ResultadoCadena:
        return _cadena()

    monkeypatch.setattr(vac, "_audit", SimpleNamespace(verify_chain=verify_chain))
    est: dict[str, int] = {}
    codigo, _m, _n = await vac.verificar(_Db(), anclas={}, slugs=["a", "b"], estadisticas=est)  # type: ignore[arg-type]
    assert codigo == 0
    assert (est["roturas_nuevas"], est["roturas_declaradas"], est["retrocesos"], est["anclas_alteradas"]) == (0, 0, 0, 0)
    assert est["tenants"] == 2 and est["asientos"] == 20
