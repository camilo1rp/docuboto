# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Aviso de envío postal DEVUELTO (B10, ítem 1): a quién y con qué contenido.

`document.envio.devuelto` lo emite document-service (outbox) cuando el operador postal
devuelve un envío, por callback o por transición manual. El evento solo trae
`shipment_id, radicado_id, operador, guia, estado`: ni asunto ni destinatario ni nivel.
Este módulo lee esos datos del propio tenant (mismo schema, nivel VIVO del radicado) en
vez de enriquecer el evento, que es un stream global y llevaría contenido desactualizado.

**Destinatarios** (un correo para cada uno, deduplicados por dirección):

1. el buzón de la **dependencia productora** del radicado. Un buzón no es una persona y
   no tiene nivel: a efectos de clearance cuenta como PUBLICA;
2. **quien radicó** (`radicados.registered_by`), si está activo y tiene correo; su
   clearance es el de su rol;
3. el **tenedor actual** del radicado (ADR-029): el buzón de su dependencia de ubicación
   (`ubicacion_dept_code`/`ubicacion_dept`) y la persona que tiene el paso de flujo
   (`flujo_usuario`). Quien radicó no es necesariamente quien tiene el trámite (la ventanilla
   radica y otro responde), y es quien debe reexpedir o notificar por otro medio;
4. si el radicado **responde a otro** (`responde_a`, p. ej. una PQRS), el buzón y el poseedor
   del flujo de la ENTRADA: de ellos depende el plazo de respuesta (Ley 1755 art. 14).

**El gate va en el `WHERE`, por destinatario** (RF-SEG-08): el contenido se pide con
`r.nivel_seguridad <= clearance` en la consulta. Sin fila, el aviso sale SIN contenido:
ni asunto, ni número de radicado, ni destinatario, ni dirección, ni motivo — solo el
operador y la guía. Con contenido y radicado RESERVADO o superior, el correo va
`sensible` (el cuerpo no se persiste) y su asunto no lleva el número.

`resolver_contexto_devolucion` hace I/O y es el ÚNICO sitio que decide quién ve qué;
`build_devolucion_notifications` es PURO y solo da formato a lo ya decidido.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import asyncpg
from orpycamcp_common.rbac import PUBLICA, resolve_caller_clearance

from app.schemas.notification import NotificationSend
from app.services.buzones import resolver_buzon

logger = logging.getLogger(__name__)

EVENTO_DEVUELTO = "document.envio.devuelto"

#: Desde este nivel (RESERVADA) el cuerpo del correo no se persiste.
_NIVEL_SENSIBLE = 2


@dataclass(frozen=True, slots=True)
class ContenidoDevolucion:
    """Lo que un destinatario CON clearance puede ver del envío devuelto."""

    tracking_number: str
    asunto: str | None
    destinatario: str | None
    direccion: str | None
    motivo: str | None
    nivel: int


@dataclass(frozen=True, slots=True)
class DestinatarioDevolucion:
    email: str
    #: `None` = la consulta con el gate no devolvió fila: el aviso sale sin contenido.
    contenido: ContenidoDevolucion | None


@dataclass(frozen=True, slots=True)
class ContextoDevolucion:
    shipment_id: str
    operador: str
    guia: str
    destinatarios: tuple[DestinatarioDevolucion, ...]


# El gate de clearance es el último predicado del WHERE: autorización y lectura son el
# mismo acto, sin comprobación previa que pueda desfasarse del nivel vigente.
_SQL_CONTENIDO = (
    "SELECT r.tracking_number, r.subject, r.nivel_seguridad, "
    "       s.destinatario, s.direccion, s.motivo_devolucion "
    "  FROM postal_shipment s JOIN radicados r ON r.id = s.radicado_id "
    " WHERE s.id = $1 AND s.estado = 'devuelto' AND r.nivel_seguridad <= $2"
)


