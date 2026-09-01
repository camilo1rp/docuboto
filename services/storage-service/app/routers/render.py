# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Generación del documento de salida (E23, RF-PRO-03).

**Es una ruta interna, sellada con `X-Internal-Token` y no gateada por permiso**
(D-02). El único llamante es document-service, que ya autorizó al funcionario
antes de componer el cuerpo: repetir aquí un `require_permission` obligaría a
propagar la identidad del usuario a un servicio que no la necesita, y un
endpoint inter-servicio se sella con token, no se gatea por permiso.

Este servicio **pinta; no decide qué dice el documento**. El cuerpo llega ya
combinado (document-service) y ya saneado (proxy BFF).
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.internal_auth import require_internal_token
from app.schemas.render import RenderDocumentoRequest, RenderDocumentoResponse
from app.services.render.factory import get_document_renderer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/render", tags=["render"])


@router.post(
    "/documento-internal",
    response_model=RenderDocumentoResponse,
    status_code=status.HTTP_200_OK,
    summary="Genera el documento de salida en PDF/A a partir del cuerpo ya combinado",
    dependencies=[Depends(require_internal_token)],
)
async def render_documento_internal(
    payload: RenderDocumentoRequest,
) -> RenderDocumentoResponse:
    """PDF/A del cuerpo recibido.

    `503 render_no_disponible` cuando este despliegue no tiene renderizador. No
    se degrada a un documento vacío ni a texto plano: un oficio que la entidad
    cree haber producido y no existe es peor que un error visible, porque el
    fallo aparecería cuando el ciudadano reclama.
    """
    renderer = get_document_renderer(settings)
    if renderer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "render_no_disponible",
                "detail": (
                    "Este despliegue no tiene renderizador de documentos "
                    "configurado (RENDER_ENGINE). No se genera un documento "
                    "incompleto en su lugar."
                ),
                "status": 503,
            },
        )

    perfil = payload.perfil or settings.render_pdfa_profile
    resultado = await renderer.render(
        payload.cuerpo_html, pagina=payload.pagina, perfil=perfil
    )

    if resultado.outcome != "generado" or resultado.contenido is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "render_fallido",
                "detail": str(resultado.detalle.get("motivo", "no se pudo generar")),
                "herramienta": resultado.herramienta,
                "status": 422,
            },
        )

    # Base64 y no `application/pdf` en crudo: quien llama necesita además el
    # formato y la herramienta para dejarlos en la traza de procedencia, y un
    # cuerpo binario no los lleva. El sobre se abre en document-service, que es
    # quien calcula la fixity y sube el fichero.
    return RenderDocumentoResponse(
        formato=resultado.formato,
        herramienta=resultado.herramienta,
        contenido_b64=base64.b64encode(resultado.contenido).decode("ascii"),
        bytes=len(resultado.contenido),
    )
