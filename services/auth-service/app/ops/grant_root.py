# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Aprovisionamiento fuera de banda de ROOT (E08, cierre de hallazgo Alto).

**Por qué existe este script**: la auditoría de seguridad encontró que un
administrador de entidad (`USUA_PERM_ADMIN`) podía auto-escalarse a
`USUA_PERM_ROOT` — vía `POST /auth/users` con `is_root: true`, o vía
`PUT /auth/groups/{id}/permissions/{id}` concediendo `USUA_PERM_ROOT` al grupo
del que él mismo es miembro. `RBACService.create_user`/`set_group_permission`
(``app/services/rbac_service.py``) ahora RECHAZAN ambas vías con 403 salvo que
el llamante YA sea ROOT.

Cerrar esas dos vías sin más deja un punto muerto: `rbac_repository.py` nunca
voltea `is_root` desde la API (`upsert_user_by_keycloak_sub` — "Never flips
is_root — admin must set it manually"), así que **ningún tenant podría tener
jamás su primer ROOT**. Este script es la contraparte deliberada: la única vía
de conceder ROOT es fuera de banda, por quien tiene acceso directo a la base
de datos (no HTTP, no expuesto por el gateway) — igual que
`purge_revoked_signing_keys.py`/`rewrap_signing_keys.py` son las únicas vías
de mutar material de firma sin pasar por un endpoint.

Uso::

    python -m app.ops.grant_root --tenant icetex --user jdoe             # concede ROOT
    python -m app.ops.grant_root --tenant icetex --user jdoe --dry-run   # solo informa

INVARIANTES (fail-closed):
 - `--tenant` es OBLIGATORIO y designa UN solo tenant — a diferencia de
   `purge_revoked_signing_keys`/`rewrap_signing_keys` (que barren todos los
   tenants por defecto), conceder ROOT es un acto puntual y nunca debe poder
   aplicarse "a todos" por omisión de flag.
 - El usuario debe existir YA en el tenant (creado con `is_root=false` vía
   `POST /auth/users`, o auto-aprovisionado por `/validate` en su primer
   login). Este script NO crea usuarios, solo promueve uno existente.
 - Idempotente: si el usuario ya es ROOT, no vuelve a escribir ni a auditar —
   informa y sale con código 0.
 - Deja asiento `rbac.root_otorgado_fuera_de_banda` en `public.audit_log`
   (misma transacción que el `UPDATE`), con `actor="system:ops-grant-root"`
   (precedente: `signing_key.private_purged`/`signing_key.rewrapped` usan el
   mismo prefijo `system:` para operaciones de ops sin actor humano en la
   sesión). Un tenant sin ROOT alguno estaría, en ausencia de este asiento,
   COMPLETAMENTE fuera de la traza de cómo obtuvo su primer superusuario.
 - Aborta si la auditoría no está disponible (no conceder el privilegio más
   alto del sistema sin dejar traza) — mismo criterio fail-closed que los dos
   scripts hermanos.
 - Exit-code != 0 si el usuario no existe o si algo falla.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg
from orpycamcp_common.tenant import slug_valido, tenant_schema

from app.repositories.rbac_repository import RBACRepository

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "auth-service"
# Perezoso, no `os.environ[...]` a nivel de módulo: importar este script no debe
# exigir `DATABASE_URL`. Los tests de integración lo importan para ejercitar su
# lógica, y en el carril UNITARIO esa variable no existe — la colección entera
# moría con un `KeyError: 'DATABASE_URL'` pelado. Al USARLO sí se exige, con un
# mensaje que dice qué falta.
_RAW_DSN = os.environ.get("DATABASE_URL", "")
_DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""


def _require_dsn() -> str:
    if not _DSN:
        raise RuntimeError(
            "DATABASE_URL no está definida: este script de operaciones necesita la "
            "cadena de conexión a PostgreSQL."
        )
    return _DSN

# Este script es un canal de confianza (acceso directo a la BD, no HTTP), pero
# igual se valida la forma del slug (validador único de `orpycamcp_common.tenant`)
# para no construir un identificador de schema arbitrario desde la línea de comandos.


def _schema_for(slug: str) -> str:
    if not slug_valido(slug):
        raise SystemExit(f"--tenant inválido: {slug!r}")
    return tenant_schema(slug)


async def _grant(conn: asyncpg.Connection, *, tenant: str, username: str, dry_run: bool) -> int:
    schema = _schema_for(tenant)
    await conn.execute(f'SET search_path = "{schema}", public')
    repo = RBACRepository(conn)
    user = await repo.get_user_by_username(username)
    if user is None:
        print(
            f"ERROR: usuario '{username}' no existe en el tenant '{tenant}' "
            "(debe crearse antes vía POST /auth/users o el auto-aprovisionamiento "
            "de /validate)",
            file=sys.stderr,
        )
        return 1
    if user["is_root"]:
        print(f"'{user['username']}' ya es ROOT en '{tenant}' — nada que hacer (idempotente)")
        return 0
    if dry_run:
        print(f"[dry-run] se otorgaría ROOT a '{user['username']}' ({user['id']}) en '{tenant}'")
        return 0

    async with conn.transaction():
        updated = await repo.set_root(user["id"])
        await _audit.append(
            conn,
            tenant_slug=tenant,
            service=_SERVICE,
            action="rbac.root_otorgado_fuera_de_banda",
            object_type="usuario",
            object_ref=str(updated["id"]),
            actor="system:ops-grant-root",
            payload={"username": updated["username"], "tenant": tenant},
            canal="system",
        )
    print(f"OK: '{updated['username']}' ({updated['id']}) ahora es ROOT en '{tenant}'")
    return 0


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Concede USUA_PERM_ROOT a un usuario existente de UN tenant "
        "(aprovisionamiento fuera de banda, E08)."
    )
    parser.add_argument("--tenant", required=True, help="slug del tenant (obligatorio, nunca 'todos')")
    parser.add_argument("--user", required=True, help="username del usuario a promover a ROOT")
    parser.add_argument("--dry-run", action="store_true", help="solo informa, no muta")
    args = parser.parse_args(argv)

    if not args.dry_run and not _AUDIT_AVAILABLE:
        raise RuntimeError(
            "orpycamcp_common (auditoría) no disponible — se aborta la concesión "
            "de ROOT para no escalar el privilegio más alto del sistema sin traza"
        )

    conn = await asyncpg.connect(dsn=_require_dsn())
    try:
        return await _grant(conn, tenant=args.tenant, username=args.user, dry_run=args.dry_run)
    finally:
        await conn.execute("SET search_path = public")
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
