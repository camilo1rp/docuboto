# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La escritura única del aparcamiento (`dead_letter.persistir`, ADR-049 §4).

Fila + asiento en una transacción, idempotente por grupo; sin entidad válida,
asiento bajo SYSTEM_TENANT y vuelta normal; un error transitorio se propaga para
que el bus no confirme (R-4). Y cada consumidor la usa con SU grupo y servicio.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from docuboto.core import dead_letter

pytestmark = pytest.mark.asyncio

META = {
    "dead_letter_reason": "max_deliveries_exceeded",
    "dead_letter_origin_stream": "orpycamcp.document.events",
    "dead_letter_group": "workflow-service",
    "dead_letter_delivery_count": "5",
}


def _sobre(tenant: str | None = "demo") -> dict:
    return {
        "event_id": "e-1",
        "event_type": "document.radicado.created",
        "correlation_id": "c-1",
        "tenant_slug": tenant,
        "occurred_at": "2026-11-27T10:00:00+00:00",
        "actor": "u-1",
        "payload": {"id": "r-1", "tracking_number": "2026-DEMO-E-000001"},
    }


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch):
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    usados: dict[str, list] = {"tenant": [], "pool": []}

    @asynccontextmanager
    async def _tenant(slug: str):
        usados["tenant"].append(slug)
        yield conn

    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            usados["pool"].append(True)
            yield conn

    insertar = AsyncMock(return_value={"event_id": "e-1"})
    asiento = AsyncMock()
    monkeypatch.setattr(dead_letter, "acquire_tenant", _tenant)
    monkeypatch.setattr(dead_letter, "get_pool", lambda: _Pool())
    monkeypatch.setattr(dead_letter.DeadLetterRepository, "insert_if_new", insertar)
    monkeypatch.setattr(dead_letter.audit, "append", asiento)
    return conn, usados, insertar, asiento


async def test_fila_y_asiento_en_la_misma_transaccion(entorno) -> None:
    conn, usados, insertar, asiento = entorno
    await dead_letter.persistir(_sobre(), META, group="workflow-service", service="workflow-service")

    assert usados["tenant"] == ["demo"]
    conn.transaction.assert_called_once()
    kw = insertar.await_args.kwargs
    assert kw["origin_group"] == "workflow-service"
    assert kw["origin_stream"] == "orpycamcp.document.events"
    assert json.loads(kw["payload_json"])["actor"] == "u-1", "el actor original se conserva"
    a = asiento.await_args.kwargs
    assert a["action"] == "evento.dead_letter" and a["tenant_slug"] == "demo"
    assert a["service"] == "workflow-service" and a["actor"] == "system"
    assert a["payload"]["business"]["tracking_number"] == "2026-DEMO-E-000001"


async def test_el_grupo_cae_al_del_consumidor_si_el_meta_no_lo_trae(entorno) -> None:
    _, _, insertar, _ = entorno
    meta = {k: v for k, v in META.items() if k != "dead_letter_group"}
    await dead_letter.persistir(_sobre(), meta, group="signature-service", service="s")
    assert insertar.await_args.kwargs["origin_group"] == "signature-service"


async def test_si_ya_estaba_no_repite_el_asiento(entorno) -> None:
    _, _, insertar, asiento = entorno
    insertar.return_value = None
    await dead_letter.persistir(_sobre(), META, group="workflow-service", service="w")
    asiento.assert_not_awaited()


@pytest.mark.parametrize("tenant", [None, "", "No Válido!"])
async def test_sin_entidad_valida_se_asienta_en_sistema_y_vuelve(entorno, tenant) -> None:
    _, usados, insertar, asiento = entorno
    await dead_letter.persistir(_sobre(tenant), META, group="signature-service", service="s")
    insertar.assert_not_awaited()
    assert usados["tenant"] == [] and usados["pool"] == [True]
    assert asiento.await_args.kwargs["tenant_slug"] is None


async def test_un_error_transitorio_se_propaga(entorno) -> None:
    _, _, insertar, _ = entorno
    insertar.side_effect = ConnectionError("base caída")
    with pytest.raises(ConnectionError):
        await dead_letter.persistir(_sobre(), META, group="workflow-service", service="w")


async def test_sin_entidad_y_con_la_base_caida_tambien_se_propaga(entorno) -> None:
    _, _, _, asiento = entorno
    asiento.side_effect = ConnectionError("base caída")
    with pytest.raises(ConnectionError):
        await dead_letter.persistir(_sobre(None), META, group="workflow-service", service="w")


@pytest.mark.parametrize(
    ("modulo", "grupo", "servicio"),
    [
        ("workflow.workers.document_consumer", "workflow-service", "workflow-service"),
        ("signature.workers.document_consumer", "signature-service", "signature-service"),
        ("notification.workers.stream_worker", "notification-service", "notification-service"),
        ("knowledge.workers.intelligence_consumer", "intelligence-gateway", "knowledge-service"),
    ],
)
async def test_cada_consumidor_aparca_con_su_grupo_y_servicio(modulo, grupo, servicio) -> None:
    import importlib

    persist = importlib.import_module(modulo)._persist_dead_letter
    assert isinstance(persist, partial)
    assert persist.func is dead_letter.persistir
    assert persist.keywords == {"group": grupo, "service": servicio}
