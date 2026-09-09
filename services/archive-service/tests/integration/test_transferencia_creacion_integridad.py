# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) de la integridad de la CREACIÓN de
transferencias (E12 H-L + H-M, Ac. AGN 001/2024 Tít. 4.4; Ley 594/2000 art. 23).

Carril de integración (`RUN_INTEGRATION=1`, ver `tests/integration/conftest.py`).
Reutiliza `_seed_expediente_closed`/`_labeled` de `test_atomicity.py` -- no se
duplica esa infraestructura.

H-L (unicidad de transferencia activa por expediente, migración 024 -- índice
único parcial): la unicidad y la carrera concurrente SOLO son observables aquí
-- el carril mockeado no tiene un índice de Postgres real que serialice nada.

H-M (coherencia origen/destino por tipo): la matriz de coherencia ya está
cubierta a nivel unitario (`tests/test_transferencia_create_coherencia.py`,
mockeado). Aquí se añaden dos casos que ejercitan `FisicoRepository.get_ubicacion`
contra ubicaciones REALES (creadas vía `FisicoService.create_ubicacion`) --
confirma que la lectura plana (sin clearance) funciona de punta a punta contra
el schema real, no solo contra un mock que ya "sabe" la respuesta.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.core.actor import Actor
from app.core.database import tenant_schema
from app.schemas.fisico import UbicacionCreate
from app.schemas.transferencia import (
    TransferenciaCreate,
    TransferenciaRechazo,
    TransferenciaResponse,
)
from app.services.fisico_service import FisicoService
from app.services.transferencia_service import TransferenciaService

from .conftest import DSN
from .test_atomicity import _labeled, _seed_expediente_closed

_UBICACION_COUNTER = 0


async def _make_ubicacion(conn: asyncpg.Connection, slug: str, *, tipo_archivo: str):
    """Ubicación REAL vía `FisicoService.create_ubicacion` -- código único por
    llamada (varios tests comparten el mismo tenant_ctx desechable, un mismo
    proceso pytest puede correr varias veces, y `codigo` es UNIQUE)."""
    global _UBICACION_COUNTER
    _UBICACION_COUNTER += 1
    fisico = FisicoService(conn, slug)
    return await fisico.create_ubicacion(
        UbicacionCreate(
            nivel="deposito",
            codigo=f"itest-ub-{tipo_archivo}-{_UBICACION_COUNTER}-{uuid.uuid4().hex[:6]}",
            nombre=f"itest depósito {tipo_archivo}",
            tipo_archivo=tipo_archivo,
        )
    )


async def _payload_of(conn: asyncpg.Connection, slug: str, *, action: str, object_ref: str) -> dict:
    row = await conn.fetchrow(
        "SELECT payload FROM public.audit_log "
        "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3 "
        "ORDER BY id DESC LIMIT 1",
        slug,
        action,
        object_ref,
    )
    assert row is not None, f"no se encontró asiento {action!r} para object_ref={object_ref!r}"
    payload = row["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


# ---------------------------------------------------------------------------
# H-L (1) -- dos transferencias activas del mismo expediente 'closed': la 2a
# (create) -> 409 transferencia_activa_existente; la 1a sigue viva e intacta.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h_l_segunda_transferencia_activa_sobre_mismo_expediente_409(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest h-l segunda activa")
    service = TransferenciaService(conn, slug)

    actor1 = Actor(id=uuid.uuid4(), username="itest-hl-primera")
    primera = await service.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor1
    )
    assert primera.estado == "preparada"

    actor2 = Actor(id=uuid.uuid4(), username="itest-hl-segunda")
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor2
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "transferencia_activa_existente"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        count_total = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencias WHERE expediente_id = $1',
            created.id,
        )
        primera_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', primera.id
        )
        preparada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_preparada",
        )
        failed_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_preparada_failed",
        )
    finally:
        await second_conn.close()

    # El corazón del test: la 2a JAMÁS se insertó (el índice único revierte el
    # INSERT dentro de su propia tx) -- sigue habiendo UNA sola fila.
    assert count_total == 1
    assert primera_estado == "preparada"
    assert preparada_audit == 1
    assert failed_audit == 1

    payload = await _payload_of(
        conn,
        slug,
        action="archive.transferencia_preparada_failed",
        object_ref=str(created.id),
    )
    assert payload["motivo"] == "transferencia_activa_existente"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# H-L (2) -- CONCURRENCIA: dos `create()` simultáneos (conexión propia por
