# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Atomicidad de `ArchiveService.create_expediente` / `BatchArchiveService` contra
Postgres REAL: commit real (a), rollback físico ante un fallo natural a mitad de
transacción (b1), rollback del asiento de auditoría cuando el fallo llega DESPUÉS
de `audit.append` dentro de la misma tx (b2), unicidad del consecutivo bajo
concurrencia real con conexiones separadas (c), aislamiento por-ítem del batch (d)
y el trigger append-only de `public.audit_log` (e).

Carril de integración (`RUN_INTEGRATION=1`, ver `conftest.py`). Usa los fixtures
`db_conn`/`tenant_ctx` ya existentes — no se reescriben aquí.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.core.actor import Actor
from app.core.database import tenant_schema
from app.repositories.archive_repository import ArchiveRepository
from app.schemas.archive import (
    ExpedienteCreate,
    ExpedienteDisposicionResult,
    ExpedienteUpdate,
    RadicadoLink,
    TrdSerieCreate,
)
from app.schemas.batch import BatchExpedienteCreate, BatchExpedientesRequest
from app.schemas.transferencia import (
    TransferenciaCreate,
    TransferenciaRechazo,
    TransferenciaResponse,
)
from app.services.archive_service import ArchiveService
from app.services.batch_service import BatchArchiveService
from app.services.transferencia_service import TransferenciaService
from tests.conftest import registrar_radicado

from .conftest import DSN, huella_en_almacen

# ---------------------------------------------------------------------------
# (a) Camino feliz: commit real + cadena de auditoría íntegra.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_expediente_commits_and_chain_is_valid(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    service = ArchiveService(conn, slug)
    payload = ExpedienteCreate(name="itest atomicidad OK", nivel_seguridad=1)

    response = await service.create_expediente(payload=payload, opened_by=None)

    # Se lee desde una SEGUNDA conexión asyncpg (no `conn`, que ya vio su propio
    # commit desde dentro): así se prueba el COMMIT real en el servidor, no solo
    # la visibilidad intra-conexión (que sería trivialmente cierta incluso si el
    # driver no hubiera confirmado nada al backend).
    schema = tenant_schema(slug)
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        persisted = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expedientes WHERE code = $1',
            response.code,
        )
        indice_v0 = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_indice '
            "WHERE expediente_id = $1 AND version = 0",
            response.id,
        )
        # (C2) El test original solo acreditaba expediente + índice v0, nunca el
        # COMMIT del propio ASIENTO DE AUDITORÍA (el último write del bloque
        # atómico) ni el de la hoja de ruta (`expediente_eventos`) — ambos desde
        # esta SEGUNDA conexión, para probar el commit real en el servidor.
        audit_row = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_created",
            response.code,
        )
        evento_row = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            response.id,
            "abierto",
        )
    finally:
        await second_conn.close()

    assert persisted == 1
    assert indice_v0 == 1
    assert audit_row == 1
    assert evento_row == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (b1) EL TEST ESTRELLA — fallo NATURAL (UNIQUE de `expedientes.code`), sin
# monkeypatch. Ver recon: es el único candidato que revienta a mitad de la tx
# del SUT usando una restricción real ya existente en el schema (no hace falta
# inyectar DDL de fault-injection).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_expediente_rollback_reverts_sequence_and_audit(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    year = datetime.now(tz=UTC).year  # mismo cálculo que archive_service.py:143

    # Semilla directa en BD (sin pasar por el SUT): fijamos `last_seq=41` y
    # colocamos un expediente "okupa" en el código que la secuencia va a emitir
    # a continuación (`EXP-{year}-0042`). `next_expediente_code` avanzará la
    # secuencia a 42 y el INSERT subsiguiente en `expedientes` chocará con el
    # okupa por `expedientes_code_key` — un fallo real de Postgres a MITAD de
    # la transacción del SUT (después de haber avanzado la secuencia, antes de
    # crear el índice v0 / evento / asiento de auditoría).
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_sequences (year, last_seq) VALUES ($1, 41)',
        year,
    )
    await conn.execute(
        f'INSERT INTO "{schema}".expedientes (code, name) VALUES ($1, $2)',
        f"EXP-{year}-0042",
        "okupa del codigo",
    )

    # (C1) Semilla de un asiento REAL antes de invocar al SUT. Sin esto, la
    # cadena de auditoría del tenant queda VACÍA todo el test y
    # `audit.verify_chain` pasa trivialmente (una cadena vacía es "íntegra"
    # por definición) — no prueba nada sobre el rollback. Con la semilla,
    # `audit_count == 1` de más abajo sí es una aserción con contenido: que el
    # intento fallido de `create_expediente` NO añadió ningún asiento colgante
    # a una cadena que ya tenía historia.
    await audit.append(
        conn,
        tenant_slug=slug,
        service="archive-service",
        action="archive.itest_seed",
        object_type="expediente",
        object_ref="EXP-ITEST-SEED",
        payload={"seed": True},
        canal="api",
    )

    service = ArchiveService(conn, slug)
    payload = ExpedienteCreate(name="victima del choque de codigo", nivel_seguridad=1)

    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await service.create_expediente(payload=payload, opened_by=None)

    # Todas las aserciones post-rollback se hacen con nombres de tabla
    # cualificados por schema: `_set_search_path` fija el search_path con un
    # `SET` (no `SET LOCAL`) DENTRO de la transacción abortada — Postgres
    # también revierte ese `SET` de sesión al hacer ROLLBACK, así que tras la
    # excepción `conn` ya NO tiene el search_path del tenant. Cualificar evita
    # depender de ese detalle interno.
    seq_after = await conn.fetchval(
        f'SELECT last_seq FROM "{schema}".expediente_sequences WHERE year = $1', year
    )
    # (ii) El corazón del test: el AVANCE de la secuencia (41 -> 42) también
    # se revirtió junto con el resto de la transacción. Si `next_expediente_code`
    # no estuviera envuelto en la misma tx que el INSERT de `expedientes`, este
    # valor habría quedado en 42 (un hueco permanente en el consecutivo, y el
    # código EXP-{year}-0042 quedaría "quemado" para siempre).
    assert seq_after == 41

    expedientes_count = await conn.fetchval(f'SELECT count(*) FROM "{schema}".expedientes')
    # (i) Solo sigue existiendo el okupa sembrado a mano — ningún expediente
    # nuevo (ni el que reventó, ni ningún artefacto a medias). NOTA (C1):
    # regression-guard, no prueba de rollback por sí sola — el UNIQUE de
    # Postgres impide el segundo INSERT ocurra o no dentro de una tx, así que
    # este assert se cumpliría igual aunque `create_expediente` no envolviera
    # nada en una transacción.
    assert expedientes_count == 1

    indice_count = await conn.fetchval(f'SELECT count(*) FROM "{schema}".expediente_indice')
    eventos_count = await conn.fetchval(f'SELECT count(*) FROM "{schema}".expediente_eventos')
    # NOTA (C1): estos dos también son regression-guards, no prueba de
    # rollback — el fallo ocurre en el INSERT de `expedientes`, ANTES de que
    # el código llegue siquiera a crear el índice v0 o el evento, con o sin
    # transacción envolvente. Lo que sí demuestran (b2, más abajo, sí revierte
    # escritura ya ejecutada) queda cubierto por el otro test de esta familia.
    assert indice_count == 0
    assert eventos_count == 0

    # (iii) `next_expediente_code` + `create_expediente` fallan ANTES de llegar
    # al `audit.append` final (es el último write del bloque) -> no debe haber
    # NINGÚN asiento colgante para este tenant_slug (que es único por test).
    # Con la semilla de más arriba, este assert SÍ es portante: la cadena del
    # tenant ya tenía 1 fila real antes del intento fallido; que siga en 1 (y
    # no en 2) prueba que el intento no dejó ningún asiento adicional.
    audit_count = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1", slug
    )
    assert audit_count == 1

    # (iv) La cadena de auditoría del tenant sigue íntegra — ahora con
    # contenido real (la semilla), no una cadena vacía trivialmente íntegra.
    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (b2) Fallo DESPUÉS de `audit.append`, dentro de la MISMA transacción del SUT.
# No hay ningún fallo natural posible en ese punto exacto (es el último write
# del bloque atómico, justo antes del `return`) — aquí SÍ se justifica un
# monkeypatch: se envuelve `orpycamcp_common.audit.append` para que primero
# ejecute la implementación real (deja el INSERT hecho, dentro de la tx del
# SUT) y luego lance una excepción, forzando el ROLLBACK de la tx externa
# DESPUÉS de que el asiento ya se escribió.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_expediente_failure_after_audit_append_reverts_audit_row(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    year = datetime.now(tz=UTC).year

    original_append = audit.append

    async def _append_then_boom(*args: object, **kwargs: object) -> str:
        # Ejecuta la escritura REAL (misma `conn`, mismo tenant) — el asiento
        # queda insertado dentro del SAVEPOINT que abre `audit.append` — y
        # SOLO DESPUÉS revienta, dentro de la tx externa de `create_expediente`.
        await original_append(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("boom-post-audit (b2 fault injection, testing-engineer)")

    # `app.services.archive_service` hace `from orpycamcp_common import audit` y
    # llama `audit.append(...)`: parcheando el atributo `append` del MÓDULO
    # (el mismo objeto que ambos módulos referencian) se intercepta la llamada
    # sin tocar el SUT. `monkeypatch` restaura el original al final del test.
    monkeypatch.setattr(audit, "append", _append_then_boom)

    service = ArchiveService(conn, slug)
    payload = ExpedienteCreate(name="victima post-audit", nivel_seguridad=1)

    with pytest.raises(RuntimeError, match="boom-post-audit"):
        await service.create_expediente(payload=payload, opened_by=None)

    # Nada de lo escrito en la tx del SUT sobrevive: ni el expediente...
    expedientes_count = await conn.fetchval(f'SELECT count(*) FROM "{schema}".expedientes')
    assert expedientes_count == 0

    # ...ni el avance de la secuencia (era la primera creación de este tenant
    # para este año -> tras el rollback no debe quedar NINGUNA fila, ni
    # siquiera con last_seq=0: el INSERT nunca se confirmó).
    seq_row = await conn.fetchrow(
        f'SELECT last_seq FROM "{schema}".expediente_sequences WHERE year = $1', year
    )
    assert seq_row is None

    # ...ni, sobre todo, el ASIENTO DE AUDITORÍA que sí llegó a ejecutarse
    # (INSERT + advisory lock liberado en su propio SAVEPOINT) antes de que la
    # excepción reventara la tx externa: prueba que `audit.append` vive DENTRO
    # del mismo bloque atómico que el resto de la creación, no aparte.
    audit_count = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1", slug
    )
    assert audit_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (c) Concurrencia REAL de `next_expediente_code`: N corrutinas, CADA UNA con
