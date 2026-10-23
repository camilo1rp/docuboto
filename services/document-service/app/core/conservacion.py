# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Excepción de LECTURA OBLIGATORIA para una entidad suspendida (ADR-035, ADR-039).

La suspensión detiene la operación de una entidad, no sus obligaciones de transparencia: el
índice de información clasificada y reservada (Ley 1712/2014 art. 20) es público y se sirve con la
entidad suspendida (archive-service, `public_indice.py`). Para componerlo archive-service llama
a `GET /api/v1/internal/documentos/clasificados`; sin esta excepción `get_tenant_conn` lo vetaba y
el índice público respondía 503 justo cuando la entidad está suspendida.

Admisión por LISTA EXPLÍCITA (método + ruta), nunca por exclusión: una ruta nueva queda vetada
hasta que alguien la añada aquí a propósito. Se exige, juntas:

  1. entidad `suspended` aprovisionada y con schema (`AtencionPublica.SOLO_LECTURA`): nunca una
     `inactive` ni una sin aprovisionar;
  2. método + ruta de la lista (solo lectura);
  3. `X-Internal-Token` válido (comparación en tiempo constante).

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

_SERVICE = "document-service"
_YA_ASENTADAS: set[tuple[str, str, str, date]] = set()
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

#: (método, patrón de ruta completo). Solo la lectura content-free del índice del art. 20.
RUTAS_DE_CONSERVACION: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (metodo, re.compile(ruta))
    for metodo, ruta in (("GET", "/api/v1/internal/documentos/clasificados"),)
)


def es_ruta_de_conservacion(metodo: str, ruta: str) -> bool:
    # `fullmatch` y no `match` con `$`: `$` admite un `\n` final y la ruta no sería la exacta.
    return any(m == metodo.upper() and p.fullmatch(ruta) for m, p in RUTAS_DE_CONSERVACION)


def _token_valido(token: str | None) -> bool:
    configurado = settings.internal_service_token
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
    # Un asiento por (entidad, ruta normalizada, día UTC) y proceso: las fases de AIP/fixity
    # llaman a la misma ruta una vez por expediente en cada pasada y el audit_log no se puede
    # borrar. El día acota el volumen sin esconder que ESE día se admitió conservación; el
    # detalle por petición vive en los asientos propios de cada operación (WORM, PREMIS).
    clave = (slug, metodo.upper(), _normaliza(ruta), datetime.now(UTC).date())
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
        payload={"estado_entidad": "suspended", "metodo": metodo.upper(), "ruta": _normaliza(ruta)},
        canal="system",
    )
    if len(_YA_ASENTADAS) > 10_000:
        _YA_ASENTADAS.clear()
    _YA_ASENTADAS.add(clave)
    return True


def _normaliza(ruta: str) -> str:
    return re.sub(_UUID, "{id}", ruta)