# corrutina) sobre el mismo expediente 'closed' -> exactamente UNO gana, el
# otro 409. No quedan dos activas.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h_l_concurrencia_dos_create_simultaneos_exactamente_uno_gana(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest h-l carrera create")
    expediente_id = created.id

    async def _create_one(i: int):
        # Conexión PROPIA por corrutina: el índice único de Postgres es quien
        # debe serializar de verdad, no el cliente.
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = TransferenciaService(own_conn, slug)
            actor = Actor(id=uuid.uuid4(), username=f"itest-hl-carrera-{i}")
            return await _labeled(
                f"create-{i}",
                own_service.create(
                    TransferenciaCreate(tipo="primaria", expediente_id=expediente_id, objeto="Transferencia primaria de la serie de contratos"), actor
                ),
            )
        finally:
            await own_conn.close()

    # Sin sleeps: las dos corrutinas compiten de verdad por el índice único
    # parcial `ux_transferencias_activa_por_expediente` sobre el mismo
    # expediente_id.
    results = await asyncio.gather(_create_one(0), _create_one(1))
    successes = [(label, r) for label, r, exc in results if exc is None]
    failures = [(label, exc) for label, r, exc in results if exc is not None]

    assert len(successes) == 1, f"se esperaba exactamente 1 ganador: {results!r}"
    assert len(failures) == 1, f"se esperaba exactamente 1 perdedor: {results!r}"

    winner_label, winner_result = successes[0]
    loser_label, loser_exc = failures[0]
    assert {winner_label, loser_label} == {"create-0", "create-1"}
    assert isinstance(winner_result, TransferenciaResponse)
    assert loser_exc.status_code == 409
    assert loser_exc.detail["error"] == "transferencia_activa_existente"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        count_total = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencias WHERE expediente_id = $1',
            expediente_id,
        )
        count_activas = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencias '
            "WHERE expediente_id = $1 AND estado IN ('preparada', 'enviada')",
            expediente_id,
        )
        preparada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_preparada",
        )
        failed_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_preparada_failed",
        )
    finally:
        await second_conn.close()

    # LA ASERCIÓN LETAL: nunca dos filas 'activas' del mismo expediente pese a
    # la carrera -- ni siquiera transitoriamente (el perdedor jamás comprometió
    # su INSERT: su propia tx revirtió con la UniqueViolationError).
    assert count_total == 1
    assert count_activas == 1
    assert preparada_audit == 1
    assert failed_audit == 1

    payload = await _payload_of(
        conn,
        slug,
        action="archive.transferencia_preparada_failed",
        object_ref=str(expediente_id),
    )
    assert payload["motivo"] == "transferencia_activa_existente"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# H-L (3) -- RE-PREPARAR tras rechazo: crear -> enviar -> rechazar -> crear de
# nuevo el mismo expediente -> 201 (el índice parcial NO cuenta 'rechazada'
# como activa, spec E12 §8 R5).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h_l_re_preparar_tras_rechazo_no_bloqueada_por_el_indice(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest h-l re-preparar tras rechazo")
    service = TransferenciaService(conn, slug)

    actor_prep1 = Actor(id=uuid.uuid4(), username="itest-hl-prep1")
    primera = await service.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor_prep1
    )
    actor_envia = Actor(id=uuid.uuid4(), username="itest-hl-envia")
    await service.enviar(primera.id, actor_envia)
    actor_rechaza = Actor(id=uuid.uuid4(), username="itest-hl-rechaza")
    rechazada = await service.rechazar(
        primera.id, TransferenciaRechazo(motivo="itest motivo rechazo h-l"), actor_rechaza
    )
    assert rechazada.estado == "rechazada"

    # El expediente sigue 'closed' (rechazar() no lo transiciona) -- create()
    # vuelve a pasar su gate de estado, y el índice único NO ve 'rechazada'
    # como activa.
    actor_prep2 = Actor(id=uuid.uuid4(), username="itest-hl-prep2")
    segunda = await service.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor_prep2
    )
    assert segunda.estado == "preparada"
    assert segunda.id != primera.id

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        rows = await second_conn.fetch(
            f'SELECT id, estado FROM "{schema}".transferencias '
            "WHERE expediente_id = $1 ORDER BY created_at",
            created.id,
        )
    finally:
        await second_conn.close()

    assert len(rows) == 2
    estados = {row["id"]: row["estado"] for row in rows}
    assert estados[primera.id] == "rechazada"
    assert estados[segunda.id] == "preparada"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# H-M -- coherencia origen/destino con ubicaciones REALES (complementa la
# matriz mockeada de `tests/test_transferencia_create_coherencia.py`).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h_m_primaria_coherente_con_ubicaciones_reales_201(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    created = await _seed_expediente_closed(conn, slug, "itest h-m primaria coherente real")
    origen = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    destino = await _make_ubicacion(conn, slug, tipo_archivo="central")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-hm-primaria-real-ok")
    resp = await service.create(
        TransferenciaCreate(
            tipo="primaria",
            objeto="Transferencia primaria de la serie de contratos",
            expediente_id=created.id,
            ubicacion_origen_id=origen.id,
            ubicacion_destino_id=destino.id,
        ),
        actor,
    )
    assert resp.estado == "preparada"
    assert resp.ubicacion_origen_id == origen.id
    assert resp.ubicacion_destino_id == destino.id

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_h_m_secundaria_incoherente_con_ubicaciones_reales_422_y_audita(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest h-m secundaria incoherente real")
    # secundaria exige central->historico; aquí origen es 'gestion' (incoherente).
    origen = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    destino = await _make_ubicacion(conn, slug, tipo_archivo="historico")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-hm-secundaria-real-bad")
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            TransferenciaCreate(
                tipo="secundaria",
                objeto="Transferencia primaria de la serie de contratos",
                expediente_id=created.id,
                ubicacion_origen_id=origen.id,
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

    # La validación de coherencia corre ANTES de la tx -- nunca llegó a
    # intentarse el INSERT.
    assert count_total == 0

    payload = await _payload_of(
        conn,
        slug,
        action="archive.transferencia_preparada_failed",
        object_ref=str(created.id),
    )
    assert payload["motivo"] == "ubicacion_incoherente_con_tipo"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
