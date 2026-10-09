# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Gate D-02 de los endpoints internos de archive-service: ``X-Internal-Token``.

Nace con ``GET /api/v1/internal/expedientes/{ref}/indice/huella`` (AREA-04-09
Media-2): signature-service resuelve la huella del índice electrónico vigente
en el SERVIDOR, en vez de firmar el identificador del expediente. Mismo patrón
fail-closed que ``document-service.app.core.internal_auth``: sin token
configurado TODAS las llamadas se rechazan (401) y no hay modo «abierto» por
omisión; la comparación es en tiempo constante.
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
