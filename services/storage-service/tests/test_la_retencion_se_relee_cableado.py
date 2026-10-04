# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.



"""El read-back de la retención está CABLEADO en los tres caminos que la escriben.

`test_la_retencion_se_relee.py` prueba `verificar_retencion_aplicada` AISLADA (la
pieza). Con la llamada borrada de `proteger`, de `renovar_retencion` o de
`empaquetar_aip`, toda la suite unitaria seguía en verde (mutación ejecutada en la
revisión de la tanda T12, AREA-08-15): el único test que lo vería necesita un S3 real
y `RUN_S3_WORM=1`, y además su control negativo (bucket sin Object Lock) lo ataja antes
otro guard (`ensure_object_lock_bucket`), no el read-back.

Aquí el almacén doble ACEPTA la orden y al releer dice que no hay retención (el caso
SeaweedFS). El servicio debe abortar y NO asentar: ni fila WORM, ni UPDATE de
renovación, ni evento `ok`, ni asiento."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.config as config_module
from app.core.minio_client import RetencionNoVerificadaError
from tests.conftest import (
    INTERNAL_HEADERS,
    make_file_record,
    make_worm_row,
)

_XML = b"<acta_fuid>firmada</acta_fuid>"
_SHA = hashlib.sha256(_XML).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _almacen_que_no_cumple(mock_minio) -> None:
    mock_minio.get_object_retention = AsyncMock(return_value=None)


def _objeto(content: bytes) -> AsyncMock:
    obj = MagicMock()
    obj.read = AsyncMock(return_value=content)
    obj.close = MagicMock()
    return AsyncMock(return_value=obj)


def _sqls(mock_conn) -> list[str]:
    out = [c.args[0] for c in mock_conn.fetchrow.call_args_list if c.args]
    out += [c.args[0] for c in mock_conn.execute.call_args_list if c.args]
    return out


@pytest.mark.asyncio
async def test_proteger_aborta_y_no_asienta_si_el_almacen_no_cumple(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        None,  # sin protección previa
        make_file_record(
            file_id=file_id, filename="acta.xml", mime_type="application/xml",
            sha256=_SHA, object_key=f"{file_id}/acta.xml",
        ),
        make_worm_row(expediente_id="EXP-1", tipo="acta_transferencia"),  # NO debe consumirse
    ]
    mock_minio.get_object = _objeto(_XML)
    _almacen_que_no_cumple(mock_minio)

    with pytest.raises(RetencionNoVerificadaError):
        await client.post(
            "/api/v1/preservacion/proteger-artefacto",
            json={
                "file_id": str(file_id), "expediente_id": "EXP-1", "tipo": "acta_transferencia",
                "retencion_anios": 5, "fecha_inicio": "2026-01-01",
            },
            headers=INTERNAL_HEADERS,
        )

    mock_minio.get_object_retention.assert_awaited()
    sqls = _sqls(mock_conn)
    assert not any("INSERT INTO preservacion_worm_objeto" in q for q in sqls)
    assert not any("INSERT INTO preservacion_evento" in q for q in sqls)
    assert not any("INSERT INTO public.audit_log" in q for q in sqls)


@pytest.mark.asyncio
async def test_renovar_aborta_y_no_asienta_si_el_almacen_no_cumple(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice",
        retain_until=datetime(2028, 1, 1, tzinfo=UTC),
        bucket="orpycamcp-demo-preservation", object_key="EXP-1/indice.xml",
        version_id="v1", retention_mode="COMPLIANCE",
    )
    mock_conn.fetchrow.side_effect = [existing]
    _almacen_que_no_cumple(mock_minio)

    with pytest.raises(RetencionNoVerificadaError):
        await client.post(
            "/api/v1/preservacion/renovar-retencion-internal",
            json={"expediente_id": "EXP-1", "tipo": "indice",
                  "retencion_anios": 5, "fecha_inicio": "2026-01-01"},
            headers=INTERNAL_HEADERS,
        )

    mock_minio.set_object_retention.assert_awaited_once()  # lo ordenó...
    mock_minio.get_object_retention.assert_awaited()       # ...y lo releyó
    sqls = _sqls(mock_conn)
    assert not any("UPDATE preservacion_worm_objeto" in q for q in sqls)
    assert not any("audit_log" in q for q in sqls)


@pytest.mark.asyncio
async def test_empaquetar_aip_aborta_y_no_asienta_si_el_almacen_no_cumple(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    doc_id = uuid.uuid4()
    contenido = b"contenido del oficio"
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        make_file_record(
            file_id=doc_id, filename="oficio.pdf", mime_type="application/pdf",
            sha256=hashlib.sha256(contenido).hexdigest(), object_key=f"{doc_id}/oficio.pdf",
        ),
        None,  # idempotencia
    ]
    mock_minio.get_object = _objeto(contenido)
    _almacen_que_no_cumple(mock_minio)

    with pytest.raises(RetencionNoVerificadaError):
        await client.post(
            "/api/v1/preservacion/aip",
            json={
                "expediente_id": "EXP-1",
                "documentos": [{"document_id": "R-1", "nombre": "oficio.pdf",
                                "file_id": str(doc_id)}],
                "retencion_anios": 5, "fecha_inicio": "2026-01-01",
            },
            headers=INTERNAL_HEADERS,
        )

    mock_minio.put_object.assert_awaited()  # el ZIP sí se subió...
    sqls = _sqls(mock_conn)
    assert not any("INSERT INTO preservacion_aip" in q or "INSERT INTO preservacion_worm_objeto" in q
                   for q in sqls)  # ...pero no se atestigua
    assert not any("INSERT INTO preservacion_evento" in q for q in sqls)
