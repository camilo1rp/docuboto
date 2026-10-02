# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lógica de autorización y administración RBAC (E08).

Reproduce el comportamiento del legado (plan-argoik/plan-icetex): Keycloak
autentica, la BD del tenant autoriza. ROOT se comprueba primero (RN-2/RN-3) y los
permisos se resuelven como MAX(crud) por nombre.

Toda escritura administrativa (crear usuario/grupo, membresías, permisos de
grupo) deja un asiento en Canal A (``public.audit_log``, ADR-008/ADR-010) en la
MISMA transacción que la mutación — igual que ``TotpService``/``SigningKeyService``
en este servicio. Estas vías son por donde se escala un privilegio, así que el
asiento NO es best-effort: si la auditoría falla, la mutación se revierte
(``_require_audit_available`` + ``async with conn.transaction()``).
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from fastapi import HTTPException, status

from app.repositories.rbac_repository import RBACRepository

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "auth-service"

# Permiso especial: bypass total (USUA_PERM_ROOT del legado).
ROOT_PERMISSION = "USUA_PERM_ROOT"


def has_permission(perms: dict[str, int], name: str, min_crud: int = 1) -> bool:
    """¿El conjunto de permisos satisface ``name`` con al menos ``min_crud``?

    ROOT primero: ``USUA_PERM_ROOT`` concede cualquier permiso.
    """
    if perms.get(ROOT_PERMISSION, 0) >= 1:
        return True
    return perms.get(name, 0) >= min_crud


def _require_audit_available() -> None:
    """Fail-closed: conceder/revocar un privilegio (usuario, grupo, membresía,
    permiso de grupo) sin traza es peor que no concederlo — a diferencia del
    resto de `_audit_event` best-effort de este servicio (enroll de TOTP/firma,
    no legal-crítico igual). Ver ``SigningKeyService._require_audit_available``.
    """
    if not _AUDIT_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "auditoria_no_disponible",
                "detail": "Auditoría inmutable no disponible — no se puede "
                "escribir RBAC sin traza",
                "status": 500,
            },
        )


