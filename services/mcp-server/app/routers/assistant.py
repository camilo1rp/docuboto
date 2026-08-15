# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Endpoint HTTP del asistente conversacional (E18/ADR-019).

`POST /api/v1/assistant/message` -- contraparte del proxy BFF SvelteKit. La
orquestación (loop manual sobre Claude + dispatcher) vive en
`app.services.assistant`; este router es solo el LÍMITE HTTP: identidad de
cabeceras, degradación honesta y mapeo de errores.

DEGRADACIÓN HONESTA (RF-FIR-15): si el asistente no está configurado (sin
`ANTHROPIC_API_KEY`) o el SDK falla en tiempo de ejecución, se responde
`503 assistant_unavailable` -- nunca un 500 crudo ni una respuesta fingida. El
proxy del frontend ya traduce ese 503 al estado "asistente no disponible".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.schemas.assistant import AssistantMessageRequest, AssistantMessageResponse
from app.services.assistant import ConversationLimitError, run_turn
from app.services.identity import IdentityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "assistant_unavailable",
            "detail": "El asistente aún no está disponible.",
            "status": 503,
        },
    )


@router.post(
    "/message",
    response_model=AssistantMessageResponse,
    summary="Turno del asistente conversacional (E18/ADR-019)",
)
async def assistant_message(
    payload: AssistantMessageRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
) -> AssistantMessageResponse:
    # Degradación honesta ANTES de tocar el SDK: sin cliente Anthropic
    # configurado, el asistente "no está disponible" (nunca un 500 ni una
    # respuesta simulada). `assistant_available` lo fija el lifespan.
    if not getattr(request.app.state, "assistant_available", False):
        raise _unavailable()

    # La identidad la valida el api-gateway aguas arriba (reescribe X-User-Id/
    # X-Tenant-Slug tras validar el token); estos guardas son defensa en
    # profundidad para el caso de que el mcp-server se exponga sin el gateway.
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthenticated", "detail": "Falta el token.", "status": 401},
        )
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_tenant", "detail": "Falta X-Tenant-Slug.", "status": 400},
        )

    try:
        return await run_turn(
            mensaje=payload.mensaje,
            conversation_id=payload.conversation_id,
            tenant_slug=x_tenant_slug,
            authorization=authorization,
            gateway_client=request.app.state.gateway_client,
            anthropic_client=request.app.state.anthropic_client,
            conversations=request.app.state.conversations,
        )
    except IdentityError as exc:
        # Fallo al resolver permisos/identidad contra el auth-service (vía
        # gateway): se propaga el estado real (401 sin token válido, 502/503 si
        # el gateway/auth no responde), sin filtrar detalle.
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.code, "status": exc.status},
        ) from exc
    except ConversationLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conversation_limit",
                "detail": "La conversación alcanzó su límite de turnos; inicia una nueva.",
                "status": 409,
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        # Cualquier fallo del SDK de Anthropic (AuthenticationError, APIError,
        # APIConnectionError, RateLimitError...) o error inesperado del loop ->
        # degradación honesta 503, NUNCA un 500 crudo ni una respuesta fingida
        # (RF-FIR-15). Se registra con traza para diagnóstico.
        logger.warning("assistant run_turn failed -> 503 assistant_unavailable", exc_info=True)
        raise _unavailable() from exc
