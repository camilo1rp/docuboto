# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Excepción de CONSERVACIÓN para una entidad suspendida (ADR-035 §8, C-6).

La suspensión de una entidad detiene su operación, no su obligación de conservar (Ley 594 art. 46;
Ac. AGN 003/2015): los plazos de los sellos de archivo y de los AIP siguen corriendo. Cuando se renueva
el ``ArchiveTimeStamp`` de una firma, archive reempaqueta el AIP con el XML vigente y para eso pregunta
cuál es (``signature.api.artefactos_vigentes``); sin esta excepción el veto de la entidad lo negaba y el
AIP se quedaba con el sello viejo mientras corría el plazo.

Solo la admiten las operaciones que la piden por nombre, y solo para una entidad ``suspended``
aprovisionada y con schema (``AtencionPublica.SOLO_LECTURA``): nunca una ``inactive`` ni una sin
aprovisionar. Cada admisión deja un asiento con el estado de la entidad."""

from __future__ import annotations

from datetime import UTC, date, datetime

import asyncpg
from orpycamcp_common import audit
from orpycamcp_common.tenant import AtencionPublica, atencion_publica

from signature.core.constants import SYSTEM_RECONCILER_ID

_SERVICE = "signature-service"
_YA_ASENTADAS: set[tuple[str, str, date]] = set()


async def admitir_conservacion(conn: asyncpg.Connection, *, slug: str, operacion: str) -> bool:
    """¿Se atiende ``operacion`` pese a que la entidad no es operativa? Si sí, deja el asiento."""
    try:
        if await atencion_publica(conn, slug) is not AtencionPublica.SOLO_LECTURA:
            return False  # inactive, sin aprovisionar o sin schema: sigue vetada
    except Exception:  # noqa: BLE001 — registro caído: se mantiene el veto (fail-closed)
        return False
    # Un asiento por (entidad, operación, día UTC) y proceso: la fase de reempaquetado la pide cada
    # pasada y el audit_log no se puede borrar. El día acota el volumen sin esconder que ESE día se
    # admitió conservación; el detalle vive en los asientos propios de cada operación.
    clave = (slug, operacion, datetime.now(UTC).date())
    if clave in _YA_ASENTADAS:
        return True
    await audit.append(
        conn,
        tenant_slug=slug,
        service=_SERVICE,
        action="entidad.conservacion_admitida",
        object_type="tenant",
        object_ref=slug,
        actor=str(SYSTEM_RECONCILER_ID),
        payload={"estado_entidad": "suspended", "operacion": operacion},
        canal="system",
    )
    if len(_YA_ASENTADAS) > 10_000:
        _YA_ASENTADAS.clear()
    _YA_ASENTADAS.add(clave)
    return True