# su propia conexión asyncpg, creando expedientes a la vez en el mismo
# tenant/año. Imposible de ejercitar en el carril mockeado (ahí no hay
# servidor Postgres real serializando el `INSERT ... ON CONFLICT ...
# RETURNING`) — es el test que justifica la existencia de este tier.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_expediente_concurrent_codes_are_unique(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    n = 8

    # (C4) Sembrar la fila del año con `last_seq=0` ANTES del `gather`: un
    # tenant virgen (sin esta semilla) hace que la carrera realmente
    # ejercitada sea la de "quién crea primero la fila de `expediente_sequences`
    # para este año" (el `ON CONFLICT` la resuelve trivialmente, sin
    # contención real sobre un valor existente), no la del INCREMENTO
    # concurrente sobre una fila YA existente — que es el escenario real en
    # producción (un año con radicados desde hace tiempo) y el que puede
    # producir códigos duplicados si `next_expediente_code` no serializa bien.
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_sequences (year, last_seq) VALUES ($1, 0)',
        datetime.now(tz=UTC).year,
    )

    async def _create_one(i: int) -> str:
        # Conexión PROPIA por corrutina: `asyncpg.Connection` no es
        # concurrency-safe (una sola conexión solo puede tener un statement en
        # vuelo), así que compartir `_conn`/`db_conn` entre las corrutinas de
        # `asyncio.gather` no ejercitaría concurrencia real en el servidor —
        # simplemente serializaría las llamadas en el cliente.
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            service = ArchiveService(own_conn, slug)
            payload = ExpedienteCreate(name=f"itest concurrente {i}", nivel_seguridad=1)
            response = await service.create_expediente(payload=payload, opened_by=None)
            return response.code
        finally:
            await own_conn.close()

    # Sin sleeps: `asyncio.gather` lanza las N corrutinas y cada una compite de
    # verdad por el `INSERT ... ON CONFLICT (year) DO UPDATE ... RETURNING`
    # sobre la MISMA fila de `expediente_sequences`.
    codes = await asyncio.gather(*[_create_one(i) for i in range(n)])

    assert len(codes) == n
    # El corazón del test: ningún código de radicación duplicado bajo carrera
    # real (row-lock del propio `ON CONFLICT`, ver `archive_repository.py`).
    assert len(set(codes)) == n

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        persisted = await verify_conn.fetchval(f'SELECT count(*) FROM "{schema}".expedientes')
        seq_row = await verify_conn.fetchrow(
            f'SELECT last_seq FROM "{schema}".expediente_sequences WHERE year = $1',
            datetime.now(tz=UTC).year,
        )
    finally:
        await verify_conn.close()

    assert persisted == n
    assert seq_row is not None
    assert seq_row["last_seq"] == n


# ---------------------------------------------------------------------------
# (d) BATCH: un ítem falla, el otro PERSISTE (aislamiento por-ítem).
#
# `BatchArchiveService` NO fija `search_path` por sí solo (gotcha de recon: en
# producción lo hace `acquire_tenant`) -> hay que fijarlo a mano sobre la MISMA
# conexión antes de invocarlo.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_batch_item_failure_does_not_corrupt_sibling_item(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    year = datetime.now(tz=UTC).year

    await conn.execute(f'SET search_path TO "{schema}"')

    # Okupa en el SEGUNDO código que la secuencia va a emitir: el ítem 0 (sin
    # secuencia previa sembrada) avanza a 1 -> 'EXP-{year}-0001' (libre, entra
    # OK); el ítem 1 avanza a 2 -> 'EXP-{year}-0002' -> choca con el okupa.
    await conn.execute(
        "INSERT INTO expedientes (code, name) VALUES ($1, $2)",
        f"EXP-{year}-0002",
        "okupa del batch",
    )

    batch_service = BatchArchiveService(conn, slug)
    request = BatchExpedientesRequest(
        expedientes=[
            BatchExpedienteCreate(name="item-0-sobrevive"),
            BatchExpedienteCreate(name="item-1-choca-con-okupa"),
        ]
    )
    created_by = uuid.uuid4()
    job_id = await batch_service.create_batch_expedientes(request, user_id=created_by)
    await batch_service.process_batch_expedientes(job_id)

    # `UPDATE batch_jobs`/`batch_job_items` de fallo corren EN AUTOCOMMIT, fuera
    # de la tx (revertida) del ítem -> deben persistir tal cual el recon predice.
    job = await conn.fetchrow(
        "SELECT status, processed_items, failed_items FROM batch_jobs WHERE id = $1", job_id
    )
    assert job["processed_items"] == 1
    assert job["failed_items"] == 1
    assert job["status"] == "failed"

    items = await conn.fetch(
        "SELECT item_index, status, result_id, error_message "
        "FROM batch_job_items WHERE job_id = $1 ORDER BY item_index",
        job_id,
    )
    assert items[0]["status"] == "success"
    assert items[0]["result_id"] is not None
    assert items[1]["status"] == "failed"
    assert items[1]["error_message"]  # no vacío/None: el mensaje de asyncpg

    # (C5) Las dos aserciones PORTANTES de este test se leen desde una SEGUNDA
    # conexión asyncpg — no desde `conn`, que es la misma que ejecutó el batch
    # y por tanto ya vería sus propios efectos incluso si `process_batch_expedientes`
    # no hubiera confirmado nada al servidor (visibilidad intra-conexión no es
    # prueba de commit real). `conn` tiene el `search_path` del tenant fijado
    # más arriba; la segunda conexión no, así que se cualifica por schema.
    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        # El ítem 0 PERSISTE pese a que el ítem 1 reventó después que él en el
        # mismo job — prueba que la tx es por-ítem, no por-job.
        expediente_count = await verify_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expedientes WHERE code = $1',
            f"EXP-{year}-0001",
        )
        # El consecutivo NO quedó avanzado por el intento fallido del ítem 1: su
        # `INSERT ... ON CONFLICT` (que había subido last_seq a 2) revirtió junto
        # con el resto de su transacción -> queda en 1 (lo que confirmó el ítem 0).
        seq_after = await verify_conn.fetchval(
            f'SELECT last_seq FROM "{schema}".expediente_sequences WHERE year = $1', year
        )
    finally:
        await verify_conn.close()

    assert expediente_count == 1
    assert seq_after == 1

    # Solo el ítem 0 dejó asiento forense (el `audit.append` del ítem 1 nunca
    # se ejecuta: revienta antes, en el INSERT de `expedientes`).
    audit_count = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log "
        "WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.expediente_created",
    )
    assert audit_count == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (e) `public.audit_log` es append-only: el TRIGGER de BD rechaza UPDATE/DELETE
# incluso ejecutados directamente por SQL (no por `orpycamcp_common.audit`, que
# de todos modos nunca los emite).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_audit_log_trigger_rejects_update_and_delete(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    await audit.append(
        conn,
        tenant_slug=slug,
        service="archive-service",
        action="archive.itest_probe",
        object_type="expediente",
        object_ref="EXP-ITEST-PROBE",
        payload={"probe": True},
        canal="api",
    )

    existing = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1", slug
    )
    assert existing == 1

    # `asyncpg.execute` fuera de un `async with conn.transaction()` corre como
    # una transacción implícita por statement: el fallo de UNA sentencia no
    # deja la conexión "abortada" para las siguientes (a diferencia de
    # psycopg2), así que se puede seguir usando `conn` normalmente después de
    # cada `pytest.raises`.
    # (C3) `PostgresError` es la clase BASE de asyncpg — atraparía igual un
    # fallo de SETUP (p. ej. columna inexistente, permiso denegado) que el
    # rechazo específico del trigger append-only. Se estrecha con `match` a
    # los mensajes que de verdad levantan `reject_audit_log_mutation`/
    # `reject_audit_log_mutation_d03` (ver `_AUDIT_LOG_CREATE_DDL` en
    # `conftest.py`): "... is append-only ..." / "... is prohibited ...".
    with pytest.raises(asyncpg.exceptions.PostgresError, match=r"append-only|prohibited"):
        await conn.execute("DELETE FROM public.audit_log WHERE tenant_slug = $1", slug)

    with pytest.raises(asyncpg.exceptions.PostgresError, match=r"append-only|prohibited"):
        await conn.execute(
            "UPDATE public.audit_log SET action = 'hackeado' WHERE tenant_slug = $1", slug
        )

    # La fila sigue intacta y la cadena del tenant sigue íntegra: ni el DELETE
    # ni el UPDATE surtieron ningún efecto.
    still_there = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.itest_probe",
    )
    assert still_there == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (f)/(g) `link_radicado`: atomicidad vínculo<->índice<->evento<->auditoría
