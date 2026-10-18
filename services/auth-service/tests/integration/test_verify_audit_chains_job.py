# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`verify_audit_chains` contra Postgres REAL (AREA-03-02).

El defecto: `backup.sh` anclaba el extremo de la cadena sin verificarla (podía
sellar una cadena YA rota) y `restore.sh` desempaquetaba `verify_chain` como una
tupla cuando devuelve un `ResultadoCadena`. Estos tests fijan que el job:

* (a) cadena íntegra -> código 0 y emite el ancla;
* (b) un asiento con `prev_hash`/hash incoherente -> código 1 y NO emite ancla;
* (c) ancla con más asientos que los reales (retroceso) -> código 1;
* (d) el hash del asiento anclado cambió -> código 1.

Todo ocurre dentro de una transacción que SIEMPRE se revierte: `audit_log` es
append-only y una fila rota insertada de verdad contaminaría para siempre la base
(y haría fallar el respaldo de cualquier entorno que comparta esa base).

Mutación que lo detecta: que `verificar` devuelva siempre 0, o que emita el ancla
antes de comparar -> (b), (c) y (d) caen.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from orpycamcp_common import audit

from app.ops import verify_audit_chains as job
from app.ops.verify_audit_chains import Ancla

pytestmark = pytest.mark.asyncio

_HASH_JUGUETE = "ab" * 32  # forma válida (64 hex) pero que no es el sello de nada


@pytest_asyncio.fixture
async def tx(db_conn: asyncpg.Connection) -> AsyncGenerator[asyncpg.Connection, None]:
    t = db_conn.transaction()
    await t.start()
    try:
        yield db_conn
    finally:
        await t.rollback()


async def _cadena(conn: asyncpg.Connection, n: int = 3) -> tuple[str, list[str]]:
    slug = f"itest-vac-{uuid.uuid4().hex[:8]}"
    hashes = [
        await audit.append(
            conn,
            tenant_slug=slug,
            service="auth-service",
            action=f"test.accion{i}",
            object_type="test",
            object_ref=str(i),
            actor="test",
            canal="api",
        )
        for i in range(n)
    ]
    return slug, hashes


async def test_cadena_integra_devuelve_cero_y_emite_el_ancla(tx: asyncpg.Connection) -> None:
    slug, hashes = await _cadena(tx, 3)
    codigo, _, nuevas = await job.verificar(tx, anclas={}, slugs=[slug], fecha_ancla="F1")
    assert codigo == 0
    assert len(nuevas) == 1
    fecha, s, n, ultimo_id, ultimo_hash = nuevas[0].split("\t")
    assert (fecha, s, int(n), ultimo_hash) == ("F1", slug, 3, hashes[-1])
    assert int(ultimo_id) > 0


async def test_una_cadena_rota_falla_y_no_se_emite_ancla(tx: asyncpg.Connection) -> None:
    slug, _ = await _cadena(tx, 2)
    # Un asiento cuyo hash y prev_hash no son los de la cadena: forma válida (pasa
    # los CHECK) y contenido que nada selló.
    await tx.execute(
        "INSERT INTO public.audit_log (tenant_slug, service, action, object_type, prev_hash, hash) "
        "VALUES ($1, 'test', 'x', 'y', $2, $2)",
        slug,
        _HASH_JUGUETE,
    )
    codigo, mensajes, nuevas = await job.verificar(tx, anclas={}, slugs=[slug], fecha_ancla="F1")
    assert codigo == 1
    assert nuevas == [], "no se puede sellar como extremo de confianza una cadena rota"
    assert any("CADENA ROTA" in m for m in mensajes)


async def test_un_retroceso_respecto_al_ancla_falla(tx: asyncpg.Connection) -> None:
    slug, hashes = await _cadena(tx, 2)
    ancla = Ancla("F0", slug, asientos=5, ultimo_id=1, ultimo_hash=hashes[-1])
    codigo, mensajes, nuevas = await job.verificar(tx, anclas={slug: ancla}, slugs=[slug], fecha_ancla="F1")
    assert codigo == 1 and nuevas == []
    assert any("RETROCEDIÓ" in m for m in mensajes)


