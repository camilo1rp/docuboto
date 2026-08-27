# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Constancia de radicación al remitente (E19 Increment 3).

**Encuadre honesto.** No es una obligación legal de oficio: la Ley 1755/2015
art. 15 exige la constancia «si quien presenta la petición solicita constancia de
haberla presentado», y la Circular Externa AGN 001/2020 —que la spec original
citaba como si lo obligara— no la menciona. Es una buena práctica que le da al
ciudadano el número con el que puede seguir su trámite, y por eso es **opt-in por
buzón**.

**Dos riesgos que el diseño cierra, y que no son teóricos:**

1. **Bucle de constancias.** Si el remitente es la propia cuenta —alguien reenvía
   desde el buzón, o un reenvío automático mal configurado— la constancia
   volvería a la carpeta de trabajo, se radicaría y generaría otra constancia. Un
   bucle que consume números de la secuencia oficial, que son inmutables. Se
   corta comprobando que el destinatario no sea la propia cuenta.

2. **Rebote a un tercero.** El `From` de un correo se falsifica trivialmente: un
   mensaje con remitente suplantado haría que la entidad mandara la constancia a
   quien nunca escribió. El riesgo es acotado —solo se acusa lo que se radica, y
   el contenido es un número de radicado, no información del expediente— pero
   existe, y por eso esto es opt-in y no el comportamiento por defecto.

El envío es **best-effort**: si falla, el radicado sigue existiendo. Su número ya
está asignado y es inmutable; deshacerlo por un fallo de correo sería mucho peor
que un ciudadano sin acuse. El fallo se registra para que alguien pueda verlo.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Comprobación deliberadamente laxa: no se trata de validar la dirección —eso lo
# hará el servidor de correo— sino de no llamar al servicio de notificaciones con
# algo que evidentemente no es una dirección, como el `From` de un correo
# malformado.
_PARECE_DIRECCION = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class ResultadoAcuse:
    enviado: bool
    motivo: str | None = None


def _cuerpo(tracking_number: str, asunto: str, recibido: str) -> str:
    """Texto de la constancia.

    Dice lo justo: que llegó, cuándo, con qué número y dónde consultarlo. **No
    dice qué se va a hacer ni en cuánto tiempo**: el plazo lo fija la ley según el
    tipo de trámite, y prometerlo aquí sería comprometer a la entidad a algo que
    esta constancia no puede saber.

    Tampoco repite el contenido de la comunicación: el correo de vuelta podría
    llegar a un buzón compartido o a quien no lo envió, si el remitente venía
    suplantado.
    """
    return (
        f"Su comunicación fue recibida y radicada.\n\n"
        f"Número de radicado: {tracking_number}\n"
        f"Asunto registrado: {asunto}\n"
        f"Fecha y hora de recibo: {recibido}\n\n"
        f"Conserve el número de radicado: es con el que puede consultar el estado "
        f"de su trámite.\n\n"
        f"Este es un mensaje automático; no responda a esta dirección."
    )


def destinatario_valido(direccion: str | None, *, direccion_cuenta: str) -> ResultadoAcuse:
    """Decide si se puede acusar recibo a `direccion`, y dice por qué no.

    Separado del envío para poder probarlo sin red y para que el motivo del «no»
    quede registrado: «no se envió porque el correo no traía remitente» y «no se
    pudo enviar» son cosas distintas para quien revise después.
    """
    if not direccion:
        return ResultadoAcuse(False, "el correo no trae dirección de remitente")
    limpia = direccion.strip().lower()
    if not _PARECE_DIRECCION.match(limpia):
        return ResultadoAcuse(False, f"la dirección del remitente no es válida: {direccion!r}")
    if limpia == direccion_cuenta.strip().lower():
        # Ver el riesgo 1 del docstring del módulo.
        return ResultadoAcuse(
            False, "el remitente es el propio buzón: no se acusa para no entrar en bucle"
        )
    return ResultadoAcuse(True)


async def enviar(
    *,
    tenant_slug: str,
    destinatario: str,
    tracking_number: str,
    asunto: str,
    recibido: str,
) -> ResultadoAcuse:
    """Pide a notification-service que envíe la constancia.

    Va por el endpoint sellado con `X-Internal-Token`: enviar correo con la
    identidad SMTP de la institución no puede quedar al alcance de cualquier JWT
    válido.
    """
    cuerpo = {
        "recipient_email": destinatario,
        "subject": f"Constancia de radicación {tracking_number}",
        "body": _cuerpo(tracking_number, asunto, recibido),
        "tenant_slug": tenant_slug,
    }
    cabeceras = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.notification_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.notification_service_url, timeout=15.0
        ) as cliente:
            respuesta = await cliente.post(
                "/api/v1/notifications/send", json=cuerpo, headers=cabeceras
            )
    except httpx.HTTPError as exc:
        return ResultadoAcuse(False, f"no se pudo contactar al servicio de correo: {exc}")

    if respuesta.status_code not in (200, 202):
        # El cuerpo del error no se propaga entero: puede reflejar la dirección
        # del destinatario, y este texto acaba en una columna que se lee desde la
        # interfaz.
        return ResultadoAcuse(
            False, f"el servicio de correo respondió {respuesta.status_code}"
        )
    return ResultadoAcuse(True)
