# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La copia congelada del contacto, contra PostgreSQL real — E01, RF-RAD-05.

**Por qué este carril y no el de dobles.** Lo que hay que demostrar aquí no es
que el código llame a las funciones correctas: es que **la base impide
falsificar el pasado**. Con un doble, «no se pudo editar» lo diría el doble. La
inmutabilidad la impone un trigger y la deduplicación un índice único: o los
ejerce Postgres, o no están probados.

Los cuatro invariantes salen de dos problemas reales del Orfeo original: el
mismo sujeto repetido treinta veces por atar identidad y contacto, y la
dirección de un envío cambiando bajo los pies del registro.

Gating: `RUN_INTEGRATION=1` + Postgres real.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


async def _sembrar(conn: asyncpg.Connection, schema: str) -> tuple[uuid.UUID, uuid.UUID]:
    await conn.execute(f"SET search_path TO {tenant_schema(schema)}")
    radicado_id = await conn.fetchval(
        """
        INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code)
        VALUES ($1, 'S', 2026, 1, 'Oficio de prueba', 'IT') RETURNING id
        """,
        f"2026-IT-S-{uuid.uuid4().hex[:6]}",
    )
    tercero_id = await conn.fetchval(
        """
        INSERT INTO tercero (tipo, nombre, tipo_documento, numero_documento, direccion)
        VALUES ('ciudadano', 'José Pérez', 'CC', $1, 'Calle 1 #2-3') RETURNING id
        """,
        uuid.uuid4().hex[:10],
    )
    return radicado_id, tercero_id


async def _vincular(conn, radicado_id, tercero_id, *, direccion, orden=0):
    await conn.execute(
        """
        INSERT INTO radicado_actor (radicado_id, tercero_id, rol, orden, nombre, direccion)
        VALUES ($1, $2, 'destinatario', $3, 'José Pérez', $4)
        """,
        radicado_id,
        tercero_id,
        orden,
        direccion,
    )


@pytest.mark.asyncio
async def test_cambiar_la_ficha_NO_cambia_a_donde_se_envio(tenant_ctx) -> None:
    """El invariante que pidió el usuario. Si una correspondencia salió a una
    dirección y mañana se actualiza la ficha, sin la copia congelada todos los
    envíos pasados dirían que se enviaron a otra parte: **información errónea
    sobre un acto hecho con un dato que era verdad entonces**."""
    schema, conn = tenant_ctx
    radicado_id, tercero_id = await _sembrar(conn, schema)
    await _vincular(conn, radicado_id, tercero_id, direccion="Calle 1 #2-3")

    await conn.execute(
        "UPDATE tercero SET direccion = 'Avenida Nueva 99' WHERE id = $1", tercero_id
    )

    congelada = await conn.fetchval(
        "SELECT direccion FROM radicado_actor WHERE radicado_id = $1", radicado_id
    )
    assert congelada == "Calle 1 #2-3"


@pytest.mark.asyncio
async def test_la_copia_congelada_NO_se_puede_editar(tenant_ctx) -> None:
    """Ni siquiera a propósito: es la evidencia de a dónde se envió."""
    schema, conn = tenant_ctx
    radicado_id, tercero_id = await _sembrar(conn, schema)
    await _vincular(conn, radicado_id, tercero_id, direccion="Calle 1 #2-3")

    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "UPDATE radicado_actor SET direccion = 'Otra' WHERE radicado_id = $1", radicado_id
        )
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "UPDATE radicado_actor SET tercero_id = gen_random_uuid() WHERE radicado_id = $1",
            radicado_id,
        )


@pytest.mark.asyncio
async def test_el_orden_SI_se_puede_ajustar(tenant_ctx) -> None:
    """El control positivo. Sin él, un trigger que bloqueara TODO update
    pasaría el test de arriba y dejaría el radicado sin poder componerse."""
    schema, conn = tenant_ctx
    radicado_id, tercero_id = await _sembrar(conn, schema)
    await _vincular(conn, radicado_id, tercero_id, direccion="Calle 1 #2-3")

    await conn.execute(
        "UPDATE radicado_actor SET orden = 3 WHERE radicado_id = $1", radicado_id
    )
    assert (
        await conn.fetchval(
            "SELECT orden FROM radicado_actor WHERE radicado_id = $1", radicado_id
        )
        == 3
    )


