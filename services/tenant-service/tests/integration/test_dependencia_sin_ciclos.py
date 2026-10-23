# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Migración 015 (C-4/C8, ADR-040) contra Postgres REAL: el organigrama es un ÁRBOL.

Lo que un mock no puede decir: que el trigger rechaza A -> B -> A en el motor, que la migración
ABORTA nombrando los códigos si ya hay un ciclo, y que el candado consultivo serializa dos
cambios concurrentes que, cada uno por su lado, parecen inocentes.

Mutaciones (verificadas):
- quitar la CTE recursiva del trigger (dejar solo `NEW.parent_id = NEW.id`) →
  `test_un_ciclo_de_dos_y_de_tres_se_rechaza` cae;
- quitar `pg_advisory_xact_lock` →
  `test_dos_cambios_concurrentes_que_cierran_un_ciclo_no_pasan_los_dos` cae (ambos confirman);
- quitar el DO de verificación previa → `test_la_015_aborta_si_ya_hay_un_ciclo_y_dice_cuales` cae.

Esquemas desechables `tenant_itest_*`.
"""

from __future__ import annotations

import asyncio
import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio

from app.repositories.dependencias_repository import DependenciaRepository
from migrations import runner

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1", reason="carril de integración: RUN_INTEGRATION=1"
)

_RAW = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
_OWNER_DSN = _RAW.replace("postgresql+asyncpg://", "postgresql://")


def _migracion(num: str) -> str:
    (ruta,) = [p for p in runner._sql_files(runner.TENANT_DIR) if p.name.startswith(num)]
    return ruta.read_text()


def _validar_host() -> None:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    if host not in {"postgres", "localhost", "127.0.0.1"}:
        raise RuntimeError(f"host {host!r} fuera de la allowlist de integración")


@pytest_asyncio.fixture
async def schema() -> AsyncGenerator[str, None]:
    """Un schema con TODAS las migraciones de tenant, para varias conexiones."""
    _validar_host()
    nombre = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        await c.execute(f'CREATE SCHEMA "{nombre}"')
        await c.execute(f'SET search_path = "{nombre}"')
        for ruta in runner._sql_files(runner.TENANT_DIR):
            await c.execute(ruta.read_text())
        yield nombre
    finally:
        assert nombre.startswith("tenant_itest_")
        await c.execute("RESET search_path")
        await c.execute(f'DROP SCHEMA IF EXISTS "{nombre}" CASCADE')
        await c.close()


async def _conectar(schema: str) -> asyncpg.Connection:
    c = await asyncpg.connect(_OWNER_DSN)
    await c.execute(f'SET search_path = "{schema}"')
    return c


@pytest_asyncio.fixture
async def conn(schema: str) -> AsyncGenerator[asyncpg.Connection, None]:
    c = await _conectar(schema)
    try:
        yield c
    finally:
        await c.close()


async def _dep(c: asyncpg.Connection, codigo: int, parent: uuid.UUID | None = None) -> uuid.UUID:
    return await c.fetchval(
        "INSERT INTO dependencias (codigo, sigla, nombre, parent_id) VALUES ($1, $2, $3, $4) RETURNING id",
        codigo,
        f"D{codigo}",
        f"Dependencia {codigo}",
        parent,
    )


async def _padre(c: asyncpg.Connection, codigo: int) -> uuid.UUID | None:
    return await c.fetchval("SELECT parent_id FROM dependencias WHERE codigo = $1", codigo)


# ── el trigger ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_ciclo_de_dos_y_de_tres_se_rechaza(conn: asyncpg.Connection) -> None:
    a = await _dep(conn, 901)
    b = await _dep(conn, 902, a)
    c = await _dep(conn, 903, b)
    # A -> B -> A
    with pytest.raises(asyncpg.CheckViolationError, match="dependencia_ciclo"):
        await conn.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", b, a)
    # A -> B -> C -> A
    with pytest.raises(asyncpg.CheckViolationError, match="dependencia_ciclo"):
        await conn.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", c, a)
    # un nodo no es su propio padre
    with pytest.raises(asyncpg.CheckViolationError, match="dependencia_ciclo"):
        await conn.execute("UPDATE dependencias SET parent_id = id WHERE id = $1", a)
    # nada quedó cambiado
    assert await _padre(conn, 901) is None


@pytest.mark.asyncio
async def test_lo_legitimo_sigue_pasando(conn: asyncpg.Connection) -> None:
    """Control positivo: mover una rama a otro padre, volver a raíz o crear hijos NO se
    confunde con un ciclo (un trigger que rechazara todo pasaría el test de arriba)."""
    a = await _dep(conn, 901)
    b = await _dep(conn, 902, a)
    c = await _dep(conn, 903)
    await conn.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", c, b)  # mover rama
    assert await _padre(conn, 902) == c
    await conn.execute("UPDATE dependencias SET parent_id = NULL WHERE id = $1", b)  # volver a raíz
    assert await _padre(conn, 902) is None
    await conn.execute("UPDATE dependencias SET nombre = 'Renombrada' WHERE id = $1", a)  # otras columnas
    await _dep(conn, 904, b)  # alta bajo un padre existente


@pytest.mark.asyncio
async def test_un_arbol_con_un_ciclo_preexistente_no_cuelga_el_trigger(conn: asyncpg.Connection) -> None:
    """Defensa: con el trigger deshabilitado se fuerza A <-> B; una inserción posterior bajo
    otra rama no debe quedarse recorriendo el ciclo para siempre (la CTE usa UNION)."""
    a = await _dep(conn, 901)
    b = await _dep(conn, 902, a)
    await conn.execute("ALTER TABLE dependencias DISABLE TRIGGER trg_dependencias_sin_ciclos")
    await conn.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", b, a)
    await conn.execute("ALTER TABLE dependencias ENABLE TRIGGER trg_dependencias_sin_ciclos")
    c = await _dep(conn, 903)
    await asyncio.wait_for(
        conn.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", b, c), timeout=10
    )  # sube por B -> A -> B ... y termina


# ── concurrencia ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dos_cambios_concurrentes_que_cierran_un_ciclo_no_pasan_los_dos(schema: str) -> None:
    """T1: A.parent = B. T2: B.parent = A. Cada una, vista sola, es válida; juntas cierran el
    ciclo. Sin el candado consultivo T2 comprueba contra el estado anterior de T1 (aún sin
    confirmar) y las dos confirman. Con él, T2 espera a T1, ve su cambio y se rechaza."""
    c0 = await _conectar(schema)
    a = await _dep(c0, 901)
    b = await _dep(c0, 902)
    await c0.close()

    async def cambio(parent: uuid.UUID, hijo: uuid.UUID, demora: float) -> str:
        c = await _conectar(schema)
        try:
            await asyncio.sleep(demora)
            async with c.transaction():
                try:
                    await c.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", parent, hijo)
                except asyncpg.CheckViolationError:
                    return "rechazado"
                await asyncio.sleep(0.6)  # T1 retiene el candado mientras T2 intenta
                return "confirmado"
        finally:
            await c.close()

    r1, r2 = await asyncio.gather(cambio(b, a, 0.0), cambio(a, b, 0.2))

    assert sorted([r1, r2]) == ["confirmado", "rechazado"], (r1, r2)
    c1 = await _conectar(schema)
    try:
        pa, pb = await _padre(c1, 901), await _padre(c1, 902)
        assert not (pa == b and pb == a), "el ciclo A <-> B se confirmó"
    finally:
        await c1.close()


# ── la migración ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_015_aborta_si_ya_hay_un_ciclo_y_dice_cuales() -> None:
    _validar_host()
    nombre = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        await c.execute(f'CREATE SCHEMA "{nombre}"')
        await c.execute(f'SET search_path = "{nombre}"')
        for ruta in runner._sql_files(runner.TENANT_DIR):
            if ruta.name < "015":
                await c.execute(ruta.read_text())
        a = await _dep(c, 901)
        b = await _dep(c, 902, a)
        await _dep(c, 903)  # fuera del ciclo
        await c.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", b, a)  # A <-> B

        with pytest.raises(asyncpg.RaiseError, match=r"901.*902|902.*901") as exc:
            await c.execute(_migracion("015"))
        assert "903" not in str(exc.value)  # solo los que están en el ciclo

        # control positivo: rota la arista, la 015 se aplica, y es idempotente
        await c.execute("UPDATE dependencias SET parent_id = NULL WHERE id = $1", a)
        await c.execute(_migracion("015"))
        await c.execute(_migracion("015"))
        with pytest.raises(asyncpg.CheckViolationError, match="dependencia_ciclo"):
            await c.execute("UPDATE dependencias SET parent_id = $1 WHERE id = $2", b, a)
    finally:
        assert nombre.startswith("tenant_itest_")
        await c.execute("RESET search_path")
        await c.execute(f'DROP SCHEMA IF EXISTS "{nombre}" CASCADE')
        await c.close()


# ── el barrido de referencias cubre la columna nueva de archive ───────────────────────────


@pytest.mark.asyncio
async def test_una_dependencia_citada_por_una_trd_no_se_puede_eliminar(conn: asyncpg.Connection) -> None:
    """`trd_serie_dependencia_productora.dependencia_codigo` (archive 070) casa con
    `dependencia\\w*_codigo$`: `referencias()` la cuenta y el DELETE responde 409
    `dependencia_con_historia` SIN tocar tenant-service. Si alguien renombra la columna, esto cae."""
    await conn.execute(
        "CREATE TABLE trd_serie_dependencia_productora ("
        " trd_serie_id uuid NOT NULL, dependencia_codigo integer NOT NULL, dependencia_nombre varchar(100) NOT NULL)"
    )
    await _dep(conn, 901)
    await _dep(conn, 902)
    await conn.execute(
        "INSERT INTO trd_serie_dependencia_productora VALUES (gen_random_uuid(), 901, 'x')"
    )

    repo = DependenciaRepository(conn)
    assert await repo.referencias(901) == {"trd_serie_dependencia_productora.dependencia_codigo": 1}
    assert await repo.referencias(902) == {}  # control: la no citada queda libre
