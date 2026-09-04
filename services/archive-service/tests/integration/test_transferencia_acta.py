# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) del acta de entrega congelada (E12 H-I,
Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/26).

Carril de integración (`RUN_INTEGRATION=1`, ver `conftest.py`). Reutiliza los
helpers de seed ya existentes en `test_atomicity.py`
(`_seed_transferencia_lista_para_recibir`,
`_seed_transferencia_lista_para_recibir_con_documento`, `_labeled`) — no se
duplica esa infraestructura.

Cinco garantías cubiertas (una por sección), cada una con su mutante
correspondiente (ver informe del testing-engineer, no en este árbol):

1. Congelación: el acta existe tras `recibir()`, con XML no vacío, SHA-256
   recomputado coincidente y los tres responsables poblados.
2. Atomicidad: el acta se escribe en la MISMA tx que la recepción -- si esa
   tx revierte (fallo natural sobre `transferencia_acta` o carrera perdida),
   NO queda acta ni recepción a medias.
3. Inmutabilidad: el acta ya congelada NO cambia si el expediente se
   modifica después (no se regenera al leer).
4. Ortogonalidad: `recibir()` congela el acta con `CLEARANCE_SIN_RESTRICCION`
   -- el acta de un expediente CLASIFICADA(3) recibido por un archivista sin
   clearance de LECTURA trae contenido real (no queda vacía por el filtro).
5. Lectura no-read-up: `GET .../acta` (vía `TransferenciaService.acta`)
   aplica el clearance REAL del lector -- un no-lector no ve el acta de un
   expediente reservado; un lector con clearance suficiente sí.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
import xml.etree.ElementTree as ET

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.core.actor import Actor
from app.core.clearance import CLASIFICADA
from app.core.database import tenant_schema
from app.schemas.fisico import ExpedienteUnidadLink, UnidadCreate
from app.schemas.transferencia import TransferenciaRechazo, TransferenciaResponse
from app.services.fisico_service import FisicoService
from app.services.transferencia_service import TransferenciaService

from .conftest import DSN
from .test_atomicity import (
    _seed_transferencia_lista_para_recibir,
    _seed_transferencia_lista_para_recibir_con_documento,
)

_FUID_NS = "urn:orpycamcp:fuid:v1"


async def _link_unidad_fisica(
    conn: asyncpg.Connection, slug: str, expediente_id: uuid.UUID, codigo: str
) -> None:
    """Vincula el expediente a una unidad de conservación física -- sin esto
    `FisicoService.generate_fuid` (el `JOIN expediente_unidad`) devuelve un
    inventario VACÍO y el acta no tendría contenido real que verificar
    (garantía 4 necesita que el XML SÍ traiga un `<registro>`)."""
    fisico = FisicoService(conn, slug)
    unidad = await fisico.create_unidad(UnidadCreate(tipo="carpeta", codigo=codigo))
    await fisico.link_expediente(unidad.id, ExpedienteUnidadLink(expediente_id=expediente_id))


def _num_registros(acta_xml: str) -> int:
    root = ET.fromstring(acta_xml)
    return len(root.findall(f"{{{_FUID_NS}}}registro"))


# ---------------------------------------------------------------------------
# RBAC mínimo, provisionado a mano dentro del schema desechable: las tablas
# `auth_users`/`auth_memberships`/`role_clearance` las crea auth-service (ADR-002/
# ADR-012, ver `app/core/clearance.py`), pero sus migraciones NO viajan en la
# imagen de archive-service -- `resolve_caller_clearance` atrapa
# `UndefinedTableError` y cae a PUBLICA(1) en su ausencia (fail-closed, ya
# documentado en `test_atomicity.py:1202`). Para la garantía 5 hace falta
# distinguir un "lector legítimo" (clearance CLASIFICADA real) de un
# no-lector sobre el MISMO expediente reservado -- sin esto ambos resolverían
# siempre a PUBLICA y el contraste sería vacío. Subconjunto MÍNIMO de columnas
# que consulta `resolve_caller_clearance` (sin FKs: tabla de prueba
# desechable, se destruye entera con el `DROP SCHEMA CASCADE` del fixture).
# `IF NOT EXISTS`: si un futuro `migrations/tenant/*.sql` de archive-service
# llegara a definir estos mismos nombres, no se pisa.
_RBAC_MINIMAL_DDL = """
CREATE TABLE IF NOT EXISTS auth_users (
    id       UUID PRIMARY KEY,
    is_root  BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS auth_memberships (
    user_id   UUID NOT NULL,
    group_id  UUID NOT NULL,
    PRIMARY KEY (user_id, group_id)
);
CREATE TABLE IF NOT EXISTS role_clearance (
    group_id   UUID PRIMARY KEY,
    max_level  SMALLINT NOT NULL
);
"""


