# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 (ítem 1, productor): los eventos `document.envio.*` salen por el OUTBOX.

Antes la transición del callback se publicaba a Redis tras el commit, tragándose el
fallo (con Redis caído la devolución se perdía), y la transición MANUAL no emitía nada.
Ahora ambas dejan la fila en `eventos_salientes` DENTRO de la transacción del cambio.

Mutaciones verificadas al entregar:
- quitar el `_encolar_estado` de `update_estado` → cae `test_la_devolucion_manual_encola_el_evento_en_la_misma_tx`;
- mover el `_encolar_estado` del callback fuera del `async with transaction()` → cae
  `test_el_callback_encola_dentro_de_la_tx`.
La atomicidad contra Postgres real (fallo de `audit.append` → sin fila) está en
`tests/integration/test_b10_postal_diferido_sql.py`.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.postal import PostalCallbackPayload, ShipmentStatusUpdate
from app.services import postal_service
from app.services.postal_service import PostalService
from tests.conftest import _make_fake_redis, _make_mock_conn

NOW = datetime.now(tz=UTC)


def _row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "operador": "4-72",
        "guia": "C472-ABCDEF0123",
        "estado": "en_transito",
        "destinatario": "Juan",
        "direccion": "Calle 1",
        "motivo_devolucion": None,
        "acuse_file_id": None,
        "acuse_sha256": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


class _TxEspia:
    """`conn.transaction()` que anota si hay una tx abierta mientras se encola."""

    def __init__(self) -> None:
        self.profundidad = 0  # el repositorio abre una tx anidada (savepoint)

    @property
    def abierta(self) -> bool:
        return self.profundidad > 0

    def __call__(self):
        espia = self

        class _CM:
            async def __aenter__(self):
                espia.profundidad += 1

            async def __aexit__(self, *exc):
                espia.profundidad -= 1
                return False

        return _CM()


def _espiar_encolado(tx: _TxEspia) -> tuple[AsyncMock, list[bool]]:
    vistos: list[bool] = []

    async def _encolar(conn, **kw):
        vistos.append(tx.abierta)
        return {"stream": kw["stream"], "event": {"event_id": "e", "event_type": kw["event_type"]}}

    return AsyncMock(side_effect=_encolar), vistos


@pytest.mark.asyncio
async def test_la_devolucion_manual_encola_el_evento_en_la_misma_tx() -> None:
    conn = _make_mock_conn()
    tx = _TxEspia()
    conn.transaction = MagicMock(side_effect=tx)
    actual = _row(estado="en_transito")
    conn.fetchrow.side_effect = [
        actual,
        _row(estado="devuelto", id=actual["id"], radicado_id=actual["radicado_id"], guia=actual["guia"]),
    ]
    encolar, vistos = _espiar_encolado(tx)
    redis = _make_fake_redis()
    svc = PostalService(conn, redis=redis, tenant_slug="demo")
    with (
        patch.object(postal_service.outbox, "encolar", encolar),
        patch.object(postal_service.outbox, "enviar", AsyncMock(return_value=True)) as enviar,
        patch.object(postal_service.audit, "append", AsyncMock()),
    ):
        await svc.update_estado(
            actual["id"], ShipmentStatusUpdate(estado="devuelto", motivo="Dirección errada"), 3, uuid.uuid4()
        )
    kw = encolar.await_args.kwargs
    assert kw["event_type"] == "document.envio.devuelto"
    assert kw["dedup_key"] == f"demo:{actual['guia']}:devuelto"
    assert kw["payload"]["estado"] == "devuelto" and kw["payload"]["radicado_id"] == str(actual["radicado_id"])
    assert vistos == [True], "el evento debe encolarse DENTRO de la transacción del cambio"
    enviar.assert_awaited_once()  # camino rápido tras el commit


@pytest.mark.asyncio
async def test_el_callback_encola_dentro_de_la_tx() -> None:
    conn = _make_mock_conn()
    tx = _TxEspia()
    conn.transaction = MagicMock(side_effect=tx)
    envio = _row(estado="en_transito")
    conn.fetchval.side_effect = [uuid.uuid4()]  # delivery_id
    conn.fetchrow.side_effect = [envio, _row(estado="devuelto")]
    encolar, vistos = _espiar_encolado(tx)
    svc = PostalService(conn, redis=_make_fake_redis(), tenant_slug="demo")
    payload = PostalCallbackPayload(
        operador="4-72", guia=envio["guia"], estado_operador="DEVUELTO", event_id="evt-d1"
    )
    with (
        patch.object(postal_service.outbox, "encolar", encolar),
        patch.object(postal_service.outbox, "enviar", AsyncMock(return_value=True)),
        patch.object(postal_service.audit, "append", AsyncMock()),
        patch.object(postal_service, "mapear_estado", return_value="devuelto"),
    ):
        res = await svc.aplicar_autenticado(
            payload, actor="postal:4-72", ocurrido_at=None, diferido_id=None
        )
        await svc.enviar_pendientes()
    assert res.status == "applied"
    assert vistos == [True]
    assert encolar.await_args.kwargs["event_type"] == "document.envio.devuelto"


@pytest.mark.asyncio
async def test_con_redis_caido_el_callback_responde_y_el_evento_queda_pendiente() -> None:
    """El camino rápido nunca lanza: la fila del outbox queda para el relé del barrido."""
    conn = _make_mock_conn()
    redis = _make_fake_redis()
    redis.xadd.side_effect = ConnectionError("redis caído")
    svc = PostalService(conn, redis=redis, tenant_slug="demo")
    svc._por_enviar.append(
        {
            "stream": "orpycamcp.document.events",
            "event": {"event_id": "e1", "event_type": "document.envio.devuelto"},
        }
    )
    with patch.object(postal_service.outbox.events, "publish", AsyncMock(side_effect=ConnectionError("x"))):
        await svc.enviar_pendientes()  # no lanza
    assert svc._por_enviar == []
    # No se marcó `enviado_at`: el UPDATE solo ocurre tras publicar con éxito.
    conn.execute.assert_not_awaited()
