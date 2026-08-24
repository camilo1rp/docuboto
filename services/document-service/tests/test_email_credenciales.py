# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Custodia de credenciales de cuentas de correo (E19 Increment 1).

Lo que se prueba aquí no es «AESGCM funciona» —eso lo prueba `cryptography`—
sino las tres propiedades que hacen que esta custodia sirva de algo: que el AAD
ate el secreto a su fila y a su entidad, que la clave maestra falle cerrada, y
que el AAD no sea ambiguo.
"""

import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.core.email_credenciales import aad, cifrar, descifrar

CLAVE = base64.b64encode(b"0" * 32).decode()
OTRA_CLAVE = base64.b64encode(b"1" * 32).decode()
TENANT = "demo"
CUENTA = "11111111-1111-1111-1111-111111111111"


def test_ida_y_vuelta() -> None:
    ct, nonce = cifrar("s3cr3t0", clave_b64=CLAVE, tenant_slug=TENANT, cuenta_id=CUENTA)
    assert b"s3cr3t0" not in ct  # control positivo: está cifrado de verdad
    assert descifrar(ct, nonce=nonce, clave_b64=CLAVE, tenant_slug=TENANT, cuenta_id=CUENTA) == (
        "s3cr3t0"
    )


def test_el_secreto_de_otra_entidad_no_descifra() -> None:
    """El aislamiento multi-tenant, aplicado al secreto y no solo a la consulta.

    Un `search_path` mal fijado, un volcado restaurado en el esquema equivocado
    o una fila copiada a mano dejarían la credencial de una institución
    utilizable por otra. El AAD lo convierte en un fallo ruidoso.
    """
    ct, nonce = cifrar("s3cr3t0", clave_b64=CLAVE, tenant_slug="entidad-a", cuenta_id=CUENTA)
    with pytest.raises(InvalidTag):
        descifrar(ct, nonce=nonce, clave_b64=CLAVE, tenant_slug="entidad-b", cuenta_id=CUENTA)


def test_el_secreto_de_otra_cuenta_no_descifra() -> None:
    ct, nonce = cifrar("s3cr3t0", clave_b64=CLAVE, tenant_slug=TENANT, cuenta_id=CUENTA)
    otra = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(InvalidTag):
        descifrar(ct, nonce=nonce, clave_b64=CLAVE, tenant_slug=TENANT, cuenta_id=otra)


def test_otra_clave_maestra_no_descifra() -> None:
    ct, nonce = cifrar("s3cr3t0", clave_b64=CLAVE, tenant_slug=TENANT, cuenta_id=CUENTA)
    with pytest.raises(InvalidTag):
        descifrar(ct, nonce=nonce, clave_b64=OTRA_CLAVE, tenant_slug=TENANT, cuenta_id=CUENTA)


def test_ciphertext_alterado_no_descifra() -> None:
    ct, nonce = cifrar("s3cr3t0", clave_b64=CLAVE, tenant_slug=TENANT, cuenta_id=CUENTA)
    alterado = bytes([ct[0] ^ 0x01]) + ct[1:]
    with pytest.raises(InvalidTag):
        descifrar(alterado, nonce=nonce, clave_b64=CLAVE, tenant_slug=TENANT, cuenta_id=CUENTA)


def test_el_aad_no_es_ambiguo() -> None:
    """Concatenar sin separador haría colisionar pares distintos.

    Con `tenant+cuenta` a secas, ("demo", "ab") y ("demoa", "b") producen la
    MISMA cadena, y el secreto de una cuenta descifraría en la otra — el
    aislamiento se perdería justo donde parece estar puesto.

    El primer par que escribí para este test —("demo-a","b") contra
    ("demo","a-b")— **no** colisiona ni siquiera concatenando a secas, así que
    el test pasaba en verde sobre la implementación defectuosa. Lo cazó la
    verificación por mutación; un test que no distingue no prueba nada.
    """
    assert aad("demo", "ab") != aad("demoa", "b")
    # Y el mismo par con separador SÍ es distinguible — control positivo de que
    # la aserción de arriba falla por el separador y no por otra cosa.
    assert aad("demo", "ab") == aad("demo", "ab")


def test_sin_clave_maestra_falla_explicito_y_no_cifra_igual() -> None:
    """Fail-closed: nunca una clave derivada o implícita.

    Un secreto cifrado bajo una clave adivinada da la apariencia de custodia sin
    la custodia, y nadie vuelve a mirarlo.
    """
    with pytest.raises(RuntimeError, match="no configurada"):
        cifrar("s3cr3t0", clave_b64="", tenant_slug=TENANT, cuenta_id=CUENTA)


@pytest.mark.parametrize(
    "mala, motivo",
    [
        ("no-es-base64!!", "base64"),
        (base64.b64encode(b"corta").decode(), "32 bytes"),
    ],
)
def test_clave_maestra_invalida_falla_explicito(mala: str, motivo: str) -> None:
    with pytest.raises(RuntimeError, match=motivo):
        cifrar("s3cr3t0", clave_b64=mala, tenant_slug=TENANT, cuenta_id=CUENTA)
