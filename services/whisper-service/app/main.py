# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
whisper-service — transcripción de voz EN LA PROPIA ENTIDAD (ADR-020).

Existe para que dictar no signifique mandar la voz de un funcionario a un
tercero. El reconocedor del navegador no sirve para eso: en Chrome y Edge la Web
Speech API clásica envía el audio a un servicio de Google, así que el
`VoiceInput` del frontend solo se ofrece cuando el navegador reconoce en el
dispositivo. Para todos los demás casos, el audio llega aquí y no sale.

## Es INTERNO, y por eso no lo enruta el gateway

Ni `/api/v1/whisper` ni nada parecido: solo el `mcp-server` de esta red le habla,
igual que storage-service sirve bytes solo al mediador de document-service
(D-02). No expone nada que un cliente externo deba alcanzar, y una superficie de
red que nadie necesita es superficie que alguien acabará usando.

## Arranca desactivado

Va bajo el perfil `assistant` de Docker Compose, con Ollama y los demás
servicios de inferencia. Una instalación mínima corre sin él —y sin descargar
ningún modelo— y `POST /api/v1/assistant/transcribe` responde 503 diciendo cómo
activarlo. La decisión de ADR-020: los servicios pesados de IA son opt-in.

## El modelo se carga una vez

`faster-whisper` tarda segundos en cargar y megabytes en ocupar. Se hace en el
`lifespan` para que la primera transcripción no pague ese coste y para que un
modelo mal configurado se note **al arrancar** y no la primera vez que alguien
dicta.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from app.core.config import settings

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Import perezoso: `faster_whisper` arrastra CTranslate2 y tarda en
    # importarse. Aquí dentro, el fallo de una dependencia ausente sale como un
    # arranque fallido con su motivo, no como un ImportError al importar el
    # módulo desde un test.
    from faster_whisper import WhisperModel

    logger.info(
        "Cargando modelo Whisper «%s» (%s, %s)…",
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
    )
    app.state.model = WhisperModel(
        settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        download_root=settings.whisper_cache_dir,
    )
    logger.info("Modelo listo. La voz no sale de esta instalación.")
    yield
    app.state.model = None


app = FastAPI(
    title="OrpycaMCP — whisper-service",
    description=(
        "Transcripción de voz en la propia entidad (ADR-020). Servicio INTERNO: "
        "solo lo alcanza el mcp-server, nunca el exterior."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", summary="Vivacidad")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", summary="¿Está el modelo cargado?")
async def ready(request: Request) -> dict[str, object]:
    """Distingue «vivo» de «listo»: cargar el modelo tarda, y durante ese rato el
    proceso responde pero no puede transcribir."""
    cargado = getattr(request.app.state, "model", None) is not None
    return {"status": "ok" if cargado else "loading", "model": settings.whisper_model}


def _exigir_token_interno(x_internal_token: str = Header(default="")) -> None:
    """Gate D-02, fail-closed. Copia del criterio de los otros cinco servicios.

    Este era el único servicio interno SIN ningún gate: el comentario daba por
    hecho que bastaba con que el gateway no lo enrutara, y eso no es una
    frontera de seguridad sino una tabla de ruteo. Dentro de la red de
    contenedores cualquiera podía hacerle transcribir audio arbitrario, y lo
    hacía además como root y sin tope de tamaño.

    Sin token configurado se responde 401 y no se atiende: un servicio que
    escucha sin saber quién debe llamarle está abierto, no «pendiente».
    """
    configurado = settings.internal_service_token
    if not configurado:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "internal_token_no_configurado",
                "detail": "El servicio no tiene token interno configurado.",
                "status": 401,
            },
        )
    if not hmac.compare_digest(x_internal_token, configurado):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 403,
            },
        )


@app.post(
    "/transcribe",
    summary="Audio → texto",
    dependencies=[Depends(_exigir_token_interno)],
)
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form(default=""),
) -> dict[str, object]:
    """Transcribe el audio recibido. Devuelve lo que se oyó, o nada.

    Un audio sin habla devuelve `text: ""`, que **no es un error**: es lo que
    había. Quien llama decide qué decirle a la persona. Inventar una frase
    plausible sería el único fallo grave posible en este servicio.
    """
    # El tope va ANTES de traer los bytes: Starlette ya spooled la parte y
    # conoce su tamaño, así que rechazar aquí es gratis. Leer primero y medir
    # después sería pagar el coste entero de la subida que se va a rechazar.
    tope = settings.max_audio_bytes
    if file.size is not None and file.size > tope:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "audio_demasiado_grande",
                "detail": f"El audio supera el máximo admitido ({tope} bytes).",
                "status": 413,
            },
        )

    # Lectura acotada: el chequeo anterior depende de que el parser declare el
    # tamaño, éste no depende de nada.
    audio = await file.read(tope + 1)
    if len(audio) > tope:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "audio_demasiado_grande",
                "detail": f"El audio supera el máximo admitido ({tope} bytes).",
                "status": 413,
            },
        )
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "audio_vacio", "detail": "No llegó audio."},
        )

    # El modelo se comprueba DESPUÉS del tope, y el orden importa: mientras el
    # modelo carga —que tarda— el servicio ya está escuchando, y comprobarlo
    # primero significaba aceptar y almacenar audios de cualquier tamaño durante
    # justo la ventana en la que menos memoria libre hay. Lo barato va delante.
    modelo = getattr(request.app.state, "model", None)
    if modelo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "modelo_no_cargado", "detail": "El modelo aún se está cargando."},
        )

    # `faster-whisper` lee de una ruta o de un objeto tipo fichero; se escribe a
    # un temporal con el sufijo original porque el decodificador usa la
    # extensión para elegir el demuxer, y el navegador manda `webm/opus`.
    sufijo = Path(file.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=sufijo, delete=True) as tmp:
        tmp.write(audio)
        tmp.flush()
        segmentos, info = modelo.transcribe(
            tmp.name,
            language=(language or settings.whisper_language) or None,
            # El dictado del asistente es una o dos frases: sin VAD, un silencio
            # al final se convierte en repeticiones del último fragmento, que es
            # el artefacto clásico de Whisper y aquí acabaría en el compositor.
            vad_filter=True,
        )
        texto = "".join(s.text for s in segmentos).strip()

    return {
        "text": texto,
        "language": getattr(info, "language", "") or (language or settings.whisper_language),
        "model": settings.whisper_model,
    }
