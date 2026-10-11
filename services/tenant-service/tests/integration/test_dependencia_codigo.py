# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Migración 013 y PATCH de dependencias contra Postgres REAL (B5, ADR-032).

Lo que un mock no puede probar: el trigger que congela `codigo`, el historial de
solo inserción, la función `resolver_codigo_dependencia` (criterio único del
alias, los backfills y el replay de la cola de fallidos) y que renombrar +
historial + asiento son UN acto (si el asiento falla, el renombre se revierte).

Esquemas desechables `tenant_itest_*`; nunca toca migraciones globales.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app
from migrations import runner

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1", reason="carril de integración: RUN_INTEGRATION=1"
)

_RAW = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
_OWNER_DSN = _RAW.replace("postgresql+asyncpg://", "postgresql://")


def _migracion(num: str) -> str:
    (ruta,) = [p for p in runner._sql_files(runner.TENANT_DIR) if p.name.startswith(num)]
    return ruta.read_text()


@pytest_asyncio.fixture
async def conn() -> AsyncGenerator[asyncpg.Connection, None]:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    if host not in {"postgres", "localhost", "127.0.0.1"}:
        raise RuntimeError(f"host {host!r} fuera de la allowlist de integración")
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        await c.execute(f'CREATE SCHEMA "{schema}"')
        await c.execute(f'SET search_path = "{schema}", public')
        for ruta in runner._sql_files(runner.TENANT_DIR):
            await c.execute(ruta.read_text())
        yield c
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute("RESET search_path")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


async def _dep(c: asyncpg.Connection, codigo: int, nombre: str, activo: bool = True) -> uuid.UUID:
    return await c.fetchval(
        "INSERT INTO dependencias (codigo, sigla, nombre, activo) VALUES ($1, $2, $3, $4) RETURNING id",
        codigo,
        f"D{codigo}",
        nombre,
        activo,
    )


async def _resolver(c: asyncpg.Connection, nombre: str, historico: bool) -> tuple[int | None, int]:
    r = await c.fetchrow("SELECT * FROM resolver_codigo_dependencia($1, $2)", nombre, historico)
    return r["codigo"], r["candidatos"]


# --- trigger: el código se congela ---------------------------------------------


@pytest.mark.asyncio
async def test_el_trigger_rechaza_cambiar_el_codigo(conn: asyncpg.Connection) -> None:
    await _dep(conn, 901, "Archivo")
    with pytest.raises(asyncpg.CheckViolationError, match="dependencia_codigo_inmutable"):
        await conn.execute("UPDATE dependencias SET codigo = 999 WHERE codigo = 901")
    assert await conn.fetchval("SELECT count(*) FROM dependencias WHERE codigo = 901") == 1


@pytest.mark.asyncio
async def test_el_trigger_permite_el_resto_de_columnas_y_el_mismo_codigo(
    conn: asyncpg.Connection,
) -> None:
    await _dep(conn, 901, "Archivo")
    await conn.execute("UPDATE dependencias SET nombre = 'Gestión', codigo = 901 WHERE codigo = 901")
    assert await conn.fetchval("SELECT nombre FROM dependencias WHERE codigo = 901") == "Gestión"


@pytest.mark.asyncio
async def test_nombre_mayor_de_100_lo_rechaza_la_base(conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _dep(conn, 901, "x" * 101)


# --- historial: solo inserción -------------------------------------------------


@pytest.mark.asyncio
async def test_el_historial_es_de_solo_insercion(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES (901, 'X', 'renombre')"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("UPDATE dependencia_nombre_historico SET nombre = 'Y'")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("DELETE FROM dependencia_nombre_historico")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES (901, 'X', 'otro')"
        )


# --- resolver_codigo_dependencia: una sola definición del criterio -------------


@pytest.mark.asyncio
async def test_resolver_unico_activo(conn: asyncpg.Connection) -> None:
    await _dep(conn, 901, "Archivo")
    assert await _resolver(conn, "Archivo", False) == (901, 1)
    assert await _resolver(conn, "Archivo", True) == (901, 1)


@pytest.mark.asyncio
async def test_resolver_huerfano_devuelve_cero_candidatos(conn: asyncpg.Connection) -> None:
    assert await _resolver(conn, "Nadie", True) == (None, 0)


@pytest.mark.asyncio
async def test_resolver_homonimos_es_ambiguo_y_no_elige(conn: asyncpg.Connection) -> None:
    """Mutación: cambiar `count(*) = 1` por `min()` asigna 901 en silencio."""
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo")
    assert await _resolver(conn, "Archivo", False) == (None, 2)
    assert await _resolver(conn, "Archivo", True) == (None, 2)


@pytest.mark.asyncio
async def test_resolver_el_alias_solo_ve_activas_el_historico_ve_todo(
    conn: asyncpg.Connection,
) -> None:
    await _dep(conn, 901, "Archivo", activo=False)
    assert await _resolver(conn, "Archivo", False) == (None, 0)
    assert await _resolver(conn, "Archivo", True) == (901, 1)


@pytest.mark.asyncio
async def test_resolver_ve_el_nombre_anterior_solo_en_modo_historico(
    conn: asyncpg.Connection,
) -> None:
    await _dep(conn, 901, "Gestión Documental")
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES (901, 'Archivo', 'renombre')"
    )
    assert await _resolver(conn, "Archivo", True) == (901, 1)
    assert await _resolver(conn, "Archivo", False) == (None, 0)


