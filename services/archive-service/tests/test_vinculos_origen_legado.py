# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-07-01: backfill del origen (productora/formato/tamaño) de vínculos LEGADOS de
expedientes ABIERTOS. Solo columnas NULL, un intento por vínculo, asiento por cambio, y los
expedientes cerrados (probatorios) no se tocan. El efecto real contra Postgres está en
`tests/integration/test_vinculos_origen_legado_sql.py`."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.document_client import DocumentClientError
from app.services.archive_service import ArchiveService

TENANT = "demo"


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _pendiente(rid: uuid.UUID) -> dict:
    return {"expediente_id": uuid.uuid4(), "radicado_id": rid, "expediente_code": "EXP-2026-0001"}


def _meta(**kw) -> dict:
    base = {"origin_dept_code": 120, "origin_dept": "Oficina de Contratacion",
            "formato": "application/pdf", "tamano_bytes": 4096}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_completa_lo_que_document_service_sabe_y_deja_asiento() -> None:
    conn = _conn()
    rid = uuid.uuid4()
    conn.fetch.return_value = [_pendiente(rid)]
    conn.fetchrow.return_value = {
        "productora_completada": True, "formato_completado": True, "tamano_completado": False,
    }
    svc = ArchiveService(conn, TENANT)

    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        new=AsyncMock(return_value={str(rid): _meta()}),
    ) as meta:
        resumen = await svc.completar_origen_legado(50)

    assert resumen == {"vistos": 1, "completados": 1, "sin_cambios": 0}
    assert meta.await_args.kwargs["max_clearance"] == 3  # sistema, sin recorte
    update = conn.fetchrow.await_args
    # solo se rellenan columnas NULL: COALESCE / CASE ... IS NULL en el UPDATE, nunca un SET directo
    assert "COALESCE(er.formato" in update.args[0] and "IS NULL" in update.args[0]
    assert "origen_completado_at = now()" in update.args[0]
    asiento = next(
        c.args for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    )
    assert asiento[5] == "archive.vinculo_origen_completado"
    assert json.loads(asiento[8])["campos"] == ["dependencia_productora", "formato"]


@pytest.mark.asyncio
async def test_un_null_legitimo_se_marca_intentado_sin_asiento() -> None:
    """Radicado sin anexos ni dependencia de origen: nada que completar, pero se marca para no
    reelegirlo en cada barrida."""
    conn = _conn()
    rid = uuid.uuid4()
    conn.fetch.return_value = [_pendiente(rid)]
    conn.fetchrow.return_value = {
        "productora_completada": False, "formato_completado": False, "tamano_completado": False,
    }
    svc = ArchiveService(conn, TENANT)

    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        new=AsyncMock(
            return_value={str(rid): _meta(origin_dept_code=None, origin_dept=None, formato=None, tamano_bytes=None)}
        ),
    ):
        resumen = await svc.completar_origen_legado(50)

    assert resumen == {"vistos": 1, "completados": 0, "sin_cambios": 1}
    # se llamó al UPDATE (que deja la marca) con todo en NULL
    args = conn.fetchrow.await_args.args
    assert args[3:] == (None, None, None, None)
    assert not [
        c for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_con_document_service_caido_no_se_marca_nada() -> None:
    """Marcar «intentado» sin haber podido preguntar sería perder el vínculo."""
    conn = _conn()
    conn.fetch.return_value = [_pendiente(uuid.uuid4())]
    svc = ArchiveService(conn, TENANT)

    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        new=AsyncMock(side_effect=DocumentClientError("caído")),
    ):
        resumen = await svc.completar_origen_legado(50)

    assert resumen == {"vistos": 0, "completados": 0, "sin_cambios": 0}
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_sin_pendientes_no_llama_a_document_service() -> None:
    svc = ArchiveService(_conn(), TENANT)
    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal", new=AsyncMock()
    ) as meta:
        assert await svc.completar_origen_legado(50) == {"vistos": 0, "completados": 0, "sin_cambios": 0}
    meta.assert_not_awaited()


@pytest.mark.asyncio
async def test_solo_elige_vinculos_vigentes_de_expedientes_abiertos_sin_marca() -> None:
    conn = _conn()
    svc = ArchiveService(conn, TENANT)
    await svc._repo.list_vinculos_origen_pendiente(10)
    sql = conn.fetch.await_args.args[0]
    assert "e.status = 'open'" in sql  # los cerrados son probatorios
    assert "er.estado = 'vinculado'" in sql
    assert "er.origen_completado_at IS NULL" in sql


@pytest.mark.asyncio
async def test_el_update_exige_expediente_abierto_en_su_where() -> None:
    """Una carrera con el cierre no puede cambiar un vínculo de un expediente que acaba de
    cerrarse: la condición va en el UPDATE, no en la lectura previa."""
    conn = _conn()
    svc = ArchiveService(conn, TENANT)
    await svc._repo.completar_origen_vinculo(
        uuid.uuid4(), uuid.uuid4(), dep_codigo=1, dep_nombre="X", formato=None, tamano_bytes=None
    )
    sql = conn.fetchrow.await_args.args[0]
    assert "e.status = 'open'" in sql and "er0.origen_completado_at IS NULL" in sql
    assert "FOR UPDATE OF er0" in sql


@pytest.mark.asyncio
async def test_el_asiento_lleva_los_valores_escritos_y_la_fuente() -> None:
    """Hallazgo T12 (Media): el asiento dice QUÉ valor entró al índice y de dónde vino."""
    conn = _conn()
    rid = uuid.uuid4()
    conn.fetch.return_value = [_pendiente(rid)]
    conn.fetchrow.return_value = {
        "productora_completada": True, "formato_completado": True, "tamano_completado": False,
    }
    svc = ArchiveService(conn, TENANT)
    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        new=AsyncMock(return_value={str(rid): _meta()}),
    ):
        await svc.completar_origen_legado(50)
    asiento = next(
        c.args for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    )
    payload = json.loads(asiento[8])
    assert payload["valores"] == {
        "dep_codigo": 120, "dep_nombre": "Oficina de Contratacion", "formato": "application/pdf",
    }  # tamano no se completó: no figura
    assert payload["fuente"] == "document-service" and payload["resuelto_at"]


@pytest.mark.asyncio
async def test_el_update_serializa_con_el_cierre_bloqueando_la_fila_del_expediente() -> None:
    conn = _conn()
    svc = ArchiveService(conn, TENANT)
    await svc._repo.completar_origen_vinculo(
        uuid.uuid4(), uuid.uuid4(), dep_codigo=1, dep_nombre="X", formato=None, tamano_bytes=None
    )
    assert "FOR SHARE OF e" in conn.fetchrow.await_args.args[0]
