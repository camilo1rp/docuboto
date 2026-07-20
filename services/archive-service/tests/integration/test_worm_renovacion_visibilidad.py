# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Delta de remediación (Postgres REAL): no-read-up de
`GET /expedientes/indices/pendientes-renovacion`
(`IndexService.list_pendientes_renovacion` -> `IndexRepository`/
`TransferenciaRepository.list_worm_renovacion_visibilidad`, E10 debt).

El invariante CRÍTICO de este endpoint (riesgo de oráculo) es que el filtro
`e.nivel_seguridad <= $max_clearance` vive EN LA CONSULTA SQL de AMBOS
repositorios -- un mock nunca ejecuta ese `WHERE`, así que aquí se siembra
contra un schema desechable real y se contrasta un llamante de clearance
bajo contra uno con clearance suficiente sobre el MISMO conjunto de datos.
Mismo criterio no-read-up que `list_expedientes`
(`tests/test_archive.py::test_list_expedientes_total_count_shares_clearance_filter_with_page`)
y que `GET .../acta` (`tests/integration/test_transferencia_acta.py`,
garantía 5) -- espejados aquí para el eje de renovación WORM.

Reutiliza los seed helpers de `test_worm_renovacion_ct_filter.py` (índice y
acta protegidos bajo WORM, con `disposition` parametrizable) y el helper RBAC
mínimo de `test_transferencia_acta.py` (`_grant_clearance`) -- mismo patrón
de reuso cross-file ya establecido en este directorio
(`test_transferencia_tenencia_fisica.py`, `test_transferencia_acta_immutable.py`).

El resto de la matriz de selección (CT estructural, ventana, agotado/backoff)
para el eje de RENOVACIÓN ya está cubierto por
`test_worm_renovacion_ct_filter.py` sobre `list_worm_renovacion_pending`
(consumido por el job); aquí se prueba la variante `..._visibilidad`
(consumida por ESTE endpoint), que difiere deliberadamente: SÍ incluye
agotados sin importar la fecha, y NO respeta el backoff del job (ver
docstring de `IndexRepository.list_worm_renovacion_visibilidad`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.clearance import CLASIFICADA
from app.core.database import tenant_schema
from app.services.index_service import IndexService

from .test_transferencia_acta import _grant_clearance
from .test_worm_renovacion_ct_filter import (
    _LEJOS_DE_VENCER,
    _PRONTO_A_VENCER,
    _mark_acta_protegido,
    _mark_aip_protegido,
    _mark_indice_protegido,
    _seed_acta_firmada,
    _seed_expediente_aip_empaquetado,
    _seed_expediente_indice_firmado,
)

_now = lambda: datetime.now(tz=UTC)  # noqa: E731


async def _set_nivel_seguridad(
    conn: asyncpg.Connection, slug: str, expediente_id: uuid.UUID, nivel: int
) -> None:
    """Bypasea el gate M3 (no-write-up al crear, ya cubierto aparte en
    `tests/test_archive.py`) -- aquí solo interesa el nivel FINAL del
    expediente para ejercitar el no-read-up de LECTURA de este endpoint."""
    schema = tenant_schema(slug)
    await conn.execute(
        f'UPDATE "{schema}".expedientes SET nivel_seguridad = $2 WHERE id = $1',
        expediente_id,
        nivel,
    )


async def _expediente_code(conn: asyncpg.Connection, slug: str, expediente_id: uuid.UUID) -> str:
    schema = tenant_schema(slug)
    return await conn.fetchval(
        f'SELECT code FROM "{schema}".expedientes WHERE id = $1', expediente_id
    )


# ---------------------------------------------------------------------------
# (A) No-read-up -- CRÍTICO: ni el expediente_id ni el expediente_code de un
# artefacto por encima del clearance del llamante deben aparecer.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_no_read_up_indice_hides_classified_from_low_clearance_caller(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    hidden_id, hidden_row = await _seed_expediente_indice_firmado(
        conn, slug, "itest renovacion indice oculto"
    )
    await _mark_indice_protegido(conn, slug, hidden_row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)
    await _set_nivel_seguridad(conn, slug, hidden_id, CLASIFICADA)
    hidden_code = await _expediente_code(conn, slug, hidden_id)

    visible_id, visible_row = await _seed_expediente_indice_firmado(
        conn, slug, "itest renovacion indice visible"
    )
    await _mark_indice_protegido(conn, slug, visible_row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)

    no_lector = uuid.uuid4()  # sin fila en auth_users -> resolve_caller_clearance = PUBLICA(1)
    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=no_lector
    )

    ids = {it.expediente_id for it in items}
    codes = {it.expediente_code for it in items}
    assert visible_id in ids
    assert hidden_id not in ids
    assert hidden_code not in codes
    assert total == 1  # X-Total-Count (post-filtro) NO cuenta el oculto


