# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Clasificación de seguridad y clearance por grupo (E08, RF-SEG-08).

Subir el `max_level` de un grupo es una vía directa de escalada de privilegio
(todo miembro del grupo hereda el nuevo techo de acceso) — se audita en Canal A
(``public.audit_log``) dentro de la MISMA transacción que la mutación, con el
nivel anterior y el nuevo en el payload (sin eso no se distingue una subida de
una bajada). No best-effort — ver ``RBACService`` para la justificación completa.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from fastapi import HTTPException, status

from app.repositories.clearance_repository import ClearanceRepository

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "auth-service"


def _require_audit_available() -> None:
    if not _AUDIT_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "auditoria_no_disponible",
                "detail": "Auditoría inmutable no disponible — no se puede "
                "cambiar el clearance de un grupo sin traza",
                "status": 500,
            },
        )


def _audit_tenant(tenant_slug: str | None) -> str:
    """Fail-closed — ver ``rbac_service._audit_tenant``. Aquí es aún más
    sensible: sin `tenant_slug` la subida de clearance quedaría imputada al
    tenant del sistema, invisible para el auditor de la entidad."""
    if not tenant_slug:
        raise RuntimeError(
            "ClearanceService requiere tenant_slug para auditar una escritura "
            "(imputar el asiento al tenant del sistema lo ocultaría de la "
            "auditoría de la entidad)"
        )
    return tenant_slug


class ClearanceService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._repo = ClearanceRepository(conn)

    async def set_group_clearance(
        self,
        group_id: uuid.UUID,
        max_level: int,
        *,
        tenant_slug: str | None,
        actor: str,
    ) -> None:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            previous_level = await self._repo.get_group_clearance(group_id)
            await self._repo.set_group_clearance(group_id, max_level)
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="clearance.grupo_actualizado",
                object_type="clearance_grupo",
                object_ref=str(group_id),
                actor=actor,
                payload={
                    "group_id": str(group_id),
                    "previous_max_level": previous_level,
                    "new_max_level": max_level,
                },
                canal=_audit.CANAL_PETICION,
            )
