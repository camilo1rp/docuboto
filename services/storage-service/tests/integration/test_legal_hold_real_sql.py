# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Legal hold (AREA-08-07) contra Postgres REAL: el orden del acto y su
atomicidad se prueban donde de verdad hay transacciones.

Corre con: `docker compose run --rm -e RUN_INTEGRATION=1 storage-service
pytest -q -m integration tests/integration/test_legal_hold_real_sql.py`
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import LegalHoldRequest
from app.services import preservation_worm_service as svc

pytestmark = pytest.mark.integration


async def _seed(slug: str, conn: asyncpg.Connection, *, hold: bool) -> str:
    exp = str(uuid.uuid4())
    await PreservacionWormRepository(conn, slug).insert(
        expediente_id=exp,
        tipo="indice",
        bucket="orpycamcp-itest-preservation",
        object_key=f"{exp}/indice.xml",
        version_id="v1",
        sha256="a" * 64,
        retain_until=datetime.now(tz=UTC) + timedelta(days=30),
        retention_mode="COMPLIANCE",
        legal_hold=hold,
        file_id=None,
    )
    return exp


def _minio() -> MagicMock:
    m = MagicMock()
    m.enable_object_legal_hold = AsyncMock()
    m.disable_object_legal_hold = AsyncMock()
    return m


async def _acciones(conn: asyncpg.Connection, slug: str, exp: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT action FROM public.audit_log WHERE tenant_slug = $1 AND object_ref = $2 "
        "ORDER BY id",
        slug,
        exp,
    )
    return [r["action"] for r in rows]


def _req(exp: str, activo: bool) -> LegalHoldRequest:
    return LegalHoldRequest(
        expediente_id=exp,
        activo=activo,
        motivo="Orden del juzgado 12 por proceso en curso",
        acta_ref="ACTA-1",
    )


@pytest.mark.asyncio
async def test_levantar_deja_solicitud_y_levantamiento_y_la_fila_cambia(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    exp = await _seed(slug, conn, hold=True)
    minio = _minio()
    monkeypatch.setattr(svc, "get_minio", lambda: minio)
    monkeypatch.setattr(svc.settings, "preservation_enabled", True)

    res = await svc.PreservationWormService(conn, slug).fijar_legal_hold(
        _req(exp, False), actor=str(uuid.uuid4())
    )

    assert res.objetos_afectados == 1
    fila = await PreservacionWormRepository(conn, slug).get_by_expediente_and_tipo(exp, "indice")
    assert fila["legal_hold"] is False
    assert await _acciones(conn, slug, exp) == [
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantado",
    ]
    minio.disable_object_legal_hold.assert_awaited_once()


@pytest.mark.asyncio
async def test_si_falla_el_almacen_al_levantar_la_fila_sigue_bloqueando_y_hay_traza(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    exp = await _seed(slug, conn, hold=True)
    minio = _minio()
    minio.disable_object_legal_hold = AsyncMock(side_effect=RuntimeError("s3 caido"))
    monkeypatch.setattr(svc, "get_minio", lambda: minio)
    monkeypatch.setattr(svc.settings, "preservation_enabled", True)

    with pytest.raises(RuntimeError, match="s3 caido"):
        await svc.PreservationWormService(conn, slug).fijar_legal_hold(
            _req(exp, False), actor=None
        )

    fila = await PreservacionWormRepository(conn, slug).get_by_expediente_and_tipo(exp, "indice")
    assert fila["legal_hold"] is True
    assert await _acciones(conn, slug, exp) == [
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantamiento_fallido",
    ]
    # El lock de sesion se libero: el acto se puede reintentar sin colgarse.
    assert await conn.fetchval(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
    ) == 0


@pytest.mark.asyncio
async def test_si_falla_el_asiento_final_no_cambia_la_fila_pero_queda_la_solicitud(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad fila+asiento: si el asiento del levantamiento falla, la fila NO
    se actualiza (misma tx). Y la solicitud previa ya esta asentada: el almacen
    liberado nunca queda sin ningun registro."""
    slug, conn = tenant_ctx
    exp = await _seed(slug, conn, hold=True)
    monkeypatch.setattr(svc, "get_minio", lambda: _minio())
    monkeypatch.setattr(svc.settings, "preservation_enabled", True)

    real_append = svc.audit.append

    async def _falla_el_final(c, **kw):
        if kw["action"] == "worm.legal_hold_levantado":
            raise RuntimeError("audit caido")
        return await real_append(c, **kw)

    monkeypatch.setattr(svc.audit, "append", _falla_el_final)

    with pytest.raises(RuntimeError, match="audit caido"):
        await svc.PreservationWormService(conn, slug).fijar_legal_hold(
            _req(exp, False), actor=None
        )

    fila = await PreservacionWormRepository(conn, slug).get_by_expediente_and_tipo(exp, "indice")
    assert fila["legal_hold"] is True
    assert await _acciones(conn, slug, exp) == ["worm.legal_hold_levantamiento_solicitado"]
