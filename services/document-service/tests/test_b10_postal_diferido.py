# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 (ítem 4): callback postal con la entidad SUSPENDIDA — se difiere, no se pierde.

Antes: firma válida => 503 con `Retry-After: 3600` fijo; si la suspensión duraba más que
los reintentos del operador, la devolución (que abre un plazo) se perdía. Ahora el cuerpo
firmado se encola (migración tenant 048) y el barrido (fase 5) lo aplica al reactivarse.

Mutaciones verificadas al entregar:
- escribir el asiento de recepción FUERA del `async with transaction()` de `diferir` →
  cae `test_la_recepcion_se_asienta_en_la_misma_tx_que_la_cola`;
- quitar el `if ... > settings.postal_diferidos_max` → cae `test_el_tope_de_pendientes_devuelve_503`;
- insertar el diferido ANTES de validar el esquema/mapeo → cae
  `test_firma_valida_con_estado_no_mapeable_es_422_y_no_encola`.
El drenaje (orden, ts de recepción, sin re-verificar HMAC, atomicidad) se prueba contra
Postgres real en `tests/integration/test_b10_postal_diferido_sql.py`.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orpycamcp_common.tenant import AtencionPublica

from app.core.config import settings
from app.services import postal_service
from tests.test_postal_callback import (  # noqa: F401  (fixture reutilizada)
    _body,
    _cred,
    _hdr,
    _ship_row,
    _url,
    callback_ctx,
)

_SUSPENDIDA = AtencionPublica.SOLO_LECTURA


def _suspendida():
    return patch("app.core.database.atencion_publica", new=AsyncMock(return_value=_SUSPENDIDA))


@pytest.mark.asyncio
async def test_suspendida_firma_valida_202_deferred_y_asiento(callback_ctx) -> None:  # noqa: F811
    client, conn, redis = callback_ctx
    body = _body(event_id="evt-d1")
    envio = _ship_row(estado="registrado")
    conn.fetchrow.side_effect = [_cred(), envio]
    conn.fetchval.side_effect = [uuid.uuid4(), 1]  # id en cola, pendientes
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()) as asiento:
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 202
    data = resp.json()
    assert data == {"status": "deferred", "shipment_id": str(envio["id"]), "estado": "registrado"}
    asiento.assert_awaited_once()
    kw = asiento.await_args.kwargs
    assert kw["action"] == "document.envio_callback_diferido"
    assert kw["object_ref"] == envio["guia"] and kw["actor"] == "postal:4-72"
    assert kw["payload"]["event_id"] == "evt-d1" and len(kw["payload"]["cuerpo_sha256"]) == 64
    redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspendida_firma_mala_404_neutro_y_no_encola(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    body = _body(event_id="evt-d2")
    conn.fetchrow.side_effect = [_cred()]
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()) as asiento:
        resp = await client.post(_url(), content=body, headers=_hdr(body, secret="otro"))
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"error": "not_found", "detail": "No encontrado", "status": 404}
    asiento.assert_not_awaited()
    conn.fetchval.assert_not_awaited()  # ni siquiera se intentó el INSERT de la cola


@pytest.mark.asyncio
async def test_diferido_duplicado_202_sin_segundo_asiento(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    body = _body(event_id="evt-d3")
    conn.fetchrow.side_effect = [_cred(), _ship_row(estado="registrado")]
    conn.fetchval.side_effect = [None]  # ON CONFLICT DO NOTHING: ya estaba en cola
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()) as asiento:
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 202 and resp.json()["status"] == "deferred"
    asiento.assert_not_awaited()


@pytest.mark.asyncio
async def test_firma_valida_con_estado_no_mapeable_es_422_y_no_encola(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    body = _body(estado_operador="ESTADO_INVENTADO", event_id="evt-d4")
    conn.fetchrow.side_effect = [_cred()]
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()) as asiento:
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "estado_operador_no_mapeable"
    asiento.assert_not_awaited()
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_firma_valida_con_guia_desconocida_es_404_y_no_encola(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    body = _body(event_id="evt-d5")
    conn.fetchrow.side_effect = [_cred(), None]
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()):
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "envio_no_encontrado"
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_tope_de_pendientes_devuelve_503(callback_ctx, monkeypatch) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    monkeypatch.setattr(settings, "postal_diferidos_max", 2)
    body = _body(event_id="evt-d6")
    conn.fetchrow.side_effect = [_cred(), _ship_row(estado="registrado")]
    conn.fetchval.side_effect = [uuid.uuid4(), 3]  # insertó, y ya hay 3 > 2
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()) as asiento:
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "canal_no_disponible"
    assert resp.headers["retry-after"]
    asiento.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_recepcion_se_asienta_en_la_misma_tx_que_la_cola(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    abierta = {"v": 0}

    class _CM:
        async def __aenter__(self):
            abierta["v"] += 1

        async def __aexit__(self, *exc):
            abierta["v"] -= 1
            return False

    conn.transaction = MagicMock(side_effect=lambda: _CM())
    body = _body(event_id="evt-d7")
    conn.fetchrow.side_effect = [_cred(), _ship_row(estado="registrado")]
    conn.fetchval.side_effect = [uuid.uuid4(), 1]
    vistos: list[bool] = []

    async def _append(*a, **k):
        vistos.append(abierta["v"] > 0)

    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock(side_effect=_append)):
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 202
    assert vistos == [True]


