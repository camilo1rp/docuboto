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
from datetime import UTC, date, datetime, timedelta

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.archive_repository import ArchiveRepository

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


# ── Fase 3: el aval y la anulación, con el repositorio REAL ──────────────────
#
# Aquí ya no se ejerce SQL crudo sino `ArchiveRepository`: lo que se prueba es
# que sus `WHERE` deciden de verdad. El unitario solo puede afirmar sobre el
# SQL emitido — con un doble, el «0 filas» lo da el doble.


async def _publicar(conn: asyncpg.Connection, lote_id: uuid.UUID, hasta: date) -> None:
    await conn.execute(
        "UPDATE eliminacion_lotes SET estado='publicado', publicado_at=now(), "
        "publicado_hasta=$2 WHERE id=$1",
        lote_id,
        hasta,
    )


@pytest.mark.asyncio
async def test_no_se_avala_con_el_plazo_de_publicidad_TODAVIA_abierto(tenant_ctx) -> None:
    """La regla de fondo de la fase 3, decidida por Postgres: mientras
    `publicado_hasta >= CURRENT_DATE` el `UPDATE` no toca ninguna fila.
    Autorizar la destrucción antes de que venza el plazo que la entidad dio
    para oponerse convertiría la publicidad previa en un adorno."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    await _publicar(conn, lote_id, MANANA)
    repo = ArchiveRepository(conn, schema)

    fila = await repo.avalar_lote_eliminacion(
        lote_id,
        acta_comite="Acta 07",
        fecha_aval=date.today(),
        acto_administrativo="Res. 512 de 2026",
        avalado_por=uuid.uuid4(),
    )

    assert fila is None
    # Y el diagnóstico sabe DECIR por qué: el plazo, no el estado.
    estado = await repo.get_lote_estado(lote_id)
    assert estado["estado"] == "publicado"
    assert estado["publicidad_abierta"] is True


@pytest.mark.asyncio
async def test_vencido_el_plazo_el_aval_SI_se_registra(tenant_ctx) -> None:
    """El control positivo: sin él, el test de arriba pasaría con un `WHERE`
    que no dejara avalar nunca."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    await _publicar(conn, lote_id, date.today() - timedelta(days=1))
    repo = ArchiveRepository(conn, schema)

    fila = await repo.avalar_lote_eliminacion(
        lote_id,
        acta_comite="Acta 07",
        fecha_aval=date.today(),
        acto_administrativo="Res. 512 de 2026",
        avalado_por=uuid.uuid4(),
    )

    assert fila is not None
    assert fila["estado"] == "avalado"
    assert fila["avalado_at"] is not None


