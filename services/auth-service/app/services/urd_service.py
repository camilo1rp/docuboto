# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""URD y contexto activo de dependencia (E08).

Crear/eliminar una URD (Usuario-Rol-Dependencia) es una vía de escalada de
privilegio (asigna un rol al usuario en una dependencia) — se audita en Canal A
(``public.audit_log``) dentro de la MISMA transacción que la mutación, no
best-effort (ver ``RBACService`` para la justificación completa del patrón).
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from fastapi import HTTPException, status

from app.repositories.urd_repository import URDRepository
from app.schemas.urd import ContextResponse, URDCreate, URDResponse

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
                "escribir una URD sin traza",
                "status": 500,
            },
        )


def _audit_tenant(tenant_slug: str | None) -> str:
    """Fail-closed — ver ``rbac_service._audit_tenant`` (mismo razonamiento:
    una URD asigna un rol/dependencia, así que su asiento no puede quedar
    imputado al tenant del sistema por omisión)."""
    if not tenant_slug:
        raise RuntimeError(
            "URDService requiere tenant_slug para auditar una escritura "
            "(imputar el asiento al tenant del sistema lo ocultaría de la "
            "auditoría de la entidad)"
        )
    return tenant_slug


def _to_urd(row: asyncpg.Record) -> URDResponse:
    return URDResponse(
        id=row["id"],
        user_id=row["user_id"],
        depe_id=row["depe_id"],
        rol_id=row["rol_id"],
        es_principal=row["es_principal"],
        is_active=row["is_active"],
        created_at=row["created_at"],
    )


class URDService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._repo = URDRepository(conn)

    async def list(self, user_id: uuid.UUID) -> list[URDResponse]:
        return [_to_urd(r) for r in await self._repo.list_by_user(user_id)]

    async def create(
        self,
        user_id: uuid.UUID,
        payload: URDCreate,
        *,
        tenant_slug: str | None,
        actor: str,
    ) -> URDResponse:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            row = await self._repo.insert(
                user_id=user_id,
                depe_id=payload.depe_id,
                rol_id=payload.rol_id,
                es_principal=payload.es_principal,
            )
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="urd.creada",
                object_type="urd",
                object_ref=str(row["id"]),
                actor=actor,
                payload={
                    "user_id": str(user_id),
                    "depe_id": payload.depe_id,
                    "rol_id": str(payload.rol_id),
                    "es_principal": payload.es_principal,
                },
            )
        return _to_urd(row)

    async def delete(
        self,
        user_id: uuid.UUID,
        urd_id: uuid.UUID,
        *,
        tenant_slug: str | None,
        actor: str,
    ) -> bool:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            deleted = await self._repo.delete(user_id, urd_id)
            if deleted:
                await _audit.append(
                    self._conn,
                    tenant_slug=tenant,
                    service=_SERVICE,
                    action="urd.eliminada",
                    object_type="urd",
                    object_ref=str(urd_id),
                    actor=actor,
                    payload={"user_id": str(user_id)},
                )
        return deleted

    async def context(self, user_id: uuid.UUID) -> ContextResponse:
        """Contexto activo, con la dependencia PRINCIPAL como valor por defecto.

        `auth_users.active_depe_id` solo se escribe al conmutar explícitamente
        (:meth:`switch`), así que quien nunca ha conmutado lo tenía en NULL —
        **aunque tuviera una URD marcada `es_principal`**, que es precisamente
        el campo que existe para decir «esta es su dependencia». El efecto
        visible era un conmutador en blanco en la barra superior de todas las
        pantallas, con una sola opción disponible en la que la persona parecía
        ya estar; y aguas abajo, un contexto nulo para quien sí tenía unidad
        asignada.

        Se resuelve en LECTURA y no escribiendo un valor al crear la URD: así
        vale para los datos que ya existen sin ninguna migración, y no depende
        del orden en que se hayan hecho las escrituras. La elección es
        determinista — `ux_urd_principal` es un índice único parcial por
        usuario, de modo que principal solo puede haber una.

        Sin principal se devuelve NULL a propósito: elegir «la primera» sería
        inventar una decisión organizacional que nadie tomó.
        """
        active = await self._repo.get_active_depe(user_id)
        available = [_to_urd(r) for r in await self._repo.list_by_user(user_id)]
        if active is None:
            active = next(
                (u.depe_id for u in available if u.es_principal and u.is_active), None
            )
        return ContextResponse(user_id=user_id, active_depe_id=active, available_depts=available)

    async def switch(
        self, user_id: uuid.UUID, depe_id: int, *, executor_id: uuid.UUID | None
    ) -> ContextResponse | None:
        """Cambia la dependencia activa. None si el usuario no tiene URD activa allí.

        ADR-013: no reemite token; solo persiste el contexto y lo audita.
        """
        urd = await self._repo.get(user_id, depe_id)
        if urd is None or not urd["is_active"]:
            return None
        await self._repo.set_active_depe(user_id, depe_id)
        await self._repo.log_historico(
            executor_id=executor_id,
            target_id=user_id,
            accion="CONTEXT_SWITCH",
            permisos={"depe_id": depe_id},
        )
        return await self.context(user_id)