@pytest.mark.asyncio
async def test_con_la_entidad_activa_todo_sigue_igual(callback_ctx) -> None:  # noqa: F811
    """Control: sin suspensión el callback se aplica (200), no se difiere."""
    client, conn, _ = callback_ctx
    body = _body(event_id="evt-d8")
    conn.fetchval.side_effect = [uuid.uuid4(), None]
    conn.fetchrow.side_effect = [_cred(), _ship_row(estado="registrado"), _ship_row(estado="en_transito")]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200 and resp.json()["status"] == "applied"


# ---------------------------------------------------------------------------
# Auditoría B10 (SEG #8): mismo tope de cuerpo con la entidad activa; conteo bajo lock
# ---------------------------------------------------------------------------
def _cuerpo_grande() -> bytes:
    """JSON válido de > 64 KiB (espacios finales): el HMAC se calcula sobre estos bytes."""
    return _body(event_id="evt-big") + b" " * (postal_service._MAX_CUERPO_DIFERIDO + 1)


@pytest.mark.asyncio
async def test_413_tambien_con_la_entidad_activa_y_tras_autenticar(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    body = _cuerpo_grande()
    conn.fetchrow.side_effect = [_cred(), _ship_row(estado="registrado")]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 413
    assert resp.json()["detail"]["error"] == "cuerpo_demasiado_grande"


@pytest.mark.asyncio
async def test_413_no_es_oraculo_para_un_anonimo(callback_ctx) -> None:  # noqa: F811
    """Con firma mala el 413 no se alcanza: sigue siendo el 401/404 de la autenticación."""
    client, conn, _ = callback_ctx
    body = _cuerpo_grande()
    conn.fetchrow.side_effect = [_cred()]
    resp = await client.post(_url(), content=body, headers=_hdr(body, secret="otro"))
    assert resp.status_code != 413


@pytest.mark.asyncio
async def test_el_conteo_del_tope_se_hace_bajo_un_lock_por_tenant(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    orden: list[str] = []

    async def _execute(sql, *a, **k):
        orden.append("lock" if "pg_advisory_xact_lock" in sql else "otro")

    async def _fetchval(sql, *a, **k):
        orden.append("count" if "count(*)" in sql else "insert")
        return 1 if "count(*)" in sql else uuid.uuid4()

    conn.execute = AsyncMock(side_effect=_execute)
    conn.fetchval = AsyncMock(side_effect=_fetchval)
    body = _body(event_id="evt-lock")
    conn.fetchrow.side_effect = [_cred(), _ship_row(estado="registrado")]
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()):
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 202
    assert orden.index("lock") < orden.index("count"), orden


# ---------------------------------------------------------------------------
# R2-5: la fecha del operador va al asiento y se acota
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_el_asiento_lleva_la_fecha_del_operador_y_la_recepcion(callback_ctx) -> None:  # noqa: F811
    from datetime import UTC, datetime, timedelta

    client, conn, _ = callback_ctx
    envio = _ship_row(estado="registrado", created_at=datetime.now(UTC) - timedelta(days=1))
    hecho = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)
    body = _body(event_id="evt-r25", ocurrido_at=hecho.isoformat())
    conn.fetchval.side_effect = [uuid.uuid4(), None]
    conn.fetchrow.side_effect = [_cred(), envio, _ship_row(estado="en_transito")]
    with patch.object(postal_service.audit, "append", AsyncMock()) as asiento:
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200, resp.text
    payload = asiento.await_args.kwargs["payload"]
    assert payload["ocurrido_operador_at"] == hecho.isoformat()
    assert payload["recibido_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [timedelta(minutes=30), -timedelta(days=30)])
async def test_ocurrido_at_futuro_o_anterior_al_envio_es_422(callback_ctx, delta) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    envio = _ship_row(estado="registrado", created_at=datetime.now(UTC) - timedelta(days=1))
    body = _body(event_id="evt-r25b", ocurrido_at=(datetime.now(UTC) + delta).isoformat())
    conn.fetchrow.side_effect = [_cred(), envio]
    with patch.object(postal_service.audit, "append", AsyncMock()) as asiento:
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ocurrido_at_inverosimil"
    asiento.assert_not_awaited()


@pytest.mark.asyncio
async def test_ocurrido_at_inverosimil_tambien_se_rechaza_al_diferir(callback_ctx) -> None:  # noqa: F811
    client, conn, _ = callback_ctx
    body = _body(event_id="evt-r25c", ocurrido_at=(datetime.now(UTC) + timedelta(hours=3)).isoformat())
    conn.fetchrow.side_effect = [_cred(), _ship_row(estado="registrado", created_at=datetime.now(UTC) - timedelta(days=1))]
    with _suspendida(), patch.object(postal_service.audit, "append", AsyncMock()):
        resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 422
    conn.fetchval.assert_not_awaited()
