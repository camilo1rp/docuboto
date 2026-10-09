# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Una variable de entorno retirada que sigue puesta engaña al operador.

`TSA_ESSCERTID_OBLIGATORIO` (booleano) fue sustituida por la fecha de corte
`TSA_ESSCERTID_CORTE`. Quien la heredó cree estar rechazando los sellos sin
ESSCertID; el servicio ya no la lee. Debe decirlo al arrancar.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app import main
from app.core.config import variables_retiradas_presentes


def test_detecta_la_variable_retirada_y_nombra_a_la_sustituta() -> None:
    assert variables_retiradas_presentes({"TSA_ESSCERTID_OBLIGATORIO": "true"}) == [
        ("TSA_ESSCERTID_OBLIGATORIO", "TSA_ESSCERTID_CORTE")
    ]


def test_sin_la_variable_no_hay_nada_que_avisar() -> None:
    assert variables_retiradas_presentes({"TSA_ESSCERTID_CORTE": "2026-10-04T00:00:00Z"}) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("presente", "esperado"), [(True, 1), (False, 0)])
async def test_el_arranque_registra_un_error_con_la_sustituta(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, presente: bool, esperado: int
) -> None:
    if presente:
        monkeypatch.setenv("TSA_ESSCERTID_OBLIGATORIO", "true")
    else:
        monkeypatch.delenv("TSA_ESSCERTID_OBLIGATORIO", raising=False)
    monkeypatch.setattr(main.common_secrets, "require_secrets", lambda *a, **k: None)
    monkeypatch.setattr(main, "create_pool", AsyncMock())
    monkeypatch.setattr(main, "close_pool", AsyncMock())
    monkeypatch.setattr(main, "init_redis", AsyncMock())
    monkeypatch.setattr(main, "close_redis", AsyncMock())
    async def _espera() -> None:
        return None

    monkeypatch.setattr(
        main.events, "spawn_supervised", lambda *a, **k: asyncio.ensure_future(_espera())
    )
    with caplog.at_level(logging.ERROR, logger="signature.startup"):
        async with main.lifespan(main.app):
            pass
    errores = [r for r in caplog.records if r.levelno == logging.ERROR and "TSA_ESSCERTID" in r.getMessage()]
    assert len(errores) == esperado
    if presente:
        assert "TSA_ESSCERTID_CORTE" in errores[0].getMessage()
