# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lecturas y actos de sistema de `document.api` que no tienen archivo propio.

Clasificados (índice del art. 20), purga por disposición final y el circuito
del borrador que cierra signature. Eran rutas internas con token (ADR-047 §1).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from document import api as document_api
from tests.document.conftest import TENANT_SLUG


@pytest.mark.asyncio
async def test_los_clasificados_son_content_free(api_conn) -> None:
    rid = uuid.uuid4()
    api_conn.fetch.return_value = [
        {
            "radicado_id": rid,
            "nivel_seguridad": 2,
            "clasificacion_causal": None,
            "clasificacion_plazo_meses": 12,
            "clasificado_at": None,
            "clasificacion_norma": None,
            "fecha_generacion": None,
        }
    ]
    datos = await document_api.fetch_clasificados(tenant_slug=TENANT_SLUG)
    assert datos["causales"]
    [fila] = datos["radicados"]
    assert fila["radicado_id"] == str(rid)
    sql = api_conn.fetch.await_args.args[0]
    assert "tracking_number" not in sql and "subject" not in sql


@pytest.mark.asyncio
async def test_una_purga_incompleta_no_es_un_recuento(api_conn, monkeypatch) -> None:
    """Si los bytes no se borraron, los documentos siguen existiendo: quien orquesta
    aborta el expediente sin lápida."""
    from document.services import purga_service

    monkeypatch.setattr(
        purga_service.PurgaService,
        "purgar_radicados",
        AsyncMock(side_effect=document_api.storage_api.StoragePurgeError("bytes vivos")),
    )
    with pytest.raises(document_api.PurgeIncompleteError):
        await document_api.purge_radicados(
            tenant_slug=TENANT_SLUG,
            radicado_ids=[uuid.uuid4()],
            expediente_id=uuid.uuid4(),
            motivo="Eliminación TRD",
            actor=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_purgar_nada_no_toca_la_bd(api_conn) -> None:
    recuento = await document_api.purge_radicados(
        tenant_slug=TENANT_SLUG, radicado_ids=[], expediente_id="E", motivo="m", actor=None
    )
    assert recuento == {"radicados_eliminados": 0, "anexos_eliminados": 0}
    api_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_la_huella_de_un_borrador_inexistente_es_draft_not_found(api_conn) -> None:
    api_conn.fetchrow.return_value = None
    with pytest.raises(document_api.DraftNotFoundError):
        await document_api.draft_fingerprint(tenant_slug=TENANT_SLUG, borrador_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_aprobar_un_borrador_ya_radicado_no_lo_toca(api_conn) -> None:
    """Un aviso repetido no deshace una radicación: solo se promueve desde `borrador`."""
    api_conn.fetchrow.return_value = {"estado": "radicado"}
    out = await document_api.approve_draft(tenant_slug=TENANT_SLUG, borrador_id=uuid.uuid4())
    assert out["cambiado"] is False and out["estado"] == "radicado"
    api_conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_aprobar_cambia_y_asienta_en_la_misma_transaccion(api_conn, monkeypatch) -> None:
    asientos: list[dict[str, Any]] = []

    async def _asiento(*_a: Any, **kw: Any) -> None:
        asientos.append(kw)

    monkeypatch.setattr(document_api.audit, "append", _asiento)
    api_conn.fetchrow.side_effect = [{"estado": "borrador"}, {"version": 3, "cuerpo_sha256": "ab"}]
    out = await document_api.approve_draft(tenant_slug=TENANT_SLUG, borrador_id=uuid.uuid4())
    assert out["cambiado"] is True
    assert asientos[0]["action"] == "borrador.aprobado"
    assert asientos[0]["canal"] == "system"
    assert asientos[0]["payload"]["origen"] == "circuito_completado"