async def _grant_clearance(
    conn: asyncpg.Connection, schema: str, user_id: uuid.UUID, level: int
) -> None:
    """Da a `user_id` un `max_level` real vía RBAC (provisiona las tablas
    mínimas la primera vez, `IF NOT EXISTS`)."""
    await conn.execute(f'SET search_path TO "{schema}", public')
    await conn.execute(_RBAC_MINIMAL_DDL)
    group_id = uuid.uuid4()
    await conn.execute("INSERT INTO auth_users (id) VALUES ($1)", user_id)
    await conn.execute(
        "INSERT INTO role_clearance (group_id, max_level) VALUES ($1, $2)", group_id, level
    )
    await conn.execute(
        "INSERT INTO auth_memberships (user_id, group_id) VALUES ($1, $2)", user_id, group_id
    )


# ---------------------------------------------------------------------------
# (1) Congelación: XML no vacío, SHA-256 recomputado, tres responsables.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_se_congela_al_recibir_con_hash_y_roles(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia, _radicado_id, _hash = (
        await _seed_transferencia_lista_para_recibir_con_documento(
            conn, slug, "itest acta congelacion"
        )
    )
    await _link_unidad_fisica(conn, slug, created.id, "itest-caja-1")

    actor = Actor(id=uuid.uuid4(), username="itest-elaboro-y-recibe")
    service = TransferenciaService(conn, slug)
    result = await service.recibir(transferencia.id, actor)
    assert result.estado == "recibida"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        acta_row = await second_conn.fetchrow(
            f'SELECT * FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
            transferencia.id,
        )
        transferencia_row = await second_conn.fetchrow(
            f'SELECT creada_por, enviada_por, decidida_por, created_at, enviada_at, '
            f'recibida_at FROM "{schema}".transferencias WHERE id = $1',
            transferencia.id,
        )
    finally:
        await second_conn.close()

    assert acta_row is not None
    # XML no vacío y con contenido real (al menos un <registro>, gracias al
    # vínculo físico creado arriba) -- no solo la etiqueta raíz.
    assert acta_row["acta_fuid_xml"].strip() != ""
    assert _num_registros(acta_row["acta_fuid_xml"]) == 1
    # SHA-256 RECOMPUTADO del XML almacenado coincide con el guardado --
    # verifica el fixity del propio acta, no solo que la columna esté poblada.
    recomputed = hashlib.sha256(acta_row["acta_fuid_xml"].encode("utf-8")).hexdigest()
    assert acta_row["acta_fuid_sha256"] == recomputed
    assert len(acta_row["acta_fuid_sha256"]) == 64

    # Los TRES responsables del Anexo FUID = snapshot de las columnas de
    # `transferencias` ya persistidas (Elaborado/Entregado/Recibido).
    assert acta_row["elaborado_por"] == transferencia_row["creada_por"]
    assert acta_row["entregado_por"] == transferencia_row["enviada_por"]
    assert acta_row["recibido_por"] == transferencia_row["decidida_por"]
    # `recibido_por` es el ÚNICO de los tres responsables que este test fija
    # explícitamente (el helper de seed usa su propio actor de
    # preparación/envío) -- el traspaso lo decide `actor`, pasado a `recibir()`.
    assert acta_row["recibido_por"] == actor.audit_actor
    assert acta_row["elaborado_por"] is not None
    assert acta_row["entregado_por"] is not None
    assert acta_row["elaborado_at"] == transferencia_row["created_at"]
    assert acta_row["entregado_at"] == transferencia_row["enviada_at"]
    assert acta_row["recibido_at"] == transferencia_row["recibida_at"]
    # `congelada_at` = el `generado` embebido en el XML sobre el que se
    # calculó el SHA-256 -- ya no `now()` recalculado aparte.
    assert acta_row["congelada_at"].isoformat() in acta_row["acta_fuid_xml"]

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (2) Atomicidad -- (2a) fallo NATURAL específico del INSERT del acta (CHECK
# sobre `transferencia_acta`): revierte TODO el gemelo (transferencia +
# expediente), no solo el acta. Este es el test que el mutante de la sección
# "saca el congelado del acta de la tx" pone en rojo.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_atomicidad_fallo_natural_en_insert_acta_revierte_todo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest acta atomicidad boom"
    )

    # Fallo NATURAL específico de `transferencia_acta`: CHECK siempre falso
    # (ningún UUID serializado supera 1000 caracteres) -- revienta justo el
    # `INSERT INTO transferencia_acta` de `recibir()`, que corre DESPUÉS de
    # comprometer (dentro de la MISMA tx) el UPDATE de transferencia y la
    # transición del expediente.
    await conn.execute(
        f'ALTER TABLE "{schema}".transferencia_acta '
        "ADD CONSTRAINT ck_itest_acta_boom CHECK (length(transferencia_id::text) > 1000)"
    )

    actor = Actor(id=uuid.uuid4(), username="itest-recibe-acta-boom")
    service = TransferenciaService(conn, slug)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await service.recibir(transferencia.id, actor)

    # Verificación post-rollback desde una SEGUNDA conexión: el fallo del
    # INSERT del acta debe arrastrar consigo TODO el acto de custodia -- si el
    # acta viviera fuera de la tx de recepción, la transferencia/expediente ya
    # habrían comprometido su cambio de estado pese al 500 de más arriba.
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia.id
        )
        expediente_status = await second_conn.fetchval(
            f'SELECT status FROM "{schema}".expedientes WHERE id = $1', created.id
        )
        acta_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
            transferencia.id,
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
    finally:
        await second_conn.close()

    # El corazón del test: NINGÚN lado del gemelo avanzó -- ni acta, ni
    # transferencia 'recibida', ni expediente 'transferred', ni asientos.
    assert transferencia_estado == "enviada"
    assert expediente_status == "closed"
    assert acta_count == 0
    assert recibida_audit == 0
    assert transferred_audit == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (2b) Atomicidad -- carrera perdida (H-A): recibir-vs-rechazar concurrente
