# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Rotación de la KEK de custodia de las claves de firma personales (E17/F4).

Re-envuelve el material privado de ``auth_user_signing_key`` bajo la KEK
**activa** del llavero (``settings.signing_key_active_version``), descifrando
cada fila con la KEK de SU ``key_version`` y re-cifrándola con la activa. El
AAD (``tenant:{slug}:user:{id}:signing``) NO cambia — solo cambia la KEK.

Canal de OPS, NO endpoint HTTP: descifra en memoria la privada de todos los
usuarios de un tenant; ese privilegio no debe existir como superficie de red.
Se ejecuta donde el operador ya monta ambas KEKs para la transición:

    python -m app.ops.rewrap_signing_keys --verify              # conteo por versión (todos los tenants)
    python -m app.ops.rewrap_signing_keys --dry-run             # cuenta lo que se re-envolvería
    python -m app.ops.rewrap_signing_keys                       # re-envuelve todos los tenants
    python -m app.ops.rewrap_signing_keys --tenant icetex       # solo un tenant

INVARIANTES (fail-closed):
 - NUNCA se retira una KEK vieja mientras alguna fila la referencie: ``--verify``
   lista los conteos por versión en TODOS los tenants; una versión con conteo 0
   en todos es retirable (quitarla del llavero es acto MANUAL del operador).
 - Round-trip obligatorio: tras re-cifrar se re-descifra y compara en tiempo
   constante contra el plaintext original ANTES de persistir. Nunca se escribe
   un ciphertext que no round-trippee (sería una clave irrecuperable).
 - Nonce NUEVO por re-cifrado (``encrypt_private_key`` acuña uno aleatorio) —
   jamás se reutiliza el nonce viejo con la KEK nueva (catastrófico en GCM).
 - El plaintext es transitorio: ``del`` tras el round-trip; jamás se loguea,
   audita ni retorna. El asiento ``signing_key.rewrapped`` lleva solo metadatos
   (user_id, from/to version).
 - Idempotente/reanudable: procesa solo ``key_version <> activa``; un fallo a
   mitad deja unas filas en vN y otras en la activa — seguro, porque la firma
   elige la KEK por la versión de CADA fila y ambas siguen cargadas durante la
   transición. Re-ejecutar recoge el remanente. Exit-code != 0 si algo falla.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
import sys

import asyncpg

from app.core.config import settings
from app.core.signing_keys import (
    build_aad,
    decrypt_private_key,
    encrypt_private_key,
)
from app.repositories.signing_key_repository import SigningKeyRepository

try:  # auditoría compartida (misma disponibilidad opcional que el servicio)
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


async def _tenant_schemas(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name LIKE 'tenant\\_%' ESCAPE '\\' ORDER BY schema_name"
    )
    return [r["schema_name"] for r in rows]


async def _verify(conn: asyncpg.Connection) -> int:
    """Conteo por ``key_version`` en todos los tenants — el operador decide qué
    KEK retirar. Devuelve exit-code 0 (informativo)."""
    active_v = settings.signing_key_active_version
    keyring = settings.signing_key_keyring()  # fail-closed si la activa falta
    print(f"KEK activa: v{active_v}; versiones cargadas: {sorted(keyring)}")
    totales: dict[int, int] = {}
    for schema in await _tenant_schemas(conn):
        await conn.execute(f'SET search_path = "{schema}"')
        repo = SigningKeyRepository(conn)
        for row in await repo.count_by_key_version():
            totales[row["key_version"]] = totales.get(row["key_version"], 0) + row["n"]
    await conn.execute("SET search_path = public")
    if not totales:
        print("  (sin credenciales de firma en ningún tenant)")
    for ver in sorted(totales):
        marca = ""
        if ver != active_v and totales[ver] == 0:
            marca = "  <- retirable (conteo 0)"
        elif ver < active_v:
            marca = "  <- pendiente de re-wrap"
        elif ver > active_v:
            marca = "  <- ANOMALÍA: versión mayor que la activa (no se re-envuelve hacia atrás)"
        print(f"  v{ver}: {totales[ver]} credencial(es){marca}")
    retirables = [v for v, n in totales.items() if v < active_v and n == 0]
    if retirables:
        print(f"Versiones retirables del llavero (conteo 0 en todos los tenants): {sorted(retirables)}")
    return 0


