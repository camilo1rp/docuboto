# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Helper de auditoría Canal A (``public.audit_log``, ADR-008/ADR-010) para las
escrituras de catálogos, dependencias y configuración (E14).

Mismo patrón fail-closed que ``auth-service/app/services/rbac_service.py``:
conceder/mutar sin traza es peor que no mutar, así que si la librería de
auditoría no está disponible o el tenant es indeterminable, la escritura se
aborta ANTES de tocar la BD (``require_audit_available`` / ``audit_tenant``).
El asiento corre dentro de la MISMA transacción que la mutación
(``async with conn.transaction():`` en el router) — un fallo de ``append``
revierte también la mutación.

``PinarService`` (E14/PINAR, Acuerdo AGN 003/2015) ya audita sus propios actos
por su cuenta (``app/services/pinar_service.py``); este módulo cierra la
cobertura para el resto de escrituras del servicio (catálogos, dependencias,
festivos, parámetros), que hasta ahora mutaban sin dejar rastro en la cadena
de hash inmutable.
"""

import uuid
from typing import Any

import asyncpg
from fastapi import HTTPException, Request, status

try:
    from orpycamcp_common import audit as _audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "tenant-service"


def actor_from(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def require_audit_available() -> None:
    if not _AUDIT_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "auditoria_no_disponible",
                "detail": "Auditoría inmutable no disponible — no se puede "
                "escribir sin traza",
                "status": 500,
            },
        )


def audit_tenant(tenant_slug: str | None) -> str:
    """Tenant al que se imputa el asiento — fail-closed.

    ``audit.append`` hace ``tenant_slug or SYSTEM_TENANT``: un llamante que lo
    omitiera archivaría la escritura bajo el tenant del sistema, fuera de la
    vista de auditoría de la entidad. Preferimos que la escritura falle a que
    su traza se archive en el sitio equivocado (mismo patrón que
    ``RBACService._audit_tenant`` / ``AnulacionService._audit_tenant``).
    """
    if not tenant_slug:
        raise RuntimeError(
            "Se requiere X-Tenant-Slug para auditar una escritura de "
            "tenant-service (imputar el asiento al tenant del sistema lo "
            "ocultaría de la auditoría de la entidad)"
        )
    return tenant_slug


async def append(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str | None,
    action: str,
    object_type: str,
    object_ref: str | None,
    actor: uuid.UUID | None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Escribe el asiento Canal A. Llamar DENTRO de ``async with conn.transaction()``
    junto con la mutación que se está auditando (fail-closed, no best-effort)."""
    require_audit_available()
    tenant = audit_tenant(tenant_slug)
    await _audit.append(
        conn,
        tenant_slug=tenant,
        service=_SERVICE,
        action=action,
        object_type=object_type,
        object_ref=object_ref,
        actor=str(actor) if actor else None,
        payload=payload or {},
    )
