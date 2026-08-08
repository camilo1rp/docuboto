# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate D-02 del registro de instituciones (``/api/v1/tenants``).

**Por qué un token de servicio y no un permiso RBAC.** Las cinco rutas de
``routers/tenants.py`` operan sobre ``public.tenants`` —el registro de TODAS las
instituciones— con una conexión sin ``search_path`` de tenant. No hay ningún
permiso de la entidad A que deba autorizar a leer o renombrar la entidad B: el
alta y la edición de instituciones son operación de PLATAFORMA, no de
aplicación. Un gate RBAC del tenant sería la herramienta equivocada, porque el
sujeto correcto no es un usuario.

Hasta ahora el único control era que ``/api/v1/tenants/`` no estuviera en la
tabla de ruteo del gateway (remediación de un hallazgo Crítico previo). Eso no
es un control: es una ausencia, y bastaba un salto de ruta ``..`` para
atravesarla (cerrado en paralelo, ver ``api-gateway/app/middleware/path_guard.py``).
Las dos capas son deliberadamente independientes — si una falla, la otra sigue
en pie.

Fail-closed, igual que sus gemelos de storage/document/notification: sin token
configurado se rechazan TODAS las llamadas. Ese es el estado por defecto y es el
correcto: hoy no existe ningún consumidor legítimo por HTTP (el alta la hace
``scripts/init_tenant.py`` escribiendo directo a ``public.tenants``), así que un
despliegue que no configure nada deja el registro inalcanzable desde la red.
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
    # Comparación en tiempo constante: `!=` sobre strings corta en el primer byte
    # distinto y filtra por temporización cuántos caracteres acertó el atacante.
    if not hmac.compare_digest(x_internal_token, configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 403,
            },
        )