@pytest.mark.integration
async def test_no_read_up_indice_visible_with_sufficient_clearance(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """MISMO par sembrado que el test anterior; la ÚNICA variable es el
    clearance real del llamante (provisionado vía RBAC) -- confirma que la
    ocultación es por clearance, no por un bug que oculte todo."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    classified_id, classified_row = await _seed_expediente_indice_firmado(
        conn, slug, "itest renovacion indice clasificado visible p-lector"
    )
    await _mark_indice_protegido(
        conn, slug, classified_row["id"], retain_until=_PRONTO_A_VENCER, agotado=True
    )
    await _set_nivel_seguridad(conn, slug, classified_id, CLASIFICADA)

    lector_legitimo = uuid.uuid4()
    await _grant_clearance(conn, schema, lector_legitimo, CLASIFICADA)

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=lector_legitimo
    )

    assert classified_id in {it.expediente_id for it in items}
    assert total == 1


@pytest.mark.integration
async def test_no_read_up_acta_hides_classified_from_low_clearance_caller(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    hidden_transferencia_id, hidden_expediente_id = await _seed_acta_firmada(
        conn, slug, "itest renovacion acta oculta"
    )
    await _mark_acta_protegido(conn, slug, hidden_transferencia_id, retain_until=_PRONTO_A_VENCER, agotado=True)
    await _set_nivel_seguridad(conn, slug, hidden_expediente_id, CLASIFICADA)
    hidden_code = await _expediente_code(conn, slug, hidden_expediente_id)

    _visible_transferencia_id, visible_expediente_id = await _seed_acta_firmada(
        conn, slug, "itest renovacion acta visible"
    )
    await _mark_acta_protegido(
        conn, slug, _visible_transferencia_id, retain_until=_PRONTO_A_VENCER, agotado=True
    )

    no_lector = uuid.uuid4()
    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=no_lector
    )

    ids = {it.expediente_id for it in items}
    codes = {it.expediente_code for it in items}
    assert visible_expediente_id in ids
    assert hidden_expediente_id not in ids
    assert hidden_code not in codes
    assert total == 1
    visible_item = next(it for it in items if it.expediente_id == visible_expediente_id)
    assert visible_item.tipo == "acta_transferencia"


@pytest.mark.integration
async def test_no_read_up_aip_hides_classified_from_low_clearance_caller(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo EXACTO de `test_no_read_up_indice_hides_classified_from_low_clearance_caller`
    para el AIP (E10 debt, fast-follow del cableado del AIP) -- el invariante
    CRÍTICO es el mismo: ni el `expediente_id` ni el `expediente_code` de un
    AIP por encima del clearance del llamante deben aparecer, y `total` es
    post-filtro."""
    slug, conn = tenant_ctx

    hidden_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest renovacion AIP oculto")
    await _mark_aip_protegido(conn, slug, hidden_id, retain_until=_PRONTO_A_VENCER, agotado=True)
    await _set_nivel_seguridad(conn, slug, hidden_id, CLASIFICADA)
    hidden_code = await _expediente_code(conn, slug, hidden_id)

    visible_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest renovacion AIP visible")
    await _mark_aip_protegido(conn, slug, visible_id, retain_until=_PRONTO_A_VENCER, agotado=True)

    no_lector = uuid.uuid4()
    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=no_lector
    )

    ids = {it.expediente_id for it in items}
    codes = {it.expediente_code for it in items}
    assert visible_id in ids
    assert hidden_id not in ids
    assert hidden_code not in codes
    assert total == 1
    visible_item = next(it for it in items if it.expediente_id == visible_id)
    assert visible_item.tipo == "aip"


