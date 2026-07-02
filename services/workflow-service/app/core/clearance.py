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

import json
import uuid
from collections.abc import Sequence

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

from app.core.database import get_db_conn, tenant_schema

PUBLICA = 1
CLASIFICADA = 3  # nivel máximo (security_levels.code)


async def resolve_caller_clearance(
    conn: asyncpg.Connection, user_id: uuid.UUID | None
) -> int:
    """Devuelve el nivel de seguridad máximo (1..3) accesible por ``user_id``.

    La conexión debe tener ``search_path`` fijado al schema del tenant. Fail-closed a
    :data:`PUBLICA` en todos los casos dudosos.
    """
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
    conn: asyncpg.Connection, user_id: uuid.UUID | None, perm_name: str
) -> bool:
    """Devuelve ``True`` si ``user_id`` tiene el permiso ``perm_name`` en el tenant.

    Gemelo de ``document-service``/``archive-service`` ``check_permission`` (D-06/D-08).
    La conexión debe tener ``search_path`` fijado al schema del tenant. Fail-closed a
    ``False`` en todos los casos dudosos: usuario ausente, tablas RBAC no provisionadas,
    ``user_id`` nulo.
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
                      AND gp.crud > 0
                )
            )
            FROM auth_users u
            WHERE u.id = $1
            """,
            user_id,
            perm_name,
        )
    except asyncpg.UndefinedTableError:
        # Tablas RBAC del tenant ausentes (entorno aislado / no provisionado).
        return False
    return bool(allowed) if allowed is not None else False


