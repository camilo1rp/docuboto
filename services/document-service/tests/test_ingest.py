# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del adaptador IMAP de la ingesta de correo (E19).

La ruta heredada (`IMAP_*` + `POST /ingest/email/poll`) está retirada; lo que se
prueba aquí es el adaptador que usa la bandeja por cuenta. Cuentas, bandeja y
radicación se prueban contra Postgres real en
`tests/integration/test_email_cuentas_integration.py` y
`test_email_bandeja_integration.py`; el acuse, en `test_acuse_correo.py`.
"""

import pytest

# ── La fecha de recibo ───────────────────────────────────────────────────────


def test_la_fecha_de_recibo_sale_del_buzon_no_del_remitente() -> None:
    """`INTERNALDATE` y no la cabecera `Date`.

    `Date` la pone el cliente del REMITENTE: depende de su reloj, puede faltar
    —el servidor de pruebas lo demostró: llegó vacía— y sobre todo **la controla
    él**, así que podría fecharse en el pasado para ganar plazo en un derecho de
    petición. El `INTERNALDATE` lo pone el servidor de la ENTIDAD cuando el
    mensaje entra en el buzón, y es literalmente la «fecha y hora de recibo» de
    la que la Circular Externa AGN 001/2020 exige dejar constancia.
    """
    from app.clients.imap_adaptador import _internaldate

    respuesta = b'1 (UID 7 INTERNALDATE "20-Aug-2026 09:15:42 +0000" BODY[] {793}'
    recibido = _internaldate(respuesta)
    assert recibido is not None
    assert (recibido.year, recibido.month, recibido.day) == (2026, 8, 20)
    assert (recibido.hour, recibido.minute) == (9, 15)


def test_un_huso_horario_no_desplaza_la_fecha_de_recibo() -> None:
    """La misma marca en otro huso es el mismo instante, no otro día.

    Interpretar la tupla como hora local desplazaría el recibo tantas horas como
    el huso del servidor — cinco en Colombia, suficientes para cambiar el día en
    que vence un plazo.
    """
    from app.clients.imap_adaptador import _internaldate

    utc = _internaldate(b'1 (INTERNALDATE "20-Aug-2026 09:15:42 +0000" BODY[] {1}')
    bogota = _internaldate(b'1 (INTERNALDATE "20-Aug-2026 04:15:42 -0500" BODY[] {1}')
    assert utc is not None and bogota is not None
    assert utc == bogota


def test_sin_internaldate_no_se_inventa_una_fecha() -> None:
    """Una fecha de recibo inventada es peor que ninguna: nadie la revisaría."""
    from app.clients.imap_adaptador import _internaldate

    assert _internaldate(b"1 (UID 7 BODY[] {793}") is None


# ── UIDVALIDITY: la comprobación que impide radicar el correo equivocado ──────


def test_un_uidvalidity_distinto_impide_operar() -> None:
    """El defecto que apareció usando el sistema, no en ningún test.

    Un `uid` solo identifica un mensaje mientras el `UIDVALIDITY` de la carpeta
    no cambie (RFC 3501 §2.3.1.1). Cuando cambia —restauración del buzón,
    migración de servidor— los `uid` se reparten de nuevo desde 1: la bandeja
    seguiría apuntando al «mensaje 7» y traería el que ahora ocupa esa posición.
    Otro correo, de otro remitente, **radicado bajo el asunto del primero**.

    El modelo guardaba el `uidvalidity` desde el principio; lo que faltaba era
    comprobarlo antes de traer o mover. Se descubrió al reiniciarse el servidor
    de pruebas, que asignó uno nuevo.
    """
    from app.clients.imap_adaptador import Conexion, ErrorDeCorreo, ImapAdaptador

    adaptador = ImapAdaptador(
        Conexion(
            host="h", puerto=993, usa_ssl=True, direccion="a@b.co",
            auth_tipo="password", secreto="x", login="a@b.co",
        )
    )
    with pytest.raises(ErrorDeCorreo) as exc:
        adaptador._exigir_uidvalidity(999, 111, "INBOX")
    assert exc.value.codigo == "uidvalidity_cambiado"
    # El mensaje dice qué hacer, no solo qué pasó.
    assert "vuelva a consultar el buzón" in str(exc.value).lower()

    # Coincidiendo, no estorba.
    adaptador._exigir_uidvalidity(111, 111, "INBOX")
    # Y sin expectativa declarada tampoco: es el caso de la propia
    # sincronización, que acaba de leerlo y no tiene con qué contrastar.
    adaptador._exigir_uidvalidity(111, None, "INBOX")
