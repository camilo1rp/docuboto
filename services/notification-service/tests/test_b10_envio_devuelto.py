# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 (ítem 1, consumidor): aviso por correo de `document.envio.devuelto`.

El consumidor ya reclamaba el evento y disparaba webhooks, pero nadie avisaba a la
dependencia. Ahora avisa a la dependencia productora y a quien radicó, con el gate de
clearance en el WHERE por destinatario (la consulta real, contra Postgres, está en
`tests/integration/test_b10_envio_devuelto_gate_sql.py`).

Mutaciones verificadas al entregar:
- clearance 3 fijo para el buzón → cae `test_reservado_al_buzon_sale_sin_contenido`;
- reclamar el evento ANTES de resolver el contexto → cae
  `test_error_de_bd_al_resolver_no_reclama_el_evento`;
- llamar al constructor sin pasar por `construir_envios_documento` en el reproceso de la DLQ →
  cae `test_replay_dlq_usa_el_mismo_nucleo`;
- quitar `sensible=` del correo con contenido → cae el test de `sensible`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import devolucion, event_consumer
from app.services.devolucion import (
    ContenidoDevolucion,
    ContextoDevolucion,
    DestinatarioDevolucion,
    build_devolucion_notifications,
    resolver_contexto_devolucion,
)

SID = uuid.uuid4()
RID = uuid.uuid4()
AUTOR = uuid.uuid4()
TENEDOR = uuid.uuid4()
TRACKING = "2026-DEMO-S-000077"
ASUNTO = "Respuesta a la solicitud de la familia Gómez"
GUIA = "C472-DEVUELTA1"


class _ConnFalsa:
    """Responde por la forma de la consulta; el gate lo emula como lo hace el `WHERE`."""

    def __init__(
        self,
        *,
        nivel: int,
        clearance_autor: int = 1,
        autor_activo: bool = True,
        con_autor: bool = True,
        ubicacion: tuple[int, str] = (902, "Oficina Jurídica"),
        tenedor: uuid.UUID | None = None,
        responde_a: uuid.UUID | None = None,
        entrada: dict | None = None,
    ):
        self.ubicacion = ubicacion
        self.tenedor = tenedor
        self.responde_a = responde_a
        self.entrada = entrada or {}
        self.nivel = nivel
        self.clearance_autor = clearance_autor
        self.autor_activo = autor_activo
        self.con_autor = con_autor
        self.contenido_pedido: list[int] = []

    async def fetchrow(self, sql: str, *args):
        if "FROM postal_shipment s JOIN radicados r" in sql:
            clearance = args[1]
            self.contenido_pedido.append(clearance)
            if self.nivel > clearance:
                return None
            return {
                "tracking_number": TRACKING,
                "subject": ASUNTO,
                "nivel_seguridad": self.nivel,
                "destinatario": "Familia Gómez",
                "direccion": "Calle 9 # 8-7",
                "motivo_devolucion": "Dirección errada",
            }
        if "FROM postal_shipment WHERE id" in sql:
            return {"radicado_id": RID, "operador": "4-72", "guia": GUIA}
        if "FROM radicados WHERE id" in sql:
            if args[0] == self.responde_a:  # la ENTRADA a la que responde
                return {"ubicacion_dept_code": 100, "ubicacion_dept": "Atención al Ciudadano",
                        "flujo_usuario": None, **self.entrada}
            return {
                "origin_dept_code": 902,
                "origin_dept": "Oficina Jurídica",
                "registered_by": AUTOR if self.con_autor else None,
                "ubicacion_dept_code": self.ubicacion[0],
                "ubicacion_dept": self.ubicacion[1],
                "flujo_usuario": self.tenedor,
                "responde_a": self.responde_a,
            }
        if "SELECT email, is_active FROM auth_users" in sql:
            if args[0] == TENEDOR:
                return {"email": "tenedor@entidad.gov.co", "is_active": True}
            if args[0] == AUTOR:
                return {"email": "autora@entidad.gov.co", "is_active": self.autor_activo}
            return {"email": "otro@entidad.gov.co", "is_active": True}
        if "u.is_root" in sql:  # resolve_caller_clearance
            return {"is_root": False, "max_level": self.clearance_autor}
        raise AssertionError(f"consulta inesperada: {sql[:80]}")


