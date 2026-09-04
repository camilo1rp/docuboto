# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado del AIP OAIS al cierre del expediente (E10 debt "AIP
sin-wiring") contra Postgres REAL: atomicidad marca<->asiento de
`AipWiringService.intentar_empaquetar` (commit conjunto, rollback conjunto
ante un fallo tras `audit.append`), idempotencia bajo dos llamadas reales
(secuenciales y concurrentes) sobre el MISMO expediente, y el filtro SQL de
`AipRepository.list_pending` (precondición dura + backoff/agotamiento).

Compromiso (igual que `test_indice_worm_atomicity.py`): NO hay tráfico HTTP
real a document-service/storage-service —
`document_client.fetch_anexos_internal`/`storage_client.empaquetar_aip_internal`
se monkeypatchean. Lo que este tier prueba es el tramo 100%-DB (UPDATE/INSERT
+ INSERT audit_log atómicos, WHERE de idempotencia, filtro SQL de
elegibilidad), no la resolución cross-servicio real de anexos ni la subida
real del ZIP a MinIO (cubiertas en document-service/storage-service).

Seed: el índice se marca 'firmado'+protegido-WORM con UPDATEs directos (mismo
atajo que `test_indice_worm_atomicity.py`) — `ArchiveService.close_expediente(
..., actor_id=None)` deja el índice 'pendiente_firma', y el seed lo promueve.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from orpycamcp_common import audit

from app.clients.storage_client import AipPackagingOutcome
from app.core.config import settings
from app.core.database import tenant_schema
from app.repositories.aip_repository import AipRepository
from app.schemas.archive import ExpedienteCreate, RadicadoLink, TrdSerieCreate
from app.services.aip_wiring_service import AipWiringService
from app.services.archive_service import ArchiveService

from .conftest import DSN, convalidate_for_test


async def _seed_expediente(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    *,
    archivo_gestion_years: int | None = 5,
    archivo_central_years: int | None = 5,
) -> uuid.UUID:
    archive = ArchiveService(conn, slug)
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"AIP{uuid.uuid4().hex[:6]}",
            name="itest serie AIP",
            retention_years=0,
            total_retention=0,
            archivo_gestion_years=archivo_gestion_years,
            archivo_central_years=archivo_central_years,
        ),
        actor=None,
    )
    # ADR-025 (Increment 1): una serie recién creada nace en 'borrador' -- NO
    # gobierna ningún cierre (D5). Este helper cierra el expediente más abajo
    # (vía los tests que lo llaman), así que necesita una versión VIGENTE
    # (convalidada/migrada); 'migrada' es el atajo de test correcto (D6): no
    # simula un acto administrativo que nunca ocurrió.
    await convalidate_for_test(conn, serie.id)
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    return created.id


async def _promote_indice_firmado(
    conn: asyncpg.Connection, schema: str, expediente_id: uuid.UUID, *, worm_protegido: bool
) -> None:
    file_id = uuid.uuid4()
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2, '
        f"minio_key_firmado = $3 WHERE expediente_id = $1 AND estado <> 'firmado'",
        expediente_id,
        uuid.uuid4(),
        f"{file_id}/indice-{expediente_id}.xml",
    )
    if worm_protegido:
        await conn.execute(
            f'UPDATE "{schema}".expediente_indice SET worm_protegido_at = now(), '
            f"worm_object_id = $2 WHERE expediente_id = $1",
            expediente_id,
            uuid.uuid4(),
        )


