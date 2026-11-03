# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Resolución del *clearance* (nivel de seguridad máximo accesible) del llamante.

Pieza de control de acceso de RF-SEG-08 / RF-BUS-04 (E08 §9, canal B "hoja de ruta
operativa"): la hoja de ruta de un radicado (`flow_events`, RF-FLU-02) la ve cualquier
usuario que pueda LEER el radicado, acotado por su clearance (no read-up), NO un permiso
de administrador. El nivel máximo de un usuario es el MAX de la matriz rol→nivel
(`role_clearance`) sobre sus grupos (`auth_memberships`); `is_root` accede a todo. Estas
tablas las provee auth-service pero VIVEN en el schema del tenant (ADR-002/012/013).

Política **fail-closed**: ante ausencia de identidad, usuario inexistente, tablas RBAC
no provisionadas o cualquier duda, se devuelve PUBLICA (1) — mínimo privilegio.
"""

import logging
import uuid

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status
from orpycamcp_common.rbac import (  # noqa: F401 — re-exportados: los demás módulos del servicio importan de aquí
    CLASIFICADA,
    PUBLICA,
    check_permission,
    mensaje_denegacion,
    nivel_efectivo,
    resolve_caller_clearance,
)

from workflow.core.database import get_db_conn, tenant_schema

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "workflow-service"
logger = logging.getLogger(__name__)


# Sentinela nombrado para los pocos call sites que, DELIBERADAMENTE, resuelven
# `WorkflowRepository.get_current` sin restricción de clearance (barrido
# RF-SEG-08 de la bandeja/pending, E05 §10). Espejo de
# `archive-service/workflow/core/clearance.py::CLEARANCE_SIN_RESTRICCION`: mover un
# radicado (rollback/devolución) es un acto de trámite/tenencia, ORTOGONAL al
# clearance de LECTURA (mismo principio que M1/M2 en archive-service — Ley
# 594/2000 arts. 22-24, Ac. AGN 001/2024 arts. 4.3.1.7/4.3.2.3). La autorización
# de esos actos ya vive en la verificación de tenencia (`devolver`: solo el
# `assigned_to` actual) o en el propio estado del paso (`rollback`); NO en el
# clearance del radicado. Usar esta constante SOLO para esa pre-condición
# interna — la lectura HTTP-facing (`GET /{radicado_id}/current`) SIEMPRE
# resuelve el clearance REAL vía `caller_clearance`/`resolve_caller_clearance`.
CLEARANCE_SIN_RESTRICCION = CLASIFICADA


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: eleva 403 si el llamante carece de ``perm_name``.

    ``min_crud`` (default ``1``, retrocompatible) exige un nivel mínimo en la escala
    crud 0..5 (RF-SEG-03); ver :func:`check_permission`.

    D-09: el workflow-service usa ``get_db_conn`` (sin search_path automático); esta
    dependencia fija el search_path al tenant antes de consultar las tablas RBAC.
    FastAPI cachea ``get_db_conn`` por request, así que la conexión que recibe esta
    dependencia es la misma que la del endpoint y el search_path es visible para ambos.
    Normaliza el slug igual que ``acquire_tenant`` (guiones → guiones bajos).

    Uso::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_X"))])
    """

    async def _dep(
        request: Request,
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_db_conn),
    ) -> None:
        from orpycamcp_common.usuario import id_opcional, slug_de
        user_id = id_opcional(request)
        x_tenant_slug = slug_de(request) or x_tenant_slug
        # Sin tenant slug no se puede verificar el RBAC del tenant → deny EXPLÍCITO
        # (no depender de que la tabla no exista en el search_path heredado del pool).
        if not x_tenant_slug:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": "Se requiere X-Tenant-Slug para autorizar",
                    "status": 403,
                },
            )
        schema = tenant_schema(x_tenant_slug)  # valida + normaliza (fail-closed 400)
        await conn.execute(f'SET search_path TO "{schema}"')
        if not await check_permission(conn, user_id, perm_name, min_crud):
            # Traza best-effort del acceso denegado (RF-SEG / MoReq). Solo para un
            # usuario IDENTIFICADO (user_id no nulo) — la rama sin X-Tenant-Slug ya
            # devolvió 403 arriba y NO llega aquí; las sondas anónimas (sin
            # X-User-Id válido) no se auditan para no contaminar la cadena de hash
            # con ruido de escaneo (el rate-limiter ya las mitiga).
            if _AUDIT_AVAILABLE and user_id is not None:
                try:
                    await audit.append_denial(
                        conn,
                        tenant_slug=x_tenant_slug,
                        service=_SERVICE,
                        actor=str(user_id),
                        perm_name=perm_name,
                        min_crud=min_crud,
                    )
                except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                    logger.warning("audit.append_denial failed", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": mensaje_denegacion(
                        perm_name, min_crud, await nivel_efectivo(conn, user_id, perm_name)
                    ),
                    "status": 403,
                },
            )

    return _dep


async def get_actor(request: Request) -> uuid.UUID:
    """UUID interno del llamante. Sale de `request.state.usuario`, no de `X-User-Id`."""
    from orpycamcp_common.usuario import id_exigido

    return id_exigido(request)
