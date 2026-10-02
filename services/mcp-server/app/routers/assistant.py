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

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from app.core.config import settings
from app.schemas.assistant import AssistantMessageRequest, AssistantMessageResponse
from app.services import proposals, write_gate
from app.services.assistant import ConversationLimitError, run_turn
from app.services.identity import IdentityError, resolve_identity
from app.services.scope import build_scope
from app.services.stt import SttNoDisponible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


# Las dos causas de un 503 comparten código —el contrato no cambia— pero NO
# comparten mensaje, porque quien lo lee tiene que hacer cosas distintas: sin
# modelo configurado, esperar no arregla nada y hay que avisar a quien administra;
# con el proveedor caído, basta con reintentar. El texto anterior, «aún no está
# disponible», servía para las dos y no decía la verdad de ninguna — la interfaz
# lo remataba con «se activará próximamente», un resto de cuando el backend no
# existía.
SIN_MODELO = (
    "Esta instalación no tiene configurado un modelo de lenguaje, así que el asistente "
    "no puede responder. Quien administra el sistema puede activarlo."
)
FALLO_PROVEEDOR = (
    "El asistente no pudo responder ahora mismo: el proveedor del modelo no contestó. "
    "Inténtalo de nuevo en unos minutos."
)


def _unavailable(detalle: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "assistant_unavailable",
            "detail": detalle,
            "status": 503,
        },
    )


@router.post(
    "/confirmar",
    summary="Ejecuta una propuesta que la persona aprobó en la tarjeta de confirmación",
)
async def assistant_confirmar(
    request: Request,
    body: dict = Body(default_factory=dict),
    authorization: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
) -> dict:
    """La confirmación de la interfaz web.

    Vive aquí y no en `/api/v1/mcp/` porque `/api/v1/assistant/` es el único
    prefijo del mcp-server que el api-gateway enruta: sin esto, la tarjeta de
    confirmación no tendría a dónde llamar.

    El cuerpo lleva **el token** y, para los actos irreversibles, la **frase**
    tecleada. Los argumentos del acto NO viajan aquí: salen del token firmado.
    La frase se comprueba en `write_gate`, que sabe por el propio token si el
    acto es irreversible — el cliente no puede declarar que no lo era.
    """
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "propuesta_invalida", "detail": "falta el token", "status": 400},
        )
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

    gateway_client = request.app.state.gateway_client
    try:
        identidad = await resolve_identity(
            gateway_client, authorization=authorization, tenant_slug=x_tenant_slug
        )
        return await write_gate.ejecutar(
            gateway_client,
            token,
            identidad=identidad,
            authorization=authorization,
            tenant_slug=x_tenant_slug,
            frase=body.get("frase"),
        )
    except IdentityError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.code, "status": exc.status},
        )
    except proposals.ProposalError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.detail, "status": exc.status},
        )


@router.get(
    "/alcance",
    summary="Qué puede hacer esta persona en el sistema, y por dónde sí cuando no puede",
)
async def assistant_alcance(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
) -> dict:
    """Marco de alcance del llamante.

    Vive bajo `/api/v1/assistant/` y no bajo `/api/v1/mcp/` por una razón
    concreta: es el único prefijo del mcp-server que el api-gateway enruta, así
    que es el único por el que la interfaz web puede preguntar. Y la interfaz
    tiene que poder: el marco existe para que la persona sepa qué puede hacer
    **sin** tener que preguntárselo al asistente.

    No exige que el asistente esté disponible. Saber qué se puede hacer no
    depende de que haya un modelo detrás, y negar esta respuesta cuando el
    asistente está caído dejaría a la persona sin la información justo cuando más
    desorientada está.
    """
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

    gateway_client = request.app.state.gateway_client
    try:
        identidad = await resolve_identity(
            gateway_client, authorization=authorization, tenant_slug=x_tenant_slug
        )
        return await build_scope(
            gateway_client,
            identidad,
            authorization=authorization,
            tenant_slug=x_tenant_slug,
        )
    except IdentityError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.code, "status": exc.status},
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
        raise _unavailable(SIN_MODELO)

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
        raise _unavailable(FALLO_PROVEEDOR) from exc


