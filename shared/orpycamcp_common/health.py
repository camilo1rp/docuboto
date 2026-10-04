# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud: dos preguntas distintas, dos endpoints distintos.

**`/health` — vivacidad.** ¿Responde el proceso? Nada más. No toca base de
datos, ni Redis, ni ningún servicio vecino, y devuelve 200 mientras el proceso
conteste. Es lo que consulta el `healthcheck` del contenedor, y por eso **no
debe** depender de nada externo: reiniciar un servicio porque Postgres está
caído no arregla Postgres — tira el trabajo en vuelo, pierde las conexiones y
suma una tormenta de reinicios a un incidente que ya estaba en curso. Un
contenedor cuyo proceso responde está vivo aunque no pueda trabajar; eso es lo
que dice esta sonda y no más.

**`/health/ready` — disponibilidad.** ¿Puede este proceso atender tráfico
**ahora**? Aquí sí se comprueban las dependencias, y un fallo devuelve **503**
para que el balanceador deje de mandarle peticiones sin que nadie lo reinicie.

**Por qué no basta con una.** Tres servicios —signature, knowledge y mcp—
devolvían un 200 fijo sin tocar nada, y ese endpoint ERA el `healthcheck` del
contenedor: un servicio sin base de datos, sin Redis o con el consumidor de
eventos muerto se reportaba sano indefinidamente. Los demás hacían lo contrario:
metían la comprobación de dependencias en la sonda que gobierna los reinicios.

**Los detalles de fallo no se publican.** Varias sondas devolvían
``f"error: {exc}"``, y el texto de una excepción de red trae el destino:
``ConnectError: [Errno -2] Name or service not known: document-service:8003``.
Estas rutas son alcanzables sin autenticar, así que el mapa de servicios,
puertos y direcciones internas quedaba a la vista de cualquiera. Aquí el estado
es ``ok`` o ``error`` y el detalle va al log, que es donde lo necesita quien
atiende el incidente.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

ESTADO_OK = "ok"
ESTADO_ERROR = "error"


async def check(nombre: str, sonda: Callable[[], Awaitable[Any]]) -> str:
    """Ejecuta una sonda y la reduce a ``ok``/``error``, sin filtrar el detalle.

    El motivo real del fallo se registra con `logger.warning` incluyendo la
    excepción; lo que sale por HTTP es una palabra.
    """
    try:
        await sonda()
    except Exception as exc:
        logger.warning("Sonda de disponibilidad '%s' falló: %s", nombre, exc, exc_info=True)
        return ESTADO_ERROR
    return ESTADO_OK


def liveness(service: str) -> dict[str, str]:
    """Cuerpo de la sonda de vivacidad. Sin dependencias, por definición."""
    return {"status": "ok", "service": service}


async def consumer_report(
    redis: Any, estado: dict[str, Any] | None, deadletter_stream: str
) -> dict[str, Any]:
    """Qué ve ``/health/ready`` del consumidor de eventos y de su dead-letter.

    ``estado`` es ``events.consumer_status()[nombre]`` (o ``None`` si el
    consumidor no corre en este proceso). Devuelve el detalle numérico del último
    barrido de reclamación, el tamaño de la DLQ de Redis (``XLEN``) y un veredicto
    ``dead_letter``: ``degraded`` si hay entradas en la DLQ o el último barrido
    aparcó mensajes, ``ok`` si no.

    Solo informa: el consumidor nunca decide el 503 (el servicio atiende peticiones
    mientras reintenta); lo que se corrige es que el tamaño de la PEL y de la DLQ
    no se veía en ningún sitio. Un ``XLEN`` que falla no tumba la sonda: el campo
    queda en ``None`` y el veredicto no se degrada por algo que no se pudo medir.
    """
    dlq_len: int | None
    try:
        dlq_len = int(await redis.xlen(deadletter_stream))
    except Exception:
        logger.warning("XLEN de la dead-letter '%s' falló", deadletter_stream, exc_info=True)
        dlq_len = None
    estado = estado or {}
    ultimo = int(estado.get("dead_lettered", 0) or 0)
    degradado = bool(dlq_len) or ultimo > 0
    return {
        "dead_letter": "degraded" if degradado else "ok",
        "detalle": {
            "dead_letter_stream_len": dlq_len,
            "ultimo_barrido": estado.get("ultimo_barrido"),
            "reclamados": estado.get("reclamados", 0),
            "dead_lettered_ultimo_barrido": ultimo,
            "dead_lettered_total": estado.get("dead_lettered_total", 0),
            "reinicios": estado.get("reinicios", 0),
        },
    }
