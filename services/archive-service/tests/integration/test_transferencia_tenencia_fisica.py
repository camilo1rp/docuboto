# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) de la obligatoriedad condicionada + cotejo de
origen/destino contra la tenencia física LOCALIZADA del expediente (E12/E17,
residual RF-FIR-15; Ley 594/2000 arts. 15/26, Anexo FUID Ac. AGN 001/2024).

Carril de integración (`RUN_INTEGRATION=1`, ver `tests/integration/conftest.py`).
Reutiliza `_seed_expediente_closed`/`_labeled` de `test_atomicity.py` y
`_make_ubicacion`/`_payload_of` de `test_transferencia_creacion_integridad.py`
-- no se duplica esa infraestructura.

Por qué integración y no mock: el cotejo compara `ubicacion_origen_id` contra
`current_ubicaciones`, un conjunto que sale de `expediente_unidad` REAL (M2M
`(expediente_id, unidad_id)`) vía `FisicoRepository.units_of_expediente`. La
matriz de límite de validación (obligatoriedad, cotejo, composición con H-M)
ya está cubierta con mocks en `tests/test_transferencia_create_tenencia_fisica.py`
-- este archivo confirma que el vínculo M2M real, creado a través de
`FisicoService.link_expediente`, produce exactamente ese conjunto contra
Postgres de verdad, no contra un mock que ya "sabe" la respuesta.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.actor import Actor
from app.core.database import tenant_schema
from app.schemas.fisico import ExpedienteUnidadLink, UnidadCreate
from app.schemas.transferencia import TransferenciaCreate, TransferenciaRechazo
from app.services.fisico_service import FisicoService
from app.services.transferencia_service import TransferenciaService

from .conftest import DSN
from .test_atomicity import _seed_expediente_closed
from .test_transferencia_creacion_integridad import _make_ubicacion, _payload_of

_UNIDAD_COUNTER = 0


async def _make_unidad_linked(
    conn: asyncpg.Connection,
    slug: str,
    expediente_id: uuid.UUID,
    *,
    tipo_archivo: str,
):
    """Crea una ubicación REAL + una unidad de conservación vinculada a ella,
    y la enlaza al expediente vía `expediente_unidad` (M2M real, no un mock).
    Devuelve `(ubicacion, unidad)`."""
    global _UNIDAD_COUNTER
    _UNIDAD_COUNTER += 1
    fisico = FisicoService(conn, slug)
    ubicacion = await _make_ubicacion(conn, slug, tipo_archivo=tipo_archivo)
    unidad = await fisico.create_unidad(
        UnidadCreate(
            tipo="caja",
            codigo=f"itest-un-{tipo_archivo}-{_UNIDAD_COUNTER}-{uuid.uuid4().hex[:6]}",
            ubicacion_id=ubicacion.id,
        )
    )
    await fisico.link_expediente(unidad.id, ExpedienteUnidadLink(expediente_id=expediente_id))
    return ubicacion, unidad


# ---------------------------------------------------------------------------
# (1) CON tenencia física + origen que COTEJA (una de sus ubicaciones
# actuales, del tipo correcto) + destino coherente -> 201.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_con_tenencia_origen_cotea_y_coherente_201(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    created = await _seed_expediente_closed(conn, slug, "itest tenencia origen cotea")
    ubicacion_actual, _unidad = await _make_unidad_linked(
        conn, slug, created.id, tipo_archivo="gestion"
    )
    destino = await _make_ubicacion(conn, slug, tipo_archivo="central")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-tenencia-cotea-ok")
    resp = await service.create(
        TransferenciaCreate(
            tipo="primaria",
            expediente_id=created.id,
            ubicacion_origen_id=ubicacion_actual.id,
            ubicacion_destino_id=destino.id,
        ),
        actor,
    )
    assert resp.estado == "preparada"
    assert resp.ubicacion_origen_id == ubicacion_actual.id
    assert resp.ubicacion_destino_id == destino.id


# ---------------------------------------------------------------------------
# (2) CON tenencia física + origen que NO cotea (una ubicación del tipo
# correcto pero que NO es la actual del expediente) -> rechazado. Corazón del
# cotejo: un origen del tipo correcto pero equivocado ya NO pasa.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_con_tenencia_origen_no_cotea_422_y_audita(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest tenencia origen no cotea")
    # El expediente SÍ tiene tenencia localizada (una ubicación 'gestion'
    # real), pero el origen aportado es OTRA ubicación 'gestion' distinta --
    # tipo correcto, no es la actual.
    await _make_unidad_linked(conn, slug, created.id, tipo_archivo="gestion")
    origen_ajeno = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    destino = await _make_ubicacion(conn, slug, tipo_archivo="central")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-tenencia-no-cotea")
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            TransferenciaCreate(
                tipo="primaria",
                expediente_id=created.id,
                ubicacion_origen_id=origen_ajeno.id,
                ubicacion_destino_id=destino.id,
            ),
            actor,
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"] == "ubicacion_origen_no_actual"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        count_total = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencias WHERE expediente_id = $1',
            created.id,
        )
    finally:
        await second_conn.close()
    assert count_total == 0

    payload = await _payload_of(
        conn,
        slug,
        action="archive.transferencia_preparada_failed",
        object_ref=str(created.id),
    )
    assert payload["motivo"] == "ubicacion_origen_no_actual"


