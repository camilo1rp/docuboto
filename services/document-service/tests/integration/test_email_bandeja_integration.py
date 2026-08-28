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

import json
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

    de_ana = await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)
    assert de_ana is not None and de_ana["reservado_por"] == ANA

    # Benito abre el mismo correo: no lo consigue.
    assert await repo.reservar(correo["id"], cuenta, actor=BENITO, minutos=10) is None

    # Y Ana puede reabrirlo sin que su propia reserva se lo impida.
    assert await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10) is not None


async def test_solo_quien_reservo_puede_radicar(tenant_ctx) -> None:
    """La reserva no es un adorno de la interfaz: gobierna la escritura.

    Si `marcar_radicado` comprobara solo que el correo sigue pendiente, dos
    personas que lo hubieran abierto antes podrían radicarlo las dos.
    """
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)

    sin_reserva = await repo.marcar_radicado(
        correo["id"], cuenta, actor=BENITO, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-1"
    )
    assert sin_reserva is None

    hecho = await repo.marcar_radicado(
        correo["id"], cuenta, actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000045"
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
    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)
    await repo.marcar_radicado(
        correo["id"], cuenta, actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000045"
    )

    assert await repo.bandeja(cuenta) == []
    assert await repo.contar(cuenta) == 0
    assert len(await repo.bandeja(cuenta, estado="radicado")) == 1


async def test_no_se_puede_radicar_dos_veces(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)
    await repo.marcar_radicado(
        correo["id"], cuenta, actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000045"
    )
    # Un segundo intento —un doble clic, una petición reintentada— no consume
    # otro número de la secuencia oficial.
    assert (
        await repo.marcar_radicado(
            correo["id"], cuenta, actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000046"
        )
        is None
    )


async def test_una_reserva_caducada_libera_el_correo(tenant_ctx) -> None:
    """Sin caducidad, quien cierra el navegador deja el correo bloqueado para
    siempre y la ventanilla necesita un administrador para desatascarlo."""
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)

    assert await repo.reservar(correo["id"], cuenta, actor=BENITO, minutos=10) is None
    # Se envejece en la BASE: lo que se prueba es la condición SQL, y un reloj de
    # Python no la tocaría.
    await conn.execute(
        "UPDATE email_mensaje SET reservado_hasta = now() - interval '1 minute' WHERE id = $1",
        correo["id"],
    )
    assert await repo.reservar(correo["id"], cuenta, actor=BENITO, minutos=10) is not None

    # Y con la reserva caducada, Ana ya no puede radicar: la ventana se cerró.
    assert (
        await repo.marcar_radicado(
            correo["id"], cuenta, actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-1"
        )
        is None
    )


async def test_liberar_no_toca_la_reserva_de_otro(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)

    assert await repo.liberar(correo["id"], cuenta, actor=BENITO) is False
    assert await repo.liberar(correo["id"], cuenta, actor=ANA) is True
    assert await repo.reservar(correo["id"], cuenta, actor=BENITO, minutos=10) is not None


async def test_descartar_exige_reserva_y_motivo(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)

    # Sin reserva no se descarta: es una decisión sobre una comunicación
    # recibida, y dos personas no deben tomarla a la vez.
    assert await repo.descartar(correo["id"], cuenta, actor=ANA, motivo="publicidad") is None

    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)
    fuera = await repo.descartar(correo["id"], cuenta, actor=ANA, motivo="Boletín publicitario")
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


async def test_el_estado_avisa_de_lo_que_cambia_la_vista(tenant_ctx) -> None:
    """El sondeo que mantiene sincronizadas las pantallas abiertas.

    La marca tiene que moverse ante **todo lo que cambia lo que se ve**, incluida
    una reserva ajena: si un compañero toma un correo y la marca no se moviera,
    el resto seguiría viéndolo libre e intentaría abrirlo para llevarse un 409
    que no esperaba.
    """
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)

    vacia = await repo.estado_bandeja(cuenta)
    assert vacia["pendientes"] == 0 and vacia["ultima_novedad"] is None

    correo = await _correo(repo, cuenta)
    con_uno = await repo.estado_bandeja(cuenta)
    assert con_uno["pendientes"] == 1
    assert con_uno["ocupados"] == 0
    assert con_uno["ultima_novedad"] is not None

    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)
    reservado = await repo.estado_bandeja(cuenta)
    assert reservado["ocupados"] == 1
    assert reservado["ultima_novedad"] > con_uno["ultima_novedad"]

    await repo.marcar_radicado(
        correo["id"], cuenta, actor=ANA, radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-1"
    )
    tras_radicar = await repo.estado_bandeja(cuenta)
    assert tras_radicar["pendientes"] == 0
    assert tras_radicar["ocupados"] == 0


