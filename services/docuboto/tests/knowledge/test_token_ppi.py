# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El token de servicio lo firma el propio emisor y no se envía una petición sin él."""

from __future__ import annotations

import pytest

from auth.core import security
from knowledge.core import token_ppi
from tests.auth.fakes_identidad import ConexionDeClaves, configurar, cuenta

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    configurar(monkeypatch)
    token_ppi.olvidar()


async def test_firma_para_la_audiencia_del_proveedor_y_guarda_el_token() -> None:
    conn = ConexionDeClaves([cuenta()])
    primero = await token_ppi.portador(conn, "demo")
    segundo = await token_ppi.portador(conn, "demo")
    assert primero == segundo
    claims = await security.verificar(conn, primero, audience=token_ppi.AUDIENCIA)  # type: ignore[arg-type]
    assert claims["sub"] == "intelligence-demo"
    assert claims["principal_type"] == "service"


async def test_sin_cuenta_de_servicio_no_hay_token() -> None:
    with pytest.raises(RuntimeError, match="sin cuenta de servicio"):
        await token_ppi.portador(ConexionDeClaves(), "demo")


async def test_una_cuenta_inactiva_no_da_token() -> None:
    with pytest.raises(RuntimeError, match="sin cuenta de servicio"):
        await token_ppi.portador(ConexionDeClaves([cuenta(is_active=False)]), "demo")


async def test_sin_clave_del_emisor_no_se_inventa_un_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from docuboto.config import auth as settings

    monkeypatch.setattr(settings, "identity_key_encryption_key", "")
    with pytest.raises(RuntimeError, match="sin token de servicio"):
        await token_ppi.portador(ConexionDeClaves([cuenta()]), "demo")