# (commits 4d86d60/a20b4a3) SIN cobertura de integración hasta ahora. (f)
# fuerza un fallo NATURAL (CHECK inyectado sobre `expediente_eventos.accion`)
# a mitad del bloque atómico; (g) ejercita la concurrencia REAL del
# `pg_advisory_xact_lock` de `orden_documental` (`ArchiveRepository.link_radicado`)
# con conexiones separadas.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_link_radicado_rollback_reverts_link_index_and_audit_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest link boom", nivel_seguridad=1),
        opened_by=None,
    )

    # Fallo NATURAL: un CHECK real de Postgres que revienta justo el evento
    # de la hoja de ruta que `link_radicado` inserta DESPUÉS de mutar el
    # vínculo y regenerar el índice (orden real en archive_service.py: INSERT
    # del vínculo -> `_regenerate_index` [índice v1 + `archive.indice_regenerated`]
    # -> `insert_expediente_evento('radicado_vinculado')` -> BOOM aquí, antes
    # de que `audit.append('archive.radicado_linked')` se alcance siquiera).
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_link_boom CHECK (accion <> 'radicado_vinculado')"
    )

    payload = RadicadoLink(
        radicado_id=registrar_radicado("2026-ITEST-E-000001"),
        tracking_number="2026-ITEST-E-000001",
    )

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.link_radicado(created.id, payload, user_id=None)

    # Verificación post-rollback desde una SEGUNDA conexión (prueba el
    # ROLLBACK real en el servidor, no solo visibilidad intra-conexión).
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        link_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_radicados WHERE expediente_id = $1',
            created.id,
        )
        v0_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_indice '
            "WHERE expediente_id = $1 AND version = 0",
            created.id,
        )
        v1_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_indice '
            "WHERE expediente_id = $1 AND version = 1",
            created.id,
        )
        # La aserción FUERTE: `regenerate()` ya había ejecutado su PROPIO
        # `audit.append('archive.indice_regenerated')` — un `INSERT` real,
        # comprometido dentro del mismo bloque atómico — ANTES de llegar al
        # `insert_expediente_evento` que revienta. Si ese asiento sobreviviera
        # al ROLLBACK de `link_radicado`, sería un asiento forense colgante
        # sin la mutación que dice describir.
        regenerated_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_regenerated",
        )
        linked_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.radicado_linked",
        )
    finally:
        await second_conn.close()

    assert link_count == 0
    assert v0_count == 1
    assert v1_count == 0
    assert regenerated_audit == 0
    assert linked_audit == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_link_radicado_concurrent_distinct_radicados_no_duplicate_order(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """N corrutinas, CADA UNA con su propia conexión asyncpg, vinculando
    radicados DISTINTOS al MISMO expediente a la vez. El `pg_advisory_xact_lock
    (hashtext(expediente_id))` de `ArchiveRepository.link_radicado` debe
    serializar el cálculo `MAX(orden_documental)+1` entre ellas; si no lo
    hiciera, dos corrutinas podrían leer el mismo MAX y chocar contra el
    índice único parcial `ux_exp_rad_orden` (o, peor, colar duplicados si esa
    constraint no bloqueara la carrera) — sería un BUG DE PRODUCCIÓN real.
    """
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest link concurrente", nivel_seguridad=1),
        opened_by=None,
    )
    expediente_id = created.id
    n = 6

    async def _link_one(i: int) -> None:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = ArchiveService(own_conn, slug)
            payload = RadicadoLink(
                radicado_id=registrar_radicado(f"2026-ITEST-E-{i:06d}"),
                tracking_number=f"2026-ITEST-E-{i:06d}",
            )
            await own_service.link_radicado(expediente_id, payload, user_id=None)
        finally:
            await own_conn.close()

    # Sin sleeps: las N corrutinas compiten de verdad por el advisory lock
    # sobre el MISMO expediente_id.
    await asyncio.gather(*[_link_one(i) for i in range(n)])

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        rows = await verify_conn.fetch(
            f'SELECT orden_documental FROM "{schema}".expediente_radicados '
            "WHERE expediente_id = $1",
            expediente_id,
        )
    finally:
        await verify_conn.close()

    ordenes = [r["orden_documental"] for r in rows]
    assert len(ordenes) == n
    # El corazón del test: ningún ordinal documental duplicado bajo carrera
    # real -- si esto fallara sería el índice único parcial `ux_exp_rad_orden`
    # rechazando una de las N corrutinas con un error inesperado (no
    # `CheckViolationError` esperado por este test) o, si el advisory lock no
    # sirviera de nada, un duplicado que SÍ pasara el índice único porque las
    # dos corrutinas commitearon exactamente el mismo valor en la misma
    # sentencia -- cualquiera de los dos es un bug real, no un flake del test.
    assert len(set(ordenes)) == n


# ---------------------------------------------------------------------------
# (h) `close_expediente` / `transfer_expediente`: atomicidad transición<->
# evento<->auditoría (fix recién aplicado en esta misma sesión, ver
# archive_service.py). Fallo NATURAL con un CHECK inyectado sobre
# `expediente_eventos.accion` -- revienta el evento de la hoja de ruta que
# ambos métodos insertan DENTRO de su `conn.transaction()`, antes de
# cualquier otro write del bloque.
#
# `close_expediente` se invoca SIEMPRE con `actor_id=None` (el default): el
# propio SUT (`IndexService.firmar_indice`) corta ANTES de intentar ninguna
# llamada HTTP a signature-service cuando falta `actor_id` (`if not actor_id:
# mark_pendiente(...); return None` -- ver index_service.py). Evita tráfico de
# red real SIN monkeypatchear nada del SUT; el compromiso es que estos tests
# no ejercitan el camino de sellado XAdES-B real (ver 'compromises').
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_close_expediente_rollback_reverts_transition_and_audit_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest close boom", nivel_seguridad=1),
        opened_by=None,
    )

    # Fallo NATURAL: revienta `insert_expediente_evento(accion='cerrado')`,
    # que en `close_expediente` corre DESPUÉS de `transition_expediente`
    # (status -> 'closed') y ANTES de `regenerate`/`audit.append`.
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_close_boom CHECK (accion <> 'cerrado')"
    )

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.close_expediente(created.id)

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        closed_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_closed",
        )
        # `regenerate` (índice v1, la versión final de cierre) corre DESPUÉS
        # del evento que revienta -- nunca debió alcanzarse.
        v1_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_indice '
            "WHERE expediente_id = $1 AND version = 1",
            created.id,
        )
    finally:
        await second_conn.close()

    # El corazón del test: la transición a 'closed' (ya comprometida por el
    # UPDATE...RETURNING antes del fallo) se REVIRTIÓ junto con el resto del
    # bloque -- el expediente sigue 'open', no queda en un limbo 'closed' sin
    # evento ni asiento de auditoría.
    assert status_row == "open"
    assert closed_audit == 0
    assert v1_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_close_expediente_commits_transition_and_audit(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest close OK", nivel_seguridad=1),
        opened_by=None,
    )

    result = await service.close_expediente(created.id)
    assert result.status == "closed"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        closed_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_closed",
        )
        evento_cerrado = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "cerrado",
        )
    finally:
        await second_conn.close()

    # Acredita el COMMIT real de AMBOS lados del gemelo transición<->auditoría
    # -- ninguno quedó atrás del otro.
    assert status_row == "closed"
    assert closed_audit == 1
    assert evento_cerrado == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transfer_expediente_rollback_reverts_transition_and_audit_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest transfer boom", nivel_seguridad=1),
        opened_by=None,
    )
    await service.close_expediente(created.id)

    # `_assert_indice_firmado` exige estado='firmado' -- se marca DIRECTO en
    # BD (sin pasar por signature-service vía HTTP, ver 'compromises') para
    # poder ejercitar en aislamiento el tramo transaccional de
    # `transfer_expediente`, que es DISTINTO del sellado en sí.
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )

    # Fallo NATURAL: revienta `insert_expediente_evento(accion='transferido')`,
    # que corre DESPUÉS de `transition_expediente` (status -> 'transferred')
    # y ANTES de `audit.append('archive.expediente_transferred')`.
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_transfer_boom CHECK (accion <> 'transferido')"
    )

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.transfer_expediente(created.id)

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        transferred_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
    finally:
        await second_conn.close()

    # El expediente 'transferred' congela el ciclo de vida (`_VALID_TRANSITIONS`
    # no permite salir de él) -- si esta transición no se revirtiera, quedaría
    # un expediente PERMANENTEMENTE transferido sin evento ni asiento, sin
    # forma de reintentar (choca con 409 `invalid_status_transition`).
    assert status_row == "closed"
    assert transferred_audit == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transfer_expediente_commits_transition_and_audit(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest transfer OK", nivel_seguridad=1),
        opened_by=None,
    )
    await service.close_expediente(created.id)
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )

    result = await service.transfer_expediente(created.id)
    assert result.status == "transferred"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        transferred_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
        evento_transferido = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferido",
        )
    finally:
        await second_conn.close()

    assert status_row == "transferred"
    assert transferred_audit == 1
    assert evento_transferido == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (i) EL TEST ESTRELLA de esta tanda — TOCTOU / doble cierre CONCURRENTE
