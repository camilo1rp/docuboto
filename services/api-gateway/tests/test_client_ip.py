# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La IP del cliente la elegía el cliente.

`X-Forwarded-For` es una cabecera de petición como otra cualquiera: la escribe
quien quiera. Tomar su primer valor —lo que hacían el limitador de tasa y el
asiento de login— dejaba que el cliente eligiera su propia identidad de red:
evadía el límite rotando la cabecera, y la IP que quedaba **sellada en el
`audit_log`**, un registro inmutable con valor probatorio, era la que él decidía.
"""

from types import SimpleNamespace

import pytest

from app.core.client_ip import resolve_client_ip

_PROXIES = "172.20.0.0/16,10.0.0.5"


def _peticion(socket: str | None, xff: str | None = None):
    """Doble mínimo de `Request`: solo se usan `client.host` y las cabeceras."""
    cabeceras = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(
        client=SimpleNamespace(host=socket) if socket else None,
        headers=cabeceras,
    )


# ---------------------------------------------------------------------------
# Fail-closed: sin proxies declarados, la cabecera no vale nada
# ---------------------------------------------------------------------------


def test_sin_proxies_declarados_se_ignora_la_cabecera() -> None:
    """El default (`trusted_proxies=""`) descarta `X-Forwarded-For` por completo.

    Un despliegue que no ha declarado sus intermediarios no puede distinguir una
    cabecera legítima de una inventada. En la duda vale más una IP inútil (la del
    proxy) que una falsa: la inútil no engaña a nadie.
    """
    peticion = _peticion("203.0.113.9", xff="1.2.3.4")

    assert resolve_client_ip(peticion, "") == "203.0.113.9"


def test_configuracion_invalida_no_amplia_la_confianza() -> None:
    """Una entrada mal escrita se descarta; nunca agranda el conjunto de confianza."""
    peticion = _peticion("172.20.0.7", xff="1.2.3.4")

    # "no-es-una-red" se ignora; queda solo el CIDR válido.
    assert resolve_client_ip(peticion, "no-es-una-red, 172.20.0.0/16") == "1.2.3.4"
    # Si TODO es inválido, no queda ninguna red de confianza → fail-closed.
    assert resolve_client_ip(peticion, "no-es-una-red") == "172.20.0.7"


# ---------------------------------------------------------------------------
# La regla de los saltos
# ---------------------------------------------------------------------------


def test_el_socket_debe_ser_un_proxy_conocido() -> None:
    """Si quien nos habla NO es un proxy nuestro, su cabecera no vale.

    Es el caso del atacante que llega directo al gateway: puede escribir lo que
    quiera en `X-Forwarded-For` y da igual.
    """
    peticion = _peticion("198.51.100.4", xff="1.2.3.4")

    assert resolve_client_ip(peticion, _PROXIES) == "198.51.100.4"


def test_se_recorre_desde_la_derecha() -> None:
    """La cadena se lee del final al principio, descartando los saltos propios.

    `X-Forwarded-For` crece por la DERECHA: cada salto añade la IP de quien le
    habló. Solo son fiables los valores escritos por saltos que controlamos, así
    que la primera dirección no-confiable **empezando por el final** es la última
    que un intermediario nuestro observó de verdad.
    """
    # El cliente inventó dos saltos falsos al principio; el proxy real añadió
    # después su observación (198.51.100.7).
    peticion = _peticion("172.20.0.3", xff="9.9.9.9, 8.8.8.8, 198.51.100.7, 172.20.0.9")

    assert resolve_client_ip(peticion, _PROXIES) == "198.51.100.7"


def test_leer_desde_la_izquierda_habria_creido_al_atacante() -> None:
    """Control explícito del defecto anterior, con el MISMO dato del test previo.

    La implementación vieja hacía `forwarded.split(",")[0]`: habría devuelto
    `9.9.9.9`, que es exactamente lo que el atacante quería que se registrara.
    """
    xff = "9.9.9.9, 8.8.8.8, 198.51.100.7, 172.20.0.9"
    a_la_izquierda = xff.split(",")[0].strip()

    peticion = _peticion("172.20.0.3", xff=xff)

    assert resolve_client_ip(peticion, _PROXIES) != a_la_izquierda


def test_cadena_toda_de_proxies_devuelve_el_socket() -> None:
    """Petición nacida DENTRO de la infraestructura.

    Ocurre cuando el SSR llama al gateway sin haber propagado la IP del
    navegador. Devolver el socket es lo honesto: no hay ninguna dirección de
    cliente que reportar y fabricarla sería peor.
    """
    peticion = _peticion("172.20.0.3", xff="172.20.0.9, 10.0.0.5")

    assert resolve_client_ip(peticion, _PROXIES) == "172.20.0.3"


def test_sin_cabecera_se_usa_el_socket() -> None:
    peticion = _peticion("172.20.0.3")

    assert resolve_client_ip(peticion, _PROXIES) == "172.20.0.3"


@pytest.mark.parametrize("basura", ["", "   ", "no-es-una-ip", ",,,"])
def test_cabecera_basura_no_rompe_ni_se_cree(basura: str) -> None:
    """Entradas no parseables no revientan la petición y tampoco se aceptan.

    Este test destapó un defecto real de la primera implementación: «no es una
    IP» implicaba «no es de confianza» y por tanto «es el cliente», así que la
    basura se devolvía tal cual — y habría acabado sellada en el `audit_log` y
    usada como clave del limitador de tasa. Ahora se descarta y se sigue.
    """
    peticion = _peticion("172.20.0.3", xff=basura)

    assert resolve_client_ip(peticion, _PROXIES) == "172.20.0.3"


def test_sin_socket_no_revienta() -> None:
    """Sin `request.client` (transportes ASGI que no lo pueblan) se degrada, no falla."""
    peticion = _peticion(None, xff="1.2.3.4")

    assert resolve_client_ip(peticion, _PROXIES) == "unknown"