def _forbid_clearance_membership() -> HTTPException:
    """403 no-write-up: entrar en un grupo cuyo techo de clearance supera el del
    propio llamante lo subiría de nivel (RF-SEG-08). Sin asiento, mismo criterio
    que `_forbid_root_grant`."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "no_write_up",
            "detail": (
                "No puede agregar a ese grupo: su techo de clearance supera el "
                "clearance efectivo del llamante (no-write-up, RF-SEG-08)."
            ),
            "status": 403,
        },
    )


def _forbid_root_grant() -> HTTPException:
    """403 para cualquier vía que intente conceder ``USUA_PERM_ROOT`` sin que el
    llamante ya sea ROOT (cierre del hallazgo Alto: auto-escalada a ROOT).

    Deliberadamente sin ``audit.append_denial``: esa función traza denegaciones
    del *gate* RBAC (`require_permission`, permiso insuficiente para invocar el
    endpoint); esto es una regla de negocio dentro de un endpoint al que el
    llamante SÍ tiene acceso (es ``USUA_PERM_ADMIN``), igual que el no-write-up
    de clasificación en `archive_service.create_expediente` /
    `document_service.reclassify` — mismo patrón: 403 explícito, sin asiento
    (el rechazo ocurre ANTES de cualquier mutación u escritura de auditoría, así
    que no hay asiento de éxito que dejar).
    """
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "forbidden",
            "detail": (
                "USUA_PERM_ROOT no puede auto-concederse: solo un ROOT existente "
                "puede otorgarlo. El primer ROOT de un tenant se aprovisiona "
                "fuera de banda (app/ops/grant_root.py), no vía la API."
            ),
            "status": 403,
        },
    )


def _audit_tenant(tenant_slug: str | None) -> str:
    """Tenant al que se imputa el asiento — fail-closed.

    ``audit.append`` hace ``tenant_slug or SYSTEM_TENANT``: un llamante que lo
    omitiera archivaría una escalada de privilegios bajo el tenant del sistema,
    fuera de la vista de auditoría de la entidad. Preferimos que la escritura
    falle a que su traza se archive en el sitio equivocado (mismo patrón que
    ``AnulacionService._audit_tenant`` en document-service).
    """
    if not tenant_slug:
        raise RuntimeError(
            "RBACService requiere tenant_slug para auditar una escritura "
            "(imputar el asiento al tenant del sistema lo ocultaría de la "
            "auditoría de la entidad)"
        )
    return tenant_slug


class RBACService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._repo = RBACRepository(conn)

    async def resolve_effective_permissions(self, user_id: uuid.UUID) -> dict[str, int]:
        """Permisos efectivos del usuario. Si es root, embebe ``USUA_PERM_ROOT``.

        Solo se devuelven permisos con ``crud > 0`` (mitiga la inflación del JWT).
        """
        perms = await self._repo.resolve_permissions(user_id)
        if await self._repo.is_root(user_id):
            perms[ROOT_PERMISSION] = 5
        return perms

    async def list_permissions(self, *, caller_is_root: bool) -> list[asyncpg.Record]:
        """Catálogo de permisos. Excluye ``USUA_PERM_ROOT`` para un llamante no-ROOT.

        Cierra la tercera vía del hallazgo Alto: `GET /auth/permissions`
        alimentaba el desplegable del panel admin con ``USUA_PERM_ROOT``
        incluso para un llamante que `set_group_permission`/`create_user` ya
        rechazarían al intentar concedérselo — ofrecerlo en la lista es
        prometer una operación que va a fallar con 403. Es un filtro de
        lectura, no una política de negocio: no se audita (no es una decisión
        de acceso a un recurso, es una proyección).
        """
        rows = await self._repo.list_permissions()
        if caller_is_root:
            return rows
        return [r for r in rows if r["nombre"] != ROOT_PERMISSION]

    # ------------------------------------------------------------------
    # Escrituras administrativas (auditadas, atómicas, fail-closed)
    # ------------------------------------------------------------------

    async def create_user(
        self,
        *,
        username: str,
        email: str | None,
        keycloak_sub: str | None,
        is_root: bool,
        tenant_slug: str | None,
        actor: str,
        caller_is_root: bool,
    ) -> asyncpg.Record:
        # Vía 1 de auto-escalada (hallazgo Alto): un USUA_PERM_ADMIN no-ROOT no
        # puede crear un usuario `is_root=true`. Chequeo de política ANTES de
        # tocar la BD o la auditoría — ver `_forbid_root_grant`.
        if is_root and not caller_is_root:
            raise _forbid_root_grant()
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            row = await self._repo.create_user(
                username=username, email=email, keycloak_sub=keycloak_sub, is_root=is_root
            )
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="rbac.usuario_creado",
                object_type="usuario",
                object_ref=str(row["id"]),
                actor=actor,
                payload={"username": row["username"], "is_root": bool(row["is_root"])},
            )
        return row

    async def create_group(
        self,
        *,
        name: str,
        description: str | None,
        tenant_slug: str | None,
        actor: str,
    ) -> asyncpg.Record:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            row = await self._repo.create_group(name=name, description=description)
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="rbac.grupo_creado",
                object_type="grupo",
                object_ref=str(row["id"]),
                actor=actor,
                payload={"name": row["name"]},
            )
        return row

    async def update_group(
        self,
        *,
        group_id: uuid.UUID,
        fields: dict[str, str | None],
        tenant_slug: str | None,
        actor: str,
    ) -> asyncpg.Record | None:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            # El «antes» se lee con la fila bloqueada, en la misma tx (MoReq/ISO
            # 15489: la traza debe permitir reconstruir el valor anterior).
            previo = await self._repo.get_group_for_update(group_id)
            if previo is None:
                return None
            row = await self._repo.update_group(group_id, fields=fields)
            if row is None:
                return None
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="rbac.grupo_actualizado",
                object_type="grupo",
                object_ref=str(group_id),
                actor=actor,
                payload={
                    "campos": sorted(fields),
                    "antes": {k: previo[k] for k in sorted(fields)},
                    "despues": {k: row[k] for k in sorted(fields)},
                },
            )
        return row

    async def delete_group(
        self,
        *,
        group_id: uuid.UUID,
        tenant_slug: str | None,
        actor: str,
    ) -> str:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            previo = await self._repo.get_group_for_update(group_id)
            outcome = await self._repo.delete_group_if_unused(group_id)
            if outcome == "deleted" and previo is not None:
                await _audit.append(
                    self._conn,
                    tenant_slug=tenant,
                    service=_SERVICE,
                    action="rbac.grupo_eliminado",
                    object_type="grupo",
                    object_ref=str(group_id),
                    actor=actor,
                    payload={"name": previo["name"], "description": previo["description"]},
                )
        return outcome

    async def add_member(
        self,
        *,
        group_id: uuid.UUID,
        user_id: uuid.UUID,
        tenant_slug: str | None,
        actor: str,
        caller_is_root: bool,
        caller_level: int,
    ) -> None:
        # No-write-up (RF-SEG-08): un administrador no-ROOT no entra en un grupo
        # cuyo techo de clearance supera el suyo. El gate vive en el INSERT
        # (`add_membership_guarded`); aquí solo se traduce `denied` a 403, sin
        # asiento (regla de negocio de un endpoint al que SÍ tiene acceso, mismo
        # criterio que `PUT /groups/{g}/clearance`).
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            outcome = await self._repo.add_membership_guarded(
                user_id=user_id,
                group_id=group_id,
                caller_is_root=caller_is_root,
                caller_level=caller_level,
            )
            if outcome == "denied":
                raise _forbid_clearance_membership()
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="rbac.miembro_agregado",
                object_type="membresia",
                object_ref=f"{group_id}:{user_id}",
                actor=actor,
                payload={"group_id": str(group_id), "user_id": str(user_id)},
            )

    async def remove_member(
        self,
        *,
        group_id: uuid.UUID,
        user_id: uuid.UUID,
        tenant_slug: str | None,
        actor: str,
    ) -> None:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            await self._repo.remove_membership(user_id=user_id, group_id=group_id)
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="rbac.miembro_removido",
                object_type="membresia",
                object_ref=f"{group_id}:{user_id}",
                actor=actor,
                payload={"group_id": str(group_id), "user_id": str(user_id)},
            )

    async def set_group_permission(
        self,
        *,
        group_id: uuid.UUID,
        permission_id: uuid.UUID,
        crud: int,
        tenant_slug: str | None,
        actor: str,
        caller_is_root: bool,
    ) -> None:
        # Vía 2 de auto-escalada (hallazgo Alto): conceder USUA_PERM_ROOT a un
        # grupo (del que el propio actor puede ser miembro) exige que el actor
        # ya sea ROOT. `crud=0` (revocar) siempre se permite — solo se bloquea
        # la CONCESIÓN. Resuelto por nombre porque el endpoint solo recibe el
        # UUID del permiso. Chequeo ANTES de mutar/auditar.
        if crud > 0 and not caller_is_root:
            perm_name = await self._repo.get_permission_name(permission_id)
            if perm_name == ROOT_PERMISSION:
                raise _forbid_root_grant()
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            await self._repo.set_group_permission(
                group_id=group_id, permission_id=permission_id, crud=crud
            )
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="rbac.permiso_grupo_asignado",
                object_type="permiso_grupo",
                object_ref=f"{group_id}:{permission_id}",
                actor=actor,
                payload={
                    "group_id": str(group_id),
                    "permission_id": str(permission_id),
                    "crud": crud,
                },
            )