# (H3): dos corrutinas, cada una con su PROPIA conexión asyncpg, cerrando el
# MISMO expediente a la vez con `asyncio.gather`. Debe reventar rojo si se
# quita el `AND status = $N` del UPDATE de `transition_expediente`.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_close_expediente_concurrent_double_close_exactly_one_wins(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest close race", nivel_seguridad=1),
        opened_by=None,
    )
    expediente_id = created.id

    async def _close_one() -> ExpedienteDisposicionResult:
        # Conexión PROPIA por corrutina — mismo razonamiento que (c)/(g):
        # `asyncpg.Connection` no es concurrency-safe, compartir `conn` entre
        # las dos corrutinas de `asyncio.gather` solo serializaría las
        # llamadas en el cliente, no ejercitaría una carrera real en el
        # servidor. `actor_id` se deja en su default (`None`): `close_expediente`
        # ya corta ANTES de intentar ninguna llamada HTTP a signature-service
        # cuando falta `actor_id` (`IndexService.firmar_indice`), así que este
        # test no genera tráfico de red real.
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = ArchiveService(own_conn, slug)
            return await own_service.close_expediente(expediente_id)
        finally:
            await own_conn.close()

    # Sin sleeps: `asyncio.gather` lanza las dos corrutinas y cada una compite
    # de verdad por el UPDATE transaccional del MISMO expediente.
    results = await asyncio.gather(_close_one(), _close_one(), return_exceptions=True)

    successes = [r for r in results if isinstance(r, ExpedienteDisposicionResult)]
    failures = [r for r in results if isinstance(r, HTTPException)]
    unexpected = [
        r for r in results if not isinstance(r, (ExpedienteDisposicionResult, HTTPException))
    ]
    assert not unexpected, f"resultado inesperado en la carrera: {unexpected!r}"

    # El corazón del test: exactamente UNA corrutina gana (cierra), la otra
    # recibe 409 -- nunca las dos, nunca ninguna.
    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0].status == "closed"

    loser = failures[0]
    assert loser.status_code == 409
    # LA PRUEBA de que la carrera se ejerció DE VERDAD y no fue una ejecución
    # secuencial disfrazada de `asyncio.gather`: si la corrutina perdedora
    # hubiera hecho su propia lectura (`_expediente_or_404`) DESPUÉS de que la
    # ganadora ya hubiera comprometido su commit, habría visto `exp.status ==
    # 'closed'` y `_assert_transition` habría reventado ANTES de entrar en la
    # tx, con el error 'invalid_status_transition' (target no válido desde
    # 'closed'). Que el error real sea 'expediente_transition_failed' -- el
    # que solo emite el fail-closed DENTRO de la tx cuando el `UPDATE ... AND
    # status = $N` (H3) no casa ninguna fila -- prueba que AMBAS corrutinas
    # superaron su propia lectura + `_assert_transition` viendo TODAVÍA
    # 'open', antes de que ninguna de las dos comprometiera nada: perdió
    # recién en el UPDATE transaccional, que es exactamente la carrera que
    # este test existe para ejercitar.
    assert loser.detail["error"] == "expediente_transition_failed"

    # Verificación post-carrera desde una SEGUNDA conexión (prueba el estado
    # real comprometido en el servidor, no solo lo que cada corrutina vio en
    # su propia conexión).
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', expediente_id
        )
        closed_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_closed",
        )
        evento_cerrado = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            expediente_id,
            "cerrado",
        )
    finally:
        await second_conn.close()

    # Lo que de verdad importa (pedido explícitamente): en el `audit_log`
    # INDELEBLE queda EXACTAMENTE 1 asiento 'archive.expediente_closed' y
    # EXACTAMENTE 1 evento 'cerrado' en la hoja de ruta append-only -- ningún
    # duplicado forense pese a que dos corrutinas intentaron el mismo cierre
    # a la vez.
    assert status_row == "closed"
    assert closed_audit == 1
    assert evento_cerrado == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (ii) `TransferenciaService.recibir` (H1): atomicidad UPDATE-transferencia +
# transición-expediente + evento + auditoría en una única `conn.transaction()`.
# (ii.a) rollback ante fallo NATURAL (CHECK inyectado); (ii.b) camino feliz --
# ambos lados del gemelo persisten, con el asiento H2 (actor no nulo).
# ---------------------------------------------------------------------------


async def _seed_transferencia_lista_para_recibir(
    conn: asyncpg.Connection, slug: str, name: str, actor: Actor | None = None
):
    """Deja un expediente 'closed' con índice 'firmado' y una transferencia
    'enviada' sobre él -- la precondición exacta de `TransferenciaService.recibir`.

    El índice se marca 'firmado' con SQL directo (mismo bypass ya documentado
    en `test_transfer_expediente_*`, ver 'compromises'): no hay sellado XAdES-B
    real ni tráfico HTTP a signature-service en este tier.
    """
    actor = actor or Actor(id=uuid.uuid4(), username="itest-seed")
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1), opened_by=None
    )
    await archive.close_expediente(created.id)
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )
    transferencia = TransferenciaService(conn, slug)
    resp = await transferencia.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor
    )
    await transferencia.enviar(resp.id, actor)
    return created, resp


@pytest.mark.integration
async def test_transferencia_recibir_rollback_reverts_transferencia_and_expediente_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest recibir boom"
    )

    # Fallo NATURAL: CHECK real sobre `expediente_eventos.accion` -- revienta
    # el `insert_expediente_evento(accion='transferido')` que
    # `TransferenciaService.recibir` inserta DESPUÉS de mutar ambas filas
    # (`transferencias.estado` -> 'recibida', `expedientes.status` ->
    # 'transferred') y ANTES de `audit.append('archive.expediente_transferred')`.
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_recibir_boom CHECK (accion <> 'transferido')"
    )

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-user")
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.recibir(transferencia.id, actor)

    # Verificación post-rollback desde una SEGUNDA conexión: los DOS lados del
    # gemelo revierten JUNTOS -- antes eran dos UPDATE en autocommit que
    # podían divergir (transferencia 'recibida' con expediente aún 'closed',
    # o viceversa).
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia.id
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        transferred_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
        evento_transferido = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferido",
        )
    finally:
        await second_conn.close()

    # El corazón del test: NINGUNO de los dos lados quedó comprometido a
    # medias -- la transferencia SIGUE 'enviada' (no 'recibida') Y el
    # expediente SIGUE 'closed' (no 'transferred'). Sin asiento colgante.
    assert transferencia_estado == "enviada"
    assert expediente_status == "closed"
    assert transferred_audit == 0
    assert evento_transferido == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transferencia_recibir_commits_transferencia_and_expediente_with_actor(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest recibir OK"
    )

    actor = Actor(id=uuid.uuid4(), username="itest-user-recibe")
    service = TransferenciaService(conn, slug)
    result = await service.recibir(transferencia.id, actor)
    assert result.estado == "recibida"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia.id
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        # (ii.b)/H2: el asiento del gemelo trae el ACTOR real de quien recibe
        # -- antes `recibir()` no emitía ni evento ni asiento en absoluto (0
        # llamadas a `audit.append` en todo el fichero), así que un actor NULO
        # aquí sería tan regresión como que el asiento faltara del todo.
        transferred_audit_row = await second_conn.fetchrow(
            "SELECT actor FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_transferred",
            created.code,
        )
        evento_transferido = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferido",
        )
    finally:
        await second_conn.close()

    # Ambos lados del gemelo persisten -- ninguno quedó atrás del otro.
    assert transferencia_estado == "recibida"
    assert expediente_status == "transferred"
    assert transferred_audit_row is not None
    # H-D: el actor escrito es SIEMPRE `Actor.audit_actor` (el UUID como
    # string) -- nunca el username, aunque `actor.username` esté poblado.
    assert transferred_audit_row["actor"] == actor.audit_actor
    assert evento_transferido == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (ii.c) H-G (E12, Ac. AGN 001/2024 Tít. 4.4; Ley 594/2000 art. 16; ISO
# 16363): `recibir()` ya no confía SOLO en `estado='firmado'` -- re-verifica
# la FIXITY del acervo (huella de cada documento vigente + re-hash de la fila
# del índice) DENTRO de la misma `conn.transaction()` que congela el
# expediente. A diferencia de `_seed_transferencia_lista_para_recibir` (sin
# documentos, fixity vacuamente verdadera), este helper deja un radicado
# VINCULADO real -- la comparación de huellas se ejercita de verdad.
# ---------------------------------------------------------------------------


async def _seed_transferencia_lista_para_recibir_con_documento(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    actor: Actor | None = None,
    *,
    nivel_seguridad: int = 1,
):
    """Como `_seed_transferencia_lista_para_recibir`, pero con UN radicado
    VINCULADO (huella real capturada en el índice firmado) -- precondición
    para ejercitar `IndexService.fixity` (E12 H-G) sobre un acervo no vacío.

    Devuelve ``(created, transferencia, radicado_id, content_hash_original)``.
    """
    actor = actor or Actor(id=uuid.uuid4(), username="itest-seed-fixity")
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)
    # M3 (no-write-up, RF-SEG-08): `create_expediente` exige que quien ABRE
    # tenga clearance >= `nivel_seguridad` -- `opened_by=None` resuelve
    # SIEMPRE a PUBLICA(1) (`resolve_caller_clearance`), y este tenant
    # desechable no tiene `auth_users`/`role_clearance` provisionados para
    # simular un actor con clearance CLASIFICADA. Se crea PUBLICA(1) (acto
    # que este helper no ejercita) y, si se pidió un nivel superior, se
    # reclasifica DIRECTO en BD -- fuera del acto de creación, sin pasar por
    # el gate M3 que no es lo que este test verifica.
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1),
        opened_by=None,
    )
    if nivel_seguridad != 1:
        await conn.execute(
            f'UPDATE "{schema}".expedientes SET nivel_seguridad = $2 WHERE id = $1',
            created.id,
            nivel_seguridad,
        )
    radicado_id = registrar_radicado("2026-ITEST-E-000001")
    content_hash = hashlib.sha256(f"{radicado_id}-contenido-original".encode()).hexdigest()
    with huella_en_almacen(radicado_id, content_hash):
        await archive.link_radicado(
            created.id,
            RadicadoLink(
                radicado_id=radicado_id,
                tracking_number="2026-ITEST-E-000001",
                content_hash=content_hash,
            ),
            user_id=None,
        )
    await archive.close_expediente(created.id)
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )
    transferencia = TransferenciaService(conn, slug)
    resp = await transferencia.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor
    )
    await transferencia.enviar(resp.id, actor)
    return created, resp, radicado_id, content_hash


