# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Barrido de vencimientos de disposición y alerta (E04, RF-TRD-08 + R.4.8).

Cierra lo que faltaba de RF-TRD-08 —«job programado evalúa vencimientos»— y de
RF-TRD-06 —«alerta a usuarios autorizados cuando vence la fecha de confirmación
sin confirmar»—. Son un solo trabajo: la alerta necesita que algo corra solo, y
ese algo es el barrido.

**Qué NO hace este job, y es lo primero que hay que saber**: no ejecuta ninguna
disposición. No elimina, no transfiere, no decide. Cuenta y avisa. La decisión
la toma una persona (`POST /trd/disposicion-vencida/{id}/decidir`) y la
ejecución (RF-TRD-07) todavía no existe. Un barrido que actuara solo sería
exactamente lo que la regla de negocio 4 del RT prohíbe.

**El aviso se compone POR DESTINATARIO, no una vez por tenant.** El recuento se
recorta por el clearance de quien lo recibe, igual que la cola: decirle «tienes
14 vencimientos» a quien solo alcanza 9 le está diciendo que existen 5
expedientes clasificados. Es la lección de la cola de anulaciones reapareciendo
en un sitio nuevo — allí el punto delicado era el total de la lista, aquí es el
número del aviso.

**El contenido del correo es deliberadamente pobre**: un número, un plazo y un
enlace. Ni números de radicado, ni asuntos, ni nombres de serie. Un correo sale
de la institución y acaba en bandejas que el SGDEA no gobierna; lo que hay que
mover es a la persona hasta la pantalla, no el archivo hasta su correo.

Cadencia: **el intervalo del bucle ES la cadencia del aviso**, a propósito. Así
no hay un «último enviado» por tenant que mantener sincronizado, y el aviso se
repite mientras el vencimiento siga sin decidirse. Lo que lo apaga es la
decisión, no haberlo visto.
"""

from __future__ import annotations

import logging

from app.clients.notification_client import NotificationDeliveryError, enviar_aviso
from app.core.config import settings
from app.core.database import acquire_tenant, get_pool
from app.jobs.indice_reconciliation import list_active_tenants
from app.repositories.archive_repository import ArchiveRepository

logger = logging.getLogger(__name__)

_ASUNTO = "Disposición documental pendiente de decisión"


def _cuerpo(*, total: int, dias: int) -> str:
    """El texto del aviso. Sin datos del archivo — ver el docstring del módulo."""
    plural = "expediente" if total == 1 else "expedientes"
    return (
        f"Hay {total} {plural} cuya retención documental venció hace {dias} días o más "
        "y sobre los que todavía no se ha tomado ninguna decisión.\n\n"
        "Vencer no significa eliminar: en Conservación Total corresponde transferir al "
        "archivo histórico y conservar permanentemente. Cada expediente indica qué acto "
        "le corresponde.\n\n"
        "Puede revisarlos en Administración → TRD / CCD, pestaña «Disposición vencida».\n\n"
        "Este aviso se repite mientras queden decisiones pendientes; lo que lo detiene es "
        "decidir, no haberlo leído."
    )


async def run_once() -> dict[str, int]:
    """Una barrida: por cada tenant activo, avisa a cada usuario autorizado con
    su propio recuento.

    Los fallos quedan **aislados por tenant y por destinatario** (WARNING, no
    abortan el resto): que el correo de una persona rebote no puede dejar sin
    avisar a las demás, ni a los demás tenants. Mismo criterio que las otras
    barridas de este servicio."""
    pool = get_pool()
    slugs = await list_active_tenants(pool)
    resumen = {"tenants": 0, "destinatarios": 0, "avisos": 0, "fallidos": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                repo = ArchiveRepository(conn, slug)
                destinatarios = await repo.list_destinatarios_disposicion()
                if not destinatarios:
                    # Ningún usuario con potestad para decidir. NO es un error
                    # del job -- es una entidad que aún no ha repartido ese
                    # permiso, y avisar a otro sería avisar a quien no puede
                    # actuar.
                    continue
                resumen["tenants"] += 1
                for d in destinatarios:
                    resumen["destinatarios"] += 1
                    total = await repo.contar_vencimientos_sin_decidir(
                        max_clearance=d["max_clearance"],
                        dias=settings.disposicion_alerta_dias_sin_decidir,
                    )
                    if total == 0:
                        # Para esta persona no hay nada pendiente A SU ALCANCE.
                        # Que otro con más clearance sí tenga trabajo no es
                        # asunto suyo, y decírselo sería la fuga.
                        continue
                    try:
                        await enviar_aviso(
                            recipient_email=d["email"],
                            subject=_ASUNTO,
                            body=_cuerpo(
                                total=total,
                                dias=settings.disposicion_alerta_dias_sin_decidir,
                            ),
                            tenant_slug=slug,
                        )
                        resumen["avisos"] += 1
                    except NotificationDeliveryError as exc:
                        resumen["fallidos"] += 1
                        logger.warning(
                            "Alerta de disposición: no se pudo avisar en tenant=%s: %s",
                            slug,
                            exc,
                        )
        except Exception:
            logger.warning(
                "Alerta de disposición: barrida fallida para tenant=%s", slug, exc_info=True
            )

    return resumen
