# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C14 (ADR-043) contra Postgres REAL: el anexo citado por una constancia de firma inobtenible
VIGENTE y su baja de soporte.

Lo que un mock no puede probar y se ejerce aquí con SQL crudo y dos conexiones:

* la vista contrato `v_anexo_citado_vigente` (migración 075): vigente / revocada / baja en lote
  anulado (sigue bloqueando) / baja en lote avalado (deja de bloquear);
* el trigger de `eliminacion_lote_soporte_citado`: append-only salvo UN UPDATE de
  `soporte_eliminado_at`, y la baja sobrevive al borrado del expediente (sin FK);
* `congelar_soportes_citados`: solo constancias vigentes, una vez por constancia;
* `FOR SHARE` de la constancia contra el `FOR UPDATE` de quien purga (la prueba de §C14.2);
* declarar sobre un radicado en un lote vivo: 409 `radicado_en_lote_de_eliminacion`.

Mutaciones que deben caer:
  * quitar `NOT EXISTS (... revocacion)` de la vista -> `test_revocar_libera_la_cita`;
  * quitar el filtro `l.estado IN ('avalado','ejecutado')` -> `test_un_lote_anulado_no_desbloquea`;
  * quitar el `FOR SHARE` -> `test_for_share_espera_a_quien_purga` (la constancia no espera);
  * dejar el trigger sin la restricción de columnas -> `test_la_baja_de_soporte_es_append_only`.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.actor import Actor
from app.core.database import tenant_schema
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.transferencia_service import TransferenciaService

from .conftest import _require_dsn
from .test_firma_inobtenible_sql import (
    ANEXO,
    RAD,
    _acta_recibida,
    _declarar,
    _req,
    _revocar,
)

pytestmark = pytest.mark.integration

_VISTA = "SELECT file_id, excepcion_id, transferencia_id, rol, ordinal FROM v_anexo_citado_vigente"


