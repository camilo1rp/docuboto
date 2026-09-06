# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Quién leyó y quién descargó UN radicado (RF-BUS-10; Ley 594/2000 art. 22).

La traza existía y era buena: `document.radicado_consultado` al leer el
metadato y `document.anexo_descargado` al bajar el fichero, las dos con actor,
canal y nivel de seguridad. Lo que no existía era **la pregunta**: no había
ningún endpoint que respondiera «¿quién ha visto este radicado?».

Un expediente sí podía responderla —`GET /expedientes/{id}/historial-acceso`,
que incluye las lecturas de los radicados que contiene—, pero un radicado suelto
no está en ningún expediente, y esos son la mayoría. La única vía era
`/admin/auditoria`, que exige `USUA_PERM_ADMIN` y saber por qué acción filtrar:
la respuesta estaba guardada y no se podía pedir desde donde importa.

**Solo lecturas, por ADMISIÓN.** Un radicado acumula además creación, anulación,
reclasificación, actualización… y volcarlas aquí convertiría el «historial de
acceso» en un historial de todo, que es otra cosa y responde otra pregunta. Una
acción nueva no entra sola: hay que decidir si es un acceso. Es la misma lista
que ya usa archive-service para el historial del expediente, y a propósito: dos
listas que respondieran distinto a «¿esto es un acceso?» darían dos historiales
distintos del mismo hecho.
"""

from __future__ import annotations

import asyncpg

_COLS = "ts, service, actor, canal, action, object_ref"

#: Lista de ADMISIÓN. Idéntica a la de
#: `archive-service/app/repositories/audit_read.py` — ver el docstring.
_ACCIONES_ACCESO = (
    "document.radicado_consultado",
    "document.anexo_descargado",
)


class AuditReadRepository:
    """Lectura de `public.audit_log`. **Nunca escribe**: el único punto de
    escritura de la auditoría es `orpycamcp_common.audit` (ADR-010)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # La condición vive UNA sola vez y la comparten el recuento y la página. Si
    # estuvieran separadas, el total podría contar entradas que la página no
    # muestra —o al revés— y nadie se enteraría hasta que alguien sumara a mano.
    _WHERE = (
        "tenant_slug = $1 AND object_type = 'radicado' "
        "AND object_ref = $2 AND action = ANY($3::text[])"
    )

    def _params(self, tenant_slug: str, tracking_number: str) -> list:
        return [tenant_slug, tracking_number, list(_ACCIONES_ACCESO)]

    async def count_for_radicado(self, tenant_slug: str, tracking_number: str) -> int:
        total = await self._conn.fetchval(
            f"SELECT count(*) FROM public.audit_log WHERE {self._WHERE}",
            *self._params(tenant_slug, tracking_number),
        )
        return int(total or 0)

    async def list_for_radicado(
        self, tenant_slug: str, tracking_number: str, *, offset: int, limit: int
    ) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM public.audit_log WHERE {self._WHERE} "
                "ORDER BY ts DESC, id DESC LIMIT $4 OFFSET $5",
                *self._params(tenant_slug, tracking_number),
                limit,
                offset,
            )
        )
