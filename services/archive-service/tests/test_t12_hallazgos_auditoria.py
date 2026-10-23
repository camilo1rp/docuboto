# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Hallazgos de auditoría de la tanda T12 (Medios): base WORM de un fondo con fecha futura,
fixity fallida/parcial como evento PREMIS."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.archive import ExpedienteCreate
from app.services.retention_math import base_worm
from tests.conftest import TENANT_HEADERS, make_expediente_record, make_radicado_link_record
from tests.test_transferencias import _indice_row, _transfer_row

HOY = datetime.now(tz=UTC).date()


def _fondo(extrema: date) -> dict:
    return {
        "name": "Fondo",
        "origen": "fondo_acumulado",
        "trd_serie_id": uuid.uuid4(),
        "fecha_extrema_final": extrema,
    }


def test_fondo_con_fecha_extrema_futura_es_422() -> None:
    with pytest.raises(ValidationError, match="futura"):
        ExpedienteCreate(**_fondo(HOY + timedelta(days=1)))


def test_fondo_con_fecha_extrema_de_hoy_se_acepta() -> None:
    assert ExpedienteCreate(**_fondo(HOY)).fecha_extrema_final == HOY


def test_base_worm_fecha_futura_es_fail_closed() -> None:
    """Defensa en profundidad: sin el guard devolvería 2120 y el lock rondaría los 100 años."""
    assert (
        base_worm(
            origen="fondo_acumulado",
            closed_at=datetime.now(tz=UTC),
            fecha_extrema_final=date(HOY.year + 100, 12, 31),
            total_years=10,
            margen_dias=settings.worm_piso_margen_dias,
            hoy=HOY,
        )
        is None
    )


@pytest.mark.asyncio
async def test_fixity_fallida_registra_evento_premis_de_fallo_solo_con_conteos(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id, doc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _indice_row(),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _indice_row(),
    ]
    mock_conn.fetch.side_effect = [
        [make_radicado_link_record(expediente_id=exp_id, radicado_id=doc_id, content_hash="a" * 64)],
        [{"document_id": doc_id, "valor_huella": "b" * 64}],
    ]
    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=True)
    ) as post:
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    post.assert_awaited_once()
    kw = post.await_args.kwargs
    assert kw["tipo"] == "FIJACION" and kw["resultado"] == "fallo"
    assert kw["object_ref"] == str(exp_id)
    assert kw["dedup_key"].startswith(f"fixity-recepcion-fallo:{tid}:")
    assert kw["detalle"]["num_documentos_fallidos"] == 1
    assert str(doc_id) not in str(kw["detalle"])  # sin UUID de radicados reservados


@pytest.mark.asyncio
async def test_otro_409_no_registra_evento_de_fixity(client_with_conn) -> None:
    """Control: un 409 que no es fixity (índice sin firmar) no emite evento de fallo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _indice_row(estado="borrador"),
    ]
    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=True)
    ) as post:
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    post.assert_not_awaited()