async def _lote(
    conn: asyncpg.Connection, estado: str, *, expediente_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Un lote en el `estado` pedido (cumpliendo los CHECK de la 047) con, opcionalmente, un ítem."""
    campos = {
        "borrador": "",
        "publicado": ", publicado_at, publicado_hasta",
        "avalado": ", acta_comite, fecha_aval, acto_administrativo, avalado_por",
        "anulado": ", anulado_at, anulado_por, anulado_motivo",
    }[estado]
    valores = {
        "borrador": "",
        "publicado": ", now(), current_date + 30",
        "avalado": ", 'Acta 9', current_date, 'Res. 5', gen_random_uuid()",
        "anulado": ", now(), gen_random_uuid(), 'anulado por la prueba de integracion'",
    }[estado]
    lote_id = await conn.fetchval(
        f"INSERT INTO eliminacion_lotes (codigo, creado_por, estado{campos}) "
        f"VALUES ($1, gen_random_uuid(), $2{valores}) RETURNING id",
        f"L-{uuid.uuid4().hex[:8]}",
        estado,
    )
    if expediente_id is not None:
        await conn.execute(
            "INSERT INTO eliminacion_lote_items (lote_id, expediente_id, expediente_code, "
            "disposition_code, fecha_vencimiento, nivel_seguridad) "
            "VALUES ($1, $2, 'EXP-IT', 'E', '2020-01-01', 1)",
            lote_id,
            expediente_id,
        )
    return lote_id


async def _baja(conn: asyncpg.Connection, lote_id: uuid.UUID, excepcion_id: uuid.UUID) -> None:
    fila = await conn.fetchrow(
        "SELECT transferencia_id, rol, ordinal, referencia_anexo_id "
        "FROM transferencia_acta_firma_excepcion WHERE id = $1",
        excepcion_id,
    )
    await conn.execute(
        "INSERT INTO eliminacion_lote_soporte_citado (lote_id, expediente_id, excepcion_id, "
        "transferencia_id, rol, ordinal, file_id, anexo_sha256) "
        "VALUES ($1, gen_random_uuid(), $2, $3, $4, $5, $6, repeat('a', 64))",
        lote_id,
        excepcion_id,
        fila["transferencia_id"],
        fila["rol"],
        fila["ordinal"],
        fila["referencia_anexo_id"],
    )


async def _constancia(conn: asyncpg.Connection, slug: str, nombre: str):
    """Transferencia recibida + constancia declarada. Devuelve (tid, excepcion_id, admin, svc)."""
    _c, tid, _elabora, _recibe = await _acta_recibida(conn, slug, nombre)
    admin = Actor(id=uuid.uuid4(), username="itest-admin")
    svc = TransferenciaService(conn, slug)
    resp = await _declarar(svc, tid, admin)
    return tid, resp.id, admin, svc


# --- la vista contrato ------------------------------------------------------------------------


async def test_la_constancia_vigente_aparece_en_la_vista_con_las_columnas_del_contrato(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tid, exc_id, _admin, _svc = await _constancia(conn, slug, "itest c14 vista")

    filas = await conn.fetch(_VISTA)
    assert len(filas) == 1
    f = filas[0]
    assert f["file_id"] == ANEXO and f["excepcion_id"] == exc_id
    assert f["transferencia_id"] == tid and f["rol"] == "recibe" and f["ordinal"] == 1


async def test_revocar_libera_la_cita(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    """Control positivo + mutación: sin el `NOT EXISTS (revocacion)` la cita seguiría vigente."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tid, _exc, admin, svc = await _constancia(conn, slug, "itest c14 revocar")
    assert len(await conn.fetch(_VISTA)) == 1

    await _revocar(svc, tid, admin)

    assert await conn.fetch(_VISTA) == []


async def test_un_lote_anulado_o_sin_avalar_no_desbloquea_y_uno_avalado_si(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    _tid, exc_id, _admin, _svc = await _constancia(conn, slug, "itest c14 baja")

    for estado in ("borrador", "publicado", "anulado"):
        await _baja(conn, await _lote(conn, estado), exc_id)
        assert len(await conn.fetch(_VISTA)) == 1, f"un lote {estado} NO da de baja el soporte"

    await _baja(conn, await _lote(conn, "avalado"), exc_id)
    assert await conn.fetch(_VISTA) == [], "el aval del Comité sí desbloquea"


# --- la baja de soporte es evidencia -----------------------------------------------------------


async def test_la_baja_de_soporte_es_append_only(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    _tid, exc_id, _admin, _svc = await _constancia(conn, slug, "itest c14 append")
    lote = await _lote(conn, "borrador")
    await _baja(conn, lote, exc_id)

    # cualquier otra columna: rechazado
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            "UPDATE eliminacion_lote_soporte_citado SET rol = 'entrega' WHERE lote_id = $1", lote
        )
    # el único UPDATE admitido: soporte_eliminado_at de NULL a un valor...
    assert await conn.execute(
        "UPDATE eliminacion_lote_soporte_citado SET soporte_eliminado_at = now() WHERE lote_id = $1",
        lote,
    ) == "UPDATE 1"
    # ... una sola vez
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            "UPDATE eliminacion_lote_soporte_citado SET soporte_eliminado_at = now() "
            "WHERE lote_id = $1",
            lote,
        )
    # ni DELETE directo ni TRUNCATE
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("DELETE FROM eliminacion_lote_soporte_citado WHERE lote_id = $1", lote)
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("TRUNCATE eliminacion_lote_soporte_citado")
    # el borrado del LOTE entero (cascada) sí pasa
    assert await conn.execute("DELETE FROM eliminacion_lotes WHERE id = $1", lote) == "DELETE 1"
    assert await conn.fetchval("SELECT count(*) FROM eliminacion_lote_soporte_citado") == 0


async def test_la_baja_sobrevive_al_borrado_del_expediente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Sin FK a `expedientes`: el expediente desaparece al ejecutar el lote y la baja es evidencia."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    _tid, exc_id, _admin, _svc = await _constancia(conn, slug, "itest c14 sobrevive")
    lote = await _lote(conn, "avalado")
    exp_id = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'x', 'closed', 1) RETURNING id",
        f"EXP-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        "INSERT INTO eliminacion_lote_soporte_citado (lote_id, expediente_id, excepcion_id, "
        "transferencia_id, rol, ordinal, file_id, anexo_sha256) "
        "SELECT $1, $2, id, transferencia_id, rol, ordinal, referencia_anexo_id, repeat('a', 64) "
        "FROM transferencia_acta_firma_excepcion WHERE id = $3",
        lote,
        exp_id,
        exc_id,
    )
    await conn.execute("DELETE FROM expedientes WHERE id = $1", exp_id)
    assert await conn.fetchval(
        "SELECT count(*) FROM eliminacion_lote_soporte_citado WHERE lote_id = $1", lote
    ) == 1


