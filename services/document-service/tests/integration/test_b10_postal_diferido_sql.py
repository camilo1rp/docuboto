# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 (ítem 4 y atomicidad del ítem 1) contra Postgres REAL: la cola de callbacks postales
diferidos (migración tenant 048) y su drenaje en la fase 5 del barrido.

Lo que un doble no puede probar: el orden de aplicación, el `ts` del historial, la
atomicidad aplicación+cierre+asiento (el rollback lo produce Postgres), el CHECK de cierre y
que el barrido solo vea tenants activos.

Mutaciones verificadas al entregar:
- usar `now()` en lugar de `recibido_at` (`ts=ocurrido_at` → None) → cae
  `test_el_drenaje_aplica_en_orden_con_el_ts_de_recepcion`;
- quitar `ORDER BY recibido_at, id` de `siguiente_diferido` → cae el mismo test (el
  `entregado` insertado primero se aplicaría desde `registrado` y quedaría sin efecto);
- volver a verificar el HMAC al drenar → cae `test_el_drenaje_no_reverifica_el_hmac_tras_rotar_el_secreto`;
- cerrar el diferido antes de aplicar y sin la tx externa → cae `test_el_drenaje_es_atomico`;
- escribir el asiento de recepción fuera de la tx de `diferir` → cae
  `test_si_falla_el_asiento_de_recepcion_no_queda_nada_en_cola`;
- quitar la comparación con `postal_diferidos_max` → cae `test_el_tope_de_pendientes_devuelve_503_y_no_encola`;
- quitar el `_encolar_estado` de `aplicar_autenticado` → cae el chequeo de `eventos_salientes`.

Auditoría B10 (H6/H7, SEG #7):
- quitar la comparación `sha256(cuerpo) != cuerpo_sha256` del drenaje → cae
  `test_una_fila_reescrita_no_se_aplica_huella_no_coincide`;
- quitar el tope de intentos (`intentos >= postal_diferido_max_intentos`) → cae
  `test_una_fila_envenenada_se_descarta_con_asiento_y_no_bloquea_la_cola`;
- no pasar `ocurrido_operador_at` al UPDATE/INSERT del historial → cae
  `test_la_fecha_del_operador_se_guarda_aparte_de_la_de_recepcion`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.database import tenant_schema
from app.repositories.postal_repository import PostalRepository
from app.schemas.postal import PostalCallbackPayload
from app.services import barrido, postal_service
from app.services.postal_service import PostalService

pytestmark = pytest.mark.integration

_GUIA = "C472-B10DIF01"


async def _envio(conn: asyncpg.Connection, guia: str = _GUIA) -> uuid.UUID:
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code) "
        "VALUES ($1, 'S', 2026, 1, 'asunto', 'DEMO') RETURNING id",
        f"2026-DEMO-S-{uuid.uuid4().hex[:6]}",
    )
    row = await PostalRepository(conn).insert(
        radicado_id=rid, operador="4-72", guia=guia, destinatario="X", direccion="Y", max_clearance=3
    )
    assert row is not None
    return row["id"]


def _cuerpo(estado: str, event_id: str, guia: str = _GUIA, extra: dict | None = None) -> bytes:
    return json.dumps(
        {"operador": "4-72", "guia": guia, "estado_operador": estado, "event_id": event_id, **(extra or {})}
    ).encode()


async def _encolar_a_mano(
    conn: asyncpg.Connection,
    *,
    fila_id: uuid.UUID,
    estado: str,
    event_id: str,
    recibido_at: datetime,
    guia: str = _GUIA,
    sha256: str | None = None,
    extra: dict | None = None,
) -> None:
    cuerpo = _cuerpo(estado, event_id, guia, extra)
    await conn.execute(
        "INSERT INTO postal_callback_diferido "
        "(id, operador, event_id, guia, cuerpo, cuerpo_sha256, firma_hmac, estado_interno, recibido_at) "
        "VALUES ($1, '4-72', $2, $3, $4, $6, 'sha256=x', 'en_transito', $5)",
        fila_id,
        event_id,
        guia,
        cuerpo,
        recibido_at,
        sha256 or hashlib.sha256(cuerpo).hexdigest(),
    )