@pytest.mark.asyncio
async def test_resolver_nombre_viejo_reutilizado_por_otra_es_ambiguo(
    conn: asyncpg.Connection,
) -> None:
    """El nombre que 901 tuvo y 902 usa hoy no se puede atribuir a nadie sin un humano."""
    await _dep(conn, 901, "Gestión")
    await _dep(conn, 902, "Archivo")
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES (901, 'Archivo', 'renombre')"
    )
    assert await _resolver(conn, "Archivo", True) == (None, 2)


@pytest.mark.asyncio
async def test_resolver_mapeo_manual_desambigua_un_huerfano(conn: asyncpg.Connection) -> None:
    await _dep(conn, 901, "Gestión")
    assert await _resolver(conn, "Archivo viejo", True) == (None, 0)
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
        "VALUES (901, 'Archivo viejo', 'mapeo_manual')"
    )
    assert await _resolver(conn, "Archivo viejo", True) == (901, 1)


@pytest.mark.asyncio
async def test_la_013_es_idempotente(conn: asyncpg.Connection) -> None:
    await _dep(conn, 901, "Archivo")
    await conn.execute(_migracion("013"))
    await conn.execute(_migracion("013"))
    assert await _resolver(conn, "Archivo", True) == (901, 1)


@pytest.mark.asyncio
async def test_la_013_con_un_nombre_largo_aborta_nombrandolo() -> None:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    assert host in {"postgres", "localhost", "127.0.0.1"}
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        await c.execute(f'CREATE SCHEMA "{schema}"')
        await c.execute(f'SET search_path = "{schema}", public')
        for ruta in runner._sql_files(runner.TENANT_DIR):
            if ruta.name < "013":
                await c.execute(ruta.read_text())
        await _dep(c, 901, "x" * 150)
        with pytest.raises(asyncpg.RaiseError, match="901"):
            await c.execute(_migracion("013"))
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute("RESET search_path")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


# --- PATCH por la API contra la base real ---------------------------------------

_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


