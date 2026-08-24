# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
OAuth2 de correo (E19 Increment 1).

Se prueban los detalles que, si se equivocan, producen un fallo que no explica
nada: el scope exacto de cada proveedor, los parámetros sin los cuales Google no
entrega token de refresco, y la negativa a aceptar una autorización que dejaría
la cuenta funcionando una hora para apagarse sola.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core import oauth_correo


def _parametros(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_pkce_cumple_la_norma() -> None:
    """RFC 7636: el verificador mide 43–128 y el reto es su SHA-256 en base64url."""
    verifier, challenge = oauth_correo.generar_pkce()
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier and "=" not in challenge
    esperado = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == esperado.decode().rstrip("=")


def test_dos_pkce_seguidos_no_se_repiten() -> None:
    assert oauth_correo.generar_pkce()[0] != oauth_correo.generar_pkce()[0]
    assert oauth_correo.generar_state() != oauth_correo.generar_state()


def test_google_pide_acceso_sin_conexion() -> None:
    """Sin `access_type=offline` y `prompt=consent`, Google NO entrega refresh
    token: la cuenta funcionaría una hora y se apagaría sola, y el diagnóstico
    sería «credenciales rechazadas» en un momento en que nadie tocó nada."""
    url = oauth_correo.url_autorizacion(
        proveedor="gmail",
        client_id="cid",
        redirect_uri="https://sgd.e.gov.co/cb",
        state="st",
        code_challenge="ch",
        directorio=None,
        direccion="pqrs@e.gov.co",
    )
    p = _parametros(url)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert p["access_type"] == "offline"
    assert p["prompt"] == "consent"
    assert p["scope"] == "https://mail.google.com/"
    assert p["code_challenge_method"] == "S256"
    # Sin `login_hint`, quien tenga varias cuentas abiertas autoriza con la que
    # el proveedor elija, y el token queda emitido para el buzón equivocado.
    assert p["login_hint"] == "pqrs@e.gov.co"


def test_microsoft_usa_el_scope_de_imap_delegado_y_su_directorio() -> None:
    """`outlook.office.com`, NO `outlook.office365.com`.

    Ese otro es el del flujo de credenciales de aplicación; usarlo aquí produce
    un token que el servidor IMAP rechaza sin decir por qué.
    """
    url = oauth_correo.url_autorizacion(
        proveedor="microsoft",
        client_id="cid",
        redirect_uri="https://sgd.e.gov.co/cb",
        state="st",
        code_challenge="ch",
        directorio="dir-123",
        direccion="pqrs@e.gov.co",
    )
    assert url.startswith("https://login.microsoftonline.com/dir-123/oauth2/v2.0/authorize?")
    scopes = _parametros(url)["scope"].split()
    assert "https://outlook.office.com/IMAP.AccessAsUser.All" in scopes
    assert "https://outlook.office365.com/IMAP.AccessAsUser.All" not in scopes
    # Sin `offline_access` Microsoft tampoco entrega token de refresco.
    assert "offline_access" in scopes


def test_microsoft_sin_directorio_falla_explicito() -> None:
    with pytest.raises(oauth_correo.ErrorOAuth) as exc:
        oauth_correo.url_autorizacion(
            proveedor="microsoft", client_id="c", redirect_uri="u", state="s",
            code_challenge="ch", directorio=None, direccion="a@b.co",
        )
    assert exc.value.codigo == "directorio_ausente"


@pytest.mark.asyncio
async def test_un_canje_sin_refresh_token_se_rechaza(monkeypatch) -> None:
    """La cuenta funcionaría una hora y se apagaría sola.

    Aceptarlo dejaría un estado que parece correcto —la autorización «funcionó»—
    y falla al día siguiente sin que nadie haya tocado nada, que es el peor
    momento posible para descubrirlo.
    """

    async def _falso_post(self, url, data=None, **kw):  # noqa: ANN001
        return httpx.Response(200, json={"access_token": "at", "expires_in": 3599})

    monkeypatch.setattr(httpx.AsyncClient, "post", _falso_post)
    with pytest.raises(oauth_correo.ErrorOAuth) as exc:
        await oauth_correo.canjear_codigo(
            proveedor="gmail", client_id="c", client_secret="s", code="code",
            redirect_uri="u", code_verifier="v", directorio=None,
        )
    assert exc.value.codigo == "sin_refresh_token"


@pytest.mark.asyncio
async def test_el_error_del_proveedor_no_se_propaga_entero(monkeypatch) -> None:
    """Solo el código normalizado (RFC 6749 §5.2), nunca la descripción.

    El cuerpo de error de un proveedor puede reflejar los parámetros enviados,
    `client_secret` incluido; propagarlo tal cual lo llevaría al registro del
    servidor y a la pantalla.
    """

    async def _falso_post(self, url, data=None, **kw):  # noqa: ANN001
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "secreto reflejado: SUPERSECRETO123",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _falso_post)
    with pytest.raises(oauth_correo.ErrorOAuth) as exc:
        await oauth_correo.refrescar_token(
            proveedor="gmail", client_id="c", client_secret="SUPERSECRETO123",
            refresh_token="rt", directorio=None,
        )
    assert "invalid_grant" in str(exc.value)
    assert "SUPERSECRETO123" not in str(exc.value)