async def _en(tenant_ctx: tuple[str, asyncpg.Connection]) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    return slug, conn


async def _asientos(conn: asyncpg.Connection, slug: str, accion: str) -> int:
    return int(
        await conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2", slug, accion
        )
    )


@pytest.mark.asyncio
async def test_el_drenaje_aplica_en_orden_con_el_ts_de_recepcion(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    base = datetime.now(UTC) - timedelta(hours=3)
    t_transito, t_entregado = base, base + timedelta(hours=1)
    # Insertados en el orden CONTRARIO al de recepción y con los id en orden inverso:
    # sin `ORDER BY recibido_at, id` el `entregado` se aplicaría primero y no tendría efecto.
    await _encolar_a_mano(
        conn, fila_id=uuid.UUID(int=1), estado="ENTREGADO", event_id="e-2", recibido_at=t_entregado
    )
    await _encolar_a_mano(
        conn, fila_id=uuid.UUID(int=2**100), estado="EN_TRANSITO", event_id="e-1", recibido_at=t_transito
    )
    cerrados = await barrido.drenar_callbacks_diferidos(conn, slug, None)
    assert cerrados == 2
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", sid) == "entregado"
    ts = {
        r["estado"]: r["ts"]
        for r in await conn.fetch("SELECT estado, ts FROM postal_shipment_event WHERE shipment_id = $1", sid)
    }
    assert ts["en_transito"] == t_transito and ts["entregado"] == t_entregado, (
        "el historial lleva el instante de RECEPCIÓN, no el del drenaje"
    )
    cola = await conn.fetch("SELECT estado, resultado, procesado_at FROM postal_callback_diferido")
    assert {(r["estado"], r["resultado"]) for r in cola} == {("procesado", "applied")}
    assert all(r["procesado_at"] is not None for r in cola)
    tipos = [r["tipo"] for r in await conn.fetch("SELECT tipo FROM eventos_salientes ORDER BY id")]
    assert tipos == ["document.envio.en_transito", "document.envio.entregado"]
    assert await _asientos(conn, slug, "document.envio_estado_operador") == 2


@pytest.mark.asyncio
async def test_el_drenaje_no_reverifica_el_hmac_tras_rotar_el_secreto(tenant_ctx) -> None:
    """Sin ninguna credencial del operador en el tenant (el secreto rotó o se retiró durante la
    suspensión) el diferido igual se aplica: su firma ya se verificó al recibirlo."""
    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    assert await conn.fetchval("SELECT count(*) FROM postal_operator_credential") == 0
    await _encolar_a_mano(
        conn, fila_id=uuid.uuid4(), estado="EN_TRANSITO", event_id="e-rot", recibido_at=datetime.now(UTC)
    )
    assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 1
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", sid) == "en_transito"


@pytest.mark.asyncio
async def test_el_drenaje_es_atomico(tenant_ctx, monkeypatch) -> None:
    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    fila = uuid.uuid4()
    await _encolar_a_mano(
        conn, fila_id=fila, estado="EN_TRANSITO", event_id="e-at", recibido_at=datetime.now(UTC)
    )
    monkeypatch.setattr(postal_service.audit, "append", AsyncMock(side_effect=RuntimeError("cadena caída")))
    assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 0
    row = await conn.fetchrow(
        "SELECT estado, intentos, ultimo_error, procesado_at FROM postal_callback_diferido WHERE id = $1", fila
    )
    assert row["estado"] == "pendiente" and row["procesado_at"] is None
    assert row["intentos"] == 1 and "cadena caída" in row["ultimo_error"]
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", sid) == "registrado"
    assert await conn.fetchval("SELECT count(*) FROM postal_webhook_delivery") == 0
    assert await conn.fetchval("SELECT count(*) FROM eventos_salientes") == 0
    assert await conn.fetchval("SELECT count(*) FROM postal_shipment_event WHERE estado = 'en_transito'") == 0


@pytest.mark.asyncio
async def test_si_falla_el_cierre_se_revierte_tambien_la_aplicacion(tenant_ctx, monkeypatch) -> None:
    """Aplicación y cierre de la fila son UN acto: si el cierre falla, el envío no cambia."""
    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    fila = uuid.uuid4()
    await _encolar_a_mano(
        conn, fila_id=fila, estado="EN_TRANSITO", event_id="e-cl", recibido_at=datetime.now(UTC)
    )
    monkeypatch.setattr(
        PostalRepository, "cerrar_diferido", AsyncMock(side_effect=RuntimeError("disco lleno"))
    )
    assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 0
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", sid) == "registrado"
    assert await conn.fetchval("SELECT count(*) FROM postal_webhook_delivery") == 0
    assert await conn.fetchval("SELECT count(*) FROM eventos_salientes") == 0
    assert await conn.fetchval("SELECT estado FROM postal_callback_diferido WHERE id = $1", fila) == "pendiente"


@pytest.mark.asyncio
async def test_una_guia_que_desaparecio_se_descarta_con_asiento(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    await _envio(conn)
    fila = uuid.uuid4()
    await _encolar_a_mano(
        conn, fila_id=fila, estado="EN_TRANSITO", event_id="e-x", recibido_at=datetime.now(UTC), guia="C472-NOEXISTE"
    )
    assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 1
    row = await conn.fetchrow("SELECT estado, resultado FROM postal_callback_diferido WHERE id = $1", fila)
    assert (row["estado"], row["resultado"]) == ("descartado", "envio_no_encontrado")
    assert await _asientos(conn, slug, "document.envio_callback_diferido_descartado") == 1


@pytest.mark.asyncio
async def test_el_cierre_de_la_cola_exige_procesado_at(tenant_ctx) -> None:
    """CHECK `postal_diferido_cierre`: pendiente <=> sin `procesado_at`."""
    _, conn = await _en(tenant_ctx)
    await _envio(conn)
    fila = uuid.uuid4()
    await _encolar_a_mano(conn, fila_id=fila, estado="EN_TRANSITO", event_id="e-ck", recibido_at=datetime.now(UTC))
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("UPDATE postal_callback_diferido SET estado = 'procesado' WHERE id = $1", fila)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("UPDATE postal_callback_diferido SET procesado_at = now() WHERE id = $1", fila)


@pytest.mark.asyncio
async def test_diferir_guarda_el_cuerpo_asienta_y_es_idempotente(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    await _envio(conn)
    svc = PostalService(conn, tenant_slug=slug)
    cuerpo = _cuerpo("EN_TRANSITO", "e-dif")
    payload = PostalCallbackPayload.model_validate_json(cuerpo)
    r1 = await svc.diferir(payload, cuerpo, "sha256=abc")
    r2 = await svc.diferir(payload, cuerpo, "sha256=abc")
    assert r1.status == r2.status == "deferred" and r1.estado == "registrado"
    fila = await conn.fetchrow("SELECT cuerpo, firma_hmac, estado, estado_interno FROM postal_callback_diferido")
    assert bytes(fila["cuerpo"]) == cuerpo and fila["firma_hmac"] == "sha256=abc"
    assert (fila["estado"], fila["estado_interno"]) == ("pendiente", "en_transito")
    assert await conn.fetchval("SELECT count(*) FROM postal_callback_diferido") == 1
    assert await _asientos(conn, slug, "document.envio_callback_diferido") == 1, "el reenvío no duplica el asiento"


@pytest.mark.asyncio
async def test_si_falla_el_asiento_de_recepcion_no_queda_nada_en_cola(tenant_ctx, monkeypatch) -> None:
    slug, conn = await _en(tenant_ctx)
    await _envio(conn)
    monkeypatch.setattr(postal_service.audit, "append", AsyncMock(side_effect=RuntimeError("cadena caída")))
    cuerpo = _cuerpo("EN_TRANSITO", "e-fa")
    with pytest.raises(RuntimeError):
        await PostalService(conn, tenant_slug=slug).diferir(
            PostalCallbackPayload.model_validate_json(cuerpo), cuerpo, "sha256=abc"
        )
    assert await conn.fetchval("SELECT count(*) FROM postal_callback_diferido") == 0


@pytest.mark.asyncio
async def test_el_tope_de_pendientes_devuelve_503_y_no_encola(tenant_ctx, monkeypatch) -> None:
    slug, conn = await _en(tenant_ctx)
    await _envio(conn)
    monkeypatch.setattr(settings, "postal_diferidos_max", 1)
    svc = PostalService(conn, tenant_slug=slug)
    for n, esperado in ((1, "deferred"), (2, None)):
        cuerpo = _cuerpo("EN_TRANSITO", f"e-top-{n}")
        payload = PostalCallbackPayload.model_validate_json(cuerpo)
        if esperado:
            assert (await svc.diferir(payload, cuerpo, "sha256=x")).status == esperado
        else:
            with pytest.raises(HTTPException) as exc:
                await svc.diferir(payload, cuerpo, "sha256=x")
            assert exc.value.status_code == 503 and exc.value.headers["Retry-After"]
    assert await conn.fetchval("SELECT count(*) FROM postal_callback_diferido") == 1
    assert await _asientos(conn, slug, "document.envio_callback_diferido") == 1


@pytest.mark.asyncio
async def test_la_devolucion_manual_deja_el_evento_en_el_outbox(tenant_ctx) -> None:
    """El hallazgo del ítem 1: la transición MANUAL no emitía nada."""
    from app.schemas.postal import ShipmentStatusUpdate

    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    svc = PostalService(conn, tenant_slug=slug)
    await svc.update_estado(sid, ShipmentStatusUpdate(estado="en_transito"), 3, uuid.uuid4())
    await svc.update_estado(sid, ShipmentStatusUpdate(estado="devuelto", motivo="Dirección errada"), 3, uuid.uuid4())
    filas = await conn.fetch("SELECT tipo, dedup_key, payload, enviado_at FROM eventos_salientes ORDER BY id")
    assert [f["tipo"] for f in filas] == ["document.envio.en_transito", "document.envio.devuelto"]
    assert filas[1]["dedup_key"] == f"{slug}:{_GUIA}:devuelto" and filas[1]["enviado_at"] is None
    assert json.loads(filas[1]["payload"])["guia"] == _GUIA


@pytest.mark.asyncio
async def test_si_falla_el_asiento_de_la_transicion_manual_no_queda_evento(tenant_ctx, monkeypatch) -> None:
    from app.schemas.postal import ShipmentStatusUpdate

    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    monkeypatch.setattr(postal_service.audit, "append", AsyncMock(side_effect=RuntimeError("cadena caída")))
    with pytest.raises(RuntimeError):
        await PostalService(conn, tenant_slug=slug).update_estado(
            sid, ShipmentStatusUpdate(estado="en_transito"), 3, uuid.uuid4()
        )
    assert await conn.fetchval("SELECT count(*) FROM eventos_salientes") == 0
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", sid) == "registrado"


class _Pool:
    """Lo mínimo de un pool para `tenants_activos`: `acquire()` cede la conexión."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        yield self._conn


@pytest.mark.asyncio
async def test_el_drenaje_solo_ve_tenants_activos(tenant_ctx) -> None:
    """El barrido recorre `tenants_activos`: una entidad suspendida no se drena, y al
    reactivarla vuelve a aparecer (ese es el disparador del drenaje)."""
    slug, conn = tenant_ctx
    codigo = f"IT{slug.replace('itest-', '')[:6].upper()}"
    await conn.execute(
        "INSERT INTO public.tenants (id, slug, name, code, status) VALUES ($1, $2, $3, $4, 'suspended')",
        uuid.uuid4(), slug, f"Entidad {slug}", codigo,
    )
    try:
        assert slug not in await barrido.tenants_activos(_Pool(conn))
        await conn.execute("UPDATE public.tenants SET status = 'active' WHERE slug = $1", slug)
        assert slug in await barrido.tenants_activos(_Pool(conn))
    finally:
        await conn.execute("DELETE FROM public.tenants WHERE slug = $1", slug)


@pytest.mark.asyncio
async def test_una_fila_reescrita_no_se_aplica_huella_no_coincide(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    fila = uuid.uuid4()
    await _encolar_a_mano(
        conn, fila_id=fila, estado="EN_TRANSITO", event_id="e-sha", recibido_at=datetime.now(UTC),
        sha256="b" * 64,
    )
    assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 1
    row = await conn.fetchrow("SELECT estado, resultado FROM postal_callback_diferido WHERE id = $1", fila)
    assert (row["estado"], row["resultado"]) == ("descartado", "huella_no_coincide")
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", sid) == "registrado"
    assert await _asientos(conn, slug, "document.envio_callback_diferido_descartado") == 1


@pytest.mark.asyncio
async def test_una_fila_envenenada_se_descarta_con_asiento_y_no_bloquea_la_cola(
    tenant_ctx, monkeypatch
) -> None:
    slug, conn = await _en(tenant_ctx)
    await _envio(conn, "C472-VENENO")
    sano = await _envio(conn, "C472-SANA")
    base = datetime.now(UTC) - timedelta(hours=2)
    malo, bueno = uuid.uuid4(), uuid.uuid4()
    await _encolar_a_mano(
        conn, fila_id=malo, estado="EN_TRANSITO", event_id="e-mal", recibido_at=base, guia="C472-VENENO"
    )
    await _encolar_a_mano(
        conn, fila_id=bueno, estado="EN_TRANSITO", event_id="e-bien", recibido_at=base + timedelta(hours=1),
        guia="C472-SANA",
    )
    original = PostalService.aplicar_autenticado

    async def _aplicar(self, payload, **kw):
        if payload.guia == "C472-VENENO":
            raise RuntimeError("dato inconsistente")
        return await original(self, payload, **kw)

    monkeypatch.setattr(PostalService, "aplicar_autenticado", _aplicar)
    monkeypatch.setattr(settings, "postal_diferido_max_intentos", 3)
    # Pasadas 1 y 2: la fila mala sigue pendiente y CORTA el lote (orden preservado).
    for esperado_intentos in (1, 2):
        assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 0
        assert await conn.fetchval("SELECT intentos FROM postal_callback_diferido WHERE id = $1", malo) == esperado_intentos
        assert await conn.fetchval("SELECT estado FROM postal_callback_diferido WHERE id = $1", bueno) == "pendiente"
    # Pasada 3: alcanza el tope -> descartada con asiento y el drenaje SIGUE con la buena.
    assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 2
    row = await conn.fetchrow("SELECT estado, resultado FROM postal_callback_diferido WHERE id = $1", malo)
    assert (row["estado"], row["resultado"]) == ("descartado", "error_persistente")
    assert await conn.fetchval("SELECT estado FROM postal_callback_diferido WHERE id = $1", bueno) == "procesado"
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", sano) == "en_transito"
    assert await _asientos(conn, slug, "document.envio_callback_diferido_descartado") == 1


@pytest.mark.asyncio
async def test_la_fecha_del_operador_se_guarda_aparte_de_la_de_recepcion(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    sid = await _envio(conn)
    recibido = datetime.now(UTC) - timedelta(hours=1)
    hecho = (recibido - timedelta(minutes=5)).replace(microsecond=0)
    await conn.execute("UPDATE postal_shipment SET created_at = now() - interval '30 days' WHERE id = $1", sid)
    await _encolar_a_mano(
        conn, fila_id=uuid.uuid4(), estado="EN_TRANSITO", event_id="e-oc", recibido_at=recibido,
        extra={"ocurrido_at": hecho.isoformat()},
    )
    assert await barrido.drenar_callbacks_diferidos(conn, slug, None) == 1
    ev = await conn.fetchrow(
        "SELECT ts, ocurrido_operador_at FROM postal_shipment_event WHERE shipment_id = $1 AND estado = 'en_transito'",
        sid,
    )
    assert ev["ts"] == recibido and ev["ocurrido_operador_at"] == hecho
    sin = await conn.fetchval(
        "SELECT ocurrido_operador_at FROM postal_shipment_event WHERE shipment_id = $1 AND estado = 'registrado'", sid
    )
    assert sin is None