# sobre la MISMA transferencia. Si `rechazar()` gana, NO debe quedar acta
# (nunca hubo recepción que atestiguar); si `recibir()` gana, debe quedar
# EXACTAMENTE una.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_consistente_con_ganador_de_carrera_recibir_vs_rechazar(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest acta carrera recibir-vs-rechazar"
    )
    transferencia_id = transferencia.id

    async def _recibir():
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = TransferenciaService(own_conn, slug)
            actor = Actor(id=uuid.uuid4(), username="itest-carrera-recibe")
            try:
                return await own_service.recibir(transferencia_id, actor)
            except HTTPException as exc:
                return exc
        finally:
            await own_conn.close()

    async def _rechazar():
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            own_service = TransferenciaService(own_conn, slug)
            actor = Actor(id=uuid.uuid4(), username="itest-carrera-rechaza")
            payload = TransferenciaRechazo(motivo="itest rechazo concurrente (acta)")
            try:
                return await own_service.rechazar(transferencia_id, payload, actor)
            except HTTPException as exc:
                return exc
        finally:
            await own_conn.close()

    recibir_result, rechazar_result = await asyncio.gather(_recibir(), _rechazar())
    results = [recibir_result, rechazar_result]
    successes = [r for r in results if isinstance(r, TransferenciaResponse)]
    failures = [r for r in results if isinstance(r, HTTPException)]
    assert len(successes) == 1, f"se esperaba exactamente 1 ganador: {results!r}"
    assert len(failures) == 1, f"se esperaba exactamente 1 perdedor: {results!r}"
    assert failures[0].status_code == 409

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        transferencia_estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia_id
        )
        acta_count = await second_conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
            transferencia_id,
        )
    finally:
        await second_conn.close()

    assert transferencia_estado in {"recibida", "rechazada"}
    if transferencia_estado == "recibida":
        assert acta_count == 1
    else:
        # `rechazar()` ganó: NUNCA hubo traspaso que atestiguar -- 0 actas,
        # nunca un acta huérfana de una recepción que no ocurrió.
        assert acta_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (3) Inmutabilidad: el acta ya congelada NO cambia si el expediente se
