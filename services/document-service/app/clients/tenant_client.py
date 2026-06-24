# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente HTTP hacia tenant-service.

document-service NO replica el calendario de días hábiles (festivos colombianos
+ no laborables por tenant): lo resuelve tenant-service, dueño de esa
configuración (E14). Aquí sólo se consume su endpoint y se reenvía el contexto
de tenant (X-Tenant-Slug), igual que lo inyecta el api-gateway.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx


class TenantServiceError(Exception):
    """El cálculo del plazo no se pudo resolver contra tenant-service."""


class TenantServiceClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def calculate_due_date(
        self, *, tenant_slug: str, from_date: date, business_days: int
    ) -> date:
        """Devuelve la fecha de vencimiento a `business_days` hábiles de `from_date`.

        Reenvía X-Tenant-Slug para que tenant-service aplique el calendario del
        tenant correcto. Lanza TenantServiceError si la respuesta no es válida.
        """
        try:
            resp = await self._http.get(
                "/api/v1/business-days/calculate",
                params={"from": from_date.isoformat(), "days": business_days},
                headers={"X-Tenant-Slug": tenant_slug},
            )
            resp.raise_for_status()
            payload = resp.json()
            return date.fromisoformat(payload["due_date"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise TenantServiceError(f"No se pudo calcular la fecha de vencimiento: {exc}") from exc

    async def get_dependencia(self, *, tenant_slug: str, codigo: int) -> dict[str, Any] | None:
        """Resuelve una dependencia por código (organigrama del tenant, E14).

        Devuelve el objeto dependencia, o None si no existe (404). Lanza
        TenantServiceError ante fallos de transporte u otros códigos de error.
        """
        try:
            resp = await self._http.get(
                f"/api/v1/dependencias/by-codigo/{codigo}",
                headers={"X-Tenant-Slug": tenant_slug},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantServiceError(f"No se pudo resolver la dependencia {codigo}: {exc}") from exc