def require_permission(perm_name: str):
    """FastAPI dependency factory: eleva 403 si el llamante carece de ``perm_name``.

    D-09: el workflow-service usa ``get_db_conn`` (sin search_path automático); esta
    dependencia fija el search_path al tenant antes de consultar las tablas RBAC.
    FastAPI cachea ``get_db_conn`` por request, así que la conexión que recibe esta
    dependencia es la misma que la del endpoint y el search_path es visible para ambos.
    Normaliza el slug igual que ``acquire_tenant`` (guiones → guiones bajos).

    Uso::

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
        await conn.execute(f'SET search_path TO "{schema}", public')
        if not await check_permission(conn, user_id, perm_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": f"Se requiere el permiso {perm_name}",
                    "status": 403,
                },
            )

    return _dep


async def get_actor(request: Request) -> uuid.UUID:
    """Extrae el UUID interno del llamante desde X-User-Id (401 si ausente/inválido).

    Espejo exacto de ``get_actor`` en signature-service/notification-service
    (ADR-021, pulido Fase 5 "Baja" [3]: unificación de identidad en el LISTADO
    de workflow-service, que hasta ahora usaba el ``_actor`` local — OPCIONAL,
    ``None`` en silencio ante header ausente/ inválido). Nunca toca la BD: la
    identidad ya fue validada por auth-service en ``/validate``.

    NOTA de comportamiento observable en workflow: el LISTADO sigue protegido
    por ``require_permission(...)`` como dependencia de ROUTER (se resuelve
    ANTES que esta), y ese gate NO delega en ``get_actor`` — ante X-User-Id
    ausente/inválido devuelve 403 (no 401) porque ``check_permission`` falla con
    ``user_id=None``. La rama 401 de aquí queda por tanto ensombrecida en la
    práctica en las rutas ya protegidas por ``require_permission``; el valor de
    unificar es tener una única fuente de extracción de identidad con tipo
    NO-opcional (``uuid.UUID``, ya no ``uuid.UUID | None``), no un cambio de
    código HTTP observable. Las rutas de detail/replay/discard de este router
    siguen usando el ``_actor`` local (opcional) — fuera de alcance de este
    pulido, ver ADR-021.
    """
    raw = request.headers.get("X-User-Id")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "X-User-Id header missing",
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
                "detail": "X-User-Id must be a valid UUID (internal auth_users.id)",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_ref(row: asyncpg.Record) -> tuple[list[str], int | None]:
    """Parsea el sobre de un dead-letter y extrae ``(identifiers, nivel_embebido)``.

    Factorizado fuera de :func:`resolve_deadletter_level` para que la resolución
    EN LOTE (:func:`resolve_deadletter_levels`, listado) use exactamente la misma
    lógica de parseo que la resolución por-fila (gate del detalle) — evita que
    ambas implementaciones diverjan con el tiempo (ADR-021, pulido Fase 5 "Baja").

    ``identifiers`` va en orden ``[tracking_number, id]`` (el orden importa: al
    resolver el nivel se prueba cada identificador en ese orden y el primero que
    exista en el lookup gana). Puede propagar ``json.loads`` si ``row["payload"]``
    es una cadena mal formada — el llamante decide cómo tratar ese caso (ambos lo
    tratan como fail-closed a CLASIFICADA).
    """
    raw_payload = row["payload"]
    payload_data = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
    inner_payload = payload_data.get("payload", {}) if isinstance(payload_data, dict) else {}

    tracking_number = inner_payload.get("tracking_number")
    doc_id = inner_payload.get("id")
    nivel_embebido = inner_payload.get("nivel_nuevo")
    if nivel_embebido is None:
        nivel_embebido = inner_payload.get("nivel_seguridad")

    identifiers = [str(x) for x in (tracking_number, doc_id) if x is not None]
    return identifiers, nivel_embebido


async def resolve_deadletter_level(conn: asyncpg.Connection, row: asyncpg.Record) -> int:
    """Determina el nivel de seguridad del objeto referenciado por un dead-letter.

    *Single source of truth* del nivel de un evento aparcado (resolución POR
    FILA): la usa el gate de detalle (:func:`assert_can_read_deadletter`) y,
    fuera de este módulo, cualquier ruta que necesite el nivel de UNA fila
    concreta. Para el LISTADO (N filas) usar en su lugar la contraparte en lote
    :func:`resolve_deadletter_levels`, que debe producir, fila a fila, el mismo
    resultado que llamar a esta función N veces — ver su docstring para el
    contrato de equivalencia.

    Resuelve sobre el nivel **VIVO** del radicado (no se confía solo en el nivel
    embebido en el sobre, que pudo quedar obsoleto tras una reclasificación).
    **No lanza**: cualquier caso dudoso resuelve a CLASIFICADA=3 (fail-closed); el
    llamante decide qué hacer con el nivel devuelto (404 en el detalle, exclusión
    silenciosa en el listado).

    Reglas de determinación de nivel (fail-closed a CLASIFICADA=3 ante cualquier duda):
      - Sin identificadores de radicado en el sobre → CLASIFICADA.
      - Radicado no encontrado (o tabla ``radicados`` ausente) → CLASIFICADA.
      - En otro caso → ``max(nivel_vivo, nivel_embebido)`` (o ``nivel_vivo`` si no
        hay nivel embebido).
    """
    identifiers, nivel_embebido = _extract_ref(row)

    if not identifiers:
        return CLASIFICADA

    nivel_vivo: int | None = None
    try:
        for ident in identifiers:
            nivel_vivo = await conn.fetchval(
                "SELECT nivel_seguridad FROM radicados "
                "WHERE tracking_number = $1 OR id::text = $1 LIMIT 1",
                ident,
            )
            if nivel_vivo is not None:
                break
    except asyncpg.UndefinedTableError:
        nivel_vivo = None

    if nivel_vivo is None:
        return CLASIFICADA
    if nivel_embebido is not None:
        return max(int(nivel_vivo), int(nivel_embebido))
    return int(nivel_vivo)


async def resolve_deadletter_levels(
    conn: asyncpg.Connection,
    rows: Sequence[asyncpg.Record],
) -> dict[str, int]:
    """Resuelve el nivel de seguridad de VARIAS filas de dead-letter con UNA sola
    consulta a ``radicados`` (ADR-021, pulido Fase 5 "Baja": elimina el N+1 del
    listado, donde antes se llamaba :func:`resolve_deadletter_level` por fila).

    Devuelve ``{event_id: nivel}``. **Nunca lanza** (fail-closed por fila, igual
    que la versión per-row). Contrato de equivalencia: para toda fila ``r``,
    ``resolve_deadletter_levels(conn, [r])[r["event_id"]]`` es IDÉNTICO al
    resultado de llamar ``resolve_deadletter_level(conn, r)`` (capturando
    cualquier excepción como CLASIFICADA, tal como hace hoy el router). El
    filtro por-fila del gate de DETALLE sigue usando :func:`resolve_deadletter_level`
    intacta; esta función es solo para el LISTADO.

    Algoritmo (3 fases, ver ADR-021 pulido Fase 5 para el detalle):
      1. Parseo por fila con :func:`_extract_ref` (misma lógica que el per-row).
         Fallos de parseo o ausencia de identificadores → CLASIFICADA de inmediato
         (sin consultar la BD para esa fila).
      2. UNA consulta ``WHERE tracking_number = ANY($1) OR id::text = ANY($1)``
         sobre la unión de todos los identificadores de las filas parseables;
         construye un mapa ``identificador -> nivel_seguridad``. Si la tabla
         ``radicados`` no existe (p. ej. servicios sin ese dominio) el mapa queda
         vacío — todas las filas parseables caen entonces a "no hallado".
      3. Resolución por fila contra el mapa en memoria, con la MISMA regla que
         el per-row: primer identificador (en orden ``[tracking_number, id]``)
         presente en el mapa gana; no-hallado → CLASIFICADA; hallado + nivel
         embebido → ``max(nivel_vivo, nivel_embebido)``; hallado sin embebido →
         ``nivel_vivo``. Envuelta en try/except por fila (fail-closed).
    """
    parsed: dict[str, tuple[list[str], int | None]] = {}
    levels: dict[str, int] = {}
    all_idents: set[str] = set()

    for row in rows:
        event_id = row["event_id"]
        try:
            identifiers, nivel_embebido = _extract_ref(row)
        except Exception:  # noqa: BLE001 — payload corrupto → fail-closed.
            levels[event_id] = CLASIFICADA
            continue
        if not identifiers:
            levels[event_id] = CLASIFICADA
            continue
        parsed[event_id] = (identifiers, nivel_embebido)
        all_idents.update(identifiers)

    level_by_ident: dict[str, int] = {}
    if all_idents:
        try:
            recs = await conn.fetch(
                "SELECT nivel_seguridad, tracking_number, id FROM radicados "
                "WHERE tracking_number = ANY($1::text[]) OR id::text = ANY($1::text[])",
                list(all_idents),
            )
        except asyncpg.UndefinedTableError:
            recs = []
        for rec in recs:
            nivel = rec["nivel_seguridad"]
            if rec["tracking_number"] is not None:
                level_by_ident[rec["tracking_number"]] = nivel
            if rec["id"] is not None:
                level_by_ident[str(rec["id"])] = nivel

    for event_id, (identifiers, nivel_embebido) in parsed.items():
        try:
            nivel_vivo: int | None = None
            for ident in identifiers:
                if ident in level_by_ident:
                    nivel_vivo = level_by_ident[ident]
                    break
            if nivel_vivo is None:
                levels[event_id] = CLASIFICADA
            elif nivel_embebido is not None:
                levels[event_id] = max(int(nivel_vivo), int(nivel_embebido))
            else:
                levels[event_id] = int(nivel_vivo)
        except Exception:  # noqa: BLE001 — fail-closed: cualquier duda excluye la fila.
            levels[event_id] = CLASIFICADA

    return levels


async def assert_can_read_deadletter(
    conn: asyncpg.Connection,
    caller_clearance: int,
    row: asyncpg.Record,
) -> None:
    """No-read-up gate sobre un dead-letter (ADR-021 Fase 5, RF-SEG-08).

    Delega la determinación de nivel en :func:`resolve_deadletter_level` (single
    source of truth, compartida con el filtro del listado). Si el llamante no
    alcanza ese nivel, responde **404 neutro** (no 403): no se revela ni la
    existencia ni el nivel del objeto.

    La conexión llega con ``search_path`` ya fijado al tenant (lo hace
    ``require_permission`` sobre la misma conexión cacheada por request).
    """
    nivel_objeto = await resolve_deadletter_level(conn, row)
    if caller_clearance < nivel_objeto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "Dead-letter event not found or not accessible",
                "status": 404,
            },
        )
