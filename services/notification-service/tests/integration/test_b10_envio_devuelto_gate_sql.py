# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10: el gate de clearance del aviso de devolución vive en el WHERE (Postgres REAL).

`radicados`, `postal_shipment` y las tablas RBAC son de otros servicios: aquí se crean con
las columnas que el aviso lee (mismo criterio que `test_dead_letter_clearance_where.py`).

Mutación verificada al entregar: sustituir `r.nivel_seguridad <= $2` por `TRUE` en
`devolucion._SQL_CONTENIDO` → cae `test_gate_en_el_where` (y el de extremo a extremo).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from app.services import devolucion
from app.services.devolucion import build_devolucion_notifications, resolver_contexto_devolucion

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]

TENANT = f"itest-{uuid.uuid4().hex[:8]}"
_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1", "db"})


def _dsn() -> str:
    raw = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    assert raw, "RUN_INTEGRATION=1 sin DATABASE_URL: configuración de entorno errónea"
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://")
    host = urlsplit(dsn).hostname or ""
    assert host in _HOSTS, f"host {host!r} fuera de la allowlist del tier de integración"
    return dsn


@pytest_asyncio.fixture
async def conn() -> AsyncGenerator[asyncpg.Connection, None]:
    c = await asyncpg.connect(_dsn())
    schema = "tenant_" + TENANT.replace("-", "_")
    await c.execute(f'CREATE SCHEMA "{schema}"')
    try:
        await c.execute(f'SET search_path TO "{schema}"')
        await c.execute(
            """
            CREATE TABLE auth_users (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email varchar(255),
                is_root boolean NOT NULL DEFAULT false, is_active boolean NOT NULL DEFAULT true);
            CREATE TABLE auth_memberships (user_id uuid NOT NULL, group_id uuid NOT NULL);
            CREATE TABLE role_clearance (group_id uuid PRIMARY KEY, max_level smallint NOT NULL);
            CREATE TABLE radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                tracking_number varchar(50) NOT NULL UNIQUE, subject text,
                origin_dept_code int, origin_dept varchar(100), registered_by uuid,
                ubicacion_dept_code int, ubicacion_dept varchar(100), flujo_usuario uuid,
                responde_a uuid, nivel_seguridad smallint NOT NULL);
            CREATE TABLE postal_shipment (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                radicado_id uuid NOT NULL REFERENCES radicados(id), operador varchar(20) NOT NULL,
                guia varchar(60) NOT NULL, estado varchar(20) NOT NULL, destinatario text,
                direccion text, motivo_devolucion text);
            """
        )
        yield c
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


async def _persona(c: asyncpg.Connection, clearance: int) -> uuid.UUID:
    uid = await c.fetchval("INSERT INTO auth_users (email) VALUES ($1) RETURNING id", f"{uuid.uuid4().hex[:6]}@e.gov.co")
    gid = uuid.uuid4()
    await c.execute("INSERT INTO role_clearance VALUES ($1, $2)", gid, clearance)
    await c.execute("INSERT INTO auth_memberships VALUES ($1, $2)", uid, gid)
    return uid


async def _envio(c: asyncpg.Connection, *, nivel: int, autor: uuid.UUID | None, estado: str = "devuelto") -> uuid.UUID:
    rid = await c.fetchval(
        "INSERT INTO radicados (tracking_number, subject, origin_dept_code, registered_by, nivel_seguridad) "
        "VALUES ($1, 'Asunto confidencial', 902, $2, $3) RETURNING id",
        f"2026-ITEST-S-{uuid.uuid4().hex[:8]}",
        autor,
        nivel,
    )
    return await c.fetchval(
        "INSERT INTO postal_shipment (radicado_id, operador, guia, estado, destinatario, direccion, motivo_devolucion) "
        "VALUES ($1, '4-72', 'C472-IT01', $2, 'Persona', 'Calle 1', 'Dirección errada') RETURNING id",
        rid,
        estado,
    )


