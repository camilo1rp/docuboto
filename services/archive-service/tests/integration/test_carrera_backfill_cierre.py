# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""T12 / AREA-07-01: carrera REAL entre el backfill del origen legado y el cierre del expediente.

`completar_origen_vinculo` toma `FOR SHARE OF e` sobre la fila del expediente y el cierre la
actualiza (`transition_expediente`): ambos se serializan. Los tests unitarios solo comprueban que
el SQL lleva la cláusula; aquí hay DOS conexiones asyncpg y DOS transacciones concurrentes contra
Postgres, y se mantiene una abierta a propósito para demostrar que la otra ESPERA (se lee en
`pg_stat_activity`, no se infiere de un `sleep`) y que, al soltar, el índice del cierre no queda
sin lo que el backfill escribió ni el backfill escribe sobre un expediente ya cerrado.

Las dos órdenes de llegada:
- el backfill entra primero: el cierre espera y su índice incluye formato/tamaño/productora;
- el cierre entra primero: el backfill espera y, al soltar, ve el expediente cerrado y no escribe.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import asyncpg
import pytest
import pytest_asyncio
from orpycamcp_common import audit

from app.core.database import tenant_schema
from app.schemas.archive import ExpedienteCreate, RadicadoLink
from app.services.archive_service import ArchiveService
from tests.conftest import registrar_radicado

from .conftest import DSN, huella_en_almacen

pytestmark = pytest.mark.integration

ASIENTO_BACKFILL = "archive.vinculo_origen_completado"
ASIENTO_CIERRE = "archive.expediente_closed"
TRACKING = "2026-ITEST-E-000777"


@pytest_asyncio.fixture
async def conexiones_extra() -> AsyncGenerator[tuple[asyncpg.Connection, asyncpg.Connection], None]:
    """Segunda conexión (la otra transacción) y una de observación (`pg_stat_activity`)."""
    otra = await asyncpg.connect(dsn=DSN)
    observador = await asyncpg.connect(dsn=DSN)
    try:
        yield otra, observador
    finally:
        await otra.close()
        await observador.close()


class _Pausa:
    """Detiene `audit.append` de UN asiento, antes de delegar: la transacción que lo emite
    ya escribió sus filas (y conserva sus bloqueos de fila) pero aún no toma el advisory lock
    del tenant ni confirma. Así lo único que puede hacer esperar a la otra es el bloqueo de
    fila de `expedientes`, que es lo que se quiere demostrar."""

    def __init__(self, accion: str) -> None:
        self.accion = accion
        self.en_vuelo = asyncio.Event()
        self.soltar = asyncio.Event()
        self._original = audit.append

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("action") == self.accion:
            self.en_vuelo.set()
            await self.soltar.wait()
        return await self._original(*args, **kwargs)


async def _espera_bloqueo(observador: asyncpg.Connection, pid: int) -> bool:
    """True si el backend `pid` queda esperando un bloqueo (hasta ~5 s)."""
    for _ in range(50):
        fila = await observador.fetchrow(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = $1", pid
        )
        if fila is not None and fila["wait_event_type"] == "Lock":
            return True
        await asyncio.sleep(0.1)
    return False


async def _sembrar(conn: asyncpg.Connection, slug: str):
    """Expediente abierto con UN vínculo legado (formato/tamaño/productora en NULL) y, después,
    document-service pasa a saber esos tres datos: lo que el backfill debe traer."""
    archive = ArchiveService(conn, slug)
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name="itest carrera backfill-cierre", nivel_seguridad=1),
        opened_by=None,
    )
    rid = registrar_radicado(TRACKING)
    huella = hashlib.sha256(f"{rid}-contenido".encode()).hexdigest()
    with huella_en_almacen(rid, huella):
        await archive.link_radicado(
            created.id,
            RadicadoLink(radicado_id=rid, tracking_number=TRACKING, content_hash=huella),
            user_id=None,
        )
    registrar_radicado(
        TRACKING, radicado_id=rid, formato="application/pdf", tamano_bytes=4096,
        origin_dept="Archivo Central", origin_dept_code=7,
    )
    return created, rid


async def _vinculo(conn: asyncpg.Connection, slug: str, expediente_id: uuid.UUID):
    return await conn.fetchrow(
        f'SELECT formato, tamano_bytes, dependencia_productora_codigo, origen_completado_at '
        f'FROM "{tenant_schema(slug)}".expediente_radicados WHERE expediente_id = $1',
        expediente_id,
    )