@pytest_asyncio.fixture
async def client(conn: asyncpg.Connection) -> AsyncGenerator[AsyncClient, None]:
    async def override() -> AsyncGenerator[asyncpg.Connection, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with (
        patch("app.core.authz.check_permission", new=AsyncMock(return_value=True)),
        patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as ap,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ac.audit_append = ap  # type: ignore[attr-defined]
            yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_codigo_distinto_422_y_la_fila_queda_intacta(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    dep_id = await _dep(conn, 901, "Archivo")
    r = await client.patch(f"/api/v1/dependencias/{dep_id}", json={"codigo": 902}, headers=_HDRS)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "dependencia_codigo_inmutable"
    assert await conn.fetchval("SELECT codigo FROM dependencias WHERE id = $1", dep_id) == 901
    client.audit_append.assert_not_awaited()  # type: ignore[attr-defined]
    r = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"codigo": 901, "sigla": "ZZ"}, headers=_HDRS
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_renombrar_con_tramite_vivo_es_200_y_deja_el_nombre_anterior(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    """Mutación: reintroducir la guarda 409 por nombre vuelve a bloquear este caso."""
    await conn.execute("CREATE TABLE flow_steps (id serial PRIMARY KEY, to_dept_code int, status text)")
    await conn.execute("INSERT INTO flow_steps (to_dept_code, status) VALUES (901, 'pending')")
    dep_id = await _dep(conn, 901, "Archivo")
    r = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"nombre": "Gestión Documental"}, headers=_HDRS
    )
    assert r.status_code == 200
    h = await conn.fetchrow("SELECT codigo, nombre, origen FROM dependencia_nombre_historico")
    assert (h["codigo"], h["nombre"], h["origen"]) == (901, "Archivo", "renombre")
    assert await _resolver(conn, "Archivo", True) == (901, 1)  # el evento viejo sigue llegando
    # Desactivar SÍ sigue bloqueado (la bandeja filtra por `activo`).
    r = await client.patch(f"/api/v1/dependencias/{dep_id}", json={"activo": False}, headers=_HDRS)
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "dependencia_con_tramite_en_curso"


@pytest.mark.asyncio
async def test_si_el_asiento_falla_el_renombre_y_el_historial_se_revierten(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    dep_id = await _dep(conn, 901, "Archivo")
    client.audit_append.side_effect = RuntimeError("audit down")  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        await client.patch(f"/api/v1/dependencias/{dep_id}", json={"nombre": "Otro"}, headers=_HDRS)
    assert await conn.fetchval("SELECT nombre FROM dependencias WHERE id = $1", dep_id) == "Archivo"
    assert await conn.fetchval("SELECT count(*) FROM dependencia_nombre_historico") == 0


@pytest.mark.asyncio
async def test_eliminar_con_solo_historia_409_y_virgen_204(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    await conn.execute("CREATE TABLE flow_events (id serial PRIMARY KEY, to_dept_code int)")
    await conn.execute("INSERT INTO flow_events (to_dept_code) VALUES (901)")
    usada = await _dep(conn, 901, "Usada")
    virgen = await _dep(conn, 902, "Virgen")
    r = await client.delete(f"/api/v1/dependencias/{usada}", headers=_HDRS)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "dependencia_con_historia"
    assert r.json()["detail"]["referencias"] == {"flow_events.to_dept_code": 1}
    r = await client.delete(f"/api/v1/dependencias/{virgen}", headers=_HDRS)
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_mapeo_manual_por_la_api_resuelve_el_huerfano(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    dep_id = await _dep(conn, 901, "Gestión")
    r = await client.post(
        f"/api/v1/dependencias/{dep_id}/mapeos-nombre",
        json={"nombre": "Archivo viejo", "motivo": "Confirmado con la oficina de archivo"},
        headers=_HDRS,
    )
    assert r.status_code == 201
    assert await _resolver(conn, "Archivo viejo", True) == (901, 1)
    assert client.audit_append.await_args.kwargs["action"] == "dependencia.mapeo_nombre"  # type: ignore[attr-defined]


# --- 014: endurecimiento tras las auditorías -------------------------------------


@pytest.mark.asyncio
async def test_mapeo_manual_desambigua_homonimas_y_gana_al_texto(conn: asyncpg.Connection) -> None:
    """Mutación: volver al UNION plano (mapeo + coincidencias textuales) deja (None, 2)."""
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo")
    assert await _resolver(conn, "Archivo", True) == (None, 2)
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
        "VALUES (902, 'Archivo', 'mapeo_manual')"
    )
    assert await _resolver(conn, "Archivo", True) == (902, 1)
    # El alias de la API (sin histórico) NO usa mapeos: sigue viendo las dos activas.
    assert await _resolver(conn, "Archivo", False) == (None, 2)


@pytest.mark.asyncio
async def test_dos_mapeos_distintos_para_el_mismo_nombre_siguen_ambiguos(
    conn: asyncpg.Connection,
) -> None:
    await _dep(conn, 901, "A")
    await _dep(conn, 902, "B")
    for cod in (901, 902):
        await conn.execute(
            "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
            "VALUES ($1, 'Viejo', 'mapeo_manual')",
            cod,
        )
    assert await _resolver(conn, "Viejo", True) == (None, 2)
    # Un mapeo repetido al MISMO código no cuenta doble.
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
        "VALUES (901, 'Viejo', 'mapeo_manual')"
    )
    assert await _resolver(conn, "Viejo", True) == (None, 2)


@pytest.mark.asyncio
async def test_el_mapeo_gana_a_la_coincidencia_textual_y_al_renombre(
    conn: asyncpg.Connection,
) -> None:
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Otra")
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES "
        "(902, 'Archivo', 'renombre'), (902, 'Archivo', 'mapeo_manual')"
    )
    assert await _resolver(conn, "Archivo", True) == (902, 1)


@pytest.mark.asyncio
async def test_las_funciones_de_la_013_y_014_fijan_su_search_path(conn: asyncpg.Connection) -> None:
    """Mutación: quitar `SET search_path FROM CURRENT` rompe la llamada desde otro schema."""
    await _dep(conn, 901, "Archivo")
    schema = await conn.fetchval("SELECT current_schema()")
    await conn.execute("SET search_path = public")
    r = await conn.fetchrow(f'SELECT * FROM "{schema}".resolver_codigo_dependencia($1, true)', "Archivo")
    assert (r["codigo"], r["candidatos"]) == (901, 1)
    configs = await conn.fetch(
        "SELECT p.proname, p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = $1 AND p.proname = ANY($2::text[])",
        schema,
        [
            "resolver_codigo_dependencia",
            "reject_dependencia_codigo_change",
            "reject_dependencia_nombre_historico_mutation",
            "reject_dependencia_codigo_retirado_mutation",
            "reject_dependencia_codigo_reutilizado",
        ],
    )
    assert len(configs) == 5
    assert all(c["proconfig"] and any(x.startswith("search_path=") for x in c["proconfig"]) for c in configs)


@pytest.mark.asyncio
async def test_codigo_retirado_trigger_y_solo_insercion(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "INSERT INTO dependencia_codigo_retirado (codigo, nombre) VALUES (905, 'Vieja')"
    )
    with pytest.raises(asyncpg.CheckViolationError, match="dependencia_codigo_retirado"):
        await _dep(conn, 905, "Nueva con código ajeno")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("UPDATE dependencia_codigo_retirado SET nombre = 'x'")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("DELETE FROM dependencia_codigo_retirado")


@pytest.mark.asyncio
async def test_historial_huerfano_bloquea_el_codigo_pero_no_un_upsert_de_la_viva(
    conn: asyncpg.Connection,
) -> None:
    await _dep(conn, 901, "Archivo")
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES (901, 'Antes', 'renombre')"
    )
    # La dependencia viva con historial: un seed `ON CONFLICT DO NOTHING` no explota.
    await conn.execute(
        "INSERT INTO dependencias (codigo, sigla, nombre) VALUES (901, 'X', 'Archivo') ON CONFLICT DO NOTHING"
    )
    # Borrada por fuera de la API: el historial queda y el código no vuelve.
    await conn.execute("DELETE FROM dependencias WHERE codigo = 901")
    with pytest.raises(asyncpg.CheckViolationError, match="dependencia_codigo_retirado"):
        await _dep(conn, 901, "Impostora")