@pytest.mark.asyncio
async def test_gate_en_el_where(conn) -> None:
    sid = await _envio(conn, nivel=3, autor=None)
    assert await conn.fetchrow(devolucion._SQL_CONTENIDO, sid, 2) is None
    assert await conn.fetchrow(devolucion._SQL_CONTENIDO, sid, 1) is None
    fila = await conn.fetchrow(devolucion._SQL_CONTENIDO, sid, 3)
    assert fila is not None and fila["motivo_devolucion"] == "Dirección errada"


@pytest.mark.asyncio
async def test_extremo_a_extremo_con_rbac_real(conn) -> None:
    """El buzón (PUBLICA) y una autora con clearance 2 sobre un radicado de nivel 2."""
    autora = await _persona(conn, 2)
    sid = await _envio(conn, nivel=2, autor=autora)
    ctx = await resolver_contexto_devolucion(conn, sid)
    assert ctx is not None and ctx.operador == "4-72"
    buzon, persona = ctx.destinatarios
    assert buzon.email == "dependencia-902@orpycamcp.local" and buzon.contenido is None
    assert persona.contenido is not None and persona.contenido.nivel == 2
    envios = build_devolucion_notifications({"tenant_slug": TENANT}, contexto=ctx)
    assert "Asunto confidencial" not in envios[0].body and envios[1].sensible is True


@pytest.mark.asyncio
async def test_autora_de_nivel_1_no_ve_un_radicado_de_nivel_3(conn) -> None:
    sid = await _envio(conn, nivel=3, autor=await _persona(conn, 1))
    ctx = await resolver_contexto_devolucion(conn, sid)
    assert ctx is not None and all(d.contenido is None for d in ctx.destinatarios)


@pytest.mark.asyncio
async def test_un_envio_que_no_esta_devuelto_no_avisa(conn) -> None:
    sid = await _envio(conn, nivel=1, autor=None, estado="en_transito")
    assert await resolver_contexto_devolucion(conn, sid) is None


@pytest.mark.asyncio
async def test_tenedor_y_entrada_reciben_aviso_con_el_gate_real(conn) -> None:
    """H8 contra Postgres: la entrada a la que responde (con su poseedor) y el tenedor actual."""
    tenedor = await _persona(conn, 1)
    poseedor_entrada = await _persona(conn, 1)
    entrada = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, origin_dept_code, ubicacion_dept_code, flujo_usuario, nivel_seguridad) "
        "VALUES ($1, 100, 100, $2, 1) RETURNING id",
        f"2026-ITEST-E-{uuid.uuid4().hex[:8]}",
        poseedor_entrada,
    )
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, subject, origin_dept_code, ubicacion_dept_code, "
        "flujo_usuario, responde_a, nivel_seguridad) VALUES ($1, 'Reservado', 902, 310, $2, $3, 2) RETURNING id",
        f"2026-ITEST-S-{uuid.uuid4().hex[:8]}",
        tenedor,
        entrada,
    )
    sid = await conn.fetchval(
        "INSERT INTO postal_shipment (radicado_id, operador, guia, estado, destinatario, direccion, motivo_devolucion) "
        "VALUES ($1, '4-72', 'C472-IT02', 'devuelto', 'P', 'C', 'm') RETURNING id",
        rid,
    )
    ctx = await resolver_contexto_devolucion(conn, sid)
    assert ctx is not None
    emails = [d.email for d in ctx.destinatarios]
    assert "dependencia-902@orpycamcp.local" in emails
    assert "dependencia-310@orpycamcp.local" in emails and "dependencia-100@orpycamcp.local" in emails
    assert len(emails) == 5  # 902, 310, 100 + tenedor + poseedor de la entrada (sin autor)
    # Todos con clearance 1 sobre un radicado de nivel 2: ninguno recibe contenido.
    assert all(d.contenido is None for d in ctx.destinatarios)
