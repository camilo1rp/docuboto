# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría inmutable contra ``public.audit_log`` (ADR-008).

Cada fila guarda ``hash = sha256(prev_hash || contenido_canónico)``, donde
``prev_hash`` es el hash de la última fila del **mismo tenant**. Alterar o borrar
una fila rompe la cadena de todas las posteriores → manipulación evidente.

Reglas:
- Es el **único** punto de escritura de ``audit_log``; nadie inserta "a mano".
- La cadena se serializa **por tenant** con un advisory lock transaccional, de
  modo que dos ``append`` concurrentes del mismo tenant no compitan por ``prev_hash``
  (tenants distintos no se bloquean entre sí).
- Append-only: esta librería nunca hace ``UPDATE``/``DELETE`` (además el trigger
  de BD lo rechaza).

Uso::

    from orpycamcp_common import audit

    await audit.append(
        conn,
        tenant_slug="icetex",
        service="document-service",
        action="radicado.creado",
        object_type="radicado",
        object_ref="2024-ICETEX-E-001234",
        actor=user_id,
        payload={"asunto": "..."},
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Cuando un evento no pertenece a ningún tenant (p. ej. refresh/logout de auth),
# se ancla en una cadena de sistema dedicada en vez de violar el NOT NULL.
SYSTEM_TENANT = "__system__"

# Enum cerrado de canales válidos (espejo del CHECK de BD en migration 003).
# El gateway deriva el valor server-side; nunca lo acepta de cabeceras de cliente.
#   api    — acceso REST directo
#   ui     — petición originada en el frontend SvelteKit
#   mcp    — petición originada en el MCP-server
#   system — operación interna de un job/worker
VALID_CANALES: frozenset[str] = frozenset({"api", "ui", "mcp", "system"})


def _validate_canal(canal: str) -> None:
    """Valida que el canal sea uno de los valores registrados.

    Raises:
        ValueError: si ``canal`` no pertenece a ``VALID_CANALES``.
    """
    if canal not in VALID_CANALES:
        raise ValueError(
            f"Invalid canal {canal!r}. Allowed values: {sorted(VALID_CANALES)}"
        )

_INSERT_SQL = """
    INSERT INTO public.audit_log
        (tenant_slug, service, actor, canal, action, object_type, object_ref,
         payload, prev_hash, hash)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
"""

_LAST_HASH_SQL = """
    SELECT hash
    FROM public.audit_log
    WHERE tenant_slug = $1
    ORDER BY id DESC
    LIMIT 1
"""


def _canonical(
    prev_hash: str | None,
    *,
    tenant_slug: str,
    service: str,
    actor: str | None,
    canal: str,
    action: str,
    object_type: str,
    object_ref: str | None,
    payload: dict[str, Any],
) -> str:
    """Representación determinista de la fila para el cálculo del hash."""
    body = json.dumps(
        {
            "tenant_slug": tenant_slug,
            "service": service,
            "actor": actor,
            "canal": canal,
            "action": action,
            "object_type": object_type,
            "object_ref": object_ref,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{prev_hash or ''}|{body}"


def compute_hash(prev_hash: str | None, **fields: Any) -> str:
    """Hash SHA-256 de una fila dado el hash previo (expuesto para verificación)."""
    canonical = _canonical(prev_hash, **fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def append(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str | None,
    service: str,
    action: str,
    object_type: str,
    object_ref: str | None = None,
    actor: str | None = None,
    canal: str = "api",
    payload: dict[str, Any] | None = None,
) -> str:
    """Añade una entrada de auditoría y devuelve su hash.

    Se ejecuta dentro de una (sub)transacción para que el advisory lock por
    tenant garantice el orden de la cadena. Si ``conn`` ya está en transacción,
    ``conn.transaction()`` crea un savepoint.
    """
    _validate_canal(canal)

    tenant = tenant_slug or SYSTEM_TENANT
    payload = payload or {}

    fields = {
        "tenant_slug": tenant,
        "service": service,
        "actor": actor,
        "canal": canal,
        "action": action,
        "object_type": object_type,
        "object_ref": object_ref,
        "payload": payload,
    }

    async with conn.transaction():
        # Serializa la cadena de ESTE tenant (no bloquea otros tenants).
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", tenant)
        prev_hash = await conn.fetchval(_LAST_HASH_SQL, tenant)
        row_hash = compute_hash(prev_hash, **fields)
        await conn.execute(
            _INSERT_SQL,
            tenant,
            service,
            actor,
            canal,
            action,
            object_type,
            object_ref,
            json.dumps(payload, default=str),
            prev_hash,
            row_hash,
        )
    return row_hash


async def append_denial(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str | None,
    service: str,
    actor: str | None,
    perm_name: str,
    min_crud: int = 1,
    canal: str = "api",
    motivo: str = "permiso_insuficiente",
) -> None:
    """Deja traza de un intento de acceso DENEGADO por el gate RBAC (RF-SEG / MoReq).

    ``require_permission`` levanta el 403 **antes** de llegar al handler del
    servicio, así que ninguna de las llamadas a :func:`append` que hace la lógica
    de negocio se ejecuta para una denegación — sin este helper, la denegación
    no deja traza alguna en ``audit_log``. Pensado para invocarse justo antes del
    ``raise HTTPException(403)`` en la rama de permiso insuficiente de cada gate.

    Escribe ``action="acceso.denegado"``, ``object_type="rbac"`` (constante:
    ``object_type`` es ``NOT NULL`` y el objeto denegado siempre es "un permiso",
    no un recurso de negocio concreto), ``object_ref=perm_name``.

    ``motivo`` distingue la causa probatoria del asiento en ``payload.motivo``.
    Por defecto ``"permiso_insuficiente"`` (el caso normal: el llamante SÍ está
    aprovisionado pero no tiene el permiso/crud exigido). Pásese un valor
    distinto (p. ej. ``"usuario_no_aprovisionado"``) cuando la denegación no
    proviene de una verificación de permiso concreta, para que un analista que
    filtre ``audit_log`` por ``payload.motivo`` no clasifique mal el evento.

    **Best-effort**: envuelta en su propio ``try/except`` que nunca relanza — una
    denegación de acceso jamás debe degradar a un 500 por un fallo de auditoría
    (BD caída, tabla no provisionada, etc.). Ante fallo, deja un ``warning`` en el
    log del servicio en vez de propagar.

    Nota de llamador: como esta función nunca lanza, un test que quiera simular
    un fallo de auditoría típicamente sustituye la función entera (mock/monkeypatch)
    en vez de forzar una excepción interna — en ese caso el ``try/except`` de aquí
    no aplica y el llamador (el gate) es responsable de no dejar que ese mock
    reviente el flujo 403 (ver ``require_permission`` en cada ``clearance.py``).
    """
    try:
        await append(
            conn,
            tenant_slug=tenant_slug,
            service=service,
            action="acceso.denegado",
            object_type="rbac",
            object_ref=perm_name,
            actor=actor,
            canal=canal,
            payload={
                "perm": perm_name,
                "min_crud": min_crud,
                "motivo": motivo,
            },
        )
    except Exception:  # noqa: BLE001 — best-effort: nunca convertir un 403 en 500.
        logger.warning(
            "audit.append_denial failed (service=%s tenant=%s perm=%s)",
            service,
            tenant_slug,
            perm_name,
            exc_info=True,
        )


async def verify_chain(conn: asyncpg.Connection, tenant_slug: str) -> tuple[bool, int | None]:
    """Recalcula la cadena de un tenant.

    Devuelve ``(ok, id_de_la_primera_fila_rota)``. ``ok=True`` y ``None`` si la
    cadena es íntegra. Pensado para un job de verificación periódico (ADR-008).
    """
    rows = await conn.fetch(
        """
        SELECT id, service, actor, canal, action, object_type, object_ref,
               payload, prev_hash, hash
        FROM public.audit_log
        WHERE tenant_slug = $1
        ORDER BY id ASC
        """,
        tenant_slug,
    )
    prev_hash: str | None = None
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        expected = compute_hash(
            prev_hash,
            tenant_slug=tenant_slug,
            service=row["service"],
            actor=row["actor"],
            canal=row["canal"],
            action=row["action"],
            object_type=row["object_type"],
            object_ref=row["object_ref"],
            payload=payload,
        )
        if expected != row["hash"] or row["prev_hash"] != prev_hash:
            return False, row["id"]
        prev_hash = row["hash"]
    return True, None