async def test_el_asiento_anclado_con_otro_hash_falla(tx: asyncpg.Connection) -> None:
    slug, hashes = await _cadena(tx, 2)
    ultimo_id = await tx.fetchval("SELECT max(id) FROM public.audit_log WHERE tenant_slug = $1", slug)
    ancla = Ancla("F0", slug, asientos=2, ultimo_id=ultimo_id, ultimo_hash=_HASH_JUGUETE)
    codigo, mensajes, nuevas = await job.verificar(tx, anclas={slug: ancla}, slugs=[slug], fecha_ancla="F1")
    assert codigo == 1 and nuevas == []
    assert any("cambió de hash" in m for m in mensajes)


async def test_control_positivo_un_ancla_coherente_con_mas_asientos_pasa(tx: asyncpg.Connection) -> None:
    """Sin este control, un job que rechaza SIEMPRE pasaría los tres rojos de arriba."""
    slug, hashes = await _cadena(tx, 2)
    ultimo_id = await tx.fetchval("SELECT max(id) FROM public.audit_log WHERE tenant_slug = $1", slug)
    ancla = Ancla("F0", slug, asientos=2, ultimo_id=ultimo_id, ultimo_hash=hashes[-1])
    await audit.append(
        tx, tenant_slug=slug, service="auth-service", action="test.otra", object_type="test", actor="t", canal="api"
    )
    codigo, _, nuevas = await job.verificar(tx, anclas={slug: ancla}, slugs=[slug], fecha_ancla="F1")
    assert codigo == 0 and len(nuevas) == 1


async def test_parsear_anclas_toma_el_ultimo_y_rechaza_lo_ilegible() -> None:
    texto = (
        "fecha\ttenant_slug\tasientos\tultimo_id\tultimo_hash\n"
        "F1\ta\t1\t10\th1\nF2\ta\t3\t30\th3\nF2\tb\t2\t20\th2\n"
    )
    anclas = job.parsear_anclas(texto)
    assert anclas["a"].asientos == 3 and anclas["b"].ultimo_id == 20
    with pytest.raises(job.AnclaIlegible):
        job.parsear_anclas("F1\ta\t1\n")
    with pytest.raises(job.AnclaIlegible):
        job.parsear_anclas("F1\ta\tmuchos\t10\th\n")


