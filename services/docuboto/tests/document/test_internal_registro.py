# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`document.api.read_radicado_record` solo sirve un nivel habilitado."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from document import api as document_api
from tests.document.conftest import TENANT_SLUG

RADICADO = uuid.uuid4()


def _fila() -> dict[str, Any]:
    return {
        "id": RADICADO,
        "tracking_number": "2026-E-1",
        "doc_type": "E",
        "subject": "Solicitud",
        "doc_class": "peticion",
        "dest_dept_code": "12",
        "status": "registered",
        "registered_at": datetime(2026, 10, 31, 15, 0, tzinfo=UTC),
        "due_date": None,
        "sender_name": "Ana",
        "sender_entity": None,
        "metadata": {"ciudad": "Bogotá"},
        "updated_at": datetime(2026, 10, 31, 15, 1, tzinfo=UTC),
        "nivel_seguridad": 1,
    }


def _consultas(fila: dict[str, Any] | None) -> Any:
    async def fetchrow(sql: str, *args: Any) -> Any:
        if "public.tenants" in sql:
            return {"intelligence_levels": [1]}
        if "registro:radicado" in sql:
            assert "anulado" in sql and "nivel_seguridad = ANY" in sql
            assert args[0] == RADICADO
            assert args[1] == [1]
            return fila
        raise AssertionError(sql)

    return fetchrow


@pytest.mark.asyncio
async def test_un_nivel_no_habilitado_no_lista_anexos(api_conn) -> None:
    conn = api_conn
    conn.fetchrow.side_effect = _consultas(None)

    async def fetch(sql: str, *args: Any) -> Any:
        raise AssertionError(sql)

    conn.fetch.side_effect = fetch
    assert (
        await document_api.read_radicado_record(tenant_slug=TENANT_SLUG, radicado_id=RADICADO)
        is None
    )


@pytest.mark.asyncio
async def test_un_nivel_habilitado_devuelve_el_registro_y_deja_traza(
    api_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = api_conn
    conn.fetchrow.side_effect = _consultas(_fila())
    anexo_id = uuid.uuid4()
    asientos: list[dict[str, Any]] = []

    async def fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
        assert "registro:anexos" in sql
        return [
            {
                "id": anexo_id,
                "filename": "solicitud.pdf",
                "mime_type": "application/pdf",
                "file_size": 120,
                "es_principal": True,
                "folios": 2,
                "sha256": "ab" * 32,
            }
        ]

    async def traza(*args: Any, **kwargs: Any) -> None:
        asientos.append(kwargs)

    conn.fetch.side_effect = fetch
    monkeypatch.setattr(document_api.audit, "append", traza)
    cuerpo = await document_api.read_radicado_record(
        tenant_slug=TENANT_SLUG, radicado_id=RADICADO
    )
    assert cuerpo is not None
    assert cuerpo["tracking_number"] == "2026-E-1"
    assert cuerpo["anexos"][0]["sha256"] == "ab" * 32
    assert "nivel_seguridad" not in cuerpo
    assert asientos[0]["action"] == "document.registro_transmitido"
    assert asientos[0]["payload"]["detalle"] == "transmitido al proveedor de inteligencia"
    assert asientos[0]["object_ref"] == "2026-E-1"
