# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ningún servicio que conecta a la base arranca antes de migrarla (AREA-13-08).

Los Dockerfile tienen `CMD uvicorn` directo: sin un paso previo, una versión
nueva podía servir tráfico contra un esquema viejo y fallar de forma parcial,
ruta a ruta.

Desde ADR-053 hay UNA cadena (`services/docuboto/migrations`) y UN migrador de
un solo uso, `migrate-docuboto` (imagen de `app`, `command: python
migrations/runner.py`, conexión del propietario). Todo servicio que conecta a
la base lleva `depends_on: migrate-docuboto: condition:
service_completed_successfully`: si el runner falla, el servicio no arranca
(fail-closed). Vale igual en desarrollo y en producción.

C19 (ADR-041): el runner e `init_tenant.py` se serializan con UN cerrojo global
(`orpycamcp_common.migrations.cerrojo`).
"""

from __future__ import annotations

import re

import pytest

from tests._compose import COMPOSE_DEV, COMPOSE_PROD, RAIZ, servicios

MIGRATOR = "migrate-docuboto"
CHAIN = RAIZ / "services" / "docuboto" / "migrations"

#: Lleva `DATABASE_URL` en el manifiesto de producción pero su código no la lee
#: (lo comprueba `test_la_excepcion_del_gateway_sigue_siendo_cierta`).
_NO_CONNECTION = {"api-gateway"}

_MANIFIESTOS = [pytest.param(COMPOSE_PROD, id="prod"), pytest.param(COMPOSE_DEV, id="dev")]


def _env_names(s: dict) -> set[str]:
    env = s.get("environment") or {}
    # prod: mapa; dev: lista `K=V`.
    return set(env) if isinstance(env, dict) else {str(e).split("=", 1)[0] for e in env}


def _db_services(todos: dict[str, dict]) -> list[str]:
    """Servicios que sirven y conectan a la base: llevan `DATABASE_URL`."""
    return sorted(
        n
        for n, s in todos.items()
        if not n.startswith("migrate-") and "DATABASE_URL" in _env_names(s) and n not in _NO_CONNECTION
    )


@pytest.mark.parametrize("compose", _MANIFIESTOS)
def test_todo_servicio_que_conecta_a_la_base_espera_al_migrador(compose) -> None:  # noqa: ANN001
    """Mutación que debe caer: quitar el `depends_on … service_completed_successfully`
    de un servicio en dev (o en prod) deja de migrar antes de servir."""
    todos = servicios(compose)
    con_base = _db_services(todos)
    # Control positivo: `app`, `worker` y los nueve de dominio del perfil `split`.
    assert len(con_base) >= 11, f"solo {con_base}: guardián ciego"
    assert MIGRATOR in todos, f"{compose.name}: falta {MIGRATOR}"
    mal: list[str] = []
    for nombre in con_base:
        deps = todos[nombre].get("depends_on") or {}
        cond = deps.get(MIGRATOR, {}).get("condition") if isinstance(deps, dict) else None
        if cond != "service_completed_successfully":
            mal.append(f"{nombre}: no depende de {MIGRATOR} con service_completed_successfully")
    assert not mal, "Migraciones al arrancar (fail-closed):\n  " + "\n  ".join(mal)


def test_la_excepcion_del_gateway_sigue_siendo_cierta() -> None:
    """Si el gateway empieza a leer `DATABASE_URL`, deja de estar exento."""
    for svc in _NO_CONNECTION:
        fuentes = [p for p in (RAIZ / "services" / svc).rglob("*.py") if "tests" not in p.parts]
        assert fuentes, f"{svc}: sin fuentes; guardián ciego"
        leen = [
            p.relative_to(RAIZ).as_posix()
            for p in fuentes
            if re.search(r"DATABASE_URL|database_url|asyncpg", p.read_text(encoding="utf-8"))
        ]
        assert not leen, f"{svc} conecta a la base y debe esperar a {MIGRATOR}: {leen}"


@pytest.mark.parametrize("compose", _MANIFIESTOS)
def test_hay_un_solo_migrador_y_es_de_un_solo_uso(compose) -> None:  # noqa: ANN001
    """Mutaciones que deben caer: migrar con la conexión de la aplicación; migrar con
    otra imagen que la de `app`; dejar el migrador tras un perfil."""
    todos = servicios(compose)
    migradores = sorted(n for n in todos if n.startswith("migrate-"))
    assert migradores == [MIGRATOR], f"{compose.name}: {migradores}"
    s = todos[MIGRATOR]
    assert s.get("restart") == "no", "un migrador de un solo uso no se reinicia"
    assert "ports" not in s, "el migrador no publica puertos"
    assert not s.get("profiles"), "el migrador corre siempre, no tras un perfil"
    cmd = s.get("command")
    cmd_txt = " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")
    assert "migrations/runner.py" in cmd_txt, f"{MIGRATOR} no ejecuta el runner: {cmd_txt!r}"
    assert "MIGRATIONS_DATABASE_URL" in _env_names(s), (
        f"{MIGRATOR}: no usa la conexión del propietario (MIGRATIONS_DATABASE_URL)"
    )
    assert s.get("image") and s.get("image") == todos["app"].get("image"), (
        f"{MIGRATOR}: no usa la MISMA imagen que app (migraría otro código)"
    )


def test_en_dev_el_migrador_reutiliza_la_imagen_de_app() -> None:
    """Dev nombra la imagen (`image:`) para que el migrador la reutilice sin construirla aparte."""
    todos = servicios(COMPOSE_DEV)
    assert todos["app"].get("image"), "app: sin `image:` explícita; el migrador no la comparte"
    assert "build" in todos["app"], "app: el servicio es quien construye la imagen"
    assert "build" not in todos[MIGRATOR], "el migrador no construye: reutiliza la imagen de app"


@pytest.mark.parametrize("compose", _MANIFIESTOS)
def test_el_proceso_unificado_solo_espera_a_su_migrador(compose) -> None:  # noqa: ANN001
    todos = servicios(compose)
    for nombre in ("app", "worker"):
        deps = todos[nombre].get("depends_on") or {}
        migradores = [k for k in deps if str(k).startswith("migrate-")]
        assert migradores == [MIGRATOR], f"{nombre}: {migradores}"


def test_la_imagen_app_copia_la_cadena() -> None:
    texto = (RAIZ / "infra" / "Dockerfile.app").read_text(encoding="utf-8")
    assert "COPY services/docuboto/migrations /app/migrations" in texto


# ── El cerrojo global (C19) ──────────────────────────────────────────────────────


def _modulo() -> str:
    return (RAIZ / "shared" / "orpycamcp_common" / "migrations.py").read_text(encoding="utf-8")


def test_el_runner_toma_el_cerrojo_global_y_aplica_cada_archivo_en_transaccion() -> None:
    """Mutaciones que deben caer: quitar `cerrojo(conn)` de `migrate`; sacar el
    `conn.execute(path.read_text())` de `conn.transaction()`; que el runner deje de
    llamar a `migrate`."""
    runner = (CHAIN / "runner.py").read_text(encoding="utf-8")
    assert "await migrations.migrate(conn, MIGRATIONS_DIR)" in runner, "el runner no usa `migrate`"
    modulo = _modulo()
    migrate = modulo[modulo.index("async def migrate("):]
    assert "async with cerrojo(conn):" in migrate, "`migrate` no toma el cerrojo"
    # El cerrojo envuelve el libro, las globales y las de tenant (no solo parte).
    cuerpo = migrate[migrate.index("async with cerrojo(conn):"):]
    assert (
        cuerpo.index("ensure_ledger")
        < cuerpo.index('migrations_dir / "global"')
        < cuerpo.index('migrations_dir / "tenant"')
    )
    chain = modulo[modulo.index("async def apply_chain("):modulo.index("async def migrate(")]
    assert "async with conn.transaction():" in chain, "apply_chain sin transacción"
    assert chain.index("conn.transaction()") < chain.index("path.read_text(") < chain.index("await record(")


def test_init_tenant_toma_el_mismo_cerrojo() -> None:
    """El alta de una entidad aplica las dos cadenas con `apply_chain` (una transacción
    por archivo) y dentro del cerrojo."""
    texto = (RAIZ / "scripts" / "init_tenant.py").read_text(encoding="utf-8")
    assert "async with ledger.cerrojo(conn):" in texto
    dentro = texto[texto.index("async with ledger.cerrojo(conn):"):]
    assert dentro.index('ledger.apply_chain(conn, MIGRATIONS_DIR / "global"') < dentro.index(
        'ledger.apply_chain(conn, MIGRATIONS_DIR / "tenant"'
    )
    antes = texto[: texto.index("async with ledger.cerrojo(conn):")]
    assert "apply_chain(" not in antes, "init_tenant aplica migraciones fuera del cerrojo"


def test_ninguna_migracion_es_incompatible_con_la_transaccion() -> None:
    """`CONCURRENTLY`, `ADD VALUE`, `VACUUM` y `BEGIN;` no corren dentro de una transacción."""
    prohibido = re.compile(
        r"(?im)^(?!\s*--).*\b(CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY|ADD\s+VALUE|VACUUM)\b|^\s*(BEGIN|COMMIT)\s*;"
    )
    sqls = sorted(CHAIN.glob("global/*.sql")) + sorted(CHAIN.glob("tenant/*.sql"))
    assert len(sqls) >= 30, "censo de migraciones vacío: guardián ciego"
    malas = [p.relative_to(RAIZ).as_posix() for p in sqls if prohibido.search(p.read_text(encoding="utf-8"))]
    assert not malas, f"incompatibles con aplicarse en una transacción: {malas}"


def test_el_texto_de_prod_y_la_doc_ya_no_afirman_un_cerrojo_que_no_existe() -> None:
    """El cerrojo es real: el manifiesto lo nombra y el módulo compartido lo define."""
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    assert "orpycamcp_common.migrations.cerrojo" in prod
    modulo = _modulo()
    assert "pg_try_advisory_lock" in modulo and "MIGRATIONS_LOCK_TIMEOUT_S" in modulo


def test_la_cadena_trae_los_catalogos() -> None:
    """Mutación que debe caer: omitir de la cadena un catálogo que el proceso unificado usa."""
    texto = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(CHAIN.glob("global/*.sql")) + sorted(CHAIN.glob("tenant/*.sql"))
    )
    for marca in (
        "auth_permissions",
        "medios_recepcion",
        "intelligence_provider",
        "trabajo_fuera",
        "ppi_nonces",
    ):
        assert marca in texto, marca
