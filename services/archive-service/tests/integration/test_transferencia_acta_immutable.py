# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) de la inmutabilidad ESTRUCTURAL del acta de
entrega (E12, migración `023_transferencia_acta_immutable.sql`).

Distinto de `test_transferencia_acta.py` (H-I, congelación/atomicidad/lectura
no-read-up): aquí se prueba específicamente que `transferencia_acta` rechaza
UPDATE/DELETE/TRUNCATE **por el motor** -- un trigger de BD, invisible al
carril unit mockeado (no hay Postgres real ahí), así que este es el ÚNICO
lugar del árbol donde se puede observar.

Decisión del arquitecto (contrato de la migración 023): se cubren los tres
vectores -- UPDATE, DELETE directo (no solo UPDATE: un DELETE por SQL directo
es tan destructivo de un registro probatorio como un UPDATE) y TRUNCATE
(bypassea los triggers de fila; omitirlo dejaría abierto un vector para
borrar TODAS las actas del tenant de un golpe). Mismo patrón que
`test_atomicity.py::test_audit_log_trigger_rejects_update_and_delete`.

Validación por mutación de este informe (ver retorno del testing-engineer,
no en este árbol): se retiró temporalmente el `CREATE TRIGGER` de la
migración 023 y se confirmó que los tests 1-3 de abajo se ponen ROJOS sin él.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest
from orpycamcp_common import audit

from app.core.actor import Actor
from app.core.database import tenant_schema
from app.services.transferencia_service import TransferenciaService

from .test_atomicity import _seed_transferencia_lista_para_recibir

# Mensajes exactos que levantan `reject_transferencia_acta_mutation` /
# `reject_transferencia_acta_truncate` (ver migración 023) -- se estrecha el
# `match` para no confundir un rechazo del trigger con un fallo de SETUP
# distinto (columna inexistente, permiso denegado, ...), igual que el
# precedente de `audit_log` en `test_atomicity.py:499`.
_REJECT_MATCH = r"append-only"


async def _recibir_y_obtener_acta(
    conn: asyncpg.Connection, slug: str, name: str
) -> tuple[uuid.UUID, uuid.UUID, str, str]:
    """Deja una transferencia `recibida` con su acta ya congelada. Devuelve
    ``(expediente_id, transferencia_id, acta_fuid_xml, acta_fuid_sha256)`` tal
    como quedaron persistidos -- el "antes" contra el que se compara tras cada
    intento de mutación rechazado."""
    schema = tenant_schema(slug)
    created, transferencia = await _seed_transferencia_lista_para_recibir(conn, slug, name)
    actor = Actor(id=uuid.uuid4(), username="itest-recibe-immutable")
    service = TransferenciaService(conn, slug)
    result = await service.recibir(transferencia.id, actor)
    assert result.estado == "recibida"

    acta_row = await conn.fetchrow(
        f'SELECT acta_fuid_xml, acta_fuid_sha256 FROM "{schema}".transferencia_acta '
        "WHERE transferencia_id = $1",
        transferencia.id,
    )
    assert acta_row is not None
    return created.id, transferencia.id, acta_row["acta_fuid_xml"], acta_row["acta_fuid_sha256"]


# ---------------------------------------------------------------------------
# (1) UPDATE directo sobre `transferencia_acta` -- rechazado por el trigger,
# la fila NO cambia.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_trigger_rechaza_update_directo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    _expediente_id, transferencia_id, xml_antes, sha_antes = await _recibir_y_obtener_acta(
        conn, slug, "itest acta update rechazado"
    )

    # `asyncpg.execute` fuera de un `async with conn.transaction()` corre como
    # tx implícita por statement: el fallo de este UPDATE no deja `conn`
    # "abortada" para las siguientes sentencias (mismo razonamiento que
    # `test_atomicity.py:488-492`).
    with pytest.raises(asyncpg.exceptions.PostgresError, match=_REJECT_MATCH):
        await conn.execute(
            f'UPDATE "{schema}".transferencia_acta SET acta_fuid_xml = $1, '
            "acta_fuid_sha256 = $2 WHERE transferencia_id = $3",
            "<tampered/>",
            "0" * 64,
            transferencia_id,
        )

    acta_row = await conn.fetchrow(
        f'SELECT acta_fuid_xml, acta_fuid_sha256 FROM "{schema}".transferencia_acta '
        "WHERE transferencia_id = $1",
        transferencia_id,
    )
    assert acta_row["acta_fuid_xml"] == xml_antes
    assert acta_row["acta_fuid_sha256"] == sha_antes

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (2) DELETE directo sobre `transferencia_acta` -- rechazado por el trigger
# (decisión (B) del arquitecto: no solo UPDATE). La fila sigue existiendo.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_trigger_rechaza_delete_directo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    _expediente_id, transferencia_id, _xml_antes, _sha_antes = await _recibir_y_obtener_acta(
        conn, slug, "itest acta delete rechazado"
    )

    with pytest.raises(asyncpg.exceptions.PostgresError, match=_REJECT_MATCH):
        await conn.execute(
            f'DELETE FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
            transferencia_id,
        )

    count = await conn.fetchval(
        f'SELECT count(*) FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
        transferencia_id,
    )
    assert count == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (3) TRUNCATE de `transferencia_acta` -- rechazado por el trigger STATEMENT
