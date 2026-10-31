# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Gates de autorización de la pasarela de conocimiento (ADR-037).

La bandera de la entidad (``require_asistente_habilitado``) y el permiso
``PERM_CONOCIMIENTO`` (``require_permission``) se resuelven en la BD del tenant
(ADR-013: la BD autoriza). El clearance sobre cada radicado ya no se resuelve aquí:
lo aplica document-service al buscar como el usuario (ADR-046 §2).

La conexión debe tener el ``search_path`` ya fijado al schema del tenant (lo hace
``get_tenant_conn``). Política **fail-closed**.
"""

import logging
import uuid
from collections.abc import Iterable

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status
from orpycamcp_common.rbac import check_permission, mensaje_denegacion, nivel_efectivo
from orpycamcp_common.tenant import (
    ERROR_ASISTENTE_DESHABILITADO,
    ERROR_ASISTENTE_NO_DISPONIBLE,
    asistente_habilitado,
)

from app.core.database import get_tenant_conn

try:
    from orpycamcp_common import audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "knowledge-service"
logger = logging.getLogger(__name__)

async def require_asistente_habilitado(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    """Gate de la bandera por entidad (ADR-037): 403 si la entidad no activó el asistente.

    Va ANTES de ``require_permission`` (el 403 honesto «la entidad no lo tiene» gana
    al «usted no tiene el permiso»). Aplica también a ROOT: la bandera es una decisión
    de la entidad, no un permiso de la persona. Sin caché (apagar debe ser inmediato) y
    fail-closed: si el registro no responde, 503, no se sirve.
    """
    try:
        activado = await asistente_habilitado(conn, request.headers.get("X-Tenant-Slug", ""))
    except Exception:  # noqa: BLE001 — no poder saberlo se responde como «no»
        logger.warning("no se pudo leer asistente_habilitado", exc_info=True)
        status_code, error, detail = ERROR_ASISTENTE_NO_DISPONIBLE
        raise HTTPException(
            status_code=status_code,
            detail={"error": error, "detail": detail, "status": status_code},
        ) from None
    if not activado:
        status_code, error, detail = ERROR_ASISTENTE_DESHABILITADO
        raise HTTPException(
            status_code=status_code,
            detail={"error": error, "detail": detail, "status": status_code},
        )


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: raises 401/403 when caller lacks ``perm_name``.

    Reads actor UUID from X-User-Id header (injected by api-gateway after auth-service
    /validate resolved the internal id).  Raises 401 when the header is absent or not
    a valid UUID; 403 when permission is denied or user not found (fail-closed).

    ``min_crud`` (default ``1``, backward-compatible) requires a minimum level on the
    0..5 crud scale (RF-SEG-03); see :func:`check_permission`.

    Usage at endpoint level::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])

    Or at router level to protect all routes::

        router = APIRouter(dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])
    """

    async def _dep(
        actor: uuid.UUID = Depends(get_actor),
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        if not await check_permission(conn, actor, perm_name, min_crud):
            # Traza best-effort del acceso denegado (RF-SEG / MoReq). ``actor``
            # aquí SIEMPRE está identificado — ``get_actor`` ya devolvió 401 si
            # faltaba/era inválido X-User-Id — así que no hay caso anónimo que
            # excluir (a diferencia de document/archive/workflow).
            if _AUDIT_AVAILABLE:
                try:
                    await audit.append_denial(
                        conn,
                        tenant_slug=x_tenant_slug,
                        service=_SERVICE,
                        actor=str(actor),
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
                        perm_name, min_crud, await nivel_efectivo(conn, actor, perm_name)
                    ),
                    "status": 403,
                },
            )

    return _dep


def motivo_salida(
    *,
    bandera: bool,
    proveedor: str,
    niveles_habilitados: Iterable[int],
    suspendido: bool,
    nivel_vivo: int | None,
) -> str | None:
    """None si el registro puede salir ahora. Si no, un motivo cerrado.

    Fail-closed (ADR-045 §3 y §4). El interruptor apagado (bandera o ``local``),
    el envío suspendido, un radicado ausente y un nivel vivo fuera de los
    habilitados niegan. No mira el nivel que traía el evento: quien llama pasa
    el nivel que acaba de leer.
    """
    if not bandera:
        return "bandera"
    if proveedor != "ppi":
        return "local"
    if suspendido:
        return "suspendido"
    if nivel_vivo is None:
        return "ausente"
    try:
        nivel = int(nivel_vivo)
        habilitados = {int(n) for n in niveles_habilitados}
    except (TypeError, ValueError):
        return "nivel"
    if nivel not in (1, 2, 3) or nivel not in habilitados:
        return "nivel"
    return None


def puede_salir(
    *,
    bandera: bool,
    proveedor: str,
    niveles_habilitados: Iterable[int],
    suspendido: bool,
    nivel_vivo: int | None,
) -> bool:
    """¿Puede salir este registro hacia el proveedor, ahora?"""
    return (
        motivo_salida(
            bandera=bandera,
            proveedor=proveedor,
            niveles_habilitados=niveles_habilitados,
            suspendido=suspendido,
            nivel_vivo=nivel_vivo,
        )
        is None
    )


async def get_actor(request: Request) -> uuid.UUID:
    """Extracts the caller's internal UUID from X-User-Id header.

    Raises 401 when the header is missing or not a valid UUID.  Never touches the
    database — identity was already validated by auth-service /validate.
    """
    raw = request.headers.get("X-User-Id")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "Falta la cabecera X-User-Id",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "La cabecera X-User-Id debe ser un UUID válido (auth_users.id interno)",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