async def _rewrap_tenant(
    conn: asyncpg.Connection,
    *,
    schema: str,
    slug: str,
    active_v: int,
    active_key: bytes,
    keyring: dict[int, bytes],
    dry_run: bool,
) -> tuple[int, int]:
    """Re-envuelve las credenciales de un tenant. Devuelve (hechas, fallidas)."""
    await conn.execute(f'SET search_path = "{schema}"')
    repo = SigningKeyRepository(conn)
    pendientes = await repo.fetch_ids_to_rewrap(active_v)
    if not pendientes:
        return (0, 0)
    if dry_run:
        print(f"  [{slug}] {len(pendientes)} credencial(es) se re-envolverían")
        return (len(pendientes), 0)

    hechas = 0
    fallidas = 0
    for p in pendientes:
        try:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}"')
                repo_tx = SigningKeyRepository(conn)
                row = await repo_tx.get_for_rewrap(p["id"])
                if (
                    row is None
                    or row["key_version"] >= active_v
                    or row["private_key_cifrado"] is None
                ):
                    # `private_key_cifrado is None`: la purga de revocadas anuló
                    # el material entre el snapshot de `fetch_ids_to_rewrap` y
                    # este FOR UPDATE (TOCTOU con purge_revoked_signing_keys) —
                    # una purgada no tiene nada que re-envolver; saltarla evita
                    # un decrypt(None). Simetría con el re-check de la purga.
                    continue  # carrera con otra corrida/enroll/purga -> idempotente
                old_key = keyring.get(row["key_version"])
                if old_key is None:
                    raise RuntimeError(
                        f"KEK v{row['key_version']} ausente para {row['id']} — no re-envolvible"
                    )
                aad = build_aad(tenant_slug=slug, user_id=str(row["user_id"]))
                pem = decrypt_private_key(
                    row["private_key_cifrado"],
                    nonce=row["private_key_nonce"],
                    key=old_key,
                    aad=aad,
                )
                ciphertext, nonce = encrypt_private_key(pem, key=active_key, aad=aad)
                # Round-trip fail-closed: nunca persistir un ciphertext que no
                # se pueda volver a descifrar a EXACTAMENTE el mismo plaintext.
                roundtrip = decrypt_private_key(
                    ciphertext, nonce=nonce, key=active_key, aad=aad
                )
                if not hmac.compare_digest(roundtrip, pem):
                    raise RuntimeError(f"round-trip falló para {row['id']} — no se persiste")
                del pem, roundtrip
                await repo_tx.rewrap(
                    row["id"],
                    private_key_cifrado=ciphertext,
                    private_key_nonce=nonce,
                    key_version=active_v,
                )
                if _AUDIT_AVAILABLE:
                    await _audit.append(
                        conn,
                        tenant_slug=slug,
                        service=_SERVICE,
                        action="signing_key.rewrapped",
                        object_type="signing_key",
                        object_ref=str(row["user_id"]),
                        actor="system:kek-rotation",
                        payload={"from_version": row["key_version"], "to_version": active_v},
                        canal="system",
                    )
            hechas += 1
        except Exception as exc:  # noqa: BLE001 — aísla el fallo por-fila, sigue
            fallidas += 1
            print(f"  [{slug}] FALLO al re-envolver {p['id']}: {exc}", file=sys.stderr)
    print(f"  [{slug}] re-envueltas: {hechas}; fallidas: {fallidas}")
    return (hechas, fallidas)


async def _rewrap_all(conn: asyncpg.Connection, *, only_tenant: str | None, dry_run: bool) -> int:
    active_v, active_key = settings.signing_key_active_key()  # fail-closed
    keyring = settings.signing_key_keyring()
    if not dry_run and not _AUDIT_AVAILABLE:
        # Re-envolver el material privado de todos los usuarios SIN dejar traza
        # es inaceptable — se aborta (fail-closed, mismo criterio que la
        # remediación R1 de la revocación: revoke/activar fallan cerrado sin
        # auditoría). El dry-run/verify no mutan, así que no lo exigen.
        raise RuntimeError(
            "orpycamcp_common (auditoría) no disponible — se aborta el re-wrap "
            "para no re-envolver material privado sin traza"
        )
    print(f"Re-wrap a KEK activa v{active_v}{' (dry-run)' if dry_run else ''}")
    total_hechas = 0
    total_fallidas = 0
    for schema in await _tenant_schemas(conn):
        # El AAD del enroll usa el slug CRUDO del claim (con guiones); el schema
        # es `tenant_{slug.replace('-','_')}` (ver core/database.tenant_schema).
        # Como el validador de slug (`_SLUG_RE`) PROHÍBE `_`, la sustitución
        # `-`->`_` es biyectiva y su inverso exacto es `_`->`-`. Sin este
        # inverso, un tenant con guión (p. ej. `mi-tenant`) daría un AAD
        # `mi_tenant` que NO casa con el del enroll → InvalidTag en cada fila →
        # rotación permanentemente bloqueada para ese tenant (hallazgo Alto).
        slug = schema[len("tenant_"):].replace("_", "-") if schema.startswith("tenant_") else schema
        if only_tenant and slug != only_tenant:
            continue
        hechas, fallidas = await _rewrap_tenant(
            conn,
            schema=schema,
            slug=slug,
            active_v=active_v,
            active_key=active_key,
            keyring=keyring,
            dry_run=dry_run,
        )
        total_hechas += hechas
        total_fallidas += fallidas
    await conn.execute("SET search_path = public")
    print(f"Total: {total_hechas} re-envuelta(s), {total_fallidas} fallida(s)")
    return 1 if total_fallidas else 0


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotación de la KEK de custodia (E17/F4)")
    parser.add_argument("--tenant", help="slug de un solo tenant (default: todos)")
    parser.add_argument(
        "--dry-run", action="store_true", help="solo cuenta lo que se re-envolvería"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="conteo por key_version (todos los tenants) — qué KEK es retirable",
    )
    args = parser.parse_args(argv)

    conn = await asyncpg.connect(dsn=_require_dsn())
    try:
        if args.verify:
            return await _verify(conn)
        return await _rewrap_all(conn, only_tenant=args.tenant, dry_run=args.dry_run)
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