async def _seed_ready(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    *,
    con_radicado: bool = True,
    archivo_gestion_years: int | None = 5,
    archivo_central_years: int | None = 5,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Expediente `closed`, con índice VIGENTE `firmado` + protegido bajo
    WORM (precondición dura de `intentar_empaquetar`) y, opcionalmente, UN
    radicado vigente vinculado ANTES del cierre. Devuelve
    ``(expediente_id, radicado_id | None)``."""
    schema = tenant_schema(slug)
    expediente_id = await _seed_expediente(
        conn,
        slug,
        name,
        archivo_gestion_years=archivo_gestion_years,
        archivo_central_years=archivo_central_years,
    )

    radicado_id: uuid.UUID | None = None
    if con_radicado:
        radicado_id = uuid.uuid4()
        await ArchiveService(conn, slug).link_radicado(
            expediente_id,
            RadicadoLink(
                radicado_id=radicado_id,
                tracking_number=f"2026-ITEST-E-{uuid.uuid4().hex[:6]}",
            ),
            user_id=None,
        )

    await ArchiveService(conn, slug).close_expediente(expediente_id)
    await _promote_indice_firmado(conn, schema, expediente_id, worm_protegido=True)
    return expediente_id, radicado_id


def _ok_outcome(aip_id: uuid.UUID | None = None) -> AipPackagingOutcome:
    return AipPackagingOutcome(ok=True, aip_id=aip_id or uuid.uuid4())


def _patched_clients(anexos_by_radicado: dict, outcome: AipPackagingOutcome):
    """Dos `patch()` sin entrar todavía — usar como
    ``with p1, p2: ...`` (`_patched_clients` devuelve el PAR, no los entra)."""
    p_document = patch(
        "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
        new=AsyncMock(return_value=anexos_by_radicado),
    )
    p_storage = patch(
        "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
        new=AsyncMock(return_value=outcome),
    )
    return p_document, p_storage


def _one_anexo(radicado_id: uuid.UUID) -> dict:
    return {str(radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "oficio-itest.pdf"}]}


# ---------------------------------------------------------------------------
# (a) Atomicidad marca<->asiento: commit conjunto en el camino feliz.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_intentar_empaquetar_commits_mark_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, radicado_id = await _seed_ready(conn, slug, "itest AIP OK")
    outcome = _ok_outcome()
    p_document, p_storage = _patched_clients(_one_anexo(radicado_id), outcome)

    with p_document, p_storage:
        resultado = await AipWiringService(conn, slug).intentar_empaquetar(expediente_id)

    assert resultado == "empaquetado"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        aip_row = await second_conn.fetchrow(
            f'SELECT estado, aip_worm_object_id, empaquetado_at FROM "{schema}".expediente_aip '
            "WHERE expediente_id = $1",
            expediente_id,
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor, canal, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.aip_empaquetado",
        )
    finally:
        await second_conn.close()

    assert aip_row["estado"] == "empaquetado"
    assert aip_row["aip_worm_object_id"] == outcome.aip_id
    assert aip_row["empaquetado_at"] is not None
    assert audit_row is not None
    assert audit_row["canal"] == "system"

    res = await audit.verify_chain(conn, slug)
    # `res.roturas` en el mensaje: si esto cae, dice CUÁLES, no solo que cayó.
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
    assert res.verificados == res.total > 0


# ---------------------------------------------------------------------------
# (b) Atomicidad marca<->asiento: fallo DESPUÉS de audit.append revierte
# ambos, aunque `intentar_empaquetar` se trague la excepción (best-effort
# real: nunca propaga, pero tampoco deja el marcado a medias).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_intentar_empaquetar_failure_after_audit_append_reverts_mark(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, radicado_id = await _seed_ready(conn, slug, "itest AIP boom")

    original_append = audit.append

    async def _append_then_boom(*args: object, **kwargs: object) -> str:
        await original_append(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("boom-post-audit (aip, b2 fault injection)")

    monkeypatch.setattr(audit, "append", _append_then_boom)

    p_document, p_storage = _patched_clients(_one_anexo(radicado_id), _ok_outcome())
    with p_document, p_storage:
        resultado = await AipWiringService(conn, slug).intentar_empaquetar(expediente_id)

    # Best-effort real: NUNCA propaga la excepción al llamador...
    assert resultado == "pendiente"

    # ...pero el UPDATE revirtió JUNTO con el asiento que sí llegó a
    # ejecutarse (dentro del SAVEPOINT de audit.append) antes de que la
    # excepción reventara la tx externa de `intentar_empaquetar`.
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        aip_row = await second_conn.fetchrow(
            f'SELECT estado, aip_worm_object_id FROM "{schema}".expediente_aip '
            "WHERE expediente_id = $1",
            expediente_id,
        )
        empaquetado_audit_count = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.aip_empaquetado",
        )
    finally:
        await second_conn.close()

    assert aip_row["estado"] == "pendiente"
    assert aip_row["aip_worm_object_id"] is None
    assert empaquetado_audit_count == 0

    res = await audit.verify_chain(conn, slug)
    # `res.roturas` en el mensaje: si esto cae, dice CUÁLES, no solo que cayó.
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
    assert res.verificados == res.total > 0


# ---------------------------------------------------------------------------
# (c) Idempotencia REAL: dos llamadas SECUENCIALES sobre el mismo expediente
# — la segunda ni siquiera reintenta la llamada a storage-service.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_intentar_empaquetar_second_call_is_idempotent(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    expediente_id, radicado_id = await _seed_ready(conn, slug, "itest AIP idempotente")
    service = AipWiringService(conn, slug)
    p_document, p_storage = _patched_clients(_one_anexo(radicado_id), _ok_outcome())

    with p_document, p_storage as mock_storage:
        primero = await service.intentar_empaquetar(expediente_id)
        segundo = await service.intentar_empaquetar(expediente_id)

    assert primero == "empaquetado"
    assert segundo == "empaquetado"
    assert mock_storage.await_count == 1  # la 2da llamada ni toca storage-service

    empaquetado_audit_count = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.aip_empaquetado",
    )
    assert empaquetado_audit_count == 1


# ---------------------------------------------------------------------------
# (d) Idempotencia bajo CONCURRENCIA real: dos corrutinas, cada una con su
# propia conexión asyncpg, disparando `intentar_empaquetar` sobre el MISMO
# expediente al mismo tiempo — solo una debe ganar `mark_empaquetado`.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_concurrent_intentar_empaquetar_does_not_double_mark(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, radicado_id = await _seed_ready(conn, slug, "itest AIP concurrente")
    p_document, p_storage = _patched_clients(_one_anexo(radicado_id), _ok_outcome())

    async def _attempt() -> str:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            return await AipWiringService(own_conn, slug).intentar_empaquetar(expediente_id)
        finally:
            await own_conn.close()

    # Sin sleeps: ambas corrutinas compiten de verdad por el MISMO UPDATE
    # (WHERE expediente_id=$1 AND estado <> 'empaquetado') sobre la misma fila.
    with p_document, p_storage:
        resultados = await asyncio.gather(_attempt(), _attempt())

    assert "empaquetado" in resultados
    assert set(resultados) <= {"empaquetado", "omitido"}

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        empaquetado_audit_count = await verify_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.aip_empaquetado",
        )
        aip_row = await verify_conn.fetchrow(
            f'SELECT estado FROM "{schema}".expediente_aip WHERE expediente_id = $1',
            expediente_id,
        )
    finally:
        await verify_conn.close()

    # CRÍTICO: solo UN asiento real, aunque ambas corrutinas hayan recibido
    # `ok=True` del cliente storage (mockeado, no compite de verdad) — el
    # candado real es el `WHERE estado <> 'empaquetado'` del UPDATE.
    assert empaquetado_audit_count == 1
    assert aip_row["estado"] == "empaquetado"

    res = await audit.verify_chain(conn, slug)
    # `res.roturas` en el mensaje: si esto cae, dice CUÁLES, no solo que cayó.
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
    assert res.verificados == res.total > 0


# ---------------------------------------------------------------------------
# (e) 0 anexos digitales -> 'omitido' contra Postgres real (mark + asiento).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_intentar_empaquetar_zero_anexos_marks_omitido_for_real(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # El camino 'omitido' (cero anexos digitales → sin AIP, TERMINAL) solo aplica
    # con el kill-switch del AIP solo-índice APAGADO. Su default es ON (un
    # expediente sin anexos empaqueta un AIP solo-índice = 'empaquetado'), así que
    # este test —que ejercita específicamente el 'omitido'— debe fijar el flag,
    # igual que su gemelo unit (`test_..._marks_omitido_and_audits`). Sin esto el
    # test quedó desalineado del default ON y aserraba 'empaquetado'.
    monkeypatch.setattr(settings, "aip_indice_only_enabled", False)
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, _radicado_id = await _seed_ready(conn, slug, "itest AIP 100% fisico")
    p_document, p_storage = _patched_clients({}, _ok_outcome())  # sin anexos digitales

    with p_document, p_storage as mock_storage:
        resultado = await AipWiringService(conn, slug).intentar_empaquetar(expediente_id)

    assert resultado == "omitido"
    mock_storage.assert_not_awaited()

    aip_row = await conn.fetchrow(
        f'SELECT estado FROM "{schema}".expediente_aip WHERE expediente_id = $1',
        expediente_id,
    )
    assert aip_row["estado"] == "omitido"
    omitido_audit_count = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.aip_omitido",
    )
    assert omitido_audit_count == 1


# ---------------------------------------------------------------------------
# (f) `AipRepository.list_pending` — filtro SQL real de elegibilidad +
# backoff/agotamiento.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_pending_respects_eligibility_backoff_and_agotado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    repo = AipRepository(conn)

    elegible, _ = await _seed_ready(conn, slug, "itest AIP elegible", con_radicado=False)

    no_cerrado = await _seed_expediente(conn, slug, "itest AIP no cerrado")
    # deliberadamente NO se cierra -> índice nunca se promueve a 'firmado'

    indice_no_firmado = await _seed_expediente(conn, slug, "itest AIP indice no firmado")
    await ArchiveService(conn, slug).close_expediente(indice_no_firmado)
    # índice queda 'pendiente_firma', nunca promovido

    worm_no_protegido = await _seed_expediente(conn, slug, "itest AIP worm no protegido")
    await ArchiveService(conn, slug).close_expediente(worm_no_protegido)
    await _promote_indice_firmado(conn, schema, worm_no_protegido, worm_protegido=False)

    ya_empaquetado, _ = await _seed_ready(conn, slug, "itest AIP ya empaquetado", con_radicado=False)
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_aip (expediente_id, estado, aip_worm_object_id, '
        f"empaquetado_at) VALUES ($1, 'empaquetado', $2, now())",
        ya_empaquetado,
        uuid.uuid4(),
    )

    backoff_futuro, _ = await _seed_ready(conn, slug, "itest AIP backoff futuro", con_radicado=False)
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_aip (expediente_id, estado, proximo_intento_at) '
        f"VALUES ($1, 'pendiente', $2)",
        backoff_futuro,
        datetime.now(tz=UTC) + timedelta(days=1),
    )

    agotado, _ = await _seed_ready(conn, slug, "itest AIP agotado", con_radicado=False)
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_aip (expediente_id, estado) VALUES ($1, \'agotado\')',
        agotado,
    )

    pending = await repo.list_pending(50)
    pending_ids = {r["expediente_id"] for r in pending}

    assert elegible in pending_ids
    assert no_cerrado not in pending_ids
    assert indice_no_firmado not in pending_ids
    assert worm_no_protegido not in pending_ids
    assert ya_empaquetado not in pending_ids
    assert backoff_futuro not in pending_ids
    assert agotado not in pending_ids
