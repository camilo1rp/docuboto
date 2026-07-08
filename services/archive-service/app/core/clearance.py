# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Resolución del *clearance* y comprobación de permisos granulares del llamante.

Gemelo de ``document-service/app/core/clearance.py`` (RF-SEG-08 / RF-BUS-04). El nivel
máximo de un usuario es el MAX de la matriz rol→nivel (``role_clearance``) sobre sus
grupos (``auth_memberships``); ``is_root`` accede a todo. Estas tablas las provee
auth-service pero VIVEN en el schema del tenant (ADR-002/ADR-012), por lo que
archive-service las resuelve desde su propia conexión (ADR-013: la BD autoriza).

Diferencia con document-service: archive-service usa ``get_db_conn`` (sin search_path
automático). El repositorio llama ``_sp()`` antes de cada query; ``require_permission``
fija el search_path explícitamente antes de consultar las tablas RBAC del tenant.

Política **fail-closed**: ante ausencia de identidad, usuario inexistente, tablas RBAC
no provisionadas o cualquier duda, se devuelve PUBLICA (1) o False.
"""

import logging
import uuid

import asyncpg
from fastapi import Depends, Header, HTTPException, status

from app.core.database import get_db_conn, tenant_schema

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "archive-service"
logger = logging.getLogger(__name__)

PUBLICA = 1
CLASIFICADA = 3  # nivel máximo (security_levels.code)

# TODO seguridad, diferido — ver hilo RF-SEG-08. `ArchiveRepository.get_expediente_by_id`
# exige `max_clearance` explícito (ya no tiene default peligroso). Los llamantes
# listados abajo lo resuelven como pre-condición interna de existencia/estado
# (p. ej. "¿existe y está 'open'?" antes de vincular/excluir/regenerar índice/
# transferir) y NO como una lectura expuesta al usuario: la autorización real de
# la operación ya la impone `require_permission(..., min_crud=3)` en el router
# (D-08). Aplicar no-read-up (clearance real del llamante) en estos puntos queda
# DIFERIDO por decisión del arquitecto — este nombre deja el riesgo VISIBLE en
# el código en vez de escondido en un default de firma.
# Usado por: ArchiveService.{link_radicado, link_radicados_batch, unlink_radicado,
# _expediente_or_404}; IndexService.regenerate; TransferenciaService.create.
CLEARANCE_SIN_RESTRICCION = CLASIFICADA


async def resolve_caller_clearance(
    conn: asyncpg.Connection, user_id: uuid.UUID | None
) -> int:
    """Devuelve el nivel de seguridad máximo (1..3) accesible por ``user_id``."""
    if user_id is None:
        return PUBLICA
    try:
        row = await conn.fetchrow(
            """
            SELECT u.is_root,
                   (SELECT MAX(rc.max_level)
                      FROM auth_memberships m
                      JOIN role_clearance rc ON rc.group_id = m.group_id
                     WHERE m.user_id = u.id) AS max_level
            FROM auth_users u
            WHERE u.id = $1
            """,
            user_id,
        )
    except asyncpg.UndefinedTableError:
        return PUBLICA
    if row is None:
        return PUBLICA
    if row.get("is_root"):
        return CLASIFICADA
    max_level = row.get("max_level")
    return int(max_level) if max_level is not None else PUBLICA


async def check_permission(
    conn: asyncpg.Connection,
    user_id: uuid.UUID | None,
    perm_name: str,
    min_crud: int = 1,
) -> bool:
    """Devuelve ``True`` si ``user_id`` tiene el permiso ``perm_name`` en el tenant.

    La conexión debe tener ``search_path`` fijado al schema del tenant. Fail-closed
    a ``False`` en todos los casos dudosos: usuario ausente, tablas RBAC no
    provisionadas, ``user_id`` nulo.

    ``min_crud`` es el nivel mínimo exigido en ``auth_group_permissions.crud`` (escala
    0..5, RF-SEG-03). El default ``1`` (Leer) es retrocompatible con el comportamiento
    previo ``crud > 0``, ya que ``crud`` nunca es negativo. Operaciones de disposición
    (cerrar/transferir/desvincular expediente, transferencias FUID, retención TRD,
    borrado de tipo documental) elevan este umbral a ``3`` (Crear/Borrar), en línea con
    Ley 594/2000 arts. 22-24 y Acuerdo AGN 001/2024.

    Usa ``fetchval`` (query booleana) para no interferir con el pool de ``fetchrow``
    de la lógica de negocio en tests unitarios.
    """
    if user_id is None:
        return False
    try:
        allowed = await conn.fetchval(
            """
            SELECT (
                u.is_root
                OR EXISTS (
                    SELECT 1
                    FROM auth_memberships m
                    JOIN auth_group_permissions gp ON gp.group_id = m.group_id
                    JOIN auth_permissions p ON p.id = gp.permission_id
                    WHERE m.user_id = u.id
                      AND p.nombre = $2
                      AND gp.crud >= $3
                )
            )
            FROM auth_users u
            WHERE u.id = $1
            """,
            user_id,
            perm_name,
            min_crud,
        )
    except asyncpg.UndefinedTableError:
        # Tablas RBAC del tenant ausentes (entorno aislado / no provisionado).
        return False
    # user not found → fetchval devuelve None → fail-closed
    return bool(allowed) if allowed is not None else False


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: eleva 403 si el llamante carece de ``perm_name``.

    ``min_crud`` (default ``1``, retrocompatible) exige un nivel mínimo en la escala
    crud 0..5 (RF-SEG-03); ver :func:`check_permission`.

    A diferencia de document-service (que usa ``get_tenant_conn`` con search_path
    automático), archive-service usa ``get_db_conn``; esta dependencia fija el
    search_path al tenant antes de consultar las tablas RBAC.

    FastAPI cachea ``get_db_conn`` por request, así que la conexión que recibe
    ``require_permission`` es la misma que la del endpoint, y el search_path fijado
    aquí es visible para ambos.

    Uso en el decorador del endpoint::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_X"))])
    """

    async def _dep(
        x_user_id: str | None = Header(default=None),
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_db_conn),
    ) -> None:
        try:
            user_id = uuid.UUID(x_user_id) if x_user_id else None
        except ValueError:
            user_id = None
        # Fija el search_path para que check_permission resuelva las tablas RBAC
        # en el schema correcto. Sin tenant slug no se puede verificar → deny EXPLÍCITO
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
        await conn.execute(f'SET search_path TO "{schema}", public')
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
                    "detail": f"Se requiere el permiso {perm_name}",
                    "status": 403,
                },
            )

    return _dep
