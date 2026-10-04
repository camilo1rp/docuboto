# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría de eventos de autenticación.

Desde ADR-010, auth-service NO escribe en su tabla propia (`auth_audit_logs`,
obsoleta): delega en la librería compartida `orpycamcp_common.audit`, que escribe
en `public.audit_log` (canónico, encadenado por hash — ADR-008).

Se conserva la firma `log(...)` para no alterar los routers; internamente mapea
al esquema canónico.
"""

import asyncpg
from orpycamcp_common import audit

_SERVICE = "auth-service"


class AuditRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def log(
        self,
        event_type: str,
        *,
        username: str | None = None,
        tenant_slug: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        detail: str | None = None,
    ) -> None:
        # Solo campos presentes van al payload (auditoría mínima).
        payload: dict[str, str] = {}
        if ip_address is not None:
            payload["ip_address"] = ip_address
        if user_agent is not None:
            payload["user_agent"] = user_agent
        if detail is not None:
            payload["detail"] = detail
        if username is not None:
            payload["username"] = username

        await audit.append(
            self._conn,
            tenant_slug=tenant_slug,  # None → cadena de sistema (audit.SYSTEM_TENANT)
            service=_SERVICE,
            action=f"auth.{event_type}",  # p. ej. 'auth.login_success'
            object_type="auth_session",
            object_ref=username,
            actor=username,
            payload=payload,
            canal=audit.CANAL_PETICION,
        )