@pytest.mark.asyncio
async def test_un_lote_EJECUTADO_no_se_anula_pero_uno_avalado_si(tenant_ctx) -> None:
    """Lo destruido no se des-destruye. Se retira hasta el último momento —
    incluso ya avalado— y nunca después."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    otro_id, _ = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    aval = (
        "acta_comite='Acta 07', fecha_aval=CURRENT_DATE, "
        "acto_administrativo='Res. 512', avalado_por=gen_random_uuid(), avalado_at=now()"
    )
    await _publicar(conn, lote_id, date.today() - timedelta(days=1))
    await conn.execute(
        f"UPDATE eliminacion_lotes SET estado='ejecutado', ejecutado_at=now(), {aval} "
        "WHERE id=$1",
        lote_id,
    )
    await _publicar(conn, otro_id, date.today() - timedelta(days=1))
    await conn.execute(
        f"UPDATE eliminacion_lotes SET estado='avalado', {aval} WHERE id=$1", otro_id
    )

    ejecutado = await repo.anular_lote_eliminacion(
        lote_id, motivo="Se retira por error material", anulado_por=uuid.uuid4()
    )
    avalado = await repo.anular_lote_eliminacion(
        otro_id, motivo="Se retira por error material", anulado_por=uuid.uuid4()
    )

    assert ejecutado is None
    assert avalado is not None and avalado["estado"] == "anulado"


@pytest.mark.asyncio
async def test_un_lote_publicado_y_luego_anulado_SIGUE_siendo_publico(tenant_ctx) -> None:
    """El chokepoint público está escrito por la MARCA del acto
    (`publicado_at IS NOT NULL`). Con la lista de estados que había antes
    —publicado/avalado/ejecutado— este lote desaparecía de la vista pública,
    justo de quien leyó el inventario y esperaba saber en qué quedó."""
    schema, conn = tenant_ctx
    publicado_id, _ = await _sembrar_lote(conn, schema)
    borrador_id, _ = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    await _publicar(conn, publicado_id, MANANA)
    await repo.anular_lote_eliminacion(
        publicado_id, motivo="Oposición de un tercero admitida", anulado_por=uuid.uuid4()
    )

    publicos = {r["id"] for r in await repo.list_lotes_publicos()}
    lote, _items = await repo.get_lote_publico(publicado_id)

    assert publicado_id in publicos
    assert lote is not None and lote["estado"] == "anulado"
    assert lote["anulado_motivo"] == "Oposición de un tercero admitida"
    # Y el que nunca se publicó sigue sin existir para el público.
    assert borrador_id not in publicos
    assert (await repo.get_lote_publico(borrador_id))[0] is None


# ── Fase 4: el acta, contra el motor ────────────────────────────────────────


async def _avalar_en_bd(conn: asyncpg.Connection, lote_id: uuid.UUID) -> None:
    await _publicar(conn, lote_id, date.today() - timedelta(days=1))
    await conn.execute(
        """
        UPDATE eliminacion_lotes
           SET estado='avalado', acta_comite='Acta 07', fecha_aval=CURRENT_DATE,
               acto_administrativo='Res. 512', avalado_por=gen_random_uuid(),
               avalado_at=now()
         WHERE id=$1
        """,
        lote_id,
    )


@pytest.mark.asyncio
async def test_el_acta_no_se_edita_ni_se_borra_JAMAS(tenant_ctx) -> None:
    """Conservación permanente, y el motor es quien lo garantiza. Un acta que
    se pudiera reescribir no serviría para lo único que sirve: dar fe de una
    destrucción irreversible cuando ya no queda nada con qué contrastarla."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    await _avalar_en_bd(conn, lote_id)
    await conn.execute(
        """
        INSERT INTO eliminacion_acta (lote_id, acta_xml, acta_sha256, metodo_borrado, generado_por)
        VALUES ($1, '<a/>', repeat('a', 64), 'Borrado seguro y picado', gen_random_uuid())
        """,
        lote_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        await conn.execute("UPDATE eliminacion_acta SET acta_xml='<b/>' WHERE lote_id=$1", lote_id)
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute("DELETE FROM eliminacion_acta WHERE lote_id=$1", lote_id)


@pytest.mark.asyncio
async def test_un_lote_CON_acta_ya_no_se_puede_borrar(tenant_ctx) -> None:
    """Consecuencia deliberada del par trigger + `ON DELETE CASCADE`: la
    cascada intenta llevarse el acta y el trigger aborta la operación entera.
    Un lote deja de ser desechable en cuanto existe el documento que da fe de
    lo que se destruyó."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    await _avalar_en_bd(conn, lote_id)
    await conn.execute(
        """
        INSERT INTO eliminacion_acta (lote_id, acta_xml, acta_sha256, metodo_borrado, generado_por)
        VALUES ($1, '<a/>', repeat('a', 64), 'Borrado seguro y picado', gen_random_uuid())
        """,
        lote_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        await conn.execute("DELETE FROM eliminacion_lotes WHERE id=$1", lote_id)


@pytest.mark.asyncio
async def test_el_acta_no_se_genera_dos_veces(tenant_ctx) -> None:
    """`ON CONFLICT DO NOTHING` lo resuelve POSTGRES: el segundo intento
    devuelve `None` y el servicio lo convierte en 409. Regenerarla produciría
    otro instante y otro SHA para una eliminación que ya tenía el suyo."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    await _avalar_en_bd(conn, lote_id)
    repo = ArchiveRepository(conn, schema)
    args = dict(
        acta_xml="<a/>",
        acta_sha256="b" * 64,
        metodo_borrado="Borrado seguro y picado de soportes",
        observaciones=None,
        generado_por=uuid.uuid4(),
        generado_at=datetime.now(UTC),
    )

    primera = await repo.insert_acta_eliminacion(lote_id, **args)
    segunda = await repo.insert_acta_eliminacion(lote_id, **args)

    assert primera is not None
    assert segunda is None


@pytest.mark.asyncio
async def test_el_nivel_para_leer_el_acta_sale_del_inventario(tenant_ctx) -> None:
    """El umbral se DERIVA del contenido, no de un campo declarado: manda el
    expediente más clasificado del lote, porque el acta se firma entera y no se
    puede servir recortada."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    exp_id = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'Historias', 'closed', 3) RETURNING id",
        f"EXP-IT-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        """
        INSERT INTO eliminacion_lote_items (
            lote_id, expediente_id, expediente_code, disposition_code,
            fecha_vencimiento, nivel_seguridad
        ) VALUES ($1, $2, 'EXP-IT-CLAS', 'E', '2020-01-01', 3)
        """,
        lote_id,
        exp_id,
    )

    # El lote ya traía un ítem de nivel 1 desde `_sembrar_lote`: manda el 3.
    assert await repo.max_nivel_items_lote(lote_id) == 3


@pytest.mark.asyncio
async def test_el_inventario_del_acta_NO_se_recorta(tenant_ctx) -> None:
    """La única lectura del inventario que no se acota, y a propósito: lo que
    construye el acta tiene que ser lo que se destruye."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    exp_id = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'Historias', 'closed', 3) RETURNING id",
        f"EXP-IT-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        """
        INSERT INTO eliminacion_lote_items (
            lote_id, expediente_id, expediente_code, disposition_code,
            fecha_vencimiento, nivel_seguridad
        ) VALUES ($1, $2, 'EXP-IT-CLAS', 'E', '2020-01-01', 3)
        """,
        lote_id,
        exp_id,
    )

    completo = await repo.list_items_lote_completo(lote_id)
    _lote, recortado = await repo.get_lote_eliminacion(lote_id, max_clearance=1)

    assert len(completo) == 2
    assert len(recortado) == 1


# ── Fase 5: el borrado, contra el motor ─────────────────────────────────────
#
# Aquí es donde estas garantías se prueban de verdad. Con dobles, «el expediente
# se borró» lo afirma el doble; lo que hay que comprobar es que las cascadas y
# las claves foráneas de la migración 049 dejan vivo exactamente lo que debe
# quedar vivo, y eso lo decide PostgreSQL.


@pytest.mark.asyncio
async def test_borrar_el_expediente_DEJA_VIVO_lo_que_debe_sobrevivir(tenant_ctx) -> None:
    """La migración 049 soltó tres claves foráneas. Dos BLOQUEABAN el borrado
    (el inventario del lote y la decisión de disposición) y una se habría
    llevado por delante las transferencias **con su acta**, que es un
    instrumento de control que debe sobrevivir al contenido (Ley 594/2000 art.
    26). Sin este test, esa pérdida habría sido silenciosa."""
    schema, conn = tenant_ctx
    lote_id, exp_id = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    await conn.execute(
        """
        INSERT INTO disposicion_decisiones
            (expediente_id, fase, decision, fecha_vencimiento, disposition_code, decidido_por)
        VALUES ($1, 'central', 'confirmada', '2026-01-01', 'E', gen_random_uuid())
        """,
        exp_id,
    )
    await conn.execute(
        """
        INSERT INTO expediente_eventos (expediente_id, accion) VALUES ($1, 'cerrado')
        """,
        exp_id,
    )

    borrado = await repo.delete_expediente(exp_id)

    assert borrado is True
    assert await conn.fetchval(
        "SELECT count(*) FROM eliminacion_lote_items WHERE expediente_id=$1", exp_id
    ) == 1
    assert await conn.fetchval(
        "SELECT count(*) FROM disposicion_decisiones WHERE expediente_id=$1", exp_id
    ) == 1
    # La hoja de ruta nunca tuvo clave foránea: sobrevive desde siempre.
    assert await conn.fetchval(
        "SELECT count(*) FROM expediente_eventos WHERE expediente_id=$1", exp_id
    ) == 1


@pytest.mark.asyncio
async def test_borrar_el_expediente_SE_LLEVA_lo_que_describe_su_contenido(tenant_ctx) -> None:
    """El control positivo del test de arriba: sin él, pasaría igual con unas
    claves foráneas que no borraran nada. El vínculo a radicados y el AIP
    DESCRIBEN un contenido que ya no existe, y mueren con él."""
    schema, conn = tenant_ctx
    _lote_id, exp_id = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    await conn.execute(
        "INSERT INTO expediente_radicados (expediente_id, radicado_id, tracking_number) "
        "VALUES ($1, gen_random_uuid(), '2026-DEMO-E-000001')",
        exp_id,
    )
    await conn.execute("INSERT INTO expediente_aip (expediente_id) VALUES ($1)", exp_id)

    await repo.delete_expediente(exp_id)

    assert await conn.fetchval(
        "SELECT count(*) FROM expediente_radicados WHERE expediente_id=$1", exp_id
    ) == 0
    assert await conn.fetchval(
        "SELECT count(*) FROM expediente_aip WHERE expediente_id=$1", exp_id
    ) == 0


@pytest.mark.asyncio
async def test_la_lapida_no_se_edita_ni_se_borra(tenant_ctx) -> None:
    """Es lo único que queda. Una lápida editable no acredita nada."""
    schema, conn = tenant_ctx
    lote_id, exp_id = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    datos = {
        "expediente_id": exp_id,
        "expediente_code": "EXP-2020-0001",
        "trd_serie_code": "DEM-100",
        "fecha_extrema_inicial": date(2020, 1, 1),
        "fecha_extrema_final": date(2020, 12, 31),
        "radicados_eliminados": 12,
        "anexos_eliminados": 30,
        "lote_id": lote_id,
        "lote_codigo": "LOTE-IT",
        "acta_comite": "Acta 07",
        "acto_administrativo": "Res. 512",
        "acta_sha256": "d" * 64,
        "eliminado_por": uuid.uuid4(),
    }

    assert await repo.insert_lapida(datos) is not None
    # Y no se duplica: un reintento tras un fallo a medias no la reescribe.
    assert await repo.insert_lapida(datos) is None

    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "UPDATE expediente_lapida SET radicados_eliminados = 0 WHERE expediente_id=$1", exp_id
        )
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute("DELETE FROM expediente_lapida WHERE expediente_id=$1", exp_id)


@pytest.mark.asyncio
async def test_la_lapida_SOBREVIVE_al_expediente(tenant_ctx) -> None:
    """No tiene clave foránea a `expedientes` a propósito: si la tuviera, el
    borrado se la llevaría y la única respuesta a «¿qué pasó con este
    expediente?» sería el silencio — indistinguible de «nunca existió»."""
    schema, conn = tenant_ctx
    lote_id, exp_id = await _sembrar_lote(conn, schema)
    repo = ArchiveRepository(conn, schema)
    await repo.insert_lapida(
        {
            "expediente_id": exp_id,
            "expediente_code": "EXP-2020-0001",
            "trd_serie_code": "DEM-100",
            "fecha_extrema_inicial": None,
            "fecha_extrema_final": None,
            "radicados_eliminados": 3,
            "anexos_eliminados": 4,
            "lote_id": lote_id,
            "lote_codigo": "LOTE-IT",
            "acta_comite": "Acta 07",
            "acto_administrativo": "Res. 512",
            "acta_sha256": "e" * 64,
            "eliminado_por": uuid.uuid4(),
        }
    )

    await repo.delete_expediente(exp_id)

    lapida = await repo.get_lapida(exp_id)
    assert lapida is not None
    assert lapida["expediente_code"] == "EXP-2020-0001"
    assert lapida["acto_administrativo"] == "Res. 512"
    # Y el expediente ya no está.
    assert await conn.fetchval("SELECT count(*) FROM expedientes WHERE id=$1", exp_id) == 0


@pytest.mark.asyncio
async def test_un_lote_no_se_ejecuta_dos_veces(tenant_ctx) -> None:
    """Lo cuenta Postgres: el segundo `UPDATE` toca cero filas. Dos clics no
    destruyen dos veces — aunque la segunda ya no quedara nada que destruir, el
    lote afirmaría dos ejecuciones distintas del mismo acto."""
    schema, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, schema)
    await _avalar_en_bd(conn, lote_id)
    repo = ArchiveRepository(conn, schema)

    primera = await repo.marcar_lote_ejecutado(lote_id, ejecutado_por=uuid.uuid4())
    segunda = await repo.marcar_lote_ejecutado(lote_id, ejecutado_por=uuid.uuid4())

    assert primera is not None and primera["estado"] == "ejecutado"
    assert segunda is None