@pytest.mark.integration
async def test_no_read_up_aip_visible_with_sufficient_clearance(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """MISMO par sembrado que el test anterior; la ÚNICA variable es el
    clearance real del llamante -- confirma que la ocultación del AIP es por
    clearance, no por un bug que oculte todo ese tipo."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    classified_id = await _seed_expediente_aip_empaquetado(
        conn, slug, "itest renovacion AIP clasificado visible p-lector"
    )
    await _mark_aip_protegido(conn, slug, classified_id, retain_until=_PRONTO_A_VENCER, agotado=True)
    await _set_nivel_seguridad(conn, slug, classified_id, CLASIFICADA)

    lector_legitimo = uuid.uuid4()
    await _grant_clearance(conn, schema, lector_legitimo, CLASIFICADA)

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=lector_legitimo
    )

    assert classified_id in {it.expediente_id for it in items}
    assert total == 1


@pytest.mark.integration
async def test_no_read_up_total_count_is_post_filter_not_raw(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """CRÍTICO (mismo hallazgo que RF-SEG-08 en el listado de expedientes):
    `X-Total-Count`/`total` NO debe reflejar el conteo SIN filtrar -- eso
    sería una fuga por conteo aunque ningún ítem CLASIFICADO aparezca en la
    página. 3 ocultos (CLASIFICADA) + 2 visibles (PUBLICA); un llamante de
    clearance baja debe ver total=2, nunca 5."""
    slug, conn = tenant_ctx

    for i in range(3):
        exp_id, row = await _seed_expediente_indice_firmado(conn, slug, f"itest renovacion oculto {i}")
        await _mark_indice_protegido(conn, slug, row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)
        await _set_nivel_seguridad(conn, slug, exp_id, CLASIFICADA)

    visible_ids = set()
    for i in range(2):
        exp_id, row = await _seed_expediente_indice_firmado(conn, slug, f"itest renovacion visible {i}")
        await _mark_indice_protegido(conn, slug, row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)
        visible_ids.add(exp_id)

    no_lector = uuid.uuid4()
    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=no_lector
    )

    assert total == 2
    assert {it.expediente_id for it in items} == visible_ids


@pytest.mark.integration
async def test_no_read_up_visible_expediente_without_pending_renewal_is_silently_absent(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """No-oráculo: un expediente PUBLICA (visible), CT, protegido, pero SIN
    renovación pendiente (`worm_retain_until` lejos, no agotado) simplemente
    NO aparece -- exactamente el mismo resultado (ausencia) que produce un
    expediente CLASIFICADA oculto por clearance. El llamante no puede
    distinguir "no hay nada pendiente" de "lo tienes oculto" a través de este
    endpoint, con NINGÚN nivel de clearance."""
    slug, conn = tenant_ctx

    sano_id, sano_row = await _seed_expediente_indice_firmado(conn, slug, "itest renovacion sano")
    await _mark_indice_protegido(conn, slug, sano_row["id"], retain_until=_LEJOS_DE_VENCER, agotado=False)

    no_lector = uuid.uuid4()
    items_bajo, total_bajo = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=no_lector
    )
    assert sano_id not in {it.expediente_id for it in items_bajo}
    assert total_bajo == 0

    # Incluso con clearance MÁXIMO, sigue ausente -- la ausencia es por
    # criterio de selección (nada pendiente), no por clearance.
    root_reader = uuid.uuid4()
    await _grant_clearance(conn, tenant_schema(slug), root_reader, CLASIFICADA)
    items_alto, total_alto = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=root_reader
    )
    assert sano_id not in {it.expediente_id for it in items_alto}
    assert total_alto == 0


# ---------------------------------------------------------------------------
# (B) Criterio de selección de `..._visibilidad` -- SÍ incluye agotados sin
# importar la fecha (a diferencia de `..._pending`, consumido por el job).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_agotado_appears_even_far_from_expiry(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    exp_id, row = await _seed_expediente_indice_firmado(conn, slug, "itest agotado lejos")
    await _mark_indice_protegido(conn, slug, row["id"], retain_until=_LEJOS_DE_VENCER, agotado=True)

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=None
    )

    assert exp_id in {it.expediente_id for it in items}
    assert total == 1


@pytest.mark.integration
async def test_aip_agotado_appears_even_far_from_expiry(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo del test anterior para el AIP (E10 debt, fast-follow del
    cableado del AIP)."""
    slug, conn = tenant_ctx

    exp_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP agotado lejos")
    await _mark_aip_protegido(conn, slug, exp_id, retain_until=_LEJOS_DE_VENCER, agotado=True)

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=None
    )

    assert exp_id in {it.expediente_id for it in items}
    assert total == 1
    item = next(it for it in items if it.expediente_id == exp_id)
    assert item.tipo == "aip"


