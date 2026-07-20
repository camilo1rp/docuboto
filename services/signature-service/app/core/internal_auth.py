# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate D-02 servicio-a-servicio: ``X-Internal-Token`` para
``POST /internal/sign-indice`` (residual Ac. AGN 001/2024 art. 4.3.2.4,
reconciliación del sellado del índice — ver ``app/jobs`` en archive-service).

Mismo patrón que ``storage-service/app/core/internal_auth.py`` y el gate D-02
de document/archive-service, con una diferencia deliberada en el orden de
fail-closed: aquí la ausencia de configuración (``settings.signature_internal_token``
vacío) responde **503** ("este despliegue no tiene la reconciliación
configurada"), no 401 — es un problema de *configuración del servicio*, no de
la petición del llamante, y así lo distingue el archive-service que reintenta
esta llamada (``app/clients/signature_client.py::sign_indice_internal``).

Esta ruta NUNCA exige ``PERM_FIRMA``/``X-User-Id``: el sellado que produce se
atribuye a ``SYSTEM_RECONCILER_ID`` (``app/core/constants.py``), no a un
usuario humano — ``require_permission``/``get_actor`` (``app/core/clearance.py``)
son deliberadamente ajenos a esta ruta.

El api-gateway NO reenvía ``X-Internal-Token`` (no está en la whitelist
``FORWARDED_REQUEST_HEADERS`` de ``api-gateway/app/core/proxy.py``): aunque el
catch-all ``/api/v1/signature/`` del gateway enrute
``/api/v1/signature/internal/sign-indice`` hacia aquí (mismo patrón ya
aceptado para ``/api/v1/preservacion/`` en storage-service), un llamador
externo nunca puede aportar un token válido — el gate de abajo sigue siendo
la defensa real.
"""

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    configured = settings.signature_internal_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "internal_signing_not_configured",
                "detail": "SIGNATURE_INTERNAL_TOKEN no está configurado en este despliegue",
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
    # Comparación en tiempo constante (remediación de auditoría, Baja): `!=`
    # sobre strings corta en el primer byte distinto, filtrando por temporización
    # cuántos caracteres iniciales acertó un atacante que mida la latencia de la
    # respuesta. `hmac.compare_digest` no lo hace — mismo criterio ya aplicado en
    # `auth-service/app/core/internal_auth.py`. El fail-closed de arriba (token no
    # configurado / ausente) queda intacto, solo cambia ESTA comparación final.
    if not hmac.compare_digest(x_internal_token, configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 403,
            },
        )
