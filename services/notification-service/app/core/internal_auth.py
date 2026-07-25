# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate ``X-Internal-Token`` para el endpoint SERVICIO-A-SERVICIO ``POST /send``.

`POST /api/v1/notifications/send` no es una acción de usuario: signature-service
lo invoca (directo, no vía gateway) para despachar los OTP de 2FA, y su cuerpo
—destinatario/asunto/body arbitrarios— sale por el SMTP de la institución. El
api-gateway, sin embargo, expone `/api/v1/notifications/` autenticando cualquier
JWT válido, así que sin este sello un usuario externo podría enviar correo
arbitrario con la identidad institucional (phishing/spam) y suplantar el propio
canal de OTP. Este gate lo restringe a los servicios que porten el secreto
compartido.

Copia deliberada del patrón de `storage-service/app/core/internal_auth.py`
(mismo `X-Internal-Token`, misma política **fail-closed**: sin
`INTERNAL_SERVICE_TOKEN` configurado TODA llamada se rechaza — no hay modo
"abierto" por omisión) y comparación en tiempo constante (`hmac.compare_digest`)
para no filtrar por temporización cuántos bytes iniciales acertó un atacante.
"""

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    configured = settings.internal_service_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "internal_access_not_configured",
                "detail": "Este endpoint requiere INTERNAL_SERVICE_TOKEN configurado",
                "status": 401,
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
