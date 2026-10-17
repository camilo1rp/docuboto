# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El plazo de oposicion de un lote no corre mientras la entidad no es operativa
(ADR-035, re-auditoria B9 hallazgo A), contra PostgreSQL real.

Durante la suspension la ventanilla -unico canal de oposicion a un lote publicado-
responde 503. El aval solo miraba `publicado_hasta < CURRENT_DATE`, asi que un
plazo podia vencer con la ventanilla cerrada. Aqui se siembran asientos
`tenant.updated` REALES (con `audit.append`, cadena valida) con fecha de pasado y
se comprueba que el `WHERE` del aval cuenta solo dias operativos.

Gating: `RUN_INTEGRATION=1` + Postgres real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.repositories.archive_repository import ArchiveRepository
from app.services.archive_service import ArchiveService

from .test_eliminacion_lote_estados import _publicar, _sembrar_lote

pytestmark = pytest.mark.integration

HOY = date.today()


def _a(dia: date, hora: int = 0) -> datetime:
    return datetime.combine(dia, time(hora), tzinfo=UTC)


async def _asiento_estado(
    conn: asyncpg.Connection, slug: str, ts: datetime, antes: str, despues: str
) -> None:
    """Un `tenant.updated` como el que deja tenant-service, fechado en `ts`."""

    class _Reloj:
        @staticmethod
        def now(tz=None) -> datetime:  # noqa: ANN001
            return ts

    with patch.object(audit, "datetime", _Reloj):
        await audit.append(
            conn,
            tenant_slug=slug,  # cadena propia del test: no ensucia la de __system__
            service="tenant-service",
            action="tenant.updated",
            object_type="tenant",
            object_ref=slug,
            canal="system",
            payload={
                "antes": {"name": "x", "status": antes},
                "despues": {"name": "x", "status": despues},
            },
        )


async def _lote_publicado(conn, slug, *, desde: date, hasta: date) -> uuid.UUID:
    lote_id, _ = await _sembrar_lote(conn, slug)
    await _publicar(conn, lote_id, hasta)
    await conn.execute(
        "UPDATE eliminacion_lotes SET publicado_at = $2 WHERE id = $1", lote_id, _a(desde, 9)
    )
    return lote_id


async def _avalar(conn, slug, lote_id):
    return await ArchiveRepository(conn, slug).avalar_lote_eliminacion(
        lote_id,
        acta_comite="Acta 07",
        fecha_aval=HOY,
        acto_administrativo="Res. 512 de 2026",
        avalado_por=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_sin_suspension_el_aval_vencido_el_plazo_SI_se_registra(tenant_ctx) -> None:
    """Control positivo: sin el, todos los bloqueos de abajo pasarian con un
    `WHERE` que no dejara avalar nunca."""
    slug, conn = tenant_ctx
    lote_id = await _lote_publicado(
        conn, slug, desde=HOY - timedelta(days=4), hasta=HOY - timedelta(days=1)
    )

    assert (await _avalar(conn, slug, lote_id)) is not None


@pytest.mark.asyncio
async def test_una_suspension_dentro_del_plazo_BLOQUEA_el_aval_y_extiende_el_plazo(
    tenant_ctx,
) -> None:
    slug, conn = tenant_ctx
    lote_id = await _lote_publicado(
        conn, slug, desde=HOY - timedelta(days=4), hasta=HOY - timedelta(days=1)
    )
    # Suspendida durante los dias -3 y -2 (dos dias tocados), luego reactivada.
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=3), 8), "active", "suspended")
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=2), 20), "suspended", "active")

    assert (await _avalar(conn, slug, lote_id)) is None  # publicado_hasta < hoy, y aun asi

    plazo = await ArchiveRepository(conn, slug).plazo_oposicion_efectivo(lote_id)
    assert plazo["plazo_efectivo_hasta"] == HOY + timedelta(days=1)

    with pytest.raises(HTTPException) as exc:
        await ArchiveService(conn, slug).avalar_lote_eliminacion(
            lote_id,
            acta_comite="Acta 07",
            fecha_aval=HOY,
            acto_administrativo="Res. 512 de 2026",
            actor=uuid.uuid4(),
        )
    detalle = exc.value.detail
    assert exc.value.status_code == 409
    assert detalle["error"] == "plazo_oposicion_interrumpido"
    assert detalle["dias_suspendidos"] == 2
    assert detalle["plazo_efectivo_hasta"] == str(HOY + timedelta(days=1))


@pytest.mark.asyncio
async def test_pasado_el_plazo_EXTENDIDO_el_aval_se_registra(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    # Plazo de 3 dias (-10..-8); suspendida los dias -9 y -8: efectivo = -6 < hoy.
    lote_id = await _lote_publicado(
        conn, slug, desde=HOY - timedelta(days=10), hasta=HOY - timedelta(days=8)
    )
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=9), 8), "active", "suspended")
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=8), 20), "suspended", "active")

    plazo = await ArchiveRepository(conn, slug).plazo_oposicion_efectivo(lote_id)
    assert plazo["plazo_efectivo_hasta"] == HOY - timedelta(days=6)
    assert (await _avalar(conn, slug, lote_id)) is not None


@pytest.mark.asyncio
async def test_una_suspension_FUERA_del_plazo_no_bloquea(tenant_ctx) -> None:
    """Sin falso positivo: suspendida antes de publicar o despues del vencimiento."""
    slug, conn = tenant_ctx
    lote_id = await _lote_publicado(
        conn, slug, desde=HOY - timedelta(days=6), hasta=HOY - timedelta(days=3)
    )
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=20), 8), "active", "suspended")
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=10), 8), "suspended", "active")
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=2), 8), "active", "suspended")

    assert (await _avalar(conn, slug, lote_id)) is not None


@pytest.mark.asyncio
async def test_una_suspension_ABIERTA_dentro_del_plazo_tambien_bloquea(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id = await _lote_publicado(
        conn, slug, desde=HOY - timedelta(days=4), hasta=HOY - timedelta(days=1)
    )
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=3), 8), "active", "suspended")

    assert (await _avalar(conn, slug, lote_id)) is None


@pytest.mark.asyncio
async def test_el_asiento_de_OTRA_entidad_no_cuenta(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id = await _lote_publicado(
        conn, slug, desde=HOY - timedelta(days=4), hasta=HOY - timedelta(days=1)
    )
    ajeno = f"{slug}-otra"
    await _asiento_estado(conn, ajeno, _a(HOY - timedelta(days=3), 8), "active", "suspended")

    assert (await _avalar(conn, slug, lote_id)) is not None


@pytest.mark.asyncio
async def test_un_cambio_de_nombre_sin_cambio_de_estado_no_cuenta(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id = await _lote_publicado(
        conn, slug, desde=HOY - timedelta(days=4), hasta=HOY - timedelta(days=1)
    )
    await _asiento_estado(conn, slug, _a(HOY - timedelta(days=3), 8), "suspended", "suspended")

    assert (await _avalar(conn, slug, lote_id)) is not None