# --- congelar al proponer -----------------------------------------------------------------------


async def test_congelar_solo_toma_constancias_vigentes_y_una_vez_por_constancia(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    await conn.execute(f'SET search_path TO "{schema}"')
    tid, exc_id, admin, svc = await _constancia(conn, slug, "itest c14 congelar")
    exp_a = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'a', 'closed', 1) RETURNING id", f"EXP-{uuid.uuid4().hex[:6]}",
    )
    exp_b = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'b', 'closed', 1) RETURNING id", f"EXP-{uuid.uuid4().hex[:6]}",
    )
    for e in (exp_a, exp_b):  # el MISMO radicado de soporte en dos expedientes del lote
        await conn.execute(
            "INSERT INTO expediente_radicados (expediente_id, radicado_id, tracking_number) "
            "VALUES ($1, $2, 'R-1')", e, RAD,
        )
    lote = await _lote(conn, "borrador")
    repo = ArchiveRepository(conn, slug)

    filas = await repo.congelar_soportes_citados(lote, [exp_a, exp_b])
    assert len(filas) == 1 and filas[0]["excepcion_id"] == exc_id
    assert filas[0]["transferencia_id"] == tid and filas[0]["rol"] == "recibe"
    assert await repo.congelar_soportes_citados(lote, [exp_a, exp_b]) == []  # idempotente

    # una constancia REVOCADA ya no cita nada: no se congela
    await _revocar(svc, tid, admin)
    lote2 = await _lote(conn, "borrador")
    assert await repo.congelar_soportes_citados(lote2, [exp_a, exp_b]) == []

    # y los bloqueantes de un expediente salen de la vista (control: vigente -> 1, tras revocar -> 0)
    assert await repo.soportes_bloqueantes_de_expediente(exp_a) == []


async def test_los_bloqueantes_de_un_expediente_salen_de_la_vista(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    _tid, exc_id, _admin, _svc = await _constancia(conn, slug, "itest c14 bloqueantes")
    exp = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'a', 'closed', 1) RETURNING id", f"EXP-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        "INSERT INTO expediente_radicados (expediente_id, radicado_id, tracking_number) "
        "VALUES ($1, $2, 'R-1')", exp, RAD,
    )
    repo = ArchiveRepository(conn, slug)

    bloqueantes = await repo.soportes_bloqueantes_de_expediente(exp)
    assert [b["excepcion_id"] for b in bloqueantes] == [exc_id]

    await _baja(conn, await _lote(conn, "avalado"), exc_id)
    assert await repo.soportes_bloqueantes_de_expediente(exp) == []


# --- FOR SHARE contra FOR UPDATE ---------------------------------------------------------------


