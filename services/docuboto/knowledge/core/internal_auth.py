# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate de las rutas internas de la pasarela.

El gateway no las enruta (el segmento ``internal`` se rechaza). Este token es
la segunda capa: sin ``INTELLIGENCE_INTERNAL_TOKEN`` no hay modo abierto.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from knowledge.core.config import settings


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    configured = settings.intelligence_internal_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "internal_access_not_configured",
                "detail": "Este endpoint requiere INTELLIGENCE_INTERNAL_TOKEN configurado",
                "status": 401,
            },
        )
    if x_internal_token is None or not hmac.compare_digest(x_internal_token, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 401,
            },
        )