@pytest.mark.integration
async def test_transferencia_recibir_fixity_intacta_congela_expediente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """H-G camino feliz: índice firmado + acervo INTACTO -- `recibir()`
    congela el expediente y el asiento de éxito lleva la atestación de
    fixity (base PREMIS, E12 H-G/d)."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia, _radicado_id, _hash = (
        await _seed_transferencia_lista_para_recibir_con_documento(conn, slug, "itest fixity OK")
    )

    actor = Actor(id=uuid.uuid4(), username="itest-recibe-fixity-ok")
    service = TransferenciaService(conn, slug)
    result = await service.recibir(transferencia.id, actor)
    assert result.estado == "recibida"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        recibida_row = await second_conn.fetchrow(
            "SELECT payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.transferencia_recibida",
            str(transferencia.id),
        )
    finally:
        await second_conn.close()

    assert expediente_status == "transferred"
    assert recibida_row is not None
    payload = (
        json.loads(recibida_row["payload"])
        if isinstance(recibida_row["payload"], str)
        else recibida_row["payload"]
    )
    # El bloque `fixity` (H-G/d) queda capturado en el asiento de ÉXITO --
    # atestación de que la comparación de huellas se ejecutó y pasó.
    assert payload["fixity"]["documentos_ok"] is True
    assert payload["fixity"]["huella_indice_ok"] is True
    assert payload["fixity"]["num_documentos"] == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transferencia_recibir_fixity_mismatch_revierte_sin_congelar(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """H-G mismatch: el radicado se ALTERA (content_hash actual distinto de la
    huella capturada en el índice firmado) DESPUÉS de la firma -- `recibir()`
    debe rechazar con 409 `fixity_mismatch` y NO congelar el expediente. Se
    verifica la REVERSIÓN real desde una SEGUNDA conexión (no solo el status
    code de la excepción): la transferencia SIGUE 'enviada', el expediente
    SIGUE 'closed', sin asiento de éxito, y con `transferencia_recibida_failed`
    (motivo='fixity_mismatch')."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia, radicado_id, _original_hash = (
        await _seed_transferencia_lista_para_recibir_con_documento(
            conn, slug, "itest fixity mismatch"
        )
    )

    # Alteración POSTERIOR a la firma del índice: simula un documento
    # sustituido/corrompido entre el cierre y esta recepción -- el
    # `content_hash` ACTUAL del vínculo ya no coincide con `valor_huella`
    # capturado en `expediente_indice_item` al firmar.
    tampered_hash = hashlib.sha256(b"contenido-alterado-tras-firma").hexdigest()
    await conn.execute(
        f'UPDATE "{schema}".expediente_radicados SET content_hash = $2 '
        "WHERE expediente_id = $1 AND radicado_id = $3",
        created.id,
        tampered_hash,
        radicado_id,
    )

    actor = Actor(id=uuid.uuid4(), username="itest-recibe-fixity-mismatch")
    service = TransferenciaService(conn, slug)
    with pytest.raises(HTTPException) as exc_info:
        await service.recibir(transferencia.id, actor)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "fixity_mismatch"
    # Redacción custodia-vs-contenido: la respuesta al actor de custodia lleva
    # solo un CONTEO, NUNCA la enumeración de radicados alterados de un
    # expediente que podría no poder leer (hallazgo #1 del auditor de seguridad).
    assert "documentos_fallidos" not in exc_info.value.detail
    assert exc_info.value.detail["num_documentos_fallidos"] == 1

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia.id
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        recibida_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_recibida",
        )
        transferred_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
        failed_row = await second_conn.fetchrow(
            "SELECT payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.transferencia_recibida_failed",
            str(transferencia.id),
        )
    finally:
        await second_conn.close()

    # El corazón del test: NI la transferencia NI el expediente avanzaron --
    # sin congelación a medias pese a la excepción.
    assert transferencia_estado == "enviada"
    assert expediente_status == "closed"
    assert recibida_audit == 0
    assert transferred_audit == 0
    assert failed_row is not None
    failed_payload = (
        json.loads(failed_row["payload"])
        if isinstance(failed_row["payload"], str)
        else failed_row["payload"]
    )
    assert failed_payload["motivo"] == "fixity_mismatch"
    # El detalle forense (los UUIDs) SÍ vive en el registro indeleble, cuya
    # lectura exige permiso de auditoría -- traza completa sin fuga en el 409.
    assert failed_payload["documentos_fallidos"] == [str(radicado_id)]

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transferencia_recibir_fixity_ortogonal_a_clearance_lectura(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """H-G ortogonalidad de clearance: un archivista SIN clearance de LECTURA
    sobre un expediente CLASIFICADA(3) puede recibir su transferencia
    igualmente -- la fixity de `recibir()` usa
    `CLEARANCE_SIN_RESTRICCION` (custodia, ortogonal a lectura, `core.
    clearance` bloque (c)) y NO debe imponer no-read-up.

    Se blinda con el contraste explícito: invocar `IndexService.fixity`
    directamente con el clearance REAL de un lector sin permisos (PUBLICA=1,
    sin membership RBAC en este tenant desechable) sobre el MISMO expediente
    SÍ aplica no-read-up (404) -- si `recibir()` alguna vez empezara a
    resolver el clearance real del actor en vez de pasar
    `CLEARANCE_SIN_RESTRICCION` explícitamente, este test lo detectaría."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia, _radicado_id, _hash = (
        await _seed_transferencia_lista_para_recibir_con_documento(
            conn, slug, "itest fixity clasificada", nivel_seguridad=3
        )
    )

    from app.core.clearance import PUBLICA
    from app.services.index_service import IndexService

    # Contraste: con el clearance REAL de un lector sin acceso, la MISMA
    # comparación de fixity SÍ es no-read-up (esto es lo que `recibir()`
    # deliberadamente NO hace).
    index_service = IndexService(conn, slug)
    with pytest.raises(HTTPException) as exc_info:
        await index_service.fixity(created.id, version=None, max_clearance=PUBLICA)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "expediente_not_found"

    # `recibir()` (acto de custodia) SÍ recibe la transferencia pese al
    # mismo expediente CLASIFICADA(3) y un actor sin ninguna clearance de
    # lectura resuelta -- 0 filas RBAC provisionadas para este `actor.id`
    # en el tenant desechable.
    actor = Actor(id=uuid.uuid4(), username="itest-archivista-sin-clearance-lectura")
    service = TransferenciaService(conn, slug)
    result = await service.recibir(transferencia.id, actor)
    assert result.estado == "recibida"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
    finally:
        await second_conn.close()
    assert expediente_status == "transferred"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (iii) ACTOR en el asiento de `ArchiveService.transfer_expediente` (H2): debe
# ponerse rojo si el `actor` que se pasa a `audit.append` se pierde por el
# camino (p. ej. si una futura refactorización volviera a llamar
# `transfer_expediente(id)` sin propagar `actor`, como hacía el router antes
# de H2).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_transfer_expediente_audit_actor_is_not_null(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest transfer actor", nivel_seguridad=1),
        opened_by=None,
    )
    await service.close_expediente(created.id)
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )

    actor = f"itest-user-{uuid.uuid4()}"
    result = await service.transfer_expediente(created.id, actor=actor)
    assert result.status == "transferred"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        audit_row = await second_conn.fetchrow(
            "SELECT actor FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_transferred",
            created.code,
        )
    finally:
        await second_conn.close()

    # El corazón del test: el actor que se pasó explícitamente llega intacto
    # al asiento de auditoría -- si `transfer_expediente` (o su router) volviera
    # a descartar la identidad del llamante (H2), esta fila tendría `actor IS
    # NULL` en vez del valor real.
    assert audit_row is not None
    assert audit_row["actor"] == actor


# ---------------------------------------------------------------------------
# (j) Cierre del bypass del PATCH: `ExpedienteUpdate` perdió `status` (capa
# HTTP, 422 `extra_forbidden` -- ya cubierto en el carril unit,
# `tests/test_archive.py::test_update_expediente_rechaza_status` /
# `..._status_rechazado_incluso_con_crud_3`, NO se duplica aquí). Este tier
# conduce el SERVICE/REPOSITORY directamente con una conexión asyncpg -- no
# hay Pydantic de por medio -- así que la aserción portante es de la CAPA DE
# DATOS: `ArchiveRepository.update_expediente` ya ni siquiera ACEPTA `status`
# como parámetro (TypeError de Python, no un valor ignorado en silencio) y
# `ArchiveService.update_expediente` (con un cambio de metadatos legítimo)
# deja el expediente exactamente donde estaba en el ciclo de vida -- ningún
# asiento de cierre/transferencia, leído desde una SEGUNDA conexión.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_update_expediente_repository_has_no_status_parameter_and_service_never_transitions(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    repo = ArchiveRepository(conn, slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest patch no transiciona", nivel_seguridad=1),
        opened_by=None,
    )

    # (A) La firma del repositorio ya no admite `status`/`closed_at` en
    # absoluto: pasarlo por keyword revienta en Python ANTES de tocar la BD --
    # no hay ningún camino, ni siquiera interno, capaz de reintroducir el
    # segundo escritor no auditado que fue el bug original.
    with pytest.raises(TypeError):
        await repo.update_expediente(  # type: ignore[call-arg]
            created.id,
            name=None,
            description=None,
            trd_serie_id=None,
            status="closed",
        )

    # (B) El PATCH real -- con un cambio de metadatos que SÍ está permitido --
    # no mueve el status ni un milímetro.
    result = await service.update_expediente(
        created.id,
        ExpedienteUpdate(name="itest patch renombrado"),
        user_id=None,
    )
    assert result.name == "itest patch renombrado"
    assert result.status == "open"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        closed_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_closed",
        )
        transferred_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
    finally:
        await second_conn.close()

    # El corazón del test: el expediente SIGUE 'open' en la BASE (no solo en
    # la respuesta HTTP) y NO aparece ningún asiento de cierre/transferencia
    # para este tenant -- la ruta alterna que transicionaba sin asiento, sin
    # disposición TRD, sin índice final y sin la guarda TOCTOU ya no existe.
    assert status_row == "open"
    assert closed_audit == 0
    assert transferred_audit == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (k) Reclasificación TRD por PATCH (Ley 594/2000 art. 24): cambiar
# `trd_serie_id` SÍ deja asiento propio `archive.expediente_reclasificado`
# con los valores ANTES/DESPUÉS y la disposición proyectada -- a diferencia
# del bypass cerrado en (j), esto NO es una transición de ciclo de vida
# (`status` no cambia), es una reclasificación archivística legítima que el
# arquitecto decidió mantener en el PATCH con su propia auditoría.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_update_expediente_reclasifica_trd_and_leaves_audit_trail(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    serie_before = await service.create_trd_serie(
        TrdSerieCreate(
            code="ITEST-RCA",
            name="itest serie antes",
            disposition="CT",
            archivo_gestion_years=2,
            archivo_central_years=3,
        ),
        actor=None,
    )
    serie_after = await service.create_trd_serie(
        TrdSerieCreate(
            code="ITEST-RCB",
            name="itest serie despues",
            disposition="E",
            archivo_gestion_years=3,
            archivo_central_years=5,
        ),
        actor=None,
    )

    created = await service.create_expediente(
        payload=ExpedienteCreate(
            name="itest reclasifica OK",
            nivel_seguridad=1,
            trd_serie_id=serie_before.id,
        ),
        opened_by=None,
    )

    actor = uuid.uuid4()
    result = await service.update_expediente(
        created.id,
        ExpedienteUpdate(trd_serie_id=serie_after.id),
        user_id=actor,
    )
    assert result.trd_serie_id == serie_after.id
    # No es una transición de ciclo de vida: sigue 'open'.
    assert result.status == "open"

    # Disposición proyectada esperada -- calculada con el mismo método puro
    # que usa el SUT (`compute_retention`), no hardcodeada a mano: evita que
    # el test dependa de la tabla `_AGN_DISPOSITION` interna.
    hoy = date.today()
    expected_after = await service.compute_retention(
        serie_after.id, hoy, base_date_origen="closed_at"
    )

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        trd_row = await second_conn.fetchval(
            f'SELECT trd_serie_id FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_reclasificado",
            created.code,
        )
        evento_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "reclasificado",
        )
    finally:
        await second_conn.close()

    assert trd_row == serie_after.id

    assert audit_row is not None
    assert audit_row["actor"] == str(actor)
    payload = audit_row["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload

    # El corazón del test: el asiento trae los valores ANTES/DESPUÉS reales
    # (no solo "cambió algo") y la disposición proyectada del lado nuevo.
    assert payload["changed"]["trd_serie_id"]["before"] == str(serie_before.id)
    assert payload["changed"]["trd_serie_id"]["after"] == str(serie_after.id)
    assert payload["serie_codigo"]["before"] == serie_before.code
    assert payload["serie_codigo"]["after"] == serie_after.code
    assert payload["disposicion_proyectada"]["before"] is not None
    assert payload["disposicion_proyectada"]["after"] is not None
    assert (
        payload["disposicion_proyectada"]["after"]["disposition_code"]
        == expected_after.disposition_code
    )
    assert payload["expediente_status"] == "open"

    assert evento_count == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_update_expediente_reclasificacion_rollback_reverts_trd_and_audit_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    serie_before = await service.create_trd_serie(
        TrdSerieCreate(
            code="ITEST-RBA",
            name="itest rollback serie antes",
            disposition="CT",
            archivo_gestion_years=2,
            archivo_central_years=3,
        ),
        actor=None,
    )
    serie_after = await service.create_trd_serie(
        TrdSerieCreate(
            code="ITEST-RBB",
            name="itest rollback serie despues",
            disposition="E",
            archivo_gestion_years=3,
            archivo_central_years=5,
        ),
        actor=None,
    )

    created = await service.create_expediente(
        payload=ExpedienteCreate(
            name="itest reclasifica boom",
            nivel_seguridad=1,
            trd_serie_id=serie_before.id,
        ),
        opened_by=None,
    )

    # Fallo NATURAL: CHECK real sobre `expediente_eventos.accion` -- revienta
    # `insert_expediente_evento(accion='reclasificado')`, que corre DESPUÉS
    # del `UPDATE ... RETURNING` (ya comprometido el nuevo `trd_serie_id`
    # dentro de la tx) y ANTES de `audit.append('archive.expediente_reclasificado')`.
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_reclasifica_boom CHECK (accion <> 'reclasificado')"
    )

    actor = uuid.uuid4()
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.update_expediente(
            created.id,
            ExpedienteUpdate(trd_serie_id=serie_after.id),
            user_id=actor,
        )

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        trd_row = await second_conn.fetchval(
            f'SELECT trd_serie_id FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        reclasificado_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_reclasificado",
        )
        evento_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "reclasificado",
        )
    finally:
        await second_conn.close()

    # El corazón del test: el `trd_serie_id` NO cambió -- sigue apuntando a
    # la serie ORIGINAL -- Y no queda ningún asiento/evento colgante: el
    # efecto (mutación) y el asiento (auditoría) revierten JUNTOS, no uno sin
    # el otro.
    assert trd_row == serie_before.id
    assert reclasificado_audit == 0
    assert evento_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (l) Traza de intentos DENEGADOS de alterar el ciclo de vida (H6, hallazgo
# ALTA del auditor de conformidad): el fix recién aplicado mueve
# `_assert_transition`/`compute_retention` DENTRO del `try` de
# `close_expediente` (antes fuera -- el intento denegado más FRECUENTE, cerrar
# un expediente ya cerrado, no dejaba rastro) y añade a `transfer_expediente`
# un asiento GEMELO `archive.expediente_transfer_failed` que antes no existía
# en absoluto. Contra Postgres REAL: el asiento de denegación sobrevive (no
# está dentro de la tx que aborta -- se escribe DESPUÉS, en autocommit) y la
# cadena de auditoría del tenant sigue íntegra tras la denegación (caso (e)
# del encargo).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_close_expediente_denied_already_closed_audits_failure_and_chain_stays_valid(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest close denegado", nivel_seguridad=1),
        opened_by=None,
    )
    await service.close_expediente(created.id)

    # Segundo intento: el expediente YA está 'closed' -- el caso denegado más
    # FRECUENTE. `_assert_transition` revienta ANTES de abrir ninguna tx.
    with pytest.raises(HTTPException) as excinfo:
        await service.close_expediente(created.id)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "invalid_status_transition"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        failed_audit = await second_conn.fetchrow(
            "SELECT payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_close_failed",
            created.code,
        )
        # Del primer cierre (exitoso) -- confirma que este test no lo
        # confunde con el segundo intento (denegado).
        closed_audit_count = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_closed",
        )
    finally:
        await second_conn.close()

    # El expediente sigue 'closed' (del primer cierre) -- el segundo intento
    # no lo movió ni lo rompió.
    assert status_row == "closed"
    assert closed_audit_count == 1

    # El corazón del test: el asiento de DENEGACIÓN existe -- antes del fix,
    # con las validaciones fuera del `try`, esta fila jamás se escribía.
    assert failed_audit is not None
    payload = failed_audit["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["status_actual"] == "closed"
    assert payload["motivo"] == "invalid_status_transition"

    # La cadena de auditoría del tenant sigue íntegra pese a la denegación.
    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transfer_expediente_denied_indice_no_firmado_audits_failure_and_chain_stays_valid(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """(b) El control del Acuerdo AGN 001/2024 art. 4.3.2.4 -- transferir sin
    índice electrónico firmado -- rechaza la operación Y deja el asiento que
    un auditor necesita para demostrar que el control ACTUÓ. Antes del fix,
    `transfer_expediente` no emitía NINGÚN asiento de denegación."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest transfer denegado sin firma", nivel_seguridad=1),
        opened_by=None,
    )
    # `close_expediente` con `actor_id=None` (default) deja el índice
    # 'pendiente_firma' -- ver docstring de la sección (h) más arriba: el SUT
    # corta ANTES de intentar tráfico HTTP a signature-service.
    await service.close_expediente(created.id)

    indice_estado = await conn.fetchval(
        f'SELECT estado FROM "{schema}".expediente_indice '
        "WHERE expediente_id = $1 ORDER BY version DESC LIMIT 1",
        created.id,
    )
    assert indice_estado == "pendiente_firma"  # precondición del test

    with pytest.raises(HTTPException) as excinfo:
        await service.transfer_expediente(created.id)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "indice_no_firmado"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        failed_audit = await second_conn.fetchrow(
            "SELECT payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_transfer_failed",
            created.code,
        )
        transferred_audit_count = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
    finally:
        await second_conn.close()

    # El expediente permanece 'closed' -- nunca llegó a transicionar.
    assert status_row == "closed"
    assert transferred_audit_count == 0

    assert failed_audit is not None
    payload = failed_audit["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["status_actual"] == "closed"
    assert payload["motivo"] == "indice_no_firmado"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transfer_expediente_denied_still_open_audits_failure_and_chain_stays_valid(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """(c) Transferir un expediente que sigue 'open' (nunca se cerró):
    `_assert_transition` revienta ANTES de `_assert_indice_firmado` -- el
    motivo auditado debe ser 'invalid_status_transition', no 'indice_no_firmado'."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    service = ArchiveService(conn, slug)

    created = await service.create_expediente(
        payload=ExpedienteCreate(name="itest transfer denegado abierto", nivel_seguridad=1),
        opened_by=None,
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.transfer_expediente(created.id)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "invalid_status_transition"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        status_row = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        failed_audit = await second_conn.fetchrow(
            "SELECT payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.expediente_transfer_failed",
            created.code,
        )
    finally:
        await second_conn.close()

    assert status_row == "open"
    assert failed_audit is not None
    payload = failed_audit["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["status_actual"] == "open"
    assert payload["motivo"] == "invalid_status_transition"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# E12 -- H-A (guarda TOCTOU en `TransferenciaRepository.update_estado`), H-B
# (auditoría de create/enviar/rechazar), H-D (identidad unificada) y H-E
# (hoja de ruta con `actor` UUID no nulo) contra Postgres REAL.
#
# Helpers de seed compartidos por esta tanda.
# ---------------------------------------------------------------------------


async def _seed_expediente_closed(
    conn: asyncpg.Connection, slug: str, name: str
) -> ExpedienteDisposicionResult:
    """Expediente 'closed' con el índice FIRMADO -- precondición de `create()`/`enviar()`/
    `rechazar()`. Desde AREA-07-03 `create()` también exige el índice firmado (antes solo
    `recibir()`), así que el seed lo deja firmado con SQL directo, el mismo bypass del otro
    seed (no hay sellado XAdES real ni HTTP a signature-service en este tier)."""
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1), opened_by=None
    )
    await archive.close_expediente(created.id)
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )
    return created


async def _seed_transferencia_preparada(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    actor: Actor | None = None,
    *,
    observacion: str | None = None,
):
    """Expediente 'closed' + transferencia 'preparada' -- precondición de
    `enviar()`/`rechazar()` cuando el test necesita partir de un estado
    posterior a `create()`.

    `observacion` (H-K): dato de PREPARACIÓN opcional -- distinto del
    `motivo` de un rechazo posterior, para que un test de `rechazar()` pueda
    comprobar que NO se pisa (a diferencia del comportamiento previo a H-K,
    donde `payload.observacion or existing["observacion"]` sí lo hacía)."""
    actor = actor or Actor(id=uuid.uuid4(), username="itest-seed-preparada")
    created = await _seed_expediente_closed(conn, slug, name)
    transferencia = TransferenciaService(conn, slug)
    resp = await transferencia.create(
        TransferenciaCreate(
            tipo="primaria",
            expediente_id=created.id,
            objeto="Transferencia primaria de la serie de contratos",
            observacion=observacion,
        ),
        actor,
    )
    return created, resp


async def _labeled(label: str, coro):
    """Envuelve una corrutina de acto de custodia para `asyncio.gather`:
    devuelve `(label, resultado, None)` en éxito o `(label, None, exc)` si
    lanzó `HTTPException` -- permite distinguir qué corrutina ganó la carrera
    sin que `return_exceptions=True` mezcle excepciones inesperadas con las
    esperadas (409 del perdedor)."""
    try:
        result = await coro
        return label, result, None
    except HTTPException as exc:
        return label, None, exc


# ---------------------------------------------------------------------------
# (m) EL TEST ESTRELLA de esta tanda -- carrera LETAL rechazar-vs-recibir
# (H-A): dos corrutinas, CADA UNA con su PROPIA conexión asyncpg, decidiendo
# la MISMA transferencia 'enviada' a la vez. Sin el `AND estado = $N` del
# `WHERE` de `update_estado`, ambas podrían leer 'enviada' en `_require`,
# pasar la validación, y comprometer AMBOS `UPDATE` -- el desenlace
# jurídicamente imposible: transferencia 'rechazada' + expediente ya
# congelado 'transferred' por el `recibir()` concurrente, en un `audit_log`
# INDELEBLE.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_transferencia_recibir_vs_rechazar_concurrent_exactly_one_wins(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest carrera recibir-vs-rechazar"
    )
    transferencia_id = transferencia.id

    actor_recibe = Actor(id=uuid.uuid4(), username="itest-recibe")
    actor_rechaza = Actor(id=uuid.uuid4(), username="itest-rechaza")

    async def _recibir() -> tuple[str, object, HTTPException | None]:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = TransferenciaService(own_conn, slug)
            return await _labeled("recibir", own_service.recibir(transferencia_id, actor_recibe))
        finally:
            await own_conn.close()

    async def _rechazar() -> tuple[str, object, HTTPException | None]:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = TransferenciaService(own_conn, slug)
            payload = TransferenciaRechazo(motivo="itest rechazo concurrente")
            return await _labeled(
                "rechazar", own_service.rechazar(transferencia_id, payload, actor_rechaza)
            )
        finally:
            await own_conn.close()

    # Sin sleeps: las dos corrutinas compiten de verdad por el mismo `WHERE
    # id = $N AND estado = 'enviada'` sobre la MISMA fila.
    results = await asyncio.gather(_recibir(), _rechazar())

    successes = [(label, r) for label, r, exc in results if exc is None]
    failures = [(label, exc) for label, r, exc in results if exc is not None]

    # El corazón del test: exactamente UNA corrutina gana -- nunca las dos,
    # nunca ninguna.
    assert len(successes) == 1, f"se esperaba exactamente 1 ganador, resultados: {results!r}"
    assert len(failures) == 1, f"se esperaba exactamente 1 perdedor, resultados: {results!r}"

    winner_label, winner_result = successes[0]
    loser_label, loser_exc = failures[0]
    assert {winner_label, loser_label} == {"recibir", "rechazar"}
    assert isinstance(winner_result, TransferenciaResponse)
    assert loser_exc.status_code == 409
    assert loser_exc.detail["error"] == "invalid_status_transition"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia_id
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        recibida_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_recibida",
        )
        rechazada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_rechazada",
        )
        transferred_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
    finally:
        await second_conn.close()

    # LA ASERCIÓN LETAL: nunca 'rechazada' con el expediente ya 'transferred'
    # -- el estado jurídicamente imposible que motivó H-A.
    assert not (transferencia_estado == "rechazada" and expediente_status == "transferred")
    # Y su gemelo simétrico, igual de imposible: 'recibida' sin que el
    # expediente se haya congelado -- ambos UPDATE (transferencia + expediente)
    # viven en la MISMA `conn.transaction()` de `recibir()`.
    assert not (transferencia_estado == "recibida" and expediente_status != "transferred")
    # Solo hay dos desenlaces coherentes posibles bajo la carrera.
    assert (transferencia_estado, expediente_status) in {
        ("recibida", "transferred"),
        ("rechazada", "closed"),
    }
    # El desenlace persistido coincide con quién ganó según el resultado en
    # memoria de cada corrutina -- no solo "algo consistente quedó en BD".
    if winner_label == "recibir":
        assert transferencia_estado == "recibida"
        assert recibida_audit == 1
        assert rechazada_audit == 0
        assert transferred_audit == 1
    else:
        assert transferencia_estado == "rechazada"
        assert rechazada_audit == 1
        assert recibida_audit == 0
        assert transferred_audit == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (n) Doble `enviar()` concurrente sobre la MISMA transferencia 'preparada':
# solo una corrutina commitea, un único asiento -- `public.audit_log` es
# INDELEBLE, un asiento duplicado sería permanente.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_transferencia_doble_enviar_concurrente_solo_uno_commitea(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_preparada(
        conn, slug, "itest doble enviar"
    )
    transferencia_id = transferencia.id

    async def _enviar_one(i: int):
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = TransferenciaService(own_conn, slug)
            actor = Actor(id=uuid.uuid4(), username=f"itest-enviar-{i}")
            return await _labeled(f"enviar-{i}", own_service.enviar(transferencia_id, actor))
        finally:
            await own_conn.close()

    results = await asyncio.gather(_enviar_one(0), _enviar_one(1))
    successes = [r for _, r, exc in results if exc is None]
    failures = [exc for _, r, exc in results if exc is not None]

    assert len(successes) == 1, f"se esperaba exactamente 1 ganador: {results!r}"
    assert len(failures) == 1, f"se esperaba exactamente 1 perdedor: {results!r}"
    assert failures[0].status_code == 409
    assert failures[0].detail["error"] == "invalid_status_transition"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        row = await second_conn.fetchrow(
            f'SELECT estado, enviada_at, enviada_por FROM "{schema}".transferencias WHERE id = $1',
            transferencia_id,
        )
        enviada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_enviada",
        )
        enviada_failed_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_enviada_failed",
        )
    finally:
        await second_conn.close()

    # El corazón del test: exactamente 1 fila terminó 'enviada' con su
    # responsable poblado -- ni 0 (las dos perdieron) ni un estado corrupto.
    assert row["estado"] == "enviada"
    assert row["enviada_at"] is not None
    assert row["enviada_por"] is not None
    assert enviada_audit == 1
    assert enviada_failed_audit == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (o) Doble `recibir()` concurrente sobre la MISMA transferencia 'enviada':
# solo una corrutina commitea -- y solo UNA congela el expediente ('transferred'),
# nunca dos intentos de congelación del mismo expediente.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_transferencia_doble_recibir_concurrente_solo_uno_commitea(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest doble recibir"
    )
    transferencia_id = transferencia.id

    async def _recibir_one(i: int):
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = TransferenciaService(own_conn, slug)
            actor = Actor(id=uuid.uuid4(), username=f"itest-recibe-{i}")
            return await _labeled(f"recibir-{i}", own_service.recibir(transferencia_id, actor))
        finally:
            await own_conn.close()

    results = await asyncio.gather(_recibir_one(0), _recibir_one(1))
    successes = [r for _, r, exc in results if exc is None]
    failures = [exc for _, r, exc in results if exc is not None]

    assert len(successes) == 1, f"se esperaba exactamente 1 ganador: {results!r}"
    assert len(failures) == 1, f"se esperaba exactamente 1 perdedor: {results!r}"
    assert failures[0].status_code == 409

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia_id
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        recibida_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_recibida",
        )
        transferred_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.expediente_transferred",
        )
        evento_transferido = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferido",
        )
    finally:
        await second_conn.close()

    # El corazón del test: la congelación del expediente ocurre EXACTAMENTE
    # una vez -- no hay forma de que dos `recibir()` concurrentes produzcan
    # dos asientos 'expediente_transferred' ni dos eventos 'transferido' en
    # la hoja de ruta append-only.
    assert transferencia_estado == "recibida"
    assert expediente_status == "transferred"
    assert recibida_audit == 1
    assert transferred_audit == 1
    assert evento_transferido == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (p) Atomicidad de `create()`: fallo NATURAL (CHECK sobre
# `expediente_eventos.accion`) revierte el INSERT de la transferencia ya
# ejecutado -- y, en el camino feliz, el actor llega no-nulo y UNIFICADO
# (H-D) a los tres sitios (fila de `transferencias`, asiento de auditoría,
# hoja de ruta) aunque la petición traiga un `username` distinto.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_transferencia_create_rollback_reverts_insert_and_audit_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest create boom")

    # Fallo NATURAL: revienta `insert_expediente_evento(accion='transferencia_preparada')`,
    # que `create()` inserta DESPUÉS de `repo.insert` (ya comprometido en la
    # tx) y ANTES de `audit.append('archive.transferencia_preparada')`.
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_create_boom CHECK (accion <> 'transferencia_preparada')"
    )

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-create-boom")
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.create(
            TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor
        )

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencias_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencias WHERE expediente_id = $1',
            created.id,
        )
        preparada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_preparada",
        )
        evento_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferencia_preparada",
        )
    finally:
        await second_conn.close()

    # El corazón del test: el INSERT de la transferencia (ya ejecutado, con
    # RETURNING, antes del CHECK) NO sobrevivió -- revirtió junto con el
    # resto del bloque atómico, no queda una fila "preparada" huérfana sin
    # su asiento.
    assert transferencias_count == 0
    assert preparada_audit == 0
    assert evento_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transferencia_create_commits_with_unified_actor_and_hoja_de_ruta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created = await _seed_expediente_closed(conn, slug, "itest create OK actor")

    service = TransferenciaService(conn, slug)
    # `username` deliberadamente DISTINTO del UUID -- H-D exige que lo escrito
    # en todas las columnas de identidad sea SIEMPRE `actor.audit_actor`
    # (el UUID), nunca "itest-create-humano".
    actor = Actor(id=uuid.uuid4(), username="itest-create-humano")
    resp = await service.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor
    )
    assert resp.estado == "preparada"
    assert resp.creada_por == actor.audit_actor

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_row = await second_conn.fetchrow(
            f'SELECT creada_por FROM "{schema}".transferencias WHERE id = $1', resp.id
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.transferencia_preparada",
            str(resp.id),
        )
        evento_row = await second_conn.fetchrow(
            f'SELECT actor FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferencia_preparada",
        )
    finally:
        await second_conn.close()

    # Los TRES sitios llevan la MISMA identidad no nula -- nunca el username.
    assert transferencia_row["creada_por"] == actor.audit_actor
    assert audit_row is not None
    assert audit_row["actor"] == actor.audit_actor
    assert evento_row is not None
    assert evento_row["actor"] == actor.id  # columna UUID tipada -- antes NULL (H-E)

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (q) Atomicidad de `enviar()`: fallo NATURAL revierte el `update_estado`
# YA comprometido en la tx (la transferencia debe volver a 'preparada', no
# quedarse a medio camino en 'enviada' sin evento/asiento).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_transferencia_enviar_rollback_reverts_estado_and_audit_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    creador = Actor(id=uuid.uuid4(), username="itest-enviar-boom-creador")
    created, transferencia = await _seed_transferencia_preparada(
        conn, slug, "itest enviar boom", actor=creador
    )

    # Fallo NATURAL: revienta `insert_expediente_evento(accion='transferencia_enviada')`,
    # que `enviar()` inserta DESPUÉS de `update_estado` (ya comprometido: la
    # fila pasó a 'enviada' dentro de la tx) y ANTES de
    # `audit.append('archive.transferencia_enviada')`.
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_enviar_boom CHECK (accion <> 'transferencia_enviada')"
    )

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-enviar-boom")
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.enviar(transferencia.id, actor)

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        row = await second_conn.fetchrow(
            f'SELECT estado, enviada_at, enviada_por FROM "{schema}".transferencias WHERE id = $1',
            transferencia.id,
        )
        enviada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_enviada",
        )
        evento_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferencia_enviada",
        )
    finally:
        await second_conn.close()

    # El corazón del test: el `UPDATE ... SET estado = 'enviada'` (ya
    # comprometido dentro de la tx, ANTES del CHECK que revienta) se revirtió
    # -- sigue 'preparada', sin `enviada_at`/`enviada_por` a medio poblar.
    assert row["estado"] == "preparada"
    assert row["enviada_at"] is None
    assert row["enviada_por"] is None
    assert enviada_audit == 0
    assert evento_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transferencia_enviar_commits_with_unified_actor_and_hoja_de_ruta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_preparada(conn, slug, "itest enviar OK")

    service = TransferenciaService(conn, slug)
    actor = Actor(id=uuid.uuid4(), username="itest-enviar-humano")
    result = await service.enviar(transferencia.id, actor)
    assert result.estado == "enviada"
    assert result.enviada_por == actor.audit_actor

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_row = await second_conn.fetchrow(
            f'SELECT enviada_por FROM "{schema}".transferencias WHERE id = $1', transferencia.id
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.transferencia_enviada",
            str(transferencia.id),
        )
        evento_row = await second_conn.fetchrow(
            f'SELECT actor FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferencia_enviada",
        )
    finally:
        await second_conn.close()

    assert transferencia_row["enviada_por"] == actor.audit_actor
    assert audit_row is not None
    assert audit_row["actor"] == actor.audit_actor
    assert evento_row is not None
    assert evento_row["actor"] == actor.id

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (r) Atomicidad de `rechazar()`: fallo NATURAL revierte el `update_estado`
# YA comprometido (la transferencia debe volver a 'enviada'), y en el camino
# feliz el expediente permanece 'closed' -- rechazar NO lo toca, a diferencia
# de `recibir()`.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_transferencia_rechazar_rollback_reverts_estado_and_audit_on_natural_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    creador = Actor(id=uuid.uuid4(), username="itest-rechazar-boom-creador")
    created, transferencia = await _seed_transferencia_preparada(
        conn, slug, "itest rechazar boom", actor=creador
    )
    service = TransferenciaService(conn, slug)
    await service.enviar(transferencia.id, creador)

    # Fallo NATURAL: revienta `insert_expediente_evento(accion='transferencia_rechazada')`,
    # que `rechazar()` inserta DESPUÉS de `update_estado` (ya comprometido:
    # la fila pasó a 'rechazada' dentro de la tx) y ANTES de
    # `audit.append('archive.transferencia_rechazada')`.
    await conn.execute(
        f'ALTER TABLE "{schema}".expediente_eventos '
        "ADD CONSTRAINT ck_itest_rechazar_boom CHECK (accion <> 'transferencia_rechazada')"
    )

    actor = Actor(id=uuid.uuid4(), username="itest-rechazar-boom")
    payload = TransferenciaRechazo(motivo="itest motivo boom")
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.rechazar(transferencia.id, payload, actor)

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        row = await second_conn.fetchrow(
            f'SELECT estado, decidida_por FROM "{schema}".transferencias WHERE id = $1',
            transferencia.id,
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        rechazada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.transferencia_rechazada",
        )
        evento_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferencia_rechazada",
        )
    finally:
        await second_conn.close()

    # El corazón del test: el `UPDATE ... SET estado = 'rechazada'` (ya
    # comprometido ANTES del CHECK) se revirtió -- sigue 'enviada', sin
    # `decidida_por` a medio poblar.
    assert row["estado"] == "enviada"
    assert row["decidida_por"] is None
    assert expediente_status == "closed"
    assert rechazada_audit == 0
    assert evento_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_transferencia_rechazar_commits_with_unified_actor_and_hoja_de_ruta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    creador = Actor(id=uuid.uuid4(), username="itest-rechazar-ok-creador")
    # `observacion` de PREPARACIÓN, deliberadamente DISTINTA del `motivo` del
    # rechazo de abajo -- H-K: si `rechazar()` volviera a pisarla (bug
    # previo, `payload.observacion or existing["observacion"]`), este valor
    # desaparecería y la aserción de abajo lo detectaría.
    created, transferencia = await _seed_transferencia_preparada(
        conn, slug, "itest rechazar OK", actor=creador, observacion="lote 2026-Q1, entrega parcial"
    )
    service = TransferenciaService(conn, slug)
    await service.enviar(transferencia.id, creador)

    actor = Actor(id=uuid.uuid4(), username="itest-rechazar-humano")
    payload = TransferenciaRechazo(motivo="faltan folios 12-15")
    result = await service.rechazar(transferencia.id, payload, actor)
    assert result.estado == "rechazada"
    assert result.decidida_por == actor.audit_actor
    # H-K/H-J en la respuesta inmediata del propio `rechazar()` (no solo en
    # la relectura por segunda conexión de abajo).
    assert result.motivo_rechazo == "faltan folios 12-15"
    assert result.observacion == "lote 2026-Q1, entrega parcial"
    assert result.rechazada_at is not None

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_row = await second_conn.fetchrow(
            f'SELECT decidida_por, observacion, motivo_rechazo, rechazada_at '
            f'FROM "{schema}".transferencias WHERE id = $1',
            transferencia.id,
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
            slug,
            "archive.transferencia_rechazada",
            str(transferencia.id),
        )
        evento_row = await second_conn.fetchrow(
            f'SELECT actor FROM "{schema}".expediente_eventos '
            "WHERE expediente_id = $1 AND accion = $2",
            created.id,
            "transferencia_rechazada",
        )
    finally:
        await second_conn.close()

    assert transferencia_row["decidida_por"] == actor.audit_actor
    # H-K, el corazón del test: el MOTIVO va a su columna propia...
    assert transferencia_row["motivo_rechazo"] == "faltan folios 12-15"
    # ...y la OBSERVACIÓN de preparación queda INTACTA -- NO pisada por el
    # motivo del rechazo (comportamiento previo a H-K, ya retirado).
    assert transferencia_row["observacion"] == "lote 2026-Q1, entrega parcial"
    # H-J: sello temporal propio del acto, gemelo de enviada_at/recibida_at.
    assert transferencia_row["rechazada_at"] is not None
    # `rechazar()` NO congela el expediente -- sigue 'closed', ortogonal al
    # ciclo de vida de la transferencia (a diferencia de `recibir()`).
    assert expediente_status == "closed"
    assert audit_row is not None
    assert audit_row["actor"] == actor.audit_actor
    payload_row = audit_row["payload"]
    payload_row = json.loads(payload_row) if isinstance(payload_row, str) else payload_row
    assert payload_row["motivo"] == "faltan folios 12-15"
    assert evento_row is not None
    assert evento_row["actor"] == actor.id

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