@pytest.mark.integration
async def test_non_ct_never_appears_even_when_agotado_and_pronto(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    ct_id, ct_row = await _seed_expediente_indice_firmado(conn, slug, "itest CT agotado", disposition="CT")
    await _mark_indice_protegido(conn, slug, ct_row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)

    e_id, e_row = await _seed_expediente_indice_firmado(conn, slug, "itest E agotado", disposition="E")
    await _mark_indice_protegido(conn, slug, e_row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=None
    )

    ids = {it.expediente_id for it in items}
    assert ct_id in ids
    assert e_id not in ids  # filtro CT estructural: E jamás entra, aunque agotado y próximo
    assert total == 1


@pytest.mark.integration
async def test_non_ct_never_appears_for_aip_even_when_agotado_and_pronto(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo del test anterior para el AIP."""
    slug, conn = tenant_ctx

    ct_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP CT agotado", disposition="CT")
    await _mark_aip_protegido(conn, slug, ct_id, retain_until=_PRONTO_A_VENCER, agotado=True)

    e_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP E agotado", disposition="E")
    await _mark_aip_protegido(conn, slug, e_id, retain_until=_PRONTO_A_VENCER, agotado=True)

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=None
    )

    ids = {it.expediente_id for it in items}
    assert ct_id in ids
    assert e_id not in ids  # filtro CT estructural: E jamás entra, aunque agotado y próximo
    assert total == 1


@pytest.mark.integration
async def test_covers_both_tipos_and_orders_agotado_first_then_soonest(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    # Acta AGOTADA (retain_until lejano -- agotado igual gana el orden).
    acta_transferencia_id, acta_expediente_id = await _seed_acta_firmada(conn, slug, "itest orden acta agotada")
    await _mark_acta_protegido(conn, slug, acta_transferencia_id, retain_until=_LEJOS_DE_VENCER, agotado=True)

    # Índice EN RIESGO más próximo (5 días).
    proximo_id, proximo_row = await _seed_expediente_indice_firmado(conn, slug, "itest orden proximo 5d")
    await _mark_indice_protegido(
        conn, slug, proximo_row["id"], retain_until=_now() + timedelta(days=5), agotado=False
    )

    # Índice EN RIESGO menos próximo (20 días).
    lejano_id, lejano_row = await _seed_expediente_indice_firmado(conn, slug, "itest orden proximo 20d")
    await _mark_indice_protegido(
        conn, slug, lejano_row["id"], retain_until=_now() + timedelta(days=20), agotado=False
    )

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=None
    )

    assert total == 3
    assert [it.expediente_id for it in items] == [acta_expediente_id, proximo_id, lejano_id]
    assert items[0].tipo == "acta_transferencia"
    assert items[1].tipo == "indice"
    assert items[2].tipo == "indice"


@pytest.mark.integration
async def test_covers_all_three_tipos_and_orders_agotado_first_then_soonest(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo del test anterior con el AIP participando en la mezcla, no solo
    índice/acta -- el orden agotado-primero-luego-más-próximo es indiferente
    a qué de los TRES repositorios aportó cada fila."""
    slug, conn = tenant_ctx

    # AIP AGOTADO (retain_until lejano -- agotado igual gana el orden).
    aip_expediente_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest orden AIP agotado")
    await _mark_aip_protegido(conn, slug, aip_expediente_id, retain_until=_LEJOS_DE_VENCER, agotado=True)

    # Acta EN RIESGO más próxima (5 días).
    acta_transferencia_id, acta_expediente_id = await _seed_acta_firmada(conn, slug, "itest orden acta 5d")
    await _mark_acta_protegido(
        conn, slug, acta_transferencia_id, retain_until=_now() + timedelta(days=5), agotado=False
    )

    # Índice EN RIESGO menos próximo (20 días).
    indice_id, indice_row = await _seed_expediente_indice_firmado(conn, slug, "itest orden indice 20d")
    await _mark_indice_protegido(
        conn, slug, indice_row["id"], retain_until=_now() + timedelta(days=20), agotado=False
    )

    items, total = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=20, user_id=None
    )

    assert total == 3
    assert [it.expediente_id for it in items] == [aip_expediente_id, acta_expediente_id, indice_id]
    assert items[0].tipo == "aip"
    assert items[1].tipo == "acta_transferencia"
    assert items[2].tipo == "indice"


# ---------------------------------------------------------------------------
# (C) Paginación en memoria: page/size + total post-filtro.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_pagination_respects_page_size_with_post_filter_total_count(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    seeded_ids = set()
    for i in range(5):
        exp_id, row = await _seed_expediente_indice_firmado(conn, slug, f"itest paginacion {i}")
        await _mark_indice_protegido(conn, slug, row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)
        seeded_ids.add(exp_id)

    page1, total1 = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=2, user_id=None
    )
    page3, total3 = await IndexService(conn, slug).list_pendientes_renovacion(
        page=3, size=2, user_id=None
    )

    assert total1 == 5
    assert total3 == 5
    assert len(page1) == 2
    assert len(page3) == 1  # 5 items, size=2 -> última página con el resto


