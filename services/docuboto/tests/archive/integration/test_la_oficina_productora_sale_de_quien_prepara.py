# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La cabecera del FUID sale de la dependencia de quien PREPARA (migración 052).

El Anexo FUID del Ac. AGN 001/2024 abre con unidad administrativa, oficina
productora y objeto. Los tres existían como parámetros y nadie los enviaba: el
acta de entrega se congelaba, se le calculaba la huella y se sellaba en XAdES-T
con la cabecera EN BLANCO — un sello exacto sobre un documento que no
identificaba la entrega ([[acta-sellada-sobre-inventario-vacio]], mismo patrón).

`dependencia_de_usuario` resuelve la oficina desde el CONTEXTO ACTIVO del
usuario y, si no lo tiene, desde su URD principal; la unidad administrativa es
la dependencia padre. Contra Postgres real y no con mocks porque lo que hay que
probar es exactamente eso: el `COALESCE` entre las dos fuentes, el `LEFT JOIN`
al padre, y que la ausencia de las tablas de OTROS servicios degrada a `None` en
vez de tumbar la preparación de la transferencia.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from archive.core.database import tenant_schema
from archive.repositories.transferencia_repository import TransferenciaRepository

pytestmark = pytest.mark.asyncio


async def _sp(conn: asyncpg.Connection, slug: str) -> None:
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')


async def _sembrar_organigrama(conn: asyncpg.Connection) -> None:
    """Secretaría General (100) con la Oficina de Contratación (120) colgando."""
    padre = await conn.fetchval(
        "INSERT INTO dependencias (codigo, sigla, nombre) "
        "VALUES (100, 'SG', 'Secretaria General') RETURNING id"
    )
    await conn.execute(
        "INSERT INTO dependencias (parent_id, codigo, sigla, nombre) "
        "VALUES ($1, 120, 'OC', 'Oficina de Contratacion')",
        padre,
    )


async def test_la_oficina_es_la_dependencia_activa_y_la_unidad_es_su_padre(
    tenant_ctx,
) -> None:
    slug, conn = tenant_ctx
    await _sp(conn, slug)
    await _sembrar_organigrama(conn)
    user_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth_users (id, username, active_depe_id) VALUES ($1, 'ANA', 120)",
        user_id,
    )

    depe = await TransferenciaRepository(conn, slug).dependencia_de_usuario(user_id)

    assert depe is not None
    assert depe["oficina_productora_codigo"] == 120
    assert depe["oficina_productora_nombre"] == "Oficina de Contratacion"
    # La unidad administrativa NO es la propia oficina: es la de arriba.
    assert depe["unidad_administrativa_codigo"] == 100
    assert depe["unidad_administrativa_nombre"] == "Secretaria General"


async def test_sin_contexto_activo_manda_la_urd_principal(tenant_ctx) -> None:
    """Quien no ha cambiado de contexto no tiene `active_depe_id`. Dejarlo en
    blanco sería perder el dato teniéndolo: su URD principal dice a qué
    dependencia pertenece."""
    slug, conn = tenant_ctx
    await _sp(conn, slug)
    await _sembrar_organigrama(conn)
    user_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth_users (id, username, active_depe_id) VALUES ($1, 'BETO', NULL)",
        user_id,
    )
    await conn.execute(
        "WITH rol AS (INSERT INTO auth_groups (name) VALUES ('itest-rol') RETURNING id) "
        "INSERT INTO urd_usuaroldep (user_id, depe_id, rol_id, es_principal, is_active) "
        "SELECT $1, 120, rol.id, TRUE, TRUE FROM rol",
        user_id,
    )

    depe = await TransferenciaRepository(conn, slug).dependencia_de_usuario(user_id)

    assert depe is not None
    assert depe["oficina_productora_codigo"] == 120
    assert depe["unidad_administrativa_nombre"] == "Secretaria General"


async def test_una_urd_inactiva_no_cuenta(tenant_ctx) -> None:
    """Control negativo: una URD dada de baja no puede seguir declarando la
    oficina productora de un acta que se firma."""
    slug, conn = tenant_ctx
    await _sp(conn, slug)
    await _sembrar_organigrama(conn)
    user_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth_users (id, username, active_depe_id) VALUES ($1, 'CARO', NULL)",
        user_id,
    )
    await conn.execute(
        "WITH rol AS (INSERT INTO auth_groups (name) VALUES ('itest-rol') RETURNING id) "
        "INSERT INTO urd_usuaroldep (user_id, depe_id, rol_id, es_principal, is_active) "
        "SELECT $1, 120, rol.id, TRUE, FALSE FROM rol",
        user_id,
    )

    depe = await TransferenciaRepository(conn, slug).dependencia_de_usuario(user_id)

    assert depe is None


async def test_una_oficina_raiz_es_su_propia_unidad_administrativa(tenant_ctx) -> None:
    """Sin padre no hay hueco que dejar: la oficina raíz ES su unidad
    administrativa. Repetirla dice la verdad; dejarla vacía no."""
    slug, conn = tenant_ctx
    await _sp(conn, slug)
    await conn.execute(
        "INSERT INTO dependencias (codigo, sigla, nombre) "
        "VALUES (100, 'SG', 'Secretaria General')"
    )
    user_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth_users (id, username, active_depe_id) VALUES ($1, 'DORA', 100)",
        user_id,
    )

    depe = await TransferenciaRepository(conn, slug).dependencia_de_usuario(user_id)

    assert depe is not None
    assert depe["oficina_productora_codigo"] == 100
    assert depe["unidad_administrativa_codigo"] == 100
    assert depe["unidad_administrativa_nombre"] == "Secretaria General"


async def test_sin_los_catalogos_de_otros_servicios_degrada_a_none(tenant_ctx) -> None:
    """Sin `dependencias` ni `auth_users` (despliegue parcial), la preparación de la
    transferencia no puede morir: la cabecera queda declaradamente vacía."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    await conn.execute(f'ALTER TABLE "{schema}".dependencias RENAME TO dependencias_fuera')
    await conn.execute(f'ALTER TABLE "{schema}".auth_users RENAME TO auth_users_fuera')
    await _sp(conn, slug)

    depe = await TransferenciaRepository(conn, slug).dependencia_de_usuario(uuid.uuid4())

    assert depe is None


async def test_auth_users_sin_active_depe_id_tambien_degrada(tenant_ctx) -> None:
    """El caso que ya mordió una vez: la tabla EXISTE pero con otra forma. Un
    esquema de pruebas crea `auth_users` con lo mínimo que necesita el
    clearance, sin `active_depe_id` — comprobar la tabla es comprobar la mitad
    y fallar igual. La guarda mira la COLUMNA."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    await conn.execute(f'ALTER TABLE "{schema}".auth_users RENAME TO auth_users_fuera')
    await conn.execute(
        f'CREATE TABLE "{schema}".auth_users (id UUID PRIMARY KEY, is_root BOOLEAN DEFAULT FALSE)'
    )
    await _sp(conn, slug)

    depe = await TransferenciaRepository(conn, slug).dependencia_de_usuario(uuid.uuid4())

    assert depe is None
