# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`public` guarda SOLO lo global (ADR-002, addendum C-7 / SEG #12) — contra Postgres REAL.

Cinco cosas que un mock no puede decir:

1. Toda relación de `public` es global: la crea `migrations/global/` (de cualquier servicio),
   es partición de `audit_log` o pertenece a una extensión. Ninguna tabla de TENANT quedó allí
   (hubo 14 copias huérfanas: causales, PINAR, admin_audit...).
2. Ninguna función de `public`/`tenant_*` fija un `search_path` que nombre `public`.
3. Ninguna función de `tenant_*` nombra un objeto global sin calificar en su cuerpo.
4. Todo tipo/opclase de EXTENSIÓN que usan las columnas e índices de `tenant_*` está en la lista
   que conoce el guardián estático (`shared/tests/test_el_search_path_es_solo_el_tenant.py`):
   un `halfvec` o un `gist_trgm_ops` futuro no escapa a la calificación.
5. Fallback cerrado: sin su tabla, un tenant ya NO lee la homónima de `public`; y el runner
   contra un schema inexistente falla con 3F000, nunca crea una tabla en `public`.

Mutaciones: quitar la migración tenant 016 cae en (2); quitar la global 005 cae en (1);
devolver `, public` al runner cae en (5).
"""

from __future__ import annotations

import os
import re
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from migrations import runner

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1", reason="carril de integración: RUN_INTEGRATION=1"
)

_RAW = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
_OWNER_DSN = _RAW.replace("postgresql+asyncpg://", "postgresql://")
_PADRES = Path(__file__).resolve().parents
#: Raíz del repo, si el árbol completo está montado (en el contenedor del servicio no lo está).
_RAIZ = _PADRES[4] if len(_PADRES) > 4 and (_PADRES[4] / "services").is_dir() else None

#: Misma lista que `_EXTENSION` del guardián estático (tipos y opclases de extensión conocidos).
_EXTENSION_CONOCIDA = {
    "vector",
    "gin_trgm_ops",
    "gist_trgm_ops",
    "vector_cosine_ops",
    "vector_l2_ops",
    "vector_ip_ops",
}
_CREA = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|FUNCTION|VIEW|SEQUENCE)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?",
    re.IGNORECASE,
)


def _validar_host() -> None:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    if host not in {"postgres", "localhost", "127.0.0.1"}:
        raise RuntimeError(f"host {host!r} fuera de la allowlist de integración")


def _creados(patron: str) -> set[str]:
    nombres: set[str] = set()
    if _RAIZ is None:
        return nombres
    for f in (_RAIZ / "services").glob(patron):
        for linea in f.read_text(encoding="utf-8").splitlines():
            if not linea.lstrip().startswith("--"):
                nombres.update(m.group(1).lower() for m in _CREA.finditer(linea))
    return nombres


#: Lo global conocido. DENTRO del contenedor de este servicio solo está su propio árbol, así que
#: el censo derivado de `migrations/global/` (todos los servicios) se UNE a esta lista cuando el
#: árbol completo está presente (`shared/tests` lo deriva siempre). Una tabla global nueva se añade
#: aquí: un fallo de este test por ese motivo es la fricción buscada.
_GLOBALES_CONOCIDOS = {
    "tenants",
    "audit_log",
    "audit_chain_runs",
    "auth_audit_logs",
    "divipola_departamento",
    "divipola_municipio",
    "schema_migrations",
    "_migrations",
    "_tenant_migrations",
}


def _globales() -> set[str]:
    return _GLOBALES_CONOCIDOS | _creados("*/migrations/global/*.sql")


@pytest_asyncio.fixture
async def owner() -> AsyncGenerator[asyncpg.Connection, None]:
    _validar_host()
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        yield c
    finally:
        await c.close()


@pytest_asyncio.fixture
async def schema(owner: asyncpg.Connection) -> AsyncGenerator[str, None]:
    nombre = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    await owner.execute(f'CREATE SCHEMA "{nombre}"')
    try:
        await owner.execute(f'SET search_path = "{nombre}"')
        for ruta in runner._sql_files(runner.TENANT_DIR):
            await owner.execute(ruta.read_text())
        yield nombre
    finally:
        assert nombre.startswith("tenant_itest_")
        await owner.execute("RESET search_path")
        await owner.execute(f'DROP SCHEMA IF EXISTS "{nombre}" CASCADE')


@pytest.mark.asyncio
async def test_public_solo_tiene_relaciones_globales(owner: asyncpg.Connection) -> None:
    filas = await owner.fetch(
        """
        SELECT c.relname
          FROM pg_class c
         WHERE c.relnamespace = 'public'::regnamespace
           AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.objid = c.oid AND d.deptype IN ('e', 'a', 'i'))
        """
    )
    permitidas = _globales()
    ajenas = sorted(
        r["relname"]
        for r in filas
        if r["relname"] not in permitidas and not r["relname"].startswith("audit_log")
    )
    assert not ajenas, (
        f"Tablas que NO crea ninguna migración global viven en `public`: {ajenas}. "
        "Son tablas de tenant caídas en `public` por un path cuyo primer schema no existía."
    )


@pytest.mark.asyncio
async def test_ninguna_funcion_fija_un_search_path_con_public(owner: asyncpg.Connection) -> None:
    filas = await owner.fetch(
        """
        SELECT n.nspname, p.proname, p.proconfig
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE (n.nspname = 'public' OR n.nspname LIKE 'tenant\\_%')
           AND p.proconfig IS NOT NULL
        """
    )
    malas = [
        f"{r['nspname']}.{r['proname']}: {r['proconfig']}"
        for r in filas
        if any(c.startswith("search_path=") and re.search(r"(^|[=, ])\"?public\"?($|[, ])", c) for c in r["proconfig"])
    ]
    assert not malas, malas


@pytest.mark.asyncio
async def test_ninguna_funcion_de_tenant_nombra_lo_global_sin_calificar(owner: asyncpg.Connection) -> None:
    nombres = {n for n in _globales() - _creados("*/migrations/tenant/*.sql") if len(n) >= 4 and n != "public"}
    rx = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|REFERENCES|PERFORM)\s+(?<![.\"])\b("
        + "|".join(sorted(nombres))
        + r")\b(?!\.)"
    )
    filas = await owner.fetch(
        "SELECT n.nspname, p.proname, p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname LIKE 'tenant\\_%' AND p.prolang = (SELECT oid FROM pg_language WHERE lanname = 'plpgsql')"
    )
    malas = [f"{r['nspname']}.{r['proname']}" for r in filas if rx.search(r["prosrc"])]
    assert not malas, malas


@pytest.mark.asyncio
async def test_los_tipos_y_opclases_de_extension_en_uso_son_los_que_el_guardian_conoce(
    owner: asyncpg.Connection,
) -> None:
    en_uso = await owner.fetch(
        """
        SELECT DISTINCT t.typname AS nombre
          FROM pg_attribute a
          JOIN pg_class c ON c.oid = a.attrelid AND c.relkind IN ('r', 'p')
          JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname LIKE 'tenant\\_%'
          JOIN pg_type t ON t.oid = a.atttypid
          JOIN pg_depend d ON d.objid = t.oid AND d.deptype = 'e'
         WHERE a.attnum > 0 AND NOT a.attisdropped
        UNION
        SELECT DISTINCT o.opcname
          FROM pg_index x
          JOIN pg_class c ON c.oid = x.indrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname LIKE 'tenant\\_%'
          JOIN pg_opclass o ON o.oid = ANY (x.indclass::oid[])
          JOIN pg_depend d ON d.objid = o.oid AND d.deptype = 'e'
        """
    )
    desconocidas = sorted({r["nombre"] for r in en_uso} - _EXTENSION_CONOCIDA)
    assert not desconocidas, (
        f"Tipos/opclases de extensión en uso que el guardián estático no califica: {desconocidas}. "
        "Añadirlos a `_EXTENSION` en shared/tests/test_el_search_path_es_solo_el_tenant.py "
        "y calificarlos `public.` en el SQL."
    )


@pytest.mark.asyncio
async def test_sin_su_tabla_un_tenant_ya_no_lee_la_de_public(
    owner: asyncpg.Connection, schema: str
) -> None:
    """Antes: `causales` ausente se resolvía contra la copia huérfana de `public`."""
    await owner.execute("DROP TABLE causales CASCADE")
    with pytest.raises(asyncpg.UndefinedTableError):
        await owner.fetchval("SELECT 1 FROM causales LIMIT 1")
    with pytest.raises(asyncpg.UndefinedTableError):
        await owner.fetchval("SELECT 1 FROM tenants LIMIT 1")


@pytest.mark.asyncio
async def test_el_runner_contra_un_schema_inexistente_no_crea_nada_en_public(
    owner: asyncpg.Connection,
) -> None:
    nombre = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    await owner.execute(f'SET search_path = "{nombre}"')
    try:
        with pytest.raises(asyncpg.InvalidSchemaNameError):
            await owner.execute("CREATE TABLE tabla_huerfana_c7 (id int)")
    finally:
        await owner.execute("RESET search_path")
    assert await owner.fetchval("SELECT to_regclass('public.tabla_huerfana_c7')") is None