def _sobre(**payload) -> dict:
    return {
        "event_id": "evt-dev-1",
        "event_type": "document.envio.devuelto",
        "tenant_slug": "demo",
        "payload": {"shipment_id": str(SID), "radicado_id": str(RID), "operador": "4-72", "guia": GUIA, **payload},
    }


async def _envios(conn: _ConnFalsa):
    ctx = await resolver_contexto_devolucion(conn, SID)  # type: ignore[arg-type]
    assert ctx is not None
    return build_devolucion_notifications(_sobre(), contexto=ctx)


@pytest.mark.asyncio
async def test_devuelto_avisa_a_dependencia_productora_y_a_quien_radico() -> None:
    envios = await _envios(_ConnFalsa(nivel=1))
    assert [e.recipient_email for e in envios] == ["dependencia-902@orpycamcp.local", "autora@entidad.gov.co"]
    for e in envios:
        assert TRACKING in e.subject and "Familia Gómez" in e.body and "Dirección errada" in e.body
        assert GUIA in e.body and e.sensible is False


@pytest.mark.asyncio
async def test_avisa_tambien_al_tenedor_actual_buzon_y_persona() -> None:
    """H8: el radicado ya no está donde nació; quien tiene el trámite también se entera."""
    envios = await _envios(_ConnFalsa(nivel=1, ubicacion=(310, "Gestión Jurídica"), tenedor=TENEDOR))
    assert [e.recipient_email for e in envios] == [
        "dependencia-902@orpycamcp.local",
        "autora@entidad.gov.co",
        "dependencia-310@orpycamcp.local",
        "tenedor@entidad.gov.co",
    ]


@pytest.mark.asyncio
async def test_si_responde_a_una_entrada_avisa_a_su_responsable() -> None:
    entrada = uuid.uuid4()
    envios = await _envios(
        _ConnFalsa(nivel=1, responde_a=entrada, entrada={"flujo_usuario": uuid.uuid4()})
    )
    correos = [e.recipient_email for e in envios]
    assert "dependencia-100@orpycamcp.local" in correos
    assert "otro@entidad.gov.co" in correos


@pytest.mark.asyncio
async def test_el_gate_aplica_a_cada_destinatario_nuevo() -> None:
    """Radicado nivel 3: el buzón del tenedor (PUBLICA) y el de la entrada salen SIN contenido,
    y el tenedor persona (clearance 1) también."""
    conn = _ConnFalsa(nivel=3, clearance_autor=1, ubicacion=(310, "X"), tenedor=TENEDOR, responde_a=uuid.uuid4())
    envios = await _envios(conn)
    assert len(envios) >= 5
    for e in envios:
        assert TRACKING not in e.body and ASUNTO not in e.body and "Familia Gómez" not in e.body
    assert all(c == 1 for c in conn.contenido_pedido)


@pytest.mark.asyncio
async def test_reservado_al_buzon_sale_sin_contenido() -> None:
    """Radicado nivel 2: el buzón (PUBLICA) no recibe asunto, número, destinatario, dirección ni motivo."""
    conn = _ConnFalsa(nivel=2, clearance_autor=2)
    envios = await _envios(conn)
    buzon = envios[0]
    assert buzon.recipient_email == "dependencia-902@orpycamcp.local"
    for secreto in (TRACKING, ASUNTO, "Familia Gómez", "Calle 9", "Dirección errada"):
        assert secreto not in buzon.subject and secreto not in buzon.body
    assert "4-72" in buzon.body and GUIA in buzon.body and "OrpycaMCP" in buzon.body
    assert conn.contenido_pedido[0] == 1, "el buzón se califica como PUBLICA"


@pytest.mark.asyncio
async def test_reservado_a_quien_radico_con_clearance_2_sale_completo_y_sensible() -> None:
    envios = await _envios(_ConnFalsa(nivel=2, clearance_autor=2))
    autora = envios[1]
    assert autora.recipient_email == "autora@entidad.gov.co"
    assert autora.sensible is True, "el cuerpo de un radicado reservado no se persiste"
    assert "Familia Gómez" in autora.body and TRACKING in autora.body
    assert TRACKING not in autora.subject, "el asunto sí se persiste: no lleva el número de un reservado"


