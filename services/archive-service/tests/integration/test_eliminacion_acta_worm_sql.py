# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El SQL del WORM del acta de eliminación, contra Postgres REAL (T6, AREA-06-02).

Los unit tests de la barrida comprueban el TEXTO del `SELECT` (`"worm_reconcile_
agotado = FALSE" in sql`); no que Postgres devuelva lo que debe. Aquí se siembran
actas en cada estado posible y se comprueba QUÉ filas elige la barrida, qué hace
`mark_acta_eliminacion_worm_fallido` con el backoff, y que `ejecutar` se niega
con un acta firmada sin candado SIN tocar el lote (el estado lo lee Postgres).

Mutación que debe tumbarlo: quitar `worm_reconcile_agotado = FALSE` o la
condición del backoff del listado; quitar `AND worm_protegido_at IS NULL` del
UPDATE de fallo; quitar la guarda `acta_sin_worm` de `ejecutar`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.repositories.archive_repository import ArchiveRepository
from app.services.archive_service import ArchiveService

from .test_eliminacion_atomicity import AYER, _avalar_por_sql, _estado
from .test_eliminacion_lote_estados import _publicar

pytestmark = pytest.mark.integration


async def _acta(
    conn: asyncpg.Connection,
    slug: str,
    *,
    estado: str = "firmado",
    protegido: bool = False,
    agotado: bool = False,
    proximo_at: datetime | None = None,
    avalado: bool = False,
) -> uuid.UUID:
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    lote_id = await conn.fetchval(
        "INSERT INTO eliminacion_lotes (codigo, creado_por) "
        "VALUES ($1, gen_random_uuid()) RETURNING id",
        f"LOTE-W-{uuid.uuid4().hex[:6]}",
    )
    if avalado:
        await _publicar(conn, lote_id, AYER)
        await _avalar_por_sql(conn, lote_id)
    await conn.execute(
        "INSERT INTO eliminacion_acta (lote_id, acta_xml, acta_sha256, metodo_borrado, "
        "generado_por) VALUES ($1, '<acta/>', $2, 'Borrado seguro y picado', gen_random_uuid())",
        lote_id,
        "b" * 64,
    )
    if estado == "firmado":
        await conn.execute(
            "INSERT INTO eliminacion_acta_firma (lote_id, estado, firma_id, minio_key_firmado, "
            "firmado_at, worm_protegido_at, worm_reconcile_agotado, worm_reconcile_proximo_at) "
            "VALUES ($1, 'firmado', gen_random_uuid(), 'k/a.xml', now(), "
            "CASE WHEN $2 THEN now() END, $3, $4)",
            lote_id,
            protegido,
            agotado,
            proximo_at,
        )
    else:
        await conn.execute(
            "INSERT INTO eliminacion_acta_firma (lote_id, estado, worm_reconcile_agotado) "
            "VALUES ($1, 'pendiente_firma', false)",
            lote_id,
        )
    return lote_id


async def test_la_barrida_elige_solo_las_actas_firmadas_sin_candado_no_agotadas_y_vencidas(
    tenant_ctx,
) -> None:
    slug, conn = tenant_ctx
    ahora = datetime.now(tz=UTC)
    quiere = {
        "nunca_intentada": await _acta(conn, slug),
        "backoff_vencido": await _acta(conn, slug, proximo_at=ahora - timedelta(minutes=5)),
    }
    descarta = {
        "ya_protegida": await _acta(conn, slug, protegido=True),
        "agotada": await _acta(conn, slug, agotado=True),
        "backoff_futuro": await _acta(conn, slug, proximo_at=ahora + timedelta(hours=1)),
        "sin_firmar": await _acta(conn, slug, estado="pendiente_firma"),
    }

    filas = await ArchiveRepository(conn, slug).list_worm_pending_actas_eliminacion(50)
    elegidas = {f["lote_id"] for f in filas}

    assert elegidas == set(quiere.values()), (
        {k: v in elegidas for k, v in {**quiere, **descarta}.items()}
    )


async def test_un_fallo_programa_el_backoff_y_no_toca_un_acta_ya_protegida(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    pendiente = await _acta(conn, slug)
    protegida = await _acta(conn, slug, protegido=True)
    repo = ArchiveRepository(conn, slug)
    proximo = datetime.now(tz=UTC) + timedelta(minutes=10)

    await repo.mark_acta_eliminacion_worm_fallido(
        pendiente, motivo="worm_no_obtenido", proximo_at=proximo, agotado=False
    )
    await repo.mark_acta_eliminacion_worm_fallido(
        protegida, motivo="no-debe-escribirse", proximo_at=proximo, agotado=True
    )

    p = await conn.fetchrow(
        "SELECT * FROM eliminacion_acta_firma WHERE lote_id = $1", pendiente
    )
    assert (p["worm_reconcile_intentos"], p["worm_last_error"]) == (1, "worm_no_obtenido")
    assert p["worm_reconcile_proximo_at"] == proximo
    assert p["worm_reconcile_agotado"] is False
    # Con el backoff programado, la barrida NO la vuelve a ver hasta que venza.
    assert pendiente not in {
        f["lote_id"] for f in await repo.list_worm_pending_actas_eliminacion(50)
    }
    q = await conn.fetchrow(
        "SELECT * FROM eliminacion_acta_firma WHERE lote_id = $1", protegida
    )
    assert (q["worm_reconcile_intentos"], q["worm_last_error"], q["worm_reconcile_agotado"]) == (
        0, None, False,
    )


async def test_ejecutar_con_el_acta_firmada_sin_candado_409_y_el_lote_sigue_avalado(
    tenant_ctx,
) -> None:
    slug, conn = tenant_ctx
    lote_id = await _acta(conn, slug, avalado=True)

    with pytest.raises(HTTPException) as exc:
        await ArchiveService(conn, slug).ejecutar_lote_eliminacion(lote_id, actor=uuid.uuid4())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "acta_sin_worm"
    assert await _estado(conn, slug, lote_id) == "avalado"
