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
from typing import Any

import asyncpg

# Cuando un evento no pertenece a ningún tenant (p. ej. refresh/logout de auth),
# se ancla en una cadena de sistema dedicada en vez de violar el NOT NULL.
SYSTEM_TENANT = "__system__"

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