@pytest.mark.asyncio
async def test_eliminar_deja_el_codigo_retirado_y_no_se_puede_recrear(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    """Mutación: omitir `registrar_retirada` deja pasar la recreación (201)."""
    virgen = await _dep(conn, 902, "Virgen")
    r = await client.delete(f"/api/v1/dependencias/{virgen}", headers=_HDRS)
    assert r.status_code == 204
    fila = await conn.fetchrow("SELECT codigo, nombre FROM dependencia_codigo_retirado")
    assert (fila["codigo"], fila["nombre"]) == (902, "Virgen")
    r = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 902, "sigla": "NV", "nombre": "Otra unidad"},
        headers=_HDRS,
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "dependencia_codigo_retirado"
    assert await conn.fetchval("SELECT count(*) FROM dependencias WHERE codigo = 902") == 0


@pytest.mark.asyncio
async def test_si_el_asiento_del_borrado_falla_el_codigo_no_queda_retirado(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    virgen = await _dep(conn, 902, "Virgen")
    client.audit_append.side_effect = RuntimeError("audit down")  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        await client.delete(f"/api/v1/dependencias/{virgen}", headers=_HDRS)
    assert await conn.fetchval("SELECT count(*) FROM dependencias WHERE codigo = 902") == 1
    assert await conn.fetchval("SELECT count(*) FROM dependencia_codigo_retirado") == 0


@pytest.mark.asyncio
async def test_referencias_barre_el_catalogo_e_incluye_las_columnas_de_los_otros_servicios(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    """Cada tabla/columna del barrido bloquea el borrado; mutación: quitar una del patrón."""
    casos = [
        ("transferencias", "oficina_productora_codigo"),
        ("transferencias", "unidad_administrativa_codigo"),
        ("expediente_indice_item", "dependencia_productora_codigo"),
        ("plantilla_dependencia", "depe_codigo"),
        ("auth_users", "active_depe_id"),
        ("urd_usuaroldep", "depe_id"),
        ("radicados", "ubicacion_dept_code"),
    ]
    for tabla in {t for t, _ in casos}:
        cols = ", ".join(f'"{c}" int' for t, c in casos if t == tabla)
        await conn.execute(f'CREATE TABLE "{tabla}" (id serial PRIMARY KEY, {cols})')
    for i, (tabla, col) in enumerate(casos):
        dep_id = await _dep(conn, 910 + i, f"Dep {i}")
        await conn.execute(f'INSERT INTO "{tabla}" ("{col}") VALUES ($1)', 910 + i)
        r = await client.delete(f"/api/v1/dependencias/{dep_id}", headers=_HDRS)
        assert r.status_code == 409, (tabla, col)
        assert r.json()["detail"]["referencias"] == {f"{tabla}.{col}": 1}
    # Una tabla de evidencia / columna no entera no cuenta.
    await conn.execute("CREATE TABLE radicado_ubicacion_recalculo (id serial PRIMARY KEY, antes_dept_code int)")
    await conn.execute("INSERT INTO radicado_ubicacion_recalculo (antes_dept_code) VALUES (999)")
    libre = await _dep(conn, 999, "Libre")
    assert (await client.delete(f"/api/v1/dependencias/{libre}", headers=_HDRS)).status_code == 204


@pytest.mark.asyncio
async def test_renombrada_no_se_elimina_el_historial_es_una_referencia(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    dep_id = await _dep(conn, 901, "Archivo")
    r = await client.patch(f"/api/v1/dependencias/{dep_id}", json={"nombre": "Gestión"}, headers=_HDRS)
    assert r.status_code == 200
    r = await client.delete(f"/api/v1/dependencias/{dep_id}", headers=_HDRS)
    assert r.status_code == 409
    assert r.json()["detail"]["referencias"] == {"dependencia_nombre_historico.codigo": 1}


@pytest.mark.asyncio
async def test_mapeo_a_dependencia_inactiva_409_y_no_deja_rastro(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    dep_id = await _dep(conn, 901, "Cerrada", activo=False)
    r = await client.post(
        f"/api/v1/dependencias/{dep_id}/mapeos-nombre",
        json={"nombre": "Archivo viejo", "motivo": "Confirmado con la oficina de archivo"},
        headers=_HDRS,
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "dependencia_inactiva"
    assert await conn.fetchval("SELECT count(*) FROM dependencia_nombre_historico") == 0
    client.audit_append.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mapeo_del_nombre_vigente_de_otra_activa_409_pero_homonimas_si(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    """Mutación: quitar la guarda deja redirigir el nombre vigente de otra unidad."""
    a = await _dep(conn, 901, "Archivo")
    b = await _dep(conn, 902, "Jurídica")
    cuerpo = {"nombre": "Archivo", "motivo": "Confirmado con la oficina de archivo"}
    r = await client.post(f"/api/v1/dependencias/{b}/mapeos-nombre", json=cuerpo, headers=_HDRS)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "nombre_de_otra_dependencia"
    assert await conn.fetchval("SELECT count(*) FROM dependencia_nombre_historico") == 0
    # Con una homónima (dos activas llamadas «Archivo») el mapeo ES el remedio.
    await _dep(conn, 903, "Archivo")
    r = await client.post(f"/api/v1/dependencias/{a}/mapeos-nombre", json=cuerpo, headers=_HDRS)
    assert r.status_code == 201
    assert await _resolver(conn, "Archivo", True) == (901, 1)


@pytest.mark.asyncio
async def test_patch_motivo_va_al_historial_y_al_asiento_y_no_es_columna(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    dep_id = await _dep(conn, 901, "Archivo")
    motivo = "Resolución 123 de 2026: reestructuración del área"
    r = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"nombre": "Gestión", "motivo": motivo}, headers=_HDRS
    )
    assert r.status_code == 200
    assert await conn.fetchval("SELECT motivo FROM dependencia_nombre_historico") == motivo
    payload = client.audit_append.await_args.kwargs["payload"]  # type: ignore[attr-defined]
    assert payload["motivo"] == motivo and "motivo" not in payload["fields"]
    # Sin motivo sigue siendo válido (opcional).
    r = await client.patch(f"/api/v1/dependencias/{dep_id}", json={"nombre": "Otra"}, headers=_HDRS)
    assert r.status_code == 200
    assert await conn.fetchval("SELECT count(*) FROM dependencia_nombre_historico WHERE motivo IS NULL") == 1


@pytest.mark.asyncio
async def test_siembra_desde_el_audit_log_es_idempotente_y_solo_con_cambio_de_nombre(
    conn: asyncpg.Connection,
) -> None:
    """El audit_log vivo es inmutable: el asiento de prueba se inserta y se REVIERTE."""
    dep_id = await _dep(conn, 901, "Gestión")
    slug = (await conn.fetchval("SELECT current_schema()")).removeprefix("tenant_")
    tr = conn.transaction()
    await tr.start()
    try:
        for antes, despues, extra in (
            ("Archivo viejo", "Gestión", {}),                      # renombre real
            ("Sin cambio", "Sin cambio", {}),                      # solo otro campo
            ("Cambio sin codigo", "Algo", {"codigo": 777}),         # código en el payload
        ):
            await conn.execute(
                "INSERT INTO public.audit_log (tenant_slug, service, actor, canal, action, object_type, "
                "object_ref, payload, hash) VALUES ($1, 'tenant', 'x', 'api', 'dependencia.actualizada', "
                "'dependencia', $2, $3::jsonb, repeat('a', 64))",
                slug,
                str(dep_id),
                json.dumps({"antes": {"nombre": antes}, "despues": {"nombre": despues}, **extra}),
            )
        await conn.execute(_migracion("014"))
        await conn.execute(_migracion("014"))
        filas = await conn.fetch("SELECT codigo, nombre, origen FROM dependencia_nombre_historico ORDER BY nombre")
        assert [(f["codigo"], f["nombre"], f["origen"]) for f in filas] == [
            (901, "Archivo viejo", "renombre"),
            (777, "Cambio sin codigo", "renombre"),
        ]
    finally:
        await tr.rollback()


@pytest.mark.asyncio
async def test_referencias_incluye_las_condiciones_jsonb_de_las_reglas(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    """Una regla que cita el código SOLO dentro de `conditions` bloquea el borrado."""
    await conn.execute("CREATE TABLE workflow_rules (id serial PRIMARY KEY, conditions jsonb NOT NULL)")
    citada = await _dep(conn, 920, "Citada")
    libre = await _dep(conn, 921, "Libre")
    await conn.execute(
        "INSERT INTO workflow_rules (conditions) VALUES ($1::jsonb)",
        '[{"field": "dest_dept_code", "op": "in", "value": [920, 5]}]',
    )
    r = await client.delete(f"/api/v1/dependencias/{citada}", headers=_HDRS)
    assert r.status_code == 409
    assert r.json()["detail"]["referencias"] == {"workflow_rules.conditions": 1}
    assert (await client.delete(f"/api/v1/dependencias/{libre}", headers=_HDRS)).status_code == 204


@pytest.mark.asyncio
async def test_mapeo_de_nombre_que_ya_resuelve_a_otra_dependencia_409_sin_rastro(
    client: AsyncClient, conn: asyncpg.Connection
) -> None:
    await _dep(conn, 930, "Archivo")
    otra = await _dep(conn, 931, "Otra")
    r = await client.post(
        f"/api/v1/dependencias/{otra}/mapeos-nombre",
        json={"nombre": "Archivo", "motivo": "Intento de secuestrar el nombre"},
        headers=_HDRS,
    )
    assert r.status_code == 409
    assert await conn.fetchval("SELECT count(*) FROM dependencia_nombre_historico") == 0
    # Un nombre huérfano sí se mapea; y después NO se puede renombrar a él otra dependencia.
    r = await client.post(
        f"/api/v1/dependencias/{otra}/mapeos-nombre",
        json={"nombre": "Archivo viejo", "motivo": "Confirmado con la oficina de archivo"},
        headers=_HDRS,
    )
    assert r.status_code == 201
    dep = await _dep(conn, 932, "Tercera")
    r = await client.patch(f"/api/v1/dependencias/{dep}", json={"nombre": "Archivo viejo"}, headers=_HDRS)
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "nombre_mapeado_a_otra_dependencia"
