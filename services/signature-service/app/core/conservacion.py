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
el ``ArchiveTimeStamp`` de una firma, archive-service reempaqueta el AIP con el XML vigente y para eso
pregunta aquí cuál es (``POST /internal/artefactos-vigentes``); sin esta excepción ``get_tenant_conn``
lo vetaba (403) en una entidad suspendida y el AIP se quedaba con el sello viejo mientras corría el
plazo. Mismo patrón que ``storage-service/app/core/conservacion.py``.

Admisión por LISTA EXPLÍCITA y no por exclusión: una ruta nueva queda vetada hasta que alguien la añada
aquí a propósito. Se exige, juntas:

  1. entidad ``suspended`` aprovisionada y con schema (``AtencionPublica.SOLO_LECTURA``): nunca una
     ``inactive`` ni una sin aprovisionar;
  2. método + ruta de la lista;
  3. ``X-Internal-Token`` válido (comparación en tiempo constante).

Cada admisión deja un asiento con el estado de la entidad."""

from __future__ import annotations

import hmac
import re
from datetime import UTC, date, datetime

import asyncpg
from orpycamcp_common import audit
from orpycamcp_common.tenant import AtencionPublica, atencion_publica

from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID

_SERVICE = "signature-service"
_YA_ASENTADAS: set[tuple[str, str, str, date]] = set()

#: (método, patrón de ruta completo). Solo lo que usa la fase de reempaquetado del AIP de archive-service.
RUTAS_DE_CONSERVACION: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (metodo, re.compile(ruta))
    for metodo, ruta in (("POST", "/api/v1/signature/internal/artefactos-vigentes"),)
)


def es_ruta_de_conservacion(metodo: str, ruta: str) -> bool:
    # `fullmatch` y no `match` con `$`: `$` admite un `\n` final y la ruta no sería la exacta.
    return any(m == metodo.upper() and p.fullmatch(ruta) for m, p in RUTAS_DE_CONSERVACION)


def _token_valido(token: str | None) -> bool:
    configurado = settings.signature_internal_token
    # Sobre BYTES: `compare_digest` con `str` no-ASCII lanza TypeError (500).
    return bool(
        configurado and token and hmac.compare_digest(token.encode(), configurado.encode())
    )


async def admitir_conservacion(
    conn: asyncpg.Connection, *, slug: str, metodo: str, ruta: str, token: str | None
) -> bool:
    """¿Se atiende esta petición pese a que la entidad no es operativa? Si sí, deja el asiento."""
    if not (es_ruta_de_conservacion(metodo, ruta) and _token_valido(token)):
        return False
    try:
        if await atencion_publica(conn, slug) is not AtencionPublica.SOLO_LECTURA:
            return False  # inactive, sin aprovisionar o sin schema: sigue vetada
    except Exception:  # noqa: BLE001 — registro caído: se mantiene el veto (fail-closed)
        return False
    # Un asiento por (entidad, ruta, día UTC) y proceso: la fase de reempaquetado llama a esta ruta cada
    # pasada y el audit_log no se puede borrar. El día acota el volumen sin esconder que ESE día se
    # admitió conservación; el detalle vive en los asientos propios de cada operación.
    clave = (slug, metodo.upper(), ruta, datetime.now(UTC).date())
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
        payload={"estado_entidad": "suspended", "metodo": metodo.upper(), "ruta": ruta},
        canal="system",
    )
    if len(_YA_ASENTADAS) > 10_000:
        _YA_ASENTADAS.clear()
    _YA_ASENTADAS.add(clave)
    return True
