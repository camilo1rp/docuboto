# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un buzón es una salida configurable, y no puede apuntar a la red interna.

Los webhooks salientes ya llevaban este filtro. El correo era la única salida
que el sistema abre por orden de alguien y que no lo tenía, pese a ser la misma
capacidad: un host y un puerto elegidos desde la pantalla de administración.
Quien administra una entidad es un rol de NEGOCIO, no de infraestructura.
"""

import pytest

from app.schemas.email_cuenta import CuentaCrear


def _cuenta(**kw):
    base = {
        "nombre": "Buzón",
        "tipo": "institucional",
        "proveedor": "imap",
        "direccion": "buzon@entidad.gov.co",
        "auth_tipo": "password",
        "password": "x",
        "usa_ssl": False,
    }
    base.update(kw)
    return CuentaCrear(**base)


@pytest.mark.parametrize(
    ("host", "motivo"),
    [
        ("postgres", "servicio interno"),
        ("redis", "servicio interno"),
        ("minio", "servicio interno"),
        ("keycloak", "servicio interno"),
        ("orpycamcp-postgres", "servicio interno"),
        ("127.0.0.1", "loopback"),
        ("169.254.169.254", "link-local"),
        ("10.0.0.5", "rango privado"),
        ("192.168.1.10", "rango privado"),
    ],
)
def test_ningun_destino_de_la_red_interna_se_admite(host, motivo):
    """Sin esto, «probar conexión» era un escáner de puertos con la respuesta servida."""
    cuenta = _cuenta(host=host, puerto=5432)
    with pytest.raises(ValueError) as exc:
        cuenta.conexion_efectiva()
    assert motivo in str(exc.value)


def test_un_servidor_de_correo_de_verdad_sigue_admitiendose():
    """El filtro no puede dejar inservible la función que protege."""
    host, puerto, ssl = _cuenta(host="imap.gmail.com", puerto=993, usa_ssl=True).conexion_efectiva()
    assert (host, puerto, ssl) == ("imap.gmail.com", 993, True)


def test_los_presets_de_proveedor_siguen_funcionando():
    """Gmail y Microsoft se resuelven por preset, sin host explícito."""
    cuenta = CuentaCrear(
        nombre="Buzón",
        tipo="institucional",
        proveedor="gmail",
        direccion="buzon@entidad.gov.co",
        auth_tipo="oauth2",
        app_oauth_id="00000000-0000-0000-0000-000000000001",
    )
    host, puerto, _ = cuenta.conexion_efectiva()
    assert host == "imap.gmail.com"
    assert puerto == 993


def test_la_conexion_imap_lleva_plazo_maximo():
    """Un destino que acepta y calla no puede colgar el sondeo para siempre.

    El sondeo institucional corre bajo un cerrojo por entidad: una sola cuenta
    así detenía la radicación automática de TODOS los buzones de esa entidad,
    sin dejar ni una línea de error.
    """
    import inspect

    from app.clients.imap_adaptador import ImapAdaptador

    assert ImapAdaptador._PLAZO_CONEXION > 0
    fuente = inspect.getsource(ImapAdaptador._abrir)
    assert "timeout=" in fuente, "la conexión se abre sin plazo máximo"