async def _item_del_indice_final(conn: asyncpg.Connection, slug: str, expediente_id: uuid.UUID):
    schema = tenant_schema(slug)
    return await conn.fetchrow(
        f'SELECT i.formato, i.tamano_bytes, i.dependencia_productora_codigo '
        f'FROM "{schema}".expediente_indice_item i '
        f'JOIN "{schema}".expediente_indice x ON x.id = i.indice_id '
        f"WHERE x.expediente_id = $1 ORDER BY x.version DESC LIMIT 1",
        expediente_id,
    )


async def test_backfill_primero_el_cierre_espera_y_el_indice_lleva_lo_escrito(
    tenant_ctx: tuple[str, asyncpg.Connection],
    conexiones_extra: tuple[asyncpg.Connection, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    otra, observador = conexiones_extra
    created, _rid = await _sembrar(conn, slug)

    pausa = _Pausa(ASIENTO_BACKFILL)
    with patch.object(audit, "append", pausa):
        backfill = asyncio.create_task(ArchiveService(conn, slug).completar_origen_legado(10))
        await asyncio.wait_for(pausa.en_vuelo.wait(), timeout=10)
        # T1 (backfill) tiene escrito el vínculo y sin confirmar: el cierre debe esperar.
        cierre = asyncio.create_task(ArchiveService(otra, slug).close_expediente(created.id))
        try:
            assert await _espera_bloqueo(observador, otra.get_server_pid()), (
                "el cierre no quedó esperando un bloqueo: FOR SHARE OF e no serializa"
            )
            assert not cierre.done()
            # Y nada del cierre es visible desde fuera de su transacción.
            estado = await observador.fetchval(
                f'SELECT status FROM "{tenant_schema(slug)}".expedientes WHERE id = $1', created.id
            )
            assert estado == "open"
        finally:
            pausa.soltar.set()
        resumen = await asyncio.wait_for(backfill, timeout=10)
        await asyncio.wait_for(cierre, timeout=20)

    assert resumen["completados"] == 1
    vinculo = await _vinculo(conn, slug, created.id)
    assert vinculo["formato"] == "application/pdf" and vinculo["tamano_bytes"] == 4096
    assert vinculo["dependencia_productora_codigo"] == 7
    # El índice del cierre refleja lo que el backfill confirmó: no queda sin ello.
    item = await _item_del_indice_final(conn, slug, created.id)
    assert item["formato"] == "application/pdf"
    assert item["tamano_bytes"] == 4096
    assert item["dependencia_productora_codigo"] == 7
    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas


async def test_cierre_primero_el_backfill_espera_y_no_escribe_sobre_lo_cerrado(
    tenant_ctx: tuple[str, asyncpg.Connection],
    conexiones_extra: tuple[asyncpg.Connection, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    otra, observador = conexiones_extra
    created, _rid = await _sembrar(conn, slug)

    pausa = _Pausa(ASIENTO_CIERRE)
    with patch.object(audit, "append", pausa):
        cierre = asyncio.create_task(ArchiveService(otra, slug).close_expediente(created.id))
        await asyncio.wait_for(pausa.en_vuelo.wait(), timeout=10)
        # T1 (cierre) transicionó y regeneró el índice, sin confirmar: el backfill espera.
        backfill = asyncio.create_task(ArchiveService(conn, slug).completar_origen_legado(10))
        try:
            assert await _espera_bloqueo(observador, conn.get_server_pid()), (
                "el backfill no quedó esperando un bloqueo: FOR SHARE OF e no serializa"
            )
            assert not backfill.done()
        finally:
            pausa.soltar.set()
        await asyncio.wait_for(cierre, timeout=20)
        resumen = await asyncio.wait_for(backfill, timeout=10)

    # Al soltar, el backfill relee el expediente ya cerrado y no escribe nada.
    assert resumen["completados"] == 0
    vinculo = await _vinculo(conn, slug, created.id)
    assert vinculo["formato"] is None and vinculo["tamano_bytes"] is None
    assert vinculo["dependencia_productora_codigo"] is None
    assert vinculo["origen_completado_at"] is None
    # Vínculo e índice coinciden: ninguno tiene lo que el backfill no escribió.
    item = await _item_del_indice_final(conn, slug, created.id)
    assert item["formato"] is None and item["tamano_bytes"] is None
    assert await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug, ASIENTO_BACKFILL,
    ) == 0
