# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate D-02 compartido: ``X-Internal-Token`` para endpoints que sirven

contenido/metadata de storage-service SOLO a mediadores internos (nunca al
exterior vía gateway — ver ``api-gateway/app/routers/proxy.py``).

Nació en ``routers/storage.py`` (remediación no-read-up de anexos, RF-SEG-08)
y se extrajo aquí para que ``routers/preservacion.py`` lo reutilice sin
importar un símbolo privado de otro router (E10 Increment C, cierre del
no-read-up del AIP mediado por archive-service). Mismo secreto
(``settings.internal_service_token``), misma política fail-closed: sin
token configurado, TODAS las llamadas se rechazan (401) — no hay modo
"abierto" por omisión.
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
    # Comparación en tiempo constante (remediación de auditoría, Baja): `!=`
    # sobre strings corta en el primer byte distinto, filtrando por temporización
    # cuántos caracteres iniciales acertó un atacante que mida la latencia de la
    # respuesta. `hmac.compare_digest` no lo hace — mismo criterio ya aplicado en
    # `auth-service/app/core/internal_auth.py`. El fail-closed de arriba (token no
    # configurado / ausente) queda intacto, solo cambia ESTA comparación final.
    if not hmac.compare_digest(x_internal_token.encode(), configured.encode()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 403,
            },
        )