async def test_el_estado_no_mezcla_cuentas(tenant_ctx) -> None:
    """Una ventanilla de PQRS y otra de notificaciones judiciales son bandejas
    distintas; el sondeo de una no puede contar los correos de la otra."""
    slug, conn = tenant_ctx
    repo, pqrs = await _preparar(conn, slug)
    judicial = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO email_cuenta
            (id, nombre, tipo, proveedor, host, direccion, auth_tipo,
             secreto_cifrado, secreto_nonce)
        VALUES ($1, 'Judicial', 'institucional', 'imap', 'h', 'j@e.gov.co',
                'password', '\\x01', '\\x02')
        """,
        judicial,
    )
    await _correo(repo, pqrs)

    assert (await repo.estado_bandeja(pqrs))["pendientes"] == 1
    assert (await repo.estado_bandeja(judicial))["pendientes"] == 0


# ── El descarte deja rastro en la cadena inmutable ────────────────────────────
#
# Radicar produce un radicado con su número y su asiento. Descartar producía
# solo un `UPDATE` sobre `email_mensaje`, que es una tabla corriente: quién lo
# descartó y por qué se podían reescribir después sin dejar rastro. Y decidir que
# una comunicación recibida NO se registra es la decisión que un tercero querría
# poder revisar — es la que hace desaparecer el documento del archivo.


async def _descartar_por_el_servicio(conn, slug, cuenta, mensaje_id, *, motivo, actor=ANA):
    from app.services.email_bandeja_service import EmailBandejaService

    # `es_admin=True`: la cuenta de `_preparar` es institucional y sin asignados,
    # así que este es el alcance legítimo. El caso contrario tiene su propio test.
    return await EmailBandejaService(conn, slug).descartar(
        mensaje_id, cuenta, actor=actor, es_admin=True, motivo=motivo
    )


async def test_descartar_deja_asiento_en_la_cadena_inmutable(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)

    await _descartar_por_el_servicio(
        conn, slug, cuenta, correo["id"], motivo="Boletín publicitario"
    )

    fila = await conn.fetchrow(
        """
        SELECT action, object_type, object_ref, actor, payload
          FROM public.audit_log
         WHERE tenant_slug = $1 AND action = 'email.mensaje.descartado'
        """,
        slug,
    )
    assert fila is not None, "el descarte no dejó asiento"
    assert fila["object_type"] == "email_mensaje"
    assert fila["object_ref"] == str(correo["id"])
    assert fila["actor"] == str(ANA)

    payload = json.loads(fila["payload"])
    assert payload["motivo"] == "Boletín publicitario"
    assert payload["cuenta_id"] == str(cuenta)
    # El asunto y el remitente NO viajan al asiento. Un correo descartado nunca
    # se radicó, así que no tiene nivel de clasificación con el que recortar su
    # lectura: en `audit_log` lo vería todo `USUA_PERM_ADMIN`. Se queda en
    # `email_mensaje`, donde lo recorta el `WHERE` de la cuenta.
    serializado = json.dumps(payload)
    assert "Derecho de petición" not in serializado
    assert "ciudadano@correo.co" not in serializado


async def test_si_el_asiento_no_se_puede_escribir_el_correo_NO_se_descarta(
    tenant_ctx, monkeypatch
) -> None:
    """Atomicidad: aquí el asiento no acompaña al acto, ES el registro del acto.

    Si se escribiera best-effort —como las trazas de lectura— un fallo de la
    cadena dejaría el correo apartado sin ninguna constancia de quién lo apartó,
    que es exactamente el estado que este trabajo existe para impedir. El
    rollback lo produce Postgres; con un doble lo produciría el doble.
    """
    slug, conn = tenant_ctx
    repo, cuenta = await _preparar(conn, slug)
    correo = await _correo(repo, cuenta)
    await repo.reservar(correo["id"], cuenta, actor=ANA, minutos=10)

    from app.services import email_bandeja_service

    async def cae(*_a, **_k):
        raise RuntimeError("cadena de auditoría no disponible")

    monkeypatch.setattr(email_bandeja_service.audit, "append", cae)

    with pytest.raises(RuntimeError):
        await _descartar_por_el_servicio(conn, slug, cuenta, correo["id"], motivo="publicidad")

    # El correo sigue en la bandeja, pendiente y con su reserva: nada se perdió.
    vivo = await conn.fetchrow("SELECT estado FROM email_mensaje WHERE id = $1", correo["id"])
    assert vivo["estado"] == "pendiente"
    assert len(await repo.bandeja(cuenta)) == 1


# ── El alcance del buzón: PERM_RADI dice quién puede, no QUÉ buzón ────────────


async def test_no_se_alcanza_la_bandeja_de_una_cuenta_ajena(tenant_ctx) -> None:
    """El hueco real: el recorte de cuentas no llegaba a las operaciones de bandeja.

    `PERM_RADI` habilita trabajar correo; el buzón concreto lo decide el `WHERE`
    de `email_cuenta_repository`, que oculta la cuenta PERSONAL de un funcionario
    incluso al administrador. Listar, reservar, liberar y descartar iban directas
    al mensaje por su id, sin pasar por ahí: cualquiera con `PERM_RADI` de la
    entidad podía leer el asunto y el remitente del correo privado de otro y
    apartárselo.
    """
    from app.services.email_bandeja_service import EmailBandejaService
    from app.services.email_cuenta_service import ErrorDeCuenta

    slug, conn = tenant_ctx
    repo, _ = await _preparar(conn, slug)

    # La cuenta PERSONAL de Benito. Ana no la alcanza — ni siquiera como admin.
    personal = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO email_cuenta
            (id, nombre, tipo, proveedor, host, direccion, auth_tipo,
             propietario_user_id)
        VALUES ($1, 'Buzón de Benito', 'personal', 'imap', 'h', 'benito@e.gov.co',
                'password', $2)
        """,
        personal, BENITO,
    )
    correo = await _correo(repo, personal, mid="<privado@x.co>", uid=99)
    servicio = EmailBandejaService(conn, slug)

    for descripcion, llamada in (
        ("listar", servicio.listar(personal, actor=ANA, es_admin=True)),
        ("estado", servicio.estado(personal, actor=ANA, es_admin=True)),
        ("reservar", servicio.reservar(correo["id"], personal, actor=ANA, es_admin=True)),
        ("liberar", servicio.liberar(correo["id"], personal, actor=ANA, es_admin=True)),
        (
            "descartar",
            servicio.descartar(
                correo["id"], personal, actor=ANA, es_admin=True, motivo="no es mío"
            ),
        ),
    ):
        with pytest.raises(ErrorDeCuenta) as exc:
            await llamada
        assert exc.value.status == 404, descripcion
        # 404 y no 403: distinguirlos diría que la cuenta existe.
        assert exc.value.codigo == "cuenta_no_encontrada", descripcion

    # Y el dueño sí llega.
    filas, total = await servicio.listar(personal, actor=BENITO, es_admin=False)
    assert total == 1 and len(filas) == 1