# ---------------------------------------------------------------------------
# (3) CON tenencia física + SIN origen/destino -> rechazado (obligatoriedad
# condicionada).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_con_tenencia_sin_origen_ni_destino_422_y_audita(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest tenencia sin par")
    await _make_unidad_linked(conn, slug, created.id, tipo_archivo="gestion")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-tenencia-sin-par")
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            TransferenciaCreate(tipo="primaria", expediente_id=created.id), actor
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"] == "ubicaciones_requeridas_fisico"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        count_total = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencias WHERE expediente_id = $1',
            created.id,
        )
    finally:
        await second_conn.close()
    assert count_total == 0

    payload = await _payload_of(
        conn,
        slug,
        action="archive.transferencia_preparada_failed",
        object_ref=str(created.id),
    )
    assert payload["motivo"] == "ubicaciones_requeridas_fisico"


# ---------------------------------------------------------------------------
# (4) Expediente ELECTRÓNICO (sin `expediente_unidad`) + sin origen/destino
# -> 201 (no se rompe el caso común de un DMS moderno).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_electronico_sin_tenencia_sin_origen_ni_destino_201(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    created = await _seed_expediente_closed(conn, slug, "itest tenencia electronico")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-tenencia-electronico")
    resp = await service.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id), actor
    )
    assert resp.estado == "preparada"
    assert resp.ubicacion_origen_id is None
    assert resp.ubicacion_destino_id is None


# ---------------------------------------------------------------------------
# (5) Multiplicidad: expediente en 2 unidades/ubicaciones distintas. El
# origen que cotea con CUALQUIERA de las actuales -> 201; uno que no es
# ninguna -> rechazado.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_multiplicidad_origen_cotea_con_cualquiera_de_las_dos_201(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    created = await _seed_expediente_closed(conn, slug, "itest tenencia multiplicidad ok")
    ubicacion_1, _u1 = await _make_unidad_linked(conn, slug, created.id, tipo_archivo="gestion")
    ubicacion_2, _u2 = await _make_unidad_linked(conn, slug, created.id, tipo_archivo="gestion")
    destino = await _make_ubicacion(conn, slug, tipo_archivo="central")

    service = TransferenciaService(conn, slug)

    # Origen = la PRIMERA ubicación actual -> cotea.
    actor1 = Actor(id=uuid.uuid4(), username="itest-multi-1")
    resp1 = await service.create(
        TransferenciaCreate(
            tipo="primaria",
            expediente_id=created.id,
            ubicacion_origen_id=ubicacion_1.id,
            ubicacion_destino_id=destino.id,
        ),
        actor1,
    )
    assert resp1.estado == "preparada"
    assert resp1.ubicacion_origen_id == ubicacion_1.id

    # Enviar + rechazar la primera para poder preparar una segunda
    # transferencia sobre el mismo expediente (H-L: a lo sumo UNA activa;
    # `rechazar()` solo es válido desde 'enviada', ver `_VALID`).
    await service.enviar(resp1.id, Actor(id=uuid.uuid4(), username="itest-multi-envia"))
    await service.rechazar(
        resp1.id,
        TransferenciaRechazo(motivo="itest libera para probar la segunda ubicación"),
        Actor(id=uuid.uuid4(), username="itest-multi-rechaza"),
    )

    # Origen = la SEGUNDA ubicación actual -> también cotea (multiplicidad
    # M2M real: cualquiera de las actuales es válida, no solo "la única").
    actor2 = Actor(id=uuid.uuid4(), username="itest-multi-2")
    resp2 = await service.create(
        TransferenciaCreate(
            tipo="primaria",
            expediente_id=created.id,
            ubicacion_origen_id=ubicacion_2.id,
            ubicacion_destino_id=destino.id,
        ),
        actor2,
    )
    assert resp2.estado == "preparada"
    assert resp2.ubicacion_origen_id == ubicacion_2.id


@pytest.mark.integration
async def test_multiplicidad_origen_ajeno_a_ambas_422(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    created = await _seed_expediente_closed(conn, slug, "itest tenencia multiplicidad bad")
    await _make_unidad_linked(conn, slug, created.id, tipo_archivo="gestion")
    await _make_unidad_linked(conn, slug, created.id, tipo_archivo="gestion")
    origen_ajeno = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    destino = await _make_ubicacion(conn, slug, tipo_archivo="central")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-multi-bad")
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            TransferenciaCreate(
                tipo="primaria",
                expediente_id=created.id,
                ubicacion_origen_id=origen_ajeno.id,
                ubicacion_destino_id=destino.id,
            ),
            actor,
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"] == "ubicacion_origen_no_actual"


# ---------------------------------------------------------------------------
# (6) Regresión H-M: la coherencia de tipo sigue vigente (primaria
# gestion->central, etc.) incluso cuando el origen SÍ cotea -- cotejo y
# coherencia COMPONEN, no se sustituyen.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_regresion_h_m_cotejo_pasa_pero_coherencia_de_tipo_falla_422(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest tenencia regresion h-m")
    # El expediente está SOLO en una ubicación 'central'; tipo='primaria'
    # espera origen 'gestion'. El origen aportado ES la ubicación actual
    # (cotejo PASA) pero es del tipo equivocado -> H-M sigue rechazando.
    ubicacion_central, _u = await _make_unidad_linked(
        conn, slug, created.id, tipo_archivo="central"
    )
    destino = await _make_ubicacion(conn, slug, tipo_archivo="central")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-tenencia-regresion-hm")
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            TransferenciaCreate(
                tipo="primaria",
                expediente_id=created.id,
                ubicacion_origen_id=ubicacion_central.id,
                ubicacion_destino_id=destino.id,
            ),
            actor,
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"] == "ubicacion_incoherente_con_tipo"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        count_total = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencias WHERE expediente_id = $1',
            created.id,
        )
    finally:
        await second_conn.close()
    assert count_total == 0