async def resolver_contexto_devolucion(
    conn: asyncpg.Connection, shipment_id: str | uuid.UUID
) -> ContextoDevolucion | None:
    """Datos del aviso, o `None` si no hay a quién avisar (envío o radicado inexistentes).

    Un error de base de datos SE PROPAGA: el consumidor lo llama antes de reclamar el
    evento, así que sin ack vuelve al PEL y, agotadas las entregas, termina en la DLQ.
    """
    try:
        sid = shipment_id if isinstance(shipment_id, uuid.UUID) else uuid.UUID(str(shipment_id))
    except ValueError:
        logger.warning("document.envio.devuelto con shipment_id inválido: %r", shipment_id)
        return None

    # Operador y guía NO salen de una consulta que una `radicados`: no son contenido
    # clasificado y no deben depender del nivel de nadie.
    envio = await conn.fetchrow(
        "SELECT radicado_id, operador, guia FROM postal_shipment WHERE id = $1 AND estado = 'devuelto'",
        sid,
    )
    if envio is None:
        logger.warning("document.envio.devuelto: el envío %s no existe o no está devuelto", sid)
        return None
    # Enrutamiento: dependencia productora y autor. Ningún texto de esta fila sale en el correo.
    origen = await conn.fetchrow(
        "SELECT origin_dept_code, origin_dept, registered_by, ubicacion_dept_code, "
        "ubicacion_dept, flujo_usuario, responde_a FROM radicados WHERE id = $1",
        envio["radicado_id"],
    )
    if origen is None:
        logger.warning("document.envio.devuelto: el radicado del envío %s ya no existe", sid)
        return None

    candidatos: list[tuple[str, int]] = [
        (await resolver_buzon(conn, origen["origin_dept_code"], origen["origin_dept"]), PUBLICA)
    ]
    async def _persona(uid: uuid.UUID | None) -> None:
        if uid is None:
            return
        fila = await conn.fetchrow("SELECT email, is_active FROM auth_users WHERE id = $1", uid)
        if fila is not None and fila["is_active"] and fila["email"]:
            candidatos.append((fila["email"], await resolve_caller_clearance(conn, uid)))

    await _persona(origen["registered_by"])
    # Tenedor actual. Si ubicación = origen el buzón se deduplica abajo; no es un envío más.
    if origen["ubicacion_dept_code"] is not None or origen["ubicacion_dept"]:
        candidatos.append(
            (await resolver_buzon(conn, origen["ubicacion_dept_code"], origen["ubicacion_dept"]), PUBLICA)
        )
    await _persona(origen["flujo_usuario"])
    if origen["responde_a"] is not None:
        entrada = await conn.fetchrow(
            "SELECT ubicacion_dept_code, ubicacion_dept, flujo_usuario FROM radicados WHERE id = $1",
            origen["responde_a"],
        )
        if entrada is not None:
            if entrada["ubicacion_dept_code"] is not None or entrada["ubicacion_dept"]:
                candidatos.append(
                    (await resolver_buzon(conn, entrada["ubicacion_dept_code"], entrada["ubicacion_dept"]), PUBLICA)
                )
            await _persona(entrada["flujo_usuario"])

    destinatarios: list[DestinatarioDevolucion] = []
    vistos: set[str] = set()
    for email, clearance in candidatos:
        clave = email.strip().lower()
        if clave in vistos:  # misma bandeja: se queda la primera (la de menor nivel va antes)
            continue
        vistos.add(clave)
        fila = await conn.fetchrow(_SQL_CONTENIDO, sid, clearance)
        contenido = (
            ContenidoDevolucion(
                tracking_number=fila["tracking_number"],
                asunto=fila["subject"],
                destinatario=fila["destinatario"],
                direccion=fila["direccion"],
                motivo=fila["motivo_devolucion"],
                nivel=int(fila["nivel_seguridad"]),
            )
            if fila is not None
            else None
        )
        destinatarios.append(DestinatarioDevolucion(email=email, contenido=contenido))
    return ContextoDevolucion(
        shipment_id=str(sid),
        operador=envio["operador"],
        guia=envio["guia"],
        destinatarios=tuple(destinatarios),
    )


def build_devolucion_notifications(
    envelope: dict, *, contexto: ContextoDevolucion
) -> list[NotificationSend]:
    """Constructor PURO: da formato a lo que `resolver_contexto_devolucion` ya decidió.

    `contexto` es keyword-only y sin valor por defecto, como el `buzon_destino` de los otros
    constructores: un llamante que lo olvide falla al escribirse, no manda un aviso vacío.
    """
    tenant_slug = envelope.get("tenant_slug")
    envios: list[NotificationSend] = []
    for d in contexto.destinatarios:
        c = d.contenido
        if c is None:
            envios.append(
                NotificationSend(
                    recipient_email=d.email,
                    subject="[OrpycaMCP] Envío postal devuelto",
                    body=(
                        "Un envío postal a cargo de su dependencia fue devuelto por el operador "
                        f"{contexto.operador} (guía {contexto.guia}). Consúltelo en OrpycaMCP.\n"
                    ),
                    tenant_slug=tenant_slug,
                )
            )
            continue
        sensible = c.nivel >= _NIVEL_SENSIBLE
        envios.append(
            NotificationSend(
                recipient_email=d.email,
                # El asunto se PERSISTE aunque el cuerpo no: de un radicado reservado no lleva el número.
                subject=(
                    "[OrpycaMCP] Envío postal devuelto"
                    if sensible
                    else f"[OrpycaMCP] Envío postal devuelto: {c.tracking_number}"
                ),
                body=(
                    f"El envío postal del radicado {c.tracking_number} fue devuelto por el operador "
                    f"{contexto.operador}.\n\n"
                    f"Asunto: {c.asunto or 'N/A'}\n"
                    f"Destinatario: {c.destinatario or 'N/A'}\n"
                    f"Dirección: {c.direccion or 'N/A'}\n"
                    f"Guía: {contexto.guia}\n"
                    f"Motivo de la devolución: {c.motivo or 'N/A'}\n"
                    f"Inquilino: {tenant_slug}\n"
                ),
                tenant_slug=tenant_slug,
                sensible=sensible,
            )
        )
    return envios
