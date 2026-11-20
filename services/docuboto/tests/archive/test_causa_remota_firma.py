# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma personal fallida: el mensaje tiene que nombrar la causa real.

Un fallo del lado de firma (5xx o excepción) llega a archive como
`remote_failure` con texto legible; un rechazo de negocio, como `rejected` tal
cual. Colapsarlos hacía acusar al servicio equivocado.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from signature import api as signature_api


def test_sobre_de_error_del_proyecto_da_codigo_y_texto() -> None:
    detail = {"error": "artefacto_no_almacenado", "detail": "MinIO rechazo el objeto", "status": 500}
    assert signature_api._causa(detail) == "artefacto_no_almacenado: MinIO rechazo el objeto"


def test_detail_de_texto_plano_se_conserva() -> None:
    assert signature_api._causa("Internal Server Error") == "Internal Server Error"


def test_texto_largo_se_acota() -> None:
    assert len(signature_api._causa("x" * 900)) == 300


def _firma_que_lanza(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    @asynccontextmanager
    async def _conn(*_a, **_k):
        yield MagicMock()

    servicio = MagicMock()
    servicio.return_value.sign = AsyncMock(side_effect=exc)
    monkeypatch.setattr(signature_api, "_tenant_conn", _conn)
    monkeypatch.setattr(signature_api, "enforce_permission", AsyncMock())
    monkeypatch.setattr(signature_api, "PersonalSigningService", servicio)


async def _firmar():
    return await signature_api.sign_personal(
        tenant_slug="demo", actor_id="00000000-0000-0000-0000-000000000001",
        payload_xml="<x/>", objeto_tipo="acta_transferencia_personal", objeto_id="t", totp_code="123456",
    )


@pytest.mark.asyncio
async def test_un_5xx_es_fallo_remoto_con_su_causa(monkeypatch: pytest.MonkeyPatch) -> None:
    _firma_que_lanza(
        monkeypatch,
        HTTPException(503, {"error": "canal_firma_personal_no_disponible", "detail": "auth caído"}),
    )
    r = await _firmar()
    assert r.signed is None and r.rejected is None
    assert r.remote_failure == (503, "canal_firma_personal_no_disponible: auth caído")


@pytest.mark.asyncio
async def test_una_excepcion_no_capturada_nombra_su_tipo(monkeypatch: pytest.MonkeyPatch) -> None:
    _firma_que_lanza(monkeypatch, RuntimeError("CHECK nivel_conformidad"))
    r = await _firmar()
    assert r.remote_failure == (500, "RuntimeError: CHECK nivel_conformidad")


@pytest.mark.asyncio
async def test_un_rechazo_de_negocio_viaja_tal_cual(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = {"error": "totp_incorrecto", "detail": "código inválido", "status": 401}
    _firma_que_lanza(monkeypatch, HTTPException(401, detail))
    r = await _firmar()
    assert r.rejected == (401, detail) and r.remote_failure is None
