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

from urllib.parse import unquote

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_DOT_SEGMENTS = {".", ".."}


#: Caracteres que NO pueden aparecer en una ruta ya percent-decodificada.
#:
#: La dirección de destino se construye concatenando esta ruta, y el cliente HTTP
#: la vuelve a analizar como URL. Un `?` o un `#` decodificados pasan entonces a
#: delimitar la consulta o el fragmento, de modo que la ruta que el gateway
#: AUTORIZÓ y la que el servicio SIRVE dejan de ser la misma. El registro de
#: acceso guarda la primera; el efecto lo produce la segunda.
#:
#: Es la misma clase de desajuste que motivó el filtro de segmentos punto, dejada
#: abierta para otros dos caracteres. Se cierra por ADMISIÓN de lo que puede
#: aparecer, no enumerando lo prohibido: `%00` y compañía caen solos.
_CARACTERES_PROHIBIDOS = frozenset({"?", "#", "\x00"})


def has_dot_segments(path: str) -> bool:
    """True si *path* contiene algún segmento `.` o `..`."""
    return any(segment in _DOT_SEGMENTS for segment in path.split("/"))


def tiene_caracteres_prohibidos(path: str) -> bool:
    """True si la ruta trae un carácter que cambiaría su significado al decodificarse.

    Se comprueba sobre la forma DECODIFICADA, y ése era el detalle que fallaba en
    la primera versión: `request.url.path` conserva el `%3F` tal cual, así que
    buscar `?` ahí no encuentra nada. Quien decodifica es el enrutador de
    FastAPI, más abajo, y para entonces la ruta ya cambió de significado.
    """
    return any(c in unquote(path) for c in _CARACTERES_PROHIBIDOS)


class PathNormalizationMiddleware(BaseHTTPMiddleware):
    """Rechaza con 400 toda ruta con segmentos `.` o `..`.

    Va montado como middleware MÁS EXTERNO: la comprobación debe ocurrir antes
    que la autenticación y que el ruteo, que son justo las dos decisiones que el
    salto de ruta engaña.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Se mira `scope["path"]`, NO `request.url.path`.
        #
        # `request.url` reconstruye la dirección como cadena y la vuelve a
        # analizar, y en ese viaje de ida y vuelta un `?` que estaba DENTRO de la
        # ruta se convierte en el separador de la consulta: la comprobación no lo
        # encontraba nunca. Es exactamente el defecto que esta guardia persigue,
        # cometido dentro de la guardia misma — se descubrió porque la prueba
        # seguía en rojo con la función ya correcta.
        ruta = request.scope.get("path", "")
        if has_dot_segments(ruta) or tiene_caracteres_prohibidos(ruta):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_path",
                    "detail": (
                        "La ruta no puede contener segmentos '.' ni '..' ni los "
                        "caracteres '?', '#' o nulos codificados."
                    ),
                    "status": 400,
                },
            )
        return await call_next(request)