@pytest.mark.integration
async def test_pagination_respects_page_size_across_all_three_tipos(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo del test anterior sembrando en LOS TRES repositorios (índice,
    acta y AIP) -- `total`/paginación combinan las tres fuentes, no solo la
    del índice."""
    slug, conn = tenant_ctx

    seeded_ids = set()
    for i in range(2):
        exp_id, row = await _seed_expediente_indice_firmado(conn, slug, f"itest paginacion indice {i}")
        await _mark_indice_protegido(conn, slug, row["id"], retain_until=_PRONTO_A_VENCER, agotado=True)
        seeded_ids.add(exp_id)

    for i in range(2):
        transferencia_id, exp_id = await _seed_acta_firmada(conn, slug, f"itest paginacion acta {i}")
        await _mark_acta_protegido(conn, slug, transferencia_id, retain_until=_PRONTO_A_VENCER, agotado=True)
        seeded_ids.add(exp_id)

    exp_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest paginacion AIP")
    await _mark_aip_protegido(conn, slug, exp_id, retain_until=_PRONTO_A_VENCER, agotado=True)
    seeded_ids.add(exp_id)

    page1, total1 = await IndexService(conn, slug).list_pendientes_renovacion(
        page=1, size=2, user_id=None
    )
    page3, total3 = await IndexService(conn, slug).list_pendientes_renovacion(
        page=3, size=2, user_id=None
    )

    assert total1 == 5
    assert total3 == 5
    assert len(page1) == 2
    assert len(page3) == 1  # 5 items (2 índice + 2 acta + 1 AIP), size=2 -> última página con el resto

    all_seen_ids = {it.expediente_id for it in page1} | {it.expediente_id for it in page3}
    assert all_seen_ids <= seeded_ids
    all_seen_tipos = {it.tipo for it in page1} | {it.tipo for it in page3}
    assert "aip" in all_seen_tipos

    all_seen_ids = {it.expediente_id for it in page1} | {it.expediente_id for it in page3}
    assert all_seen_ids <= seeded_ids
