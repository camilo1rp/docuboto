# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La máquina de estados del lote de eliminación, contra PostgreSQL real.

**Por qué este carril y no el de dobles.** El test unitario de «un lote que ya
no está en borrador no se republica» mockeaba `fetchrow` devolviendo `None`… y
al mutar el `WHERE ... AND estado = 'borrador'` **siguió en verde**: el «0
filas» lo daba el mock, no la condición. Es la misma trampa que ya se había
pagado en este repositorio con un test de un-solo-uso, y la conclusión es la
misma: **una condición de carrera se prueba donde el rollback y el conteo de
filas los produce Postgres**, o no se prueba.

Aquí se ejerce el motor a propósito con SQL crudo, bypaseando la capa de
servicio: lo que se comprueba son los `CHECK` y el trigger de la migración 047,
que son la última barrera y la que nadie puede saltarse desde el código.

Gating: `RUN_INTEGRATION=1` + Postgres real (ver CLAUDE.md de este servicio).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration

MANANA = date.today() + timedelta(days=30)


async def _sembrar_lote(conn: asyncpg.Connection, schema: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Un expediente y un lote en `borrador` con un ítem."""
    await conn.execute(f"SET search_path TO {tenant_schema(schema)}")
    exp_id = await conn.fetchval(
        """
        INSERT INTO expedientes (code, name, status, nivel_seguridad)
        VALUES ($1, 'Contratos', 'closed', 1) RETURNING id
        """,
        f"EXP-IT-{uuid.uuid4().hex[:6]}",
    )
    lote_id = await conn.fetchval(
        """
        INSERT INTO eliminacion_lotes (codigo, creado_por)
        VALUES ($1, gen_random_uuid()) RETURNING id
        """,
        f"LOTE-IT-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        """
        INSERT INTO eliminacion_lote_items (
            lote_id, expediente_id, expediente_code, disposition_code,
            fecha_vencimiento, nivel_seguridad
        ) VALUES ($1, $2, 'EXP-IT', 'E', '2020-01-01', 1)
        """,
        lote_id,
        exp_id,
    )
    return lote_id, exp_id


@pytest.mark.asyncio
async def test_publicar_dos_veces_solo_afecta_a_una_fila(tenant_ctx) -> None:
    """LA razón de ser de este archivo: el `WHERE ... AND estado = 'borrador'`
    lo cuenta POSTGRES. El segundo `UPDATE` toca **cero** filas — y es eso,
    no un mock, lo que hace que el servicio devuelva 409."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)

    primera = await conn.execute(
        "UPDATE eliminacion_lotes SET estado='publicado', publicado_at=now(), "
        "publicado_hasta=$2 WHERE id=$1 AND estado='borrador'",
        lote_id,
        MANANA,
    )
    segunda = await conn.execute(
        "UPDATE eliminacion_lotes SET estado='publicado', publicado_at=now(), "
        "publicado_hasta=$2 WHERE id=$1 AND estado='borrador'",
        lote_id,
        MANANA,
    )

    assert primera == "UPDATE 1"
    assert segunda == "UPDATE 0"


@pytest.mark.asyncio
async def test_publicar_sin_plazo_lo_rechaza_el_motor(tenant_ctx) -> None:
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)

    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "UPDATE eliminacion_lotes SET estado='publicado', publicado_at=now() WHERE id=$1",
            lote_id,
        )


@pytest.mark.asyncio
async def test_el_inventario_se_congela_al_publicar(tenant_ctx) -> None:
    """En `borrador` se puede quitar una fila; publicado, ya no. Lo publicado
    es sobre lo que un tercero decidió no oponerse."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)

    borrado = await conn.execute(
        "DELETE FROM eliminacion_lote_items WHERE lote_id=$1", lote_id
    )
    assert borrado == "DELETE 1"


@pytest.mark.asyncio
async def test_publicado_el_inventario_ya_no_se_toca(tenant_ctx) -> None:
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    await conn.execute(
        "UPDATE eliminacion_lotes SET estado='publicado', publicado_at=now(), "
        "publicado_hasta=$2 WHERE id=$1",
        lote_id,
        MANANA,
    )

    with pytest.raises(asyncpg.PostgresError):
        await conn.execute("DELETE FROM eliminacion_lote_items WHERE lote_id=$1", lote_id)


@pytest.mark.asyncio
async def test_el_inventario_no_se_edita_nunca(tenant_ctx) -> None:
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)

    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "UPDATE eliminacion_lote_items SET expediente_code='OTRO' WHERE lote_id=$1",
            lote_id,
        )


@pytest.mark.asyncio
async def test_avalar_sin_acta_lo_rechaza_el_motor(tenant_ctx) -> None:
    """Una aprobación sin acta, fecha y acto administrativo es la misma
    afirmación no verificada que convalidar una TRD sin sus tres campos."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)

    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "UPDATE eliminacion_lotes SET estado='avalado' WHERE id=$1", lote_id
        )