async def test_for_share_espera_a_quien_purga(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    """§C14.2: la purga bloquea la fila de `files` (FOR UPDATE) y comprueba la vista en OTRA
    sentencia; la constancia toma FOR SHARE. Una espera a la otra, así que o la constancia ve el
    archivo ya borrado (`anexo_no_disponible`) o la purga ve la constancia. Sin el FOR SHARE, la
    constancia NO esperaría y se insertaría sobre un archivo que está a punto de borrarse.

    Mutación: quitar `FOR SHARE` de `asegurar_soporte_citable` -> `esperando.done()` ya es True
    mientras la purga sigue abierta y el test cae."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    await conn.execute(f'SET search_path TO "{schema}"')
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS files (id uuid PRIMARY KEY, deleted_at timestamptz)"
    )
    await conn.execute("INSERT INTO files (id) VALUES ($1)", ANEXO)

    purga = await asyncpg.connect(dsn=_require_dsn())
    try:
        await purga.execute(f'SET search_path TO "{schema}"')
        tx = purga.transaction()
        await tx.start()
        await purga.fetchrow("SELECT id FROM files WHERE id = $1 FOR UPDATE", ANEXO)

        repo = TransferenciaRepository(conn, slug)
        esperando = asyncio.ensure_future(repo.asegurar_soporte_citable(RAD, ANEXO))
        await asyncio.sleep(0.4)
        assert not esperando.done(), "la constancia debe ESPERAR a la purga que bloqueó la fila"

        await purga.execute("DELETE FROM files WHERE id = $1", ANEXO)
        await tx.commit()
        assert await asyncio.wait_for(esperando, timeout=5) == "anexo_no_disponible"
    finally:
        await purga.close()


async def test_con_el_archivo_vivo_y_sin_purga_la_constancia_procede(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Control positivo del FOR SHARE: no bloquea a nadie si nadie purga."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS files (id uuid PRIMARY KEY, deleted_at timestamptz)"
    )
    await conn.execute("INSERT INTO files (id) VALUES ($1)", ANEXO)
    assert await TransferenciaRepository(conn, slug).asegurar_soporte_citable(RAD, ANEXO) is None


# --- declarar sobre un radicado en un lote vivo --------------------------------------------------


@pytest.mark.parametrize("estado_lote", ["borrador", "publicado", "avalado"])
async def test_declarar_sobre_un_radicado_en_un_lote_vivo_es_409(
    tenant_ctx: tuple[str, asyncpg.Connection], estado_lote: str
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    _c, tid, _elabora, _recibe = await _acta_recibida(conn, slug, f"itest c14 lote {estado_lote}")
    exp = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'a', 'closed', 1) RETURNING id", f"EXP-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        "INSERT INTO expediente_radicados (expediente_id, radicado_id, tracking_number) "
        "VALUES ($1, $2, 'R-1')", exp, RAD,
    )
    await _lote(conn, estado_lote, expediente_id=exp)

    with pytest.raises(HTTPException) as e:
        await _declarar(TransferenciaService(conn, slug), tid, Actor(id=uuid.uuid4(), username="a"))
    assert e.value.status_code == 409
    assert e.value.detail["error"] == "radicado_en_lote_de_eliminacion"
    # nada quedó escrito
    assert await conn.fetchval("SELECT count(*) FROM transferencia_acta_firma_excepcion") == 0


async def test_declarar_con_el_lote_anulado_procede(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Control positivo: un lote ANULADO no cuenta como vivo."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    _c, tid, _elabora, _recibe = await _acta_recibida(conn, slug, "itest c14 lote anulado")
    exp = await conn.fetchval(
        "INSERT INTO expedientes (code, name, status, nivel_seguridad) "
        "VALUES ($1, 'a', 'closed', 1) RETURNING id", f"EXP-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        "INSERT INTO expediente_radicados (expediente_id, radicado_id, tracking_number) "
        "VALUES ($1, $2, 'R-1')", exp, RAD,
    )
    await _lote(conn, "anulado", expediente_id=exp)
    resp = await _declarar(
        TransferenciaService(conn, slug), tid, Actor(id=uuid.uuid4(), username="a"), _req()
    )
    assert resp.soporte_estado == "vigente"