async def test_cli_un_ancla_ilegible_o_un_retroceso_salen_con_codigo_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Extremo a extremo por `main()`: el código de salida es lo que lee el shell."""
    slug = f"itest-cli-{uuid.uuid4().hex[:8]}"
    ancla = tmp_path / "anclas.tsv"
    ancla.write_text(f"F0\t{slug}\t5\t99\t{_HASH_JUGUETE}\n")
    assert await job._main(job._argumentos(["--anclas", str(ancla), "--slug", slug])) == 1
    assert capsys.readouterr().out == "", "con un retroceso no se imprime ninguna línea de ancla"

    ancla.write_text("basura sin tabuladores\n")
    assert await job._main(job._argumentos(["--anclas", str(ancla), "--slug", slug])) == 1

    # Control positivo: sin ancla previo y un slug sin asientos, el job pasa y no emite nada.
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert await job._main(job._argumentos(["--anclas", "-", "--slug", slug])) == 0


def test_el_proceso_real_propaga_el_codigo_de_salida_y_no_imprime_ancla(tmp_path: Path) -> None:
    """`backup.sh` lee el código del PROCESO (`if ! NUEVAS="$(... python -m ...)"`), no el
    retorno de `_main`. Si `__main__` llamara `main()` sin `sys.exit`, una cadena que
    retrocedió saldría con 0 y el respaldo anclaría igual. Mutación: quitar `sys.exit(`
    del bloque `__main__` -> este test cae (los de `_main` en proceso no lo ven)."""
    import os
    import subprocess
    import sys

    slug = f"itest-proc-{uuid.uuid4().hex[:8]}"
    ancla = tmp_path / "anclas.tsv"
    ancla.write_text(f"F0\t{slug}\t5\t99\t{_HASH_JUGUETE}\n")
    app_dir = Path(job.__file__).resolve().parents[2]
    r = subprocess.run(
        [sys.executable, "-m", "app.ops.verify_audit_chains", "--anclas", str(ancla), "--slug", slug, "--emitir-ancla", "F1"],
        cwd=app_dir,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert r.returncode == 1, f"un retroceso debe salir con 1 desde el proceso real: {r.returncode}\n{r.stderr}"
    assert r.stdout == "", f"con un fallo no se emite ninguna línea de ancla por stdout: {r.stdout!r}"
    assert slug in r.stderr and "RETROCEDI" in r.stderr
    # B10 ítem 8: sin `--slug` el subproceso recorría las ~23.000 cadenas de la base viva.
    # Fija que recorre SOLO su objetivo.
    lineas = [ln for ln in r.stderr.splitlines() if ln.startswith(("   ✓", "   ✗", "   !"))]
    assert len(lineas) == 1 and slug in lineas[0], lineas


# ── Línea base de roturas históricas declaradas (T14, hallazgo de auditoría) ──────
# `audit_log` es de solo-anexar: una rotura vieja no se repara jamás. Sin línea base,
# una sola fila mala dejaba a la entidad sin respaldo sellado para siempre.


async def _rota(tx: asyncpg.Connection, slug: str) -> int:
    return int(
        await tx.fetchval(
            "INSERT INTO public.audit_log (tenant_slug, service, action, object_type, prev_hash, hash) "
            "VALUES ($1, 'test', 'x', 'y', $2, $2) RETURNING id",
            slug,
            _HASH_JUGUETE,
        )
    )


async def test_una_rotura_declarada_pasa_y_emite_el_ancla(tx: asyncpg.Connection) -> None:
    slug, _ = await _cadena(tx, 2)
    rota = await _rota(tx, slug)
    codigo, mensajes, nuevas = await job.verificar(
        tx, anclas={}, slugs=[slug], fecha_ancla="F1", roturas_declaradas={slug: frozenset({rota})}
    )
    assert codigo == 0, mensajes
    assert len(nuevas) == 1, "con la rotura declarada y sin roturas nuevas el ancla SÍ se emite"
    assert any("HISTÓRICAS declaradas" in m for m in mensajes), "la rotura declarada se informa, no se calla"


async def test_una_rotura_nueva_fuera_de_la_linea_base_falla(tx: asyncpg.Connection) -> None:
    slug, _ = await _cadena(tx, 2)
    vieja = await _rota(tx, slug)
    nueva = await _rota(tx, slug)
    codigo, mensajes, nuevas = await job.verificar(
        tx, anclas={}, slugs=[slug], fecha_ancla="F1", roturas_declaradas={slug: frozenset({vieja})}
    )
    assert codigo == 1 and nuevas == []
    assert any(f"id={nueva}" in m for m in mensajes)


async def test_la_declaracion_de_otro_tenant_no_cubre_este(tx: asyncpg.Connection) -> None:
    slug, _ = await _cadena(tx, 2)
    rota = await _rota(tx, slug)
    codigo, _, nuevas = await job.verificar(
        tx, anclas={}, slugs=[slug], fecha_ancla="F1", roturas_declaradas={"otro": frozenset({rota})}
    )
    assert codigo == 1 and nuevas == []


async def test_la_linea_base_no_tolera_un_retroceso(tx: asyncpg.Connection) -> None:
    slug, hashes = await _cadena(tx, 2)
    rota = await _rota(tx, slug)
    ancla = Ancla("F0", slug, asientos=50, ultimo_id=1, ultimo_hash=hashes[-1])
    codigo, mensajes, _ = await job.verificar(
        tx, anclas={slug: ancla}, slugs=[slug], roturas_declaradas={slug: frozenset({rota})}
    )
    assert codigo == 1 and any("RETROCEDIÓ" in m for m in mensajes)


async def test_parsear_roturas_declaradas() -> None:
    texto = "20260101\ta\t1\t10\th1\nROTURA\ta\t41004\tincidente 2026\nROTURA\tb\t7\n"
    assert job.parsear_roturas_declaradas(texto) == {"a": frozenset({41004}), "b": frozenset({7})}
    assert job.parsear_anclas(texto)["a"].asientos == 1, "las líneas ROTURA no se leen como ancla"
    with pytest.raises(job.AnclaIlegible):
        job.parsear_roturas_declaradas("ROTURA\ta\tNOID\n")


async def test_main_cablea_la_linea_base_desde_stdin_y_desde_fichero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """El cableado, no solo la pieza: `_main` debe entregar a `verificar` las roturas
    declaradas que llegan mezcladas en el stdin del ancla (backup.sh no monta ficheros)
    y las del fichero de `--roturas-declaradas`."""
    visto: dict[str, object] = {}

    async def _falso(conn: object, **kw: object) -> tuple[int, list[str], list[str]]:
        visto.update(kw)
        return 0, [], []

    monkeypatch.setattr(job, "verificar", _falso)
    base = tmp_path / "base.tsv"
    base.write_text("ROTURA\tb\t7\tnota\n")
    monkeypatch.setattr("sys.stdin", io.StringIO("ROTURA\ta\t41004\tincidente\n"))
    codigo = await job._main(job._argumentos(["--anclas", "-", "--slug", "x", "--roturas-declaradas", str(base)]))
    assert codigo == 0
    assert visto["roturas_declaradas"] == {"a": frozenset({41004}), "b": frozenset({7})}


async def test_roturas_no_enumerables_no_las_cubre_la_linea_base(tx: asyncpg.Connection) -> None:
    """Más roturas que el tope de `verify_chain`: las omitidas no se pueden comparar con
    la lista declarada, así que NO se toleran aunque se declaren todas las enumeradas."""
    slug, _ = await _cadena(tx, 1)
    await tx.execute(
        "INSERT INTO public.audit_log (tenant_slug, service, action, object_type, prev_hash, hash) "
        "SELECT $1, 'test', 'x', 'y', $2, $2 FROM generate_series(1, $3)",
        slug,
        _HASH_JUGUETE,
        audit.MAX_ROTURAS_REPORTADAS + 1,
    )
    res = await audit.verify_chain(tx, slug)
    assert res.roturas_omitidas >= 1
    declaradas = {slug: frozenset(r.id for r in res.roturas)}
    codigo, _, nuevas = await job.verificar(
        tx, anclas={}, slugs=[slug], fecha_ancla="F1", roturas_declaradas=declaradas
    )
    assert codigo == 1 and nuevas == []


async def test_un_filtro_que_deja_cero_tenants_sale_con_2(
    tx: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, _ = await _cadena(tx, 1)
    codigo, mensajes, nuevas = await job.verificar(tx, anclas={}, slugs=[], fecha_ancla="F1")
    assert codigo == 2 and nuevas == [] and any("objetivo vacío" in m for m in mensajes)

    # `_slugs` hace un DISTINCT sobre TODO `audit_log`: se sustituye para no recorrer la base viva.
    async def _solo_el_mio(_conn: asyncpg.Connection) -> list[str]:
        return [slug]

    monkeypatch.setattr(job, "_slugs", _solo_el_mio)
    codigo, _, _ = await job.verificar(tx, anclas={}, excluir_prefijos=(slug[:4], "itest", ""), fecha_ancla="F1")
    assert codigo == 2, "excluir todo (prefijo vacío) deja el objetivo en cero"


async def test_sin_filtro_y_sin_tenants_solo_pasa_con_el_permiso_explicito(
    tx: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _nadie(_conn: asyncpg.Connection) -> list[str]:
        return []

    monkeypatch.setattr(job, "_slugs", _nadie)
    codigo, _, _ = await job.verificar(tx, anclas={})
    assert codigo == 2
    codigo, _, _ = await job.verificar(tx, anclas={}, permitir_objetivo_vacio=True)
    assert codigo == 0