# dedicado (un TRUNCATE bypassea los triggers FOR EACH ROW de (1)/(2), así
# que necesita su propia guarda). Ninguna acta desaparece, incluidas las de
# OTRAS transferencias del mismo tenant.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_trigger_rechaza_truncate(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    _e1, t1_id, _x1, _s1 = await _recibir_y_obtener_acta(conn, slug, "itest acta truncate uno")
    _e2, t2_id, _x2, _s2 = await _recibir_y_obtener_acta(conn, slug, "itest acta truncate dos")

    # H-H (E06/F4, migración 026): `transferencia_acta_firma` referencia esta
    # tabla por FK -- un `TRUNCATE` sin `CASCADE` ahora es rechazado por
    # Postgres ANTES de que el trigger STATEMENT dedicado (append-only)
    # siquiera se evalúe ("cannot truncate a table referenced in a foreign
    # key constraint"), una guarda ADICIONAL, no una regresión: el resultado
    # observable sigue siendo el mismo (nada se borra). El trigger
    # append-only sigue siendo la defensa que dispara con `TRUNCATE ...
    # CASCADE` (ver el test siguiente).
    with pytest.raises(
        asyncpg.exceptions.PostgresError,
        match=rf"{_REJECT_MATCH}|foreign key constraint",
    ):
        await conn.execute(f'TRUNCATE "{schema}".transferencia_acta')

    count = await conn.fetchval(f'SELECT count(*) FROM "{schema}".transferencia_acta')
    assert count == 2
    for transferencia_id in (t1_id, t2_id):
        exists = await conn.fetchval(
            f'SELECT count(*) FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
            transferencia_id,
        )
        assert exists == 1

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (4) Camino feliz intacto: el trigger BEFORE UPDATE OR DELETE / BEFORE
# TRUNCATE no interfiere con el único INSERT legítimo (`recibir()`) ni con la
# lectura (`acta()`). Regresión directa de H-I bajo la migración 023.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_insert_de_recibir_y_lectura_siguen_verdes_con_trigger(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest acta camino feliz con trigger"
    )
    actor = Actor(id=uuid.uuid4(), username="itest-recibe-camino-feliz")
    service = TransferenciaService(conn, slug)
    result = await service.recibir(transferencia.id, actor)
    assert result.estado == "recibida"

    acta = await service.acta(transferencia.id, user_id=None)
    assert acta.transferencia_id == transferencia.id
    assert acta.expediente_id == created.id
    assert acta.acta_fuid_xml.strip() != ""

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (5) Teardown del schema desechable: `DROP SCHEMA ... CASCADE` es DDL, no
# dispara triggers de fila/statement (DML) -- el trigger nuevo no debe
# bloquear el teardown de `tenant_ctx`. Se prueba explícitamente aquí (en vez
# de fiarse solo del teardown implícito del fixture, que si fallara
# reportaría un ERROR de teardown en vez de un FAIL de aserción legible).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_trigger_no_bloquea_drop_schema_cascade(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    await _recibir_y_obtener_acta(conn, slug, "itest acta drop schema cascade")

    # DDL explícito -- si el trigger (DML) fuera a interferir con el CASCADE
    # dormido (transferencia_acta -> transferencias -> expedientes, todas
    # ON DELETE CASCADE), sería aquí donde reventaría.
    await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')

    still_exists = await conn.fetchval(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = $1", schema
    )
    assert still_exists == 0

    # El fixture `tenant_ctx` reintentará el DROP en su teardown
    # (`DROP SCHEMA IF EXISTS ... CASCADE`) -- idempotente, no debe fallar
    # sobre un schema que este test ya destruyó.


# ---------------------------------------------------------------------------
# (6) Tamper-evidence ANCLADA en la hash-chain: el trigger impide la mutación
# a nivel de motor, pero `transferencia_acta` no tiene hash-chain propia y su
# `acta_fuid_sha256` vive en la misma fila que protege -> un bypass del trigger
# (sólo superusuario) podría reescribir XML+SHA coherentemente. Anclar el SHA
# del acta en el asiento INDELEBLE `archive.transferencia_recibida` lo hace
# detectable: el valor encadenado no se puede reescribir sin romper la cadena.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_acta_sha256_anclado_en_asiento_recibida(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx

    _expediente_id, transferencia_id, _xml, sha_acta = await _recibir_y_obtener_acta(
        conn, slug, "itest acta sha anclado"
    )

    payload_raw = await conn.fetchval(
        "SELECT payload FROM public.audit_log "
        "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3",
        slug,
        "archive.transferencia_recibida",
        str(transferencia_id),
    )
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    # El SHA del acta queda anclado en la cadena indeleble -> tamper-evidence
    # aunque el trigger fuera saltado por un superusuario.
    assert payload["acta_fuid_sha256"] == sha_acta

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