async def test_un_mensaje_no_se_opera_desde_OTRA_cuenta_que_si_se_alcanza(tenant_ctx) -> None:
    """Segunda capa: el `cuenta_id` va también en el `WHERE` de la mutación.

    El guardián comprueba «¿alcanzo este buzón?». Sin atar además el mensaje al
    buzón, alcanzar UNO bastaría para operar sobre el correo de cualquier otro
    con solo conocer su id — el guardián diría que sí, porque la cuenta que se le
    pasa es la alcanzable.
    """
    slug, conn = tenant_ctx
    repo, propia = await _preparar(conn, slug)

    ajena = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO email_cuenta
            (id, nombre, tipo, proveedor, host, direccion, auth_tipo,
             secreto_cifrado, secreto_nonce)
        VALUES ($1, 'Otra ventanilla', 'institucional', 'imap', 'h', 'o@e.gov.co',
                'password', '\\x01', '\\x02')
        """,
        ajena,
    )
    de_la_ajena = await _correo(repo, ajena, mid="<otra@x.co>", uid=42)

    # Se declara la cuenta alcanzable, pero el mensaje es de la otra.
    assert await repo.reservar(de_la_ajena["id"], propia, actor=ANA, minutos=10) is None

    # Y con su cuenta de verdad, sí.
    assert await repo.reservar(de_la_ajena["id"], ajena, actor=ANA, minutos=10) is not None
