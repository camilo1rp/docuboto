# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Gestor de token OAuth por sesión (E18, ADR-019).

El mcp-server obtiene y **refresca** el token de acceso Keycloak a través del
auth-service (endpoints públicos del gateway `/api/v1/auth/token` y `/refresh`),
en vez de depender de un bearer estático. Precedencia:

1. `MCP_BEARER_TOKEN` estático (sin refresco) si está definido.
2. `MCP_REFRESH_TOKEN` → refresh_token grant (se reusa el nuevo refresh).
3. `MCP_AUTH_USERNAME`/`MCP_AUTH_PASSWORD` (+ tenant) → ROPC.

El token se cachea hasta su expiración (con margen). En transporte HTTP, el
cliente ya aporta su propio bearer por cabecera, por lo que este gestor no interviene.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.core.config import settings


class TokenManager:
    def __init__(self) -> None:
        self._access: str | None = None
        self._refresh: str | None = settings.mcp_refresh_token or None
        self._exp: float = 0.0
        self._lock = asyncio.Lock()

    async def authorization(self, client: httpx.AsyncClient) -> str | None:
        if settings.mcp_bearer_token:
            return f"Bearer {settings.mcp_bearer_token}"
        async with self._lock:
            if self._access and time.time() < self._exp:
                return f"Bearer {self._access}"
            token = await self._obtain(client)
            return f"Bearer {token}" if token else None

    async def _obtain(self, client: httpx.AsyncClient) -> str | None:
        if self._refresh:
            data = await self._post(
                client, "/api/v1/auth/refresh", {"refresh_token": self._refresh}
            )
            if data:
                self._store(data)
                return self._access
        if settings.mcp_auth_username and settings.mcp_auth_password and settings.mcp_tenant_slug:
            data = await self._post(
                client,
                "/api/v1/auth/token",
                {
                    "username": settings.mcp_auth_username,
                    "password": settings.mcp_auth_password,
                    "tenant_slug": settings.mcp_tenant_slug,
                },
            )
            if data:
                self._store(data)
                return self._access
        return None

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict) -> dict | None:
        try:
            resp = await client.post(path, json=body)
        except httpx.RequestError:
            return None
        return resp.json() if resp.status_code == 200 else None

    def _store(self, data: dict) -> None:
        self._access = data.get("access_token")
        self._refresh = data.get("refresh_token") or self._refresh
        self._exp = time.time() + max(int(data.get("expires_in", 60)) - 30, 5)


token_manager = TokenManager()
