# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Rechazo de rutas con segmentos `.` o `..` en la frontera del gateway.

**Por qué existe este archivo.** El gateway decide DOS cosas sobre la ruta cruda
—si es pública (``middleware/auth.py::_is_public``) y a qué servicio va
(``routers/proxy.py::_route_to_upstream``, por ``startswith``)— y luego entrega
esa misma ruta a httpx, que aplica ``remove_dot_segments`` de RFC 3986 antes de
enviarla. Las dos capas ven rutas DISTINTAS, y esa diferencia es explotable:

    /api/v1/dependencias/../tenants/   se autoriza como «dependencias»
                                       y llega al upstream como /api/v1/tenants/

``/api/v1/tenants/`` se dejó deliberadamente FUERA de la tabla de ruteo como
remediación de un hallazgo Crítico (el registro de instituciones vive en el
schema ``public``): ese salto reabría el agujero. Y con
``/api/v1/public/../documents/`` el salto ocurre además ANTES de validar el JWT,
porque ``_is_public`` casa con el prefijo crudo.

**La defensa.** Rechazar, no reescribir. Reescribir en silencio deja al cliente
creyendo que pidió una cosa y al sistema sirviendo otra; y cualquier capa futura
que vuelva a mirar la ruta cruda —un log, una métrica, una regla de WAF—
heredaría de nuevo la discrepancia. Un 400 explícito es también la respuesta
correcta: ninguna ruta legítima de esta API contiene segmentos ``.`` ni ``..``.

**Por qué por segmentos y no comparando con ``normpath``.** ``normpath`` también
quita la barra final (``/api/v1/auth/`` → ``/api/v1/auth``) y colapsa las barras
dobles, ninguna de las dos cosas es un salto de ruta y ambas darían falsos
positivos sobre tráfico legítimo. Lo que httpx normaliza —y por tanto lo único
que abre el hueco— son exactamente los segmentos ``.`` y ``..``.

La comparación se hace sobre ``scope["path"]``, que uvicorn ya entrega
percent-decodificado: así ``%2e%2e`` y ``..`` se detectan por igual, sin tener
que enumerar codificaciones.
"""

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_DOT_SEGMENTS = {".", ".."}


def has_dot_segments(path: str) -> bool:
    """True si *path* contiene algún segmento `.` o `..`."""
    return any(segment in _DOT_SEGMENTS for segment in path.split("/"))


class PathNormalizationMiddleware(BaseHTTPMiddleware):
    """Rechaza con 400 toda ruta con segmentos `.` o `..`.

    Va montado como middleware MÁS EXTERNO: la comprobación debe ocurrir antes
    que la autenticación y que el ruteo, que son justo las dos decisiones que el
    salto de ruta engaña.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if has_dot_segments(request.url.path):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_path",
                    "detail": "La ruta no puede contener segmentos '.' ni '..'",
                    "status": 400,
                },
            )
        return await call_next(request)