@pytest.mark.asyncio
async def test_quien_radico_sin_clearance_suficiente_sale_sin_contenido() -> None:
    envios = await _envios(_ConnFalsa(nivel=3, clearance_autor=2))
    assert all(TRACKING not in e.body and "Familia Gómez" not in e.body for e in envios)
    assert all(e.sensible is False for e in envios)  # no hay contenido que proteger


@pytest.mark.asyncio
async def test_autor_inactivo_o_ausente_no_recibe_nada() -> None:
    assert len(await _envios(_ConnFalsa(nivel=1, autor_activo=False))) == 1
    assert len(await _envios(_ConnFalsa(nivel=1, con_autor=False))) == 1


@pytest.mark.asyncio
async def test_una_misma_bandeja_no_recibe_dos_correos() -> None:
    conn = _ConnFalsa(nivel=2, clearance_autor=3)
    original = conn.fetchrow

    async def _mismo_correo(sql: str, *args):
        r = await original(sql, *args)
        if r is not None and "SELECT email, is_active" in sql:
            return {**r, "email": "Dependencia-902@orpycamcp.local"}
        return r

    conn.fetchrow = _mismo_correo  # type: ignore[method-assign]
    envios = await _envios(conn)
    assert len(envios) == 1 and TRACKING not in envios[0].body, "se queda el buzón, el de menor nivel"


@pytest.mark.asyncio
async def test_envio_inexistente_devuelve_none_sin_lanzar() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    assert await resolver_contexto_devolucion(conn, SID) is None
    assert await resolver_contexto_devolucion(conn, "no-es-uuid") is None


def test_el_constructor_exige_el_contexto_por_palabra_clave() -> None:
    with pytest.raises(TypeError):
        build_devolucion_notifications(_sobre())  # type: ignore[call-arg]
    ctx = ContextoDevolucion(shipment_id=str(SID), operador="4-72", guia=GUIA, destinatarios=())
    assert build_devolucion_notifications(_sobre(), contexto=ctx) == []


def test_el_contenido_es_inmutable_y_el_sin_contenido_no_lo_trae() -> None:
    d = DestinatarioDevolucion(email="a@b.c", contenido=None)
    ctx = ContextoDevolucion(shipment_id=str(SID), operador="4-72", guia=GUIA, destinatarios=(d,))
    (envio,) = build_devolucion_notifications(_sobre(), contexto=ctx)
    assert envio.sensible is False and "devuelto por el operador 4-72 (guía " in envio.body
    c = ContenidoDevolucion("T", None, None, None, None, 1)
    with pytest.raises(AttributeError):
        c.nivel = 3  # type: ignore[misc]


# --- consumidor y reproceso de la DLQ ----------------------------------------------


def _acquire(conn):
    @asynccontextmanager
    async def _a(slug):  # noqa: ARG001
        yield conn

    return _a


def _raw(event_type: str = "document.envio.devuelto") -> dict[str, str]:
    from orpycamcp_common.events import to_fields

    return to_fields(
        {
            "event_id": "evt-dev-1",
            "event_type": event_type,
            "version": 1,
            "tenant_slug": "demo",
            "occurred_at": "2026-10-10T10:00:00+00:00",
            "actor": "postal:4-72",
            "correlation_id": "c",
            "dedup_key": "",
            "payload": {"shipment_id": str(SID), "radicado_id": str(RID), "operador": "4-72", "guia": GUIA, "estado": "devuelto"},
        }
    )


