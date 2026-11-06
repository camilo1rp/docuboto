# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
OAuth2 para el acceso IMAP de Google y Microsoft (E19 Increment 1).

Los valores de aquí no son configurables ni inventados: son los que publican los
dos proveedores para el acceso IMAP, y equivocarse en uno produce un error que
no explica nada. Se citan en cada constante.

**Por qué OAuth2 y no una contraseña**: Exchange Online retiró la autenticación
básica de IMAP a finales de 2022, y Google Workspace dejó de aceptar
usuario+contraseña —incluidas las contraseñas de aplicación— el 2025-05-01. Para
esas dos plataformas esto no es una alternativa más segura: es la única.

**PKCE aunque el cliente sea confidencial** (RFC 7636). El código de
autorización viaja por el navegador del administrador, donde puede quedar en el
historial, en un registro del servidor intermedio o en el `Referer`. Con PKCE, un
código interceptado no sirve sin el verificador, que nunca sale del servidor.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


@dataclass(frozen=True)
class Proveedor:
    autorizacion: str
    token: str
    scopes: tuple[str, ...]
    # Parámetros extra en la autorización. Google los necesita para entregar un
    # refresh token; Microsoft lo entrega con `offline_access` en el scope.
    extra_autorizacion: dict[str, str]


# Google: el scope `https://mail.google.com/` es el que cubre IMAP, SMTP y POP, y
# está clasificado como RESTRINGIDO — de ahí que la aplicación deba registrarla
# la propia entidad como interna de su organización (ver `email_app_oauth`).
#
# `access_type=offline` + `prompt=consent` son imprescindibles: sin ellos Google
# entrega un access token de una hora y NINGÚN refresh token, así que la ingesta
# dejaría de funcionar al día siguiente sin que nadie tocara nada.
GOOGLE = Proveedor(
    autorizacion="https://accounts.google.com/o/oauth2/v2/auth",
    token="https://oauth2.googleapis.com/token",
    scopes=("https://mail.google.com/",),
    extra_autorizacion={"access_type": "offline", "prompt": "consent"},
)

# Microsoft: el scope de IMAP delegado es `outlook.office.com`, NO
# `outlook.office365.com` — ese último es el del flujo de credenciales de
# aplicación, y usarlo aquí da un token que el servidor IMAP rechaza.
# `offline_access` es lo que hace que se entregue refresh token.
MICROSOFT = Proveedor(
    autorizacion="https://login.microsoftonline.com/{directorio}/oauth2/v2.0/authorize",
    token="https://login.microsoftonline.com/{directorio}/oauth2/v2.0/token",
    scopes=("https://outlook.office.com/IMAP.AccessAsUser.All", "offline_access"),
    extra_autorizacion={},
)

PROVEEDORES = {"gmail": GOOGLE, "microsoft": MICROSOFT}


class ErrorOAuth(RuntimeError):
    def __init__(self, mensaje: str, *, codigo: str = "oauth_error") -> None:
        super().__init__(mensaje)
        self.codigo = codigo


def generar_pkce() -> tuple[str, str]:
    """Devuelve `(verifier, challenge)` con S256 (RFC 7636 §4).

    El verificador es de 64 bytes en base64url sin relleno: 86 caracteres, dentro
    del rango 43–128 que exige la norma.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def generar_state() -> str:
    """Valor imprevisible contra CSRF (RFC 6749 §10.12)."""
    return secrets.token_urlsafe(48)


def _resolver(proveedor: str, directorio: str | None) -> tuple[Proveedor, str, str]:
    conf = PROVEEDORES.get(proveedor)
    if conf is None:
        raise ErrorOAuth(f"Proveedor sin OAuth2: {proveedor}", codigo="proveedor_sin_oauth")
    if proveedor == "microsoft" and not directorio:
        raise ErrorOAuth(
            "Falta el identificador del directorio de Azure AD de la aplicación",
            codigo="directorio_ausente",
        )
    dir_seguro = directorio or ""
    return (
        conf,
        conf.autorizacion.format(directorio=dir_seguro),
        conf.token.format(directorio=dir_seguro),
    )


def url_autorizacion(
    *,
    proveedor: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    directorio: str | None,
    direccion: str,
) -> str:
    """URL a la que se manda al administrador para que consienta.

    `login_hint` lleva la dirección del buzón: sin él, quien tenga varias cuentas
    abiertas en el navegador autoriza con la que el proveedor eligiera por su
    cuenta, y el refresh token quedaría emitido para el buzón equivocado — un
    error que solo se descubre cuando la ingesta trae el correo de otro.
    """
    conf, url_auth, _ = _resolver(proveedor, directorio)
    parametros = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(conf.scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "login_hint": direccion,
        **conf.extra_autorizacion,
    }
    return f"{url_auth}?{urlencode(parametros)}"


async def canjear_codigo(
    *,
    proveedor: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    directorio: str | None,
) -> dict[str, str]:
    """Canjea el código por tokens. Devuelve al menos `refresh_token`.

    Si el proveedor no entrega refresh token se falla **explícitamente**: la
    cuenta quedaría funcionando una hora y se apagaría sola, y el diagnóstico
    entonces sería «credenciales rechazadas» en un momento en que nadie tocó
    nada.
    """
    datos = await _pedir_token(
        proveedor,
        directorio,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )
    if not datos.get("refresh_token"):
        raise ErrorOAuth(
            "El proveedor no entregó un token de refresco, así que el acceso "
            "caducaría en una hora sin posibilidad de renovarlo. Revise que la "
            "aplicación pida acceso sin conexión (offline).",
            codigo="sin_refresh_token",
        )
    return datos


async def refrescar_token(
    *,
    proveedor: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    directorio: str | None,
) -> dict[str, str]:
    """Canjea el refresh token por uno de acceso, para una sesión IMAP."""
    return await _pedir_token(
        proveedor,
        directorio,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )


async def _pedir_token(
    proveedor: str, directorio: str | None, cuerpo: dict[str, str]
) -> dict[str, str]:
    _, _, url_token = _resolver(proveedor, directorio)
    try:
        async with httpx.AsyncClient(timeout=15.0) as cliente:
            respuesta = await cliente.post(url_token, data=cuerpo)
    except httpx.HTTPError as exc:
        raise ErrorOAuth(
            "No se pudo contactar con el servidor de autorización", codigo="proveedor_inalcanzable"
        ) from exc

    if respuesta.status_code != 200:
        # El cuerpo de error del proveedor puede traer el `client_secret`
        # reflejado; se toma SOLO el código de error normalizado (RFC 6749 §5.2),
        # nunca la descripción completa.
        try:
            codigo = str(respuesta.json().get("error", "invalid_request"))
        except ValueError:
            codigo = "respuesta_ilegible"
        raise ErrorOAuth(
            f"El proveedor rechazó la autorización ({codigo})", codigo="autorizacion_rechazada"
        )

    datos = respuesta.json()
    if not datos.get("access_token"):
        raise ErrorOAuth(
            "El proveedor no devolvió un token de acceso", codigo="respuesta_incompleta"
        )
    return dict(datos)
