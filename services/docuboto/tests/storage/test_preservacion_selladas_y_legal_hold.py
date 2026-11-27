# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Protección WORM sin legal hold en el body y singleton por (expediente, tipo)
(AREA-08-16, AREA-08-06, AREA-08-07).

Desde ADR-047 la protección solo se alcanza por `storage.api` (sin rutas HTTP):
lo que aquí se prueba vive en el esquema y en `PreservationWormService`.

- La segunda protección del mismo (expediente, tipo) con OTRO `file_id` es
  conflicto y no sube nada.
- `legal_hold` no es un campo de ningún body de protección (ValidationError).
- El legal hold por motivo (fijar/levantar/listar) se prueba en
  `tests/test_c5_legal_hold_por_motivo.py` (C-5/C7).
"""

from __future__ import annotations

import uuid
from datetime import date

import pydantic
import pytest

from docuboto import config as config_module
from storage import api as storage_api
from storage.schemas.preservacion import AipCreate, ProtegerIndiceRequest, RenovarRetencionRequest
from storage.services.preservation_worm_service import (
    PreservationWormService,
    WormSingletonConflictError,
)
from tests.storage.conftest import TENANT_SLUG, make_worm_row


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.storage, "preservation_enabled", True)


def _body(extra: dict) -> dict:
    return {
        "expediente_id": "EXP-1",
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
        **extra,
    }


_ESQUEMAS = [
    (ProtegerIndiceRequest, {"file_id": str(uuid.uuid4())}),
    (ProtegerIndiceRequest, {"file_id": str(uuid.uuid4()), "tipo": "acta_transferencia"}),
    (
        AipCreate,
        {
            "documentos": [
                {"document_id": "R-1", "nombre": "a.pdf", "file_id": str(uuid.uuid4())}
            ]
        },
    ),
]


@pytest.mark.parametrize(("esquema", "extra"), _ESQUEMAS)
def test_legal_hold_en_el_body_es_invalido_en_toda_proteccion(esquema, extra) -> None:
    esquema(**_body(extra))  # control positivo: sin legal_hold es válido
    with pytest.raises(pydantic.ValidationError):
        esquema(**_body({**extra, "legal_hold": True}))


def test_renovar_retencion_tambien_rechaza_legal_hold() -> None:
    with pytest.raises(pydantic.ValidationError):
        RenovarRetencionRequest(**_body({"tipo": "indice", "legal_hold": False}))


async def _proteger(mock_conn, extra: dict):
    return await PreservationWormService(mock_conn, TENANT_SLUG).proteger_indice(
        ProtegerIndiceRequest(**_body(extra)), actor="actor"
    )


@pytest.mark.asyncio
async def test_segunda_proteccion_con_otro_file_id_es_conflicto_sin_subir(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    existente = make_worm_row(expediente_id="EXP-1", file_id=uuid.uuid4())
    mock_conn.fetchrow.side_effect = [existente]
    with pytest.raises(WormSingletonConflictError):
        await _proteger(mock_conn, {"file_id": str(uuid.uuid4()), "tipo": "indice"})
    mock_minio.get_object.assert_not_called()
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_conflicto_de_singleton_no_es_ok_por_la_api(client_with_conn) -> None:
    """Por `storage.api` el conflicto no se da por protegido."""
    _client, mock_conn, mock_minio = client_with_conn
    mock_conn.fetchrow.side_effect = [make_worm_row(expediente_id="EXP-1", file_id=uuid.uuid4())]
    outcome = await storage_api.protect_index_as_system(
        tenant_slug=TENANT_SLUG, file_id=str(uuid.uuid4()), expediente_id="EXP-1",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    assert not outcome.ok
    assert outcome.worm_object_id is None
    mock_minio.put_object.assert_not_awaited()


# -- huecos cerrados en la revision de pruebas -----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["indice", "acta_transferencia"])
async def test_reintento_con_el_mismo_file_id_sigue_siendo_idempotente(
    client_with_conn, tipo
) -> None:
    """Control positivo del conflicto: la comparacion de file_id NO puede convertir
    el reintento legitimo (mismo file_id) en conflicto (UUID vs UUID, no str)."""
    _client, mock_conn, mock_minio = client_with_conn
    fid = uuid.uuid4()
    existente = make_worm_row(expediente_id="EXP-1", file_id=fid)
    mock_conn.fetchrow.side_effect = [existente]
    response, created = await _proteger(mock_conn, {"file_id": str(fid), "tipo": tipo})
    assert created is False
    assert response.id == existente["id"]
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_fila_previa_sin_file_id_conserva_la_idempotencia(client_with_conn) -> None:
    _client, mock_conn, _minio = client_with_conn
    mock_conn.fetchrow.side_effect = [make_worm_row(expediente_id="EXP-1", file_id=None)]
    _response, created = await _proteger(mock_conn, {"file_id": str(uuid.uuid4()), "tipo": "indice"})
    assert created is False


@pytest.mark.asyncio
async def test_conflicto_de_singleton_tambien_en_proteger_artefacto(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    mock_conn.fetchrow.side_effect = [make_worm_row(expediente_id="EXP-1", file_id=uuid.uuid4())]
    with pytest.raises(WormSingletonConflictError):
        await _proteger(mock_conn, {"file_id": str(uuid.uuid4()), "tipo": "acta_transferencia"})
    mock_minio.put_object.assert_not_awaited()