@pytest.mark.asyncio
async def test_el_consumidor_envia_los_dos_correos_tras_reclamar() -> None:
    conn = _ConnFalsa(nivel=1)
    orden: list[str] = []

    async def _claim(self, event_id):  # noqa: ARG001
        orden.append("claim")
        return True

    async def _enviar(send, **kw):  # noqa: ARG001
        orden.append(f"send:{send.recipient_email}")

    with (
        patch.object(event_consumer, "acquire_tenant", _acquire(conn)),
        patch.object(event_consumer.ProcessedEventsRepository, "claim", _claim),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as webhooks,
        patch.object(event_consumer, "send_notification", AsyncMock(side_effect=_enviar)),
    ):
        await event_consumer.process_document_event(_raw())
    webhooks.assert_awaited_once()
    assert orden == ["claim", "send:dependencia-902@orpycamcp.local", "send:autora@entidad.gov.co"]


@pytest.mark.asyncio
async def test_error_de_bd_al_resolver_no_reclama_el_evento() -> None:
    """Sin claim ni ack el mensaje vuelve al PEL y, tras las entregas, a la DLQ."""
    claim = AsyncMock(return_value=True)
    with (
        patch.object(event_consumer, "acquire_tenant", _acquire(object())),
        patch.object(
            event_consumer,
            "resolver_contexto_devolucion",
            AsyncMock(side_effect=asyncpg.PostgresConnectionError("base caída")),
        ),
        patch.object(event_consumer.ProcessedEventsRepository, "claim", claim),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as webhooks,
        patch.object(event_consumer, "send_notification", AsyncMock()) as enviar,
    ):
        with pytest.raises(asyncpg.PostgresConnectionError):
            await event_consumer.process_document_event(_raw())
    claim.assert_not_awaited()
    webhooks.assert_not_awaited()
    enviar.assert_not_awaited()


@pytest.mark.asyncio
async def test_reentrega_ya_reclamada_no_manda_otro_correo() -> None:
    conn = _ConnFalsa(nivel=1)
    with (
        patch.object(event_consumer, "acquire_tenant", _acquire(conn)),
        patch.object(event_consumer.ProcessedEventsRepository, "claim", AsyncMock(return_value=False)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as webhooks,
        patch.object(event_consumer, "send_notification", AsyncMock()) as enviar,
    ):
        await event_consumer.process_document_event(_raw())
    webhooks.assert_not_awaited()
    enviar.assert_not_awaited()


@pytest.mark.asyncio
async def test_envio_purgado_no_manda_correo_y_no_lanza() -> None:
    with (
        patch.object(event_consumer, "acquire_tenant", _acquire(object())),
        patch.object(event_consumer, "resolver_contexto_devolucion", AsyncMock(return_value=None)),
        patch.object(event_consumer.ProcessedEventsRepository, "claim", AsyncMock(return_value=True)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as webhooks,
        patch.object(event_consumer, "send_notification", AsyncMock()) as enviar,
    ):
        await event_consumer.process_document_event(_raw())
    webhooks.assert_awaited_once()  # el webhook sigue su camino
    enviar.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_dlq_usa_el_mismo_nucleo() -> None:
    from app.routers import admin_deadletter
    from app.workers.stream_worker import DOCUMENT_STREAM

    ctx = ContextoDevolucion(
        shipment_id=str(SID),
        operador="4-72",
        guia=GUIA,
        destinatarios=(DestinatarioDevolucion(email="x@y.z", contenido=None),),
    )
    resolver = AsyncMock(return_value=ctx)
    with patch.object(event_consumer, "resolver_contexto_devolucion", resolver):
        envios = await admin_deadletter._construir_envios(object(), DOCUMENT_STREAM, _sobre())
    resolver.assert_awaited_once()
    assert [e.recipient_email for e in envios] == ["x@y.z"]


def test_el_nucleo_unico_es_el_que_usan_consumidor_y_reproceso() -> None:
    """Censo: ni el consumidor ni el reproceso llaman a un constructor de documento por su cuenta."""
    import inspect

    from app.routers import admin_deadletter

    assert "construir_envios_documento" in inspect.getsource(event_consumer.process_document_event)
    assert "construir_envios_documento" in inspect.getsource(admin_deadletter._construir_envios)
    assert "build_document_notifications" not in inspect.getsource(admin_deadletter)


def test_devolucion_exporta_el_gate_en_el_where() -> None:
    assert "r.nivel_seguridad <= $2" in devolucion._SQL_CONTENIDO
