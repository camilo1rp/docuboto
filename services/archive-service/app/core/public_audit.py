# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Traza de los accesos PÚBLICOS ANÓNIMOS (OAI-PMH multinivel de expedientes, E11).

**Qué faltaba.** El OAI multinivel de expedientes se cosecha sin autenticación y
no dejaba un solo asiento: cero llamadas a `audit.append` en `routers/oai.py`. Una
entidad no podía responder a «¿quién se llevó nuestro cuadro de clasificación,
cuándo y cuánto?» — que es justo la pregunta que se hace después, no antes.

Gemelo de `document-service/app/core/public_audit.py`; se mantienen separados
porque cada servicio importa su propio `clearance`.

**Best-effort, a diferencia de las escrituras.** En las mutaciones el asiento va
DENTRO de la transacción y su fallo revierte el acto (patrón de `AnulacionService`).
Aquí no: esto es una lectura, y hacer que un fallo de auditoría tumbe una cosecha
convertiría el registro en un punto de caída de un servicio público. Mismo criterio
que la auditoría de lecturas de radicados, ya establecido en este servicio.

**Sin actor y sin IP.** El actor es `None` porque realmente no hay ninguno: inventar
uno sería peor que dejarlo vacío. La IP tampoco se registra aquí a propósito — hoy
llega en una cabecera que el cliente elige, así que anotarla daría una falsa
sensación de trazabilidad; entra cuando el gateway la derive por saltos de confianza.
"""

import logging
from typing import Any

import asyncpg

from app.core.clearance import _AUDIT_AVAILABLE, audit

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"


async def audit_public_access(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str,
    action: str,
    object_type: str,
    object_ref: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Asienta un acceso público anónimo. Nunca propaga excepciones."""
    if not _AUDIT_AVAILABLE or audit is None:
        return
    try:
        await audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action=action,
            object_type=object_type,
            object_ref=object_ref,
            actor=None,  # anónimo por definición — ver docstring del módulo
            canal="api",
            payload=payload or {},
        )
    except Exception:  # noqa: BLE001 — best-effort: una lectura pública no debe caerse por esto
        logger.warning("No se pudo auditar el acceso público %s", action, exc_info=True)
