# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate D-02 servicio-a-servicio: ``X-Internal-Token`` para
``/internal/signing-key/*`` (E17/F4, Incremento 1) — mismo patrón que
``signature-service/app/core/internal_auth.py`` y ``storage-service``.

``POST /internal/signing-key/sign-digest`` combina ESTE gate CON la
identidad derivada del Bearer reenviado (D1, cierra IDOR) — ver
``app/routers/signing_key.py``: el token demuestra que el LLAMADOR es
signature-service; el Bearer demuestra QUIÉN firma. Ninguno sustituye al
otro. ``POST /internal/signing-key/activate`` (carga del certificado emitido
offline) usa SOLO este gate — es una operación administrativa/de sistema sin
un usuario humano interactivo detrás.
"""

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    configured = settings.signing_key_internal_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "internal_signing_key_not_configured",
                "detail": "SIGNING_KEY_INTERNAL_TOKEN no está configurado en este despliegue",
                "status": 503,
            },
        )
    if x_internal_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_internal_token",
                "detail": "Se requiere el header X-Internal-Token",
                "status": 401,
            },
        )
    if not hmac.compare_digest(x_internal_token, configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 403,
            },
        )
