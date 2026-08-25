# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
La bandeja compartida, contra Postgres real (E19 Increment 2).

Aquí se prueba lo que motivó el incremento: **que dos funcionarios trabajando el
mismo buzón no puedan radicar el mismo correo dos veces**. Nada de esto se puede
comprobar con dobles — el «cero filas» que distingue «lo tomé» de «alguien se me
adelantó» lo produce la base de datos, y con un mock lo produciría el mock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.email_mensaje_repository import EmailMensajeRepository

pytestmark = pytest.mark.asyncio

ANA = uuid.uuid4()
BENITO = uuid.uuid4()


async def _preparar(conn: asyncpg.Connection, slug: str) -> tuple[EmailMensajeRepository, uuid.UUID]:
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    cuenta_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO email_cuenta
            (id, nombre, tipo, proveedor, host, direccion, auth_tipo,
             secreto_cifrado, secreto_nonce)
        VALUES ($1, 'Ventanilla', 'institucional', 'imap', 'h', 'v@e.gov.co',
                'password', '\\x01', '\\x02')
        """,
        cuenta_id,
    )
    return EmailMensajeRepository(conn), cuenta_id


async def _correo(repo, cuenta_id, *, mid="<p1@ciudadano.co>", uid=7):
    return await repo.registrar_si_es_nuevo(
        {
            "cuenta_id": cuenta_id,
            "message_id": mid,
            "carpeta": "INBOX",
            "uidvalidity": 1,
            "uid": uid,
            "remitente_email": "ciudadano@correo.co",
            "asunto": "Derecho de petición",
            "fecha_recibo": datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        }
    )


async def test_el_mismo_correo_no_entra_dos_veces(tenant_ctx) -> None:
    """Dos sincronizaciones a la vez —dos operadores pulsando «actualizar»— no
    duplican la bandeja ni fallan."""
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)

    primero = await _correo(repo, cuenta)
    assert primero is not None
    # Mismo Message-ID con OTRO uid y otro UIDVALIDITY: es lo que pasa cuando el
    # servidor recrea la carpeta y reutiliza los identificadores desde cero.
    repetido = await repo.registrar_si_es_nuevo(
        {
            "cuenta_id": cuenta,
            "message_id": "<p1@ciudadano.co>",
            "carpeta": "Radicados",
            "uidvalidity": 999,
            "uid": 3,
            "fecha_recibo": datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        }
    )
    assert repetido is None
    assert await repo.contar(cuenta) == 1


async def test_dos_funcionarios_no_pueden_reservar_el_mismo_correo(tenant_ctx) -> None:
    """El caso que motivó todo el incremento."""
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)

    de_ana = await repo.reservar(correo["id"], actor=ANA, minutos=10)
    assert de_ana is not None and de_ana["reservado_por"] == ANA

    # Benito abre el mismo correo: no lo consigue.
    assert await repo.reservar(correo["id"], actor=BENITO, minutos=10) is None

    # Y Ana puede reabrirlo sin que su propia reserva se lo impida.
    assert await repo.reservar(correo["id"], actor=ANA, minutos=10) is not None


async def test_solo_quien_reservo_puede_radicar(tenant_ctx) -> None:
    """La reserva no es un adorno de la interfaz: gobierna la escritura.

    Si `marcar_radicado` comprobara solo que el correo sigue pendiente, dos
    personas que lo hubieran abierto antes podrían radicarlo las dos.
    """
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], actor=ANA, minutos=10)

    sin_reserva = await repo.marcar_radicado(
        correo["id"], actor=BENITO, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-1"
    )
    assert sin_reserva is None

    hecho = await repo.marcar_radicado(
        correo["id"], actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000045"
    )
    assert hecho is not None
    assert hecho["estado"] == "radicado"
    assert hecho["tracking_number"] == "2026-DEMO-E-000045"
    # Y queda LIBERADO: un correo cerrado no arrastra una reserva viva.
    assert hecho["reservado_por"] is None


async def test_un_correo_radicado_sale_de_la_bandeja(tenant_ctx) -> None:
    """Lo que Orfeo NO hacía: allí seguía en la lista con una «R» roja."""
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], actor=ANA, minutos=10)
    await repo.marcar_radicado(
        correo["id"], actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000045"
    )

    assert await repo.bandeja(cuenta) == []
    assert await repo.contar(cuenta) == 0
    assert len(await repo.bandeja(cuenta, estado="radicado")) == 1


async def test_no_se_puede_radicar_dos_veces(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], actor=ANA, minutos=10)
    await repo.marcar_radicado(
        correo["id"], actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000045"
    )
    # Un segundo intento —un doble clic, una petición reintentada— no consume
    # otro número de la secuencia oficial.
    assert (
        await repo.marcar_radicado(
            correo["id"], actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000046"
        )
        is None
    )


async def test_una_reserva_caducada_libera_el_correo(tenant_ctx) -> None:
    """Sin caducidad, quien cierra el navegador deja el correo bloqueado para
    siempre y la ventanilla necesita un administrador para desatascarlo."""
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], actor=ANA, minutos=10)

    assert await repo.reservar(correo["id"], actor=BENITO, minutos=10) is None
    # Se envejece en la BASE: lo que se prueba es la condición SQL, y un reloj de
    # Python no la tocaría.
    await conn.execute(
        "UPDATE email_mensaje SET reservado_hasta = now() - interval '1 minute' WHERE id = $1",
        correo["id"],
    )
    assert await repo.reservar(correo["id"], actor=BENITO, minutos=10) is not None

    # Y con la reserva caducada, Ana ya no puede radicar: la ventana se cerró.
    assert (
        await repo.marcar_radicado(
            correo["id"], actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-1"
        )
        is None
    )


async def test_liberar_no_toca_la_reserva_de_otro(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], actor=ANA, minutos=10)

    assert await repo.liberar(correo["id"], actor=BENITO) is False
    assert await repo.liberar(correo["id"], actor=ANA) is True
    assert await repo.reservar(correo["id"], actor=BENITO, minutos=10) is not None


async def test_descartar_exige_reserva_y_motivo(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)

    # Sin reserva no se descarta: es una decisión sobre una comunicación
    # recibida, y dos personas no deben tomarla a la vez.
    assert await repo.descartar(correo["id"], actor=ANA, motivo="publicidad") is None

    await repo.reservar(correo["id"], actor=ANA, minutos=10)
    fuera = await repo.descartar(correo["id"], actor=ANA, motivo="Boletín publicitario")
    assert fuera is not None and fuera["estado"] == "descartado"
    assert await repo.bandeja(cuenta) == []


async def test_la_bandeja_va_por_fecha_de_RECIBO(tenant_ctx) -> None:
    """FIFO por la fecha del correo, no por la del sondeo que lo trajo.

    Un correo que llegó el viernes y se sincronizó el lunes va delante del que
    llegó el domingo, aunque ambos entraran en la misma sincronización.
    """
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    await _correo(repo, cuenta, mid="<domingo@x>", uid=2)
    await conn.execute(
        "UPDATE email_mensaje SET fecha_recibo = '2026-08-23T10:00:00+00:00' "
        "WHERE message_id = '<domingo@x>'"
    )
    await _correo(repo, cuenta, mid="<viernes@x>", uid=3)
    await conn.execute(
        "UPDATE email_mensaje SET fecha_recibo = '2026-08-21T10:00:00+00:00' "
        "WHERE message_id = '<viernes@x>'"
    )

    orden = [m["message_id"] for m in await repo.bandeja(cuenta)]
    assert orden == ["<viernes@x>", "<domingo@x>"]
