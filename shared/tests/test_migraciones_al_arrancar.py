# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un servicio con migraciones no arranca antes de aplicarlas (AREA-13-08).

Los doce Dockerfile tienen `CMD uvicorn` directo y la migración solo se
ejecutaba a mano o en CI: una versión nueva podía servir tráfico contra un
esquema viejo y fallar de forma parcial, ruta a ruta.

Producción lo resuelve sin tocar las imágenes: cada servicio con
`migrations/runner.py` tiene un `migrate-<servicio>` de un solo uso (misma
imagen, `command: python migrations/runner.py`, conexión del propietario) y
`depends_on: migrate-<servicio>: condition: service_completed_successfully`.
Si el runner falla, el servicio no arranca (fail-closed). Se acepta también que
el propio Dockerfile invoque `runner.py` antes de `uvicorn`.

C19 (ADR-041): desarrollo hace lo mismo (los `migrate-<svc>` de `docker-compose.yml`
comparten el guardián con producción) y los nueve runners más `init_tenant.py` se
serializan con UN cerrojo global (`orpycamcp_common.migrations.cerrojo`). Antes el
manifiesto de producción afirmaba un `pg_advisory_lock` que el código no tenía.
"""

from __future__ import annotations

import re

import pytest

from tests._compose import COMPOSE_DEV, COMPOSE_PROD, RAIZ, servicios

_CON_MIGRACIONES = sorted(
    p.parent.parent.name for p in (RAIZ / "services").glob("*/migrations/runner.py")
)
#: El paquete vive en `services/docuboto`; el servicio de Compose se llama `app`.
_COMPOSE_DE = {"docuboto": "app"}
_DOMINIO_MIGRA = (
    "auth",
    "tenant",
    "document",
    "storage",
    "workflow",
    "archive",
    "notification",
    "signature",
    "knowledge",
)


def _compose_de(svc: str) -> str:
    return _COMPOSE_DE.get(svc, svc)


def _dockerfile_de(svc: str):
    if svc == "docuboto":
        return RAIZ / "infra" / "Dockerfile.app"
    return RAIZ / "services" / svc / "Dockerfile"


def _dockerfile_migra(svc: str) -> bool:
    ruta = _dockerfile_de(svc)
    if not ruta.is_file():
        return False
    texto = ruta.read_text(encoding="utf-8")
    m = re.search(r"^(?:CMD|ENTRYPOINT)\s.*$", texto, re.MULTILINE)
    return bool(m and "runner.py" in m.group(0) and m.group(0).index("runner.py") < m.group(0).find("uvicorn"))


def _migrador_de(svc: str, todos: dict[str, dict]) -> str | None:
    token = _compose_de(svc)
    for nombre, s in todos.items():
        cmd = s.get("command")
        cmd_txt = " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")
        img = str(s.get("image", ""))
        if nombre.startswith("migrate-") and "migrations/runner.py" in cmd_txt and f"/{token}:" in img:
            return nombre
    return None


def test_se_encontraron_servicios_con_migraciones() -> None:
    assert len(_CON_MIGRACIONES) >= 9, f"solo {_CON_MIGRACIONES}: guardián ciego"


_MANIFIESTOS = [pytest.param(COMPOSE_PROD, id="prod"), pytest.param(COMPOSE_DEV, id="dev")]


@pytest.mark.parametrize("compose", _MANIFIESTOS)
def test_todo_servicio_con_migraciones_las_aplica_antes_de_servir(compose) -> None:  # noqa: ANN001
    """Mutación que debe caer: quitar el `depends_on … service_completed_successfully`
    de un servicio en dev (o en prod) deja de migrar antes de servir."""
    todos = servicios(compose)
    mal: list[str] = []
    for svc in _CON_MIGRACIONES:
        if _dockerfile_migra(svc):
            continue
        migrador = _migrador_de(svc, todos)
        if migrador is None:
            mal.append(f"{svc}: ni el Dockerfile ni el manifiesto de producción migran antes de servir")
            continue
        nombre = _compose_de(svc)
        deps = todos[nombre].get("depends_on") or {}
        cond = deps.get(migrador, {}).get("condition") if isinstance(deps, dict) else None
        if cond != "service_completed_successfully":
            mal.append(f"{nombre}: no depende de {migrador} con service_completed_successfully")
        env = todos[migrador].get("environment") or {}
        # prod: mapa; dev: lista `K=V`.
        nombres = set(env) if isinstance(env, dict) else {str(e).split("=", 1)[0] for e in env}
        if "MIGRATIONS_DATABASE_URL" not in nombres:
            mal.append(f"{migrador}: no usa la conexión del propietario (MIGRATIONS_DATABASE_URL)")
        if todos[migrador].get("image") != todos[nombre].get("image"):
            mal.append(f"{migrador}: no usa la MISMA imagen que {nombre} (migraría otro código)")
    assert not mal, "Migraciones al arrancar (fail-closed):\n  " + "\n  ".join(mal)


@pytest.mark.parametrize("compose", _MANIFIESTOS)
def test_un_migrador_no_reinicia_ni_publica_puertos(compose) -> None:  # noqa: ANN001
    migradores = {n: s for n, s in servicios(compose).items() if n.startswith("migrate-")}
    assert len(migradores) >= 9, f"solo {sorted(migradores)}: guardián ciego"  # control positivo
    for nombre, s in migradores.items():
        assert s.get("restart") == "no", f"{nombre}: un migrador de un solo uso no se reinicia"
        assert "ports" not in s, f"{nombre}: no publica puertos"


def test_en_dev_cada_servicio_con_runner_tiene_su_migrador() -> None:
    """Dev nombra la imagen (`image:`) para que el migrador la reutilice sin construirla aparte."""
    todos = servicios(COMPOSE_DEV)
    for svc in _CON_MIGRACIONES:
        nombre = _compose_de(svc)
        migrador = f"migrate-{svc.removesuffix('-service')}"
        assert migrador in todos, f"dev sin migrador para {svc}"
        assert todos[nombre].get("image"), f"{nombre}: sin `image:` explícita; el migrador no la comparte"
        assert "build" in todos[nombre], f"{nombre}: el servicio es quien construye la imagen"
        assert "build" not in todos[migrador], (
            "el migrador no construye: reutiliza la imagen del servicio"
        )


# ── El cerrojo global (C19) ──────────────────────────────────────────────────────


def _runner(svc: str) -> str:
    return (RAIZ / "services" / svc / "migrations" / "runner.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("svc", _CON_MIGRACIONES)
def test_cada_runner_toma_el_cerrojo_global_y_aplica_las_globales_en_transaccion(svc: str) -> None:
    """Mutaciones que deben caer: quitar `ledger.cerrojo(conn)` de un runner; sacar el
    `conn.execute(path.read_text())` global de `conn.transaction()`."""
    texto = _runner(svc)
    assert "async with ledger.cerrojo(conn):" in texto, f"{svc}: el runner no toma el cerrojo"
    # El cerrojo envuelve el libro, las globales y las de tenant (no solo parte).
    cuerpo = texto[texto.index("async with ledger.cerrojo(conn):"):]
    assert cuerpo.index("ensure_ledger") < cuerpo.index("_apply_global(conn)") < cuerpo.index("_apply_tenant(conn)")
    global_ = texto[texto.index("async def _apply_global"):texto.index("async def _tenant_schemas")]
    assert "async with conn.transaction():" in global_, f"{svc}: _apply_global sin transacción"
    assert global_.index("conn.transaction()") < global_.index("path.read_text()") < global_.index("ledger.record")


def test_init_tenant_toma_el_mismo_cerrojo() -> None:
    texto = (RAIZ / "scripts" / "init_tenant.py").read_text(encoding="utf-8")
    assert "async with ledger.cerrojo(conn):" in texto
    assert texto.count("async with conn.transaction():") >= 2  # globales y tenant


def test_ninguna_migracion_global_es_incompatible_con_la_transaccion() -> None:
    """`CONCURRENTLY`, `ADD VALUE`, `VACUUM` y `BEGIN;` no corren dentro de una transacción."""
    prohibido = re.compile(
        r"(?im)^(?!\s*--).*\b(CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY|ADD\s+VALUE|VACUUM)\b|^\s*(BEGIN|COMMIT)\s*;"
    )
    sqls = sorted((RAIZ / "services").glob("*/migrations/global/*.sql"))
    assert len(sqls) >= 10, "censo de migraciones globales vacío: guardián ciego"
    malas = [p.relative_to(RAIZ).as_posix() for p in sqls if prohibido.search(p.read_text(encoding="utf-8"))]
    assert not malas, f"incompatibles con aplicarse en una transacción: {malas}"


def test_el_texto_de_prod_y_la_doc_ya_no_afirman_un_cerrojo_que_no_existe() -> None:
    """El cerrojo es real: el manifiesto lo nombra y el módulo compartido lo define."""
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    assert "orpycamcp_common.migrations.cerrojo" in prod
    modulo = (RAIZ / "shared" / "orpycamcp_common" / "migrations.py").read_text(encoding="utf-8")
    assert "pg_try_advisory_lock" in modulo and "MIGRATIONS_LOCK_TIMEOUT_S" in modulo


def test_el_baseline_es_el_generado_y_trae_los_catalogos() -> None:
    """Mutación que debe caer: editar 001_baseline.sql a mano; omitir un catálogo."""
    import importlib.util

    script = RAIZ / "scripts" / "generar_baseline.py"
    spec = importlib.util.spec_from_file_location("generar_baseline", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for ambito in ("global", "tenant"):
        dest = RAIZ / "services" / "docuboto" / "migrations" / ambito / "001_baseline.sql"
        assert dest.is_file(), dest
        assert dest.read_text(encoding="utf-8") == mod.concatenar(ambito)
    global_ = (RAIZ / "services/docuboto/migrations/global/001_baseline.sql").read_text(
        encoding="utf-8"
    )
    tenant = (RAIZ / "services/docuboto/migrations/tenant/001_baseline.sql").read_text(
        encoding="utf-8"
    )
    texto = global_ + "\n" + tenant
    for marca in (
        "auth_permissions",
        "medios_recepcion",
        "intelligence_provider",
        "trabajo_fuera",
        "ppi_nonces",
    ):
        assert marca in texto, marca


@pytest.mark.parametrize("compose", _MANIFIESTOS)
def test_el_proceso_unificado_aplica_un_solo_baseline(compose) -> None:  # noqa: ANN001
    todos = servicios(compose)
    assert "migrate-docuboto" in todos
    assert (todos["migrate-docuboto"].get("profiles") or []) in ([], None)
    for corto in _DOMINIO_MIGRA:
        perfiles = todos[f"migrate-{corto}"].get("profiles") or []
        assert perfiles == ["split"], f"migrate-{corto} debe quedar en split"
    for nombre in ("app", "worker"):
        deps = todos[nombre].get("depends_on") or {}
        migradores = [k for k in deps if str(k).startswith("migrate-")]
        assert migradores == ["migrate-docuboto"], f"{nombre}: {migradores}"


def test_la_imagen_app_copia_el_baseline() -> None:
    texto = (RAIZ / "infra" / "Dockerfile.app").read_text(encoding="utf-8")
    assert "COPY services/docuboto/migrations /app/migrations" in texto


def test_los_nueve_runners_sellan_si_el_baseline_ya_corrio() -> None:
    vistos = 0
    for svc in _CON_MIGRACIONES:
        if svc == "docuboto":
            continue
        texto = _runner(svc)
        assert "sello_si_baseline" in texto, svc
        vistos += 1
    assert vistos >= 9