# modifica después -- `acta()` sirve el snapshot, nunca regenera.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_inmutable_tras_modificar_expediente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia, _radicado_id, _hash = (
        await _seed_transferencia_lista_para_recibir_con_documento(
            conn, slug, "itest acta inmutable ANTES"
        )
    )
    await _link_unidad_fisica(conn, slug, created.id, "itest-caja-inmutable")

    actor = Actor(id=uuid.uuid4(), username="itest-recibe-inmutable")
    service = TransferenciaService(conn, slug)
    await service.recibir(transferencia.id, actor)

    acta_antes = await service.acta(transferencia.id, user_id=None)

    # El expediente se modifica DESPUÉS del traspaso (p. ej. una corrección de
    # metadatos post-transferencia) -- regenerar el FUID en vivo AHORA
    # produciría un `asunto` distinto. El acta congelada no debe reflejarlo.
    await conn.execute(
        f'UPDATE "{schema}".expedientes SET name = $2 WHERE id = $1',
        created.id,
        "itest acta inmutable DESPUES (nombre cambiado tras el traspaso)",
    )

    acta_despues = await service.acta(transferencia.id, user_id=None)

    # Mismo snapshot exacto -- byte a byte, no solo "sigue siendo válido".
    assert acta_despues.acta_fuid_xml == acta_antes.acta_fuid_xml
    assert acta_despues.acta_fuid_sha256 == acta_antes.acta_fuid_sha256
    assert acta_despues.congelada_at == acta_antes.congelada_at
    # El nombre VIEJO (el del momento del traspaso) sigue en el XML; el nuevo
    # NO aparece -- prueba directa de que no se regeneró.
    assert "itest acta inmutable ANTES" in acta_despues.acta_fuid_xml
    assert "DESPUES" not in acta_despues.acta_fuid_xml

    # Contraste explícito: el FUID EN VIVO (no el acta) SÍ refleja el cambio
    # -- confirma que la diferencia es real y no un artefacto de caché.
    fuid_en_vivo = await FisicoService(conn, slug).generate_fuid(
        ubicacion_id=None,
        trd_serie_id=None,
        expediente_id=created.id,
        max_clearance=CLASIFICADA,
    )
    assert "DESPUES" in fuid_en_vivo.rows[0].asunto
    assert "ANTES" not in fuid_en_vivo.rows[0].asunto

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (4) Ortogonalidad: `recibir()` congela el acta de un expediente CLASIFICADA
# (3) recibido por un actor SIN clearance de lectura -- el XML trae contenido
# REAL (no queda vacío por el filtro `e.nivel_seguridad <= max_clearance`).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_ortogonal_a_clearance_lectura_expediente_clasificada(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia, _radicado_id, _hash = (
        await _seed_transferencia_lista_para_recibir_con_documento(
            conn, slug, "itest acta ortogonalidad", nivel_seguridad=CLASIFICADA
        )
    )
    await _link_unidad_fisica(conn, slug, created.id, "itest-caja-clasificada")

    # 0 filas RBAC provisionadas para este `actor.id` -- `caller_clearance`
    # resolvería PUBLICA(1) si `recibir()` alguna vez empezara a usarlo.
    actor = Actor(id=uuid.uuid4(), username="itest-archivista-sin-clearance")
    service = TransferenciaService(conn, slug)
    result = await service.recibir(transferencia.id, actor)
    assert result.estado == "recibida"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        acta_xml = await second_conn.fetchval(
            f'SELECT acta_fuid_xml FROM "{schema}".transferencia_acta '
            "WHERE transferencia_id = $1",
            transferencia.id,
        )
    finally:
        await second_conn.close()

    assert acta_xml is not None
    # LA ASERCIÓN LETAL: si `generate_fuid` resolviera el clearance REAL del
    # actor (PUBLICA=1) en vez de `CLEARANCE_SIN_RESTRICCION`, el filtro
    # `e.nivel_seguridad <= max_clearance` (3 <= 1, falso) dejaría el
    # inventario VACÍO -- 0 `<registro>`, un acta sin contenido pese a que el
    # traspaso sí ocurrió.
    assert _num_registros(acta_xml) == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (5) Lectura no-read-up: `TransferenciaService.acta()` aplica el clearance
# REAL del lector -- un no-lector no ve el acta de un expediente reservado;
# un lector con clearance suficiente sí.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_lectura_no_read_up_pero_lector_legitimo_si(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    created, transferencia, _radicado_id, _hash = (
        await _seed_transferencia_lista_para_recibir_con_documento(
            conn, slug, "itest acta lectura no-read-up", nivel_seguridad=CLASIFICADA
        )
    )
    await _link_unidad_fisica(conn, slug, created.id, "itest-caja-lectura")

    actor = Actor(id=uuid.uuid4(), username="itest-archivista-recibe-lectura")
    service = TransferenciaService(conn, slug)
    await service.recibir(transferencia.id, actor)

    # No-lector: sin fila en `auth_users` -> `resolve_caller_clearance`
    # resuelve PUBLICA(1) < nivel_seguridad(3) del expediente -- 404 TOTAL,
    # indistinguible de un expediente inexistente.
    no_lector = uuid.uuid4()
    with pytest.raises(HTTPException) as exc_info:
        await service.acta(transferencia.id, user_id=no_lector)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "expediente_not_found"

    # Lector legítimo: clearance CLASIFICADA(3) real, provisionado vía RBAC
    # (mismo expediente, misma transferencia -- la ÚNICA variable es el
    # clearance resuelto del `user_id`).
    lector_legitimo = uuid.uuid4()
    await _grant_clearance(conn, schema, lector_legitimo, CLASIFICADA)
    acta = await service.acta(transferencia.id, user_id=lector_legitimo)
    assert acta.transferencia_id == transferencia.id
    assert acta.expediente_id == created.id
    assert _num_registros(acta.acta_fuid_xml) == 1
    assert acta.acta_fuid_sha256 == hashlib.sha256(
        acta.acta_fuid_xml.encode("utf-8")
    ).hexdigest()

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