@router.get(
    "/transcribe",
    summary="¿Hay transcripción disponible en esta instalación?",
)
async def assistant_transcribe_disponible(request: Request) -> dict:
    """Capacidad, para que la interfaz no ofrezca un botón que va a fallar.

    Sin esto, el frontend solo puede averiguarlo **intentándolo**: la persona
    graba, espera, y recibe un 503. Preguntar antes es el mismo criterio que el
    marco de alcance — no ofrecer lo que el backend va a negar.

    No exige autenticación: no dice nada de nadie, solo si esta instalación tiene
    activada la transcripción. Que el propio dictado sí la exija es otra cosa.
    """
    proveedor = request.app.state.stt_provider
    return {
        "disponible": proveedor.nombre != "disabled",
        "proveedor": proveedor.nombre,
        # Se dice DÓNDE se procesa. Es la pregunta que importa cuando lo que se
        # manda es la voz de un funcionario público.
        "procesa_en": (
            "la propia entidad" if proveedor.nombre == "whisper" else "ningún sitio"
        ),
    }


@router.post(
    "/transcribe",
    summary="Audio → texto (STT). El audio NO sale de la entidad.",
)
async def assistant_transcribe(
    request: Request,
    file: UploadFile = File(...),
    idioma: str = Form(default="es"),
    authorization: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
) -> dict:
    """Transcribe un dictado, en servidor y sin terceros (ADR-020).

    Es el camino que funciona en **cualquier** navegador. El `VoiceInput` del
    frontend usa el reconocedor del dispositivo cuando existe, pero en Chrome y
    Edge la Web Speech API clásica manda el audio a Google, así que allí no se
    ofrece: para esos navegadores, esto es lo que hay, y aquí el audio no cruza
    la red de la entidad.

    **Nunca devuelve texto que no se haya oído.** Sin proveedor configurado
    responde 503 diciendo qué falta; no hay un modo «de prueba» que aproxime,
    porque una transcripción inventada acabaría en el cuerpo de un documento
    oficial (ver `app/services/stt.py`).

    Se gatea por autenticación y nada más, igual que el resto del asistente: es
    transporte de la propia voz de quien llama, no acceso a información de
    nadie. Lo que se haga con el texto después sí pasa por su RBAC.
    """
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

    # El tope se aplica ANTES de traer los bytes, y el comentario anterior decía
    # lo contrario: sostenía que había que leer primero porque el trasvase ya
    # había ocurrido. Es cierto que el análisis del multipart ya escribió la
    # parte, pero de ahí no se sigue que haya que copiarla a memoria: el tamaño
    # se conoce sin leer, y leer es justamente lo que se quiere evitar. Un
    # comentario que justifica el orden equivocado lo perpetúa.
    tope = settings.stt_max_bytes
    if file.size is not None and file.size > tope:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "audio_demasiado_grande",
                "detail": f"El audio supera el máximo admitido ({tope} bytes).",
                "status": 413,
            },
        )

    # Lectura acotada: el chequeo anterior depende de que el analizador declare
    # el tamaño; ésta no depende de nada.
    audio = await file.read(tope + 1)
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "audio_vacio", "detail": "No llegó audio.", "status": 400},
        )
    if len(audio) > settings.stt_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "audio_demasiado_grande",
                "detail": (
                    f"El audio supera {settings.stt_max_bytes // (1024 * 1024)} MB. "
                    "Esto es para dictar al compositor, no para grabar una reunión."
                ),
                "status": 413,
            },
        )

    proveedor = request.app.state.stt_provider
    try:
        resultado = await proveedor.transcribir(
            audio,
            nombre_fichero=file.filename or "dictado.webm",
            mime=file.content_type or "application/octet-stream",
            idioma=idioma,
        )
    except SttNoDisponible as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": "stt_no_disponible", "detail": exc.detalle, "status": exc.status},
        ) from exc

    return {
        "texto": resultado.texto,
        "idioma": resultado.idioma,
        # Se dice QUIÉN transcribió y con qué modelo. Quien revisa un documento
        # que salió de un dictado tiene derecho a saber por dónde pasó su voz.
        "proveedor": resultado.proveedor,
        "modelo": resultado.modelo,
    }
