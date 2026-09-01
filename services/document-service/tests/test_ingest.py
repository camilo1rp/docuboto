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


# ── La puerta del buzón ──────────────────────────────────────────────────────


def _gate_de(ruta: str, metodo: str) -> set[str]:
    """Permisos que exige una ruta, leídos de la app MONTADA, no del fichero.

    Un barrido por texto vería un solo gate cuando hay dos: los declarados en el
    `APIRouter` y los del endpoint se ACUMULAN, y la pregunta «qué exige esta
    ruta» solo la responde la suma. Por eso se leen los dos niveles.

    FastAPI 0.141 pospone la inclusión —`app.routes` guarda `_IncludedRouter`, no
    las rutas—, así que se baja al `original_router`. El nombre del permiso vive
    en el cierre de la dependencia que fabrica `require_permission`.
    """
    from app.main import app

    exigidos: set[str] = set()
    for incluido in app.routes:
        router = getattr(incluido, "original_router", None)
        if router is None:
            continue
        for route in router.routes:
            if getattr(route, "path", None) != ruta:
                continue
            if metodo not in (getattr(route, "methods", None) or ()):
                continue
            for dep in list(router.dependencies) + list(route.dependencies):
                cierre = getattr(dep.dependency, "__closure__", None) or ()
                exigidos |= {
                    celda.cell_contents
                    for celda in cierre
                    if isinstance(celda.cell_contents, str)
                    and celda.cell_contents.startswith(("PERM_", "USUA_", "SGD_"))
                }
    return exigidos


def test_el_lector_de_gates_encuentra_algo() -> None:
    """Sin esto, un `_gate_de` que devuelve siempre el conjunto vacío haría pasar
    el test de «crear una cuenta sigue siendo de administrador» sin comprobarlo:
    un guardián que no ve nada es indistinguible de un sistema abierto."""
    assert _gate_de("/api/v1/email/cuentas", "POST"), "el lector de gates no ve ninguna ruta"


def test_saber_que_buzones_tengo_es_la_misma_competencia_que_trabajarlos() -> None:
    """El listado de cuentas y la bandeja se gatean IGUAL, y por una razón.

    La bandeja exige `PERM_RADI` a propósito: quien trabaja el correo recibido es
    la unidad de correspondencia, no un administrador. Pero el listado de cuentas
    exigía `USUA_PERM_ADMIN`, y era **la única forma de saber qué buzones existen
    y con qué identificador**. Resultado: el funcionario al que la bandeja está
    dirigida no podía llegar a ella — capacidad construida, gateada para su
    público y sin puerta.

    Que se pueda aflojar el gate sin aflojar nada depende de un detalle del
    diseño: **qué cuentas devuelve el listado no lo decide el permiso, lo decide
    el `WHERE` del repositorio** (`_VISIBLE`), que reparte la personal a su dueño
    y la institucional a los asignados. Ese `WHERE` recibe `es_admin` como
    parámetro —está escrito para servir también a quien no lo es— y el gate de
    administrador dejaba esa rama en código muerto. Sus casos viven en
    `tests/integration/test_email_cuentas_integration.py`.
    """
    listado = _gate_de("/api/v1/email/cuentas", "GET")
    bandeja = _gate_de("/api/v1/email/cuentas/{cuenta_id}/bandeja", "GET")

    assert listado == {"PERM_RADI"}, (
        f"el listado de cuentas exige {sorted(listado)}: si vuelve a pedir "
        "USUA_PERM_ADMIN, el funcionario de ventanilla se queda otra vez sin "
        "forma de encontrar su buzón"
    )
    assert listado == bandeja, (
        f"listado {sorted(listado)} y bandeja {sorted(bandeja)} divergen: saber "
        "qué buzones tengo y trabajarlos son la misma competencia"
    )


def test_crear_una_cuenta_sigue_siendo_de_administrador() -> None:
    """Control positivo: aflojar el listado no aflojó la configuración.

    Sin esto, el test anterior pasaría igual con todo el router abierto, que es
    exactamente el error que no se quiere cometer al corregir un gate de más.
    """
    assert _gate_de("/api/v1/email/cuentas", "POST") == {"USUA_PERM_ADMIN"}
    assert _gate_de("/api/v1/email/apps-oauth", "GET") == {"USUA_PERM_ADMIN"}