@pytest.mark.asyncio
async def test_la_misma_persona_en_DOS_direcciones_es_legitimo(tenant_ctx) -> None:
    """Pasa, y hay que poder hacerlo. Deduplicar solo por identidad lo
    impediría."""
    schema, conn = tenant_ctx
    radicado_id, tercero_id = await _sembrar(conn, schema)

    await _vincular(conn, radicado_id, tercero_id, direccion="Calle 1 #2-3")
    await _vincular(conn, radicado_id, tercero_id, direccion="Carrera 9 #10-11", orden=1)

    total = await conn.fetchval(
        "SELECT count(*) FROM radicado_actor WHERE radicado_id = $1", radicado_id
    )
    assert total == 2


@pytest.mark.asyncio
async def test_la_misma_persona_en_la_MISMA_direccion_es_un_duplicado(tenant_ctx) -> None:
    schema, conn = tenant_ctx
    radicado_id, tercero_id = await _sembrar(conn, schema)
    await _vincular(conn, radicado_id, tercero_id, direccion="Calle 1 #2-3")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _vincular(conn, radicado_id, tercero_id, direccion="Calle 1 #2-3", orden=1)


@pytest.mark.asyncio
async def test_dos_terceros_no_comparten_documento_de_identidad(tenant_ctx) -> None:
    """La deduplicación DURA. «José Pérez» y «JOSE PEREZ» con la misma cédula
    son la misma persona, por mucho que se escriban distinto."""
    schema, conn = tenant_ctx
    await conn.execute(f"SET search_path TO {tenant_schema(schema)}")
    documento = uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO tercero (tipo, nombre, tipo_documento, numero_documento) "
        "VALUES ('ciudadano', 'José Pérez', 'CC', $1)",
        documento,
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            "INSERT INTO tercero (tipo, nombre, tipo_documento, numero_documento) "
            "VALUES ('ciudadano', 'JOSE PEREZ', 'CC', $1)",
            documento,
        )


@pytest.mark.asyncio
async def test_la_busqueda_encuentra_sin_tildes_ni_mayusculas(tenant_ctx) -> None:
    """La defensa que MÁS duplicados evita: los duplicados no se crean por
    saltarse un índice, se crean por no encontrar lo que ya está."""
    from app.repositories.tercero_repository import TerceroRepository

    schema, conn = tenant_ctx
    await conn.execute(f"SET search_path TO {tenant_schema(schema)}")
    await conn.execute(
        "INSERT INTO tercero (tipo, nombre) VALUES ('ciudadano', 'José Pérez Gómez')"
    )
    repo = TerceroRepository(conn, schema)

    encontrados = await repo.buscar("jose perez", tipo=None, limit=10)

    assert any(t["nombre"] == "José Pérez Gómez" for t in encontrados)


# ── El canal: condición material del envío ──────────────────────────────────


@pytest.mark.asyncio
async def test_un_envio_IMPOSIBLE_no_se_puede_ni_registrar(tenant_ctx) -> None:
    """Sin dirección no hay envío físico y sin correo no hay electrónico. Lo
    impone la base, no la pantalla: dejarlo registrado haría que el radicado
    prometiera algo incumplible, y el fallo aparecería al despachar."""
    schema, conn = tenant_ctx
    radicado_id, tercero_id = await _sembrar(conn, schema)

    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "INSERT INTO radicado_actor (radicado_id, tercero_id, rol, nombre, canal) "
            "VALUES ($1, $2, 'destinatario', 'José Pérez', 'fisico')",
            radicado_id,
            tercero_id,
        )
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "INSERT INTO radicado_actor (radicado_id, tercero_id, rol, nombre, canal, direccion) "
            "VALUES ($1, $2, 'destinatario', 'José Pérez', 'email', 'Calle 1')",
            radicado_id,
            tercero_id,
        )


@pytest.mark.asyncio
async def test_el_canal_se_puede_decidir_DESPUES_si_el_dato_esta(tenant_ctx) -> None:
    """`NULL` = todavía sin decidir, y se admite a propósito: obligar a elegirlo
    al vincular empujaría a poner un canal cualquiera para poder seguir."""
    schema, conn = tenant_ctx
    radicado_id, tercero_id = await _sembrar(conn, schema)
    await _vincular(conn, radicado_id, tercero_id, direccion="Calle 1 #2-3")

    await conn.execute(
        "UPDATE radicado_actor SET canal = 'fisico' WHERE radicado_id = $1", radicado_id
    )
    assert (
        await conn.fetchval(
            "SELECT canal FROM radicado_actor WHERE radicado_id = $1", radicado_id
        )
        == "fisico"
    )

    # Y NO se puede fijar un canal cuyo dato falta, tampoco después.
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "UPDATE radicado_actor SET canal = 'ambos' WHERE radicado_id = $1", radicado_id
        )
