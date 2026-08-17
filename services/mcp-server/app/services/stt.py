# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Transcripción de voz (STT) — proveedor conmutable, y **sin `stub`**.

ADR-020 decidió que el dictado se transcribe **en servidor con Whisper** y que el
audio no sale a terceros. El bloque 11 del plan MCP funcional intentó atajar con
el reconocedor del navegador, y ahí se descubrió que en Chrome y Edge ese camino
**manda el audio a Google** — así que el `VoiceInput` del frontend solo se ofrece
cuando el navegador reconoce en el dispositivo. Este módulo es el otro camino: el
que funciona en cualquier navegador sin que la voz salga de la entidad.

## Por qué aquí NO hay proveedor `stub`, a diferencia de los embeddings

`knowledge-service` tiene un `StubEmbeddingProvider` y está bien: un vectorizador
por *hashing* no es un vector falso, es **recuperación léxica de verdad**, más
pobre pero honesta, y sirve para trabajar sin descargar un modelo.

Con la voz no existe el equivalente. Cualquier «stub» de transcripción tendría
que **inventarse lo que la persona dijo**, y ese texto acabaría en el compositor
del asistente, o en el cuerpo de un documento oficial. No hay versión pobre pero
honesta de adivinar un audio.

Por eso el default es `disabled` y su comportamiento es **negarse**: el endpoint
responde 503 diciendo qué falta configurar. Un sistema que contesta «no puedo
transcribir» es utilizable; uno que devuelve una frase inventada como si la
hubiera oído, no — y el daño aparece cuando alguien la firma.

## Los proveedores

- `disabled` (**default**): no transcribe. 503 con el motivo.
- `whisper`: habla con el `whisper-service` de esta misma red, que corre
  `faster-whisper` en local. Es el camino soberano: el audio no cruza la red de
  la entidad y no toca ningún proveedor externo.

Añadir un proveedor en la nube sería una decisión de producto con consecuencias
de soberanía, y **no se deja preparada la costura para que sea fácil**: quien la
quiera tendrá que escribirla, verla y justificarla.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


class SttNoDisponible(Exception):
    """No hay transcripción posible. Nunca se devuelve texto inventado."""

    def __init__(self, detalle: str, *, status: int = 503) -> None:
        self.detalle = detalle
        self.status = status
        super().__init__(detalle)


@dataclass(frozen=True)
class Transcripcion:
    texto: str
    idioma: str
    proveedor: str
    modelo: str


class SttProvider(ABC):
    """Contrato de transcripción. Una implementación o ninguna: no hay término medio."""

    nombre: str

    @abstractmethod
    async def transcribir(
        self, audio: bytes, *, nombre_fichero: str, mime: str, idioma: str
    ) -> Transcripcion:
        """Devuelve lo que se oyó, o eleva `SttNoDisponible`. Nunca aproxima."""


class SttDeshabilitado(SttProvider):
    """El default. Se niega, y dice qué hacer.

    No es un hueco pendiente de rellenar: es la respuesta correcta mientras nadie
    haya decidido —y desplegado— dónde se transcribe la voz de sus funcionarios.
    """

    nombre = "disabled"

    async def transcribir(
        self, audio: bytes, *, nombre_fichero: str, mime: str, idioma: str
    ) -> Transcripcion:
        raise SttNoDisponible(
            "La transcripción de voz no está activada en esta instalación. "
            "Para habilitarla: STT_PROVIDER=whisper y levantar el perfil "
            "`assistant` de Docker Compose (`docker compose --profile assistant up -d "
            "whisper-service`). El audio se procesa en la propia entidad."
        )


class WhisperLocal(SttProvider):
    """`whisper-service` de esta red, con `faster-whisper` en CPU.

    El audio viaja del navegador al gateway, del gateway al mcp-server y de ahí a
    este servicio: todo dentro de la instalación. No hay ningún salto a un
    tercero, que es la propiedad que ADR-020 pedía y que el reconocedor del
    navegador no cumple en Chrome ni en Edge.
    """

    nombre = "whisper"

    def __init__(self, url: str, timeout: float) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout

    async def transcribir(
        self, audio: bytes, *, nombre_fichero: str, mime: str, idioma: str
    ) -> Transcripcion:
        try:
            async with httpx.AsyncClient(base_url=self._url, timeout=self._timeout) as c:
                resp = await c.post(
                    "/transcribe",
                    files={"file": (nombre_fichero, audio, mime)},
                    data={"language": idioma},
                )
        except httpx.RequestError as exc:
            # Se distingue de «no configurado»: aquí SÍ está activado y no
            # responde, que es otra conversación y otra cosa que arreglar.
            raise SttNoDisponible(
                "El servicio de transcripción no responde. ¿Está levantado el "
                "perfil `assistant`? (`docker compose --profile assistant up -d "
                f"whisper-service`). Detalle: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise SttNoDisponible(
                f"El servicio de transcripción devolvió {resp.status_code}.",
                status=502,
            )
        datos = resp.json()
        texto = str(datos.get("text") or "").strip()
        if not texto:
            # Silencio o ruido. NO es un error: es lo que se oyó. Se devuelve
            # vacío y quien llama decide qué decirle a la persona.
            texto = ""
        return Transcripcion(
            texto=texto,
            idioma=str(datos.get("language") or idioma),
            proveedor=self.nombre,
            modelo=str(datos.get("model") or "faster-whisper"),
        )


def build_stt_provider(nombre: str, *, whisper_url: str, timeout: float) -> SttProvider:
    """Fábrica. Un nombre desconocido cae en `disabled`, no en el más capaz.

    Un error tipográfico en `STT_PROVIDER` tiene que dejar el sistema sin
    transcribir y diciéndolo, nunca activando por descarte un camino que nadie
    eligió.
    """
    if nombre.strip().lower() == "whisper":
        return WhisperLocal(whisper_url, timeout)
    return SttDeshabilitado()
