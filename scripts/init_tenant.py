# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
init_tenant.py — Inicializar un nuevo tenant en OrpycaMCP.

Aplica migraciones globales (public schema) y por-tenant para todos los servicios,
registra el tenant en public.tenants y crea su cliente de servicio de inteligencia
en el realm (ADR-045 §4). Ese cliente no entra en ALLOWED_AZP.

Lo aplicado se anota en el libro único `public.schema_migrations`, compartido con
`services/*/migrations/runner.py` (ver `orpycamcp_common.migrations`). Antes cada
ejecutor llevaba su propia contabilidad con claves distintas y no se veían entre
sí: uno reaplicaba lo que el otro ya había hecho, y el nombre desnudo del archivo
—que se repite entre servicios— hacía saltar migraciones en silencio.

Uso:
    python scripts/init_tenant.py --slug icetex --name "ICETEX" --code "ICETEX"

Variables de entorno requeridas:
    DATABASE_URL  postgresql+asyncpg://... o postgresql://...
"""

import argparse
import asyncio
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

import asyncpg
from orpycamcp_common import migrations as ledger

# ---------------------------------------------------------------------------
# Rutas a las migraciones de cada servicio
# (relativas a la raíz del proyecto, montada en /project dentro del contenedor)
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent

# Each entry points to the service root; _sql_files() appends the sub-dir.
# Migrations live in migrations/global/*.sql  (schema public)
#                  and migrations/tenant/*.sql  (schema tenant_{slug}).
# Las GLOBALES se aplican SIEMPRE antes que las de tenant (ver main()), lo que
# importa porque instalan extensiones de las que dependen tablas por-tenant:
# `document-service/001_extensions.sql` (pg_trgm/unaccent → FTS). Omitirlas hacía
# fallar la provisión de ese servicio.
GLOBAL_MIGRATIONS = [
    BASE / "services" / "auth-service"   / "migrations" / "global",
    BASE / "services" / "tenant-service" / "migrations" / "global",
    BASE / "services" / "document-service"  / "migrations" / "global",
]

# ORDEN: `auth-service` primero (RBAC/usuarios) y `tenant-service` a continuación
# por ser fundacional (dependencias, parámetros, catálogos, PINAR); los servicios
# de dominio después; `knowledge-service` al final, por ser una capa DERIVADA
# (no fuente de verdad — ADR-046).
#
# Esta lista debe cubrir TODO servicio con `migrations/tenant/`. Al añadir un
# servicio nuevo, añadirlo también aquí: una omisión no da error: el tenant se
# provisiona "bien" y el servicio falla en runtime contra tablas inexistentes.
TENANT_MIGRATIONS = [
    BASE / "services" / "auth-service"    / "migrations" / "tenant",
    BASE / "services" / "tenant-service"    / "migrations" / "tenant",
    BASE / "services" / "document-service"  / "migrations" / "tenant",
    BASE / "services" / "storage-service"   / "migrations" / "tenant",
    BASE / "services" / "workflow-service"  / "migrations" / "tenant",
    BASE / "services" / "archive-service"   / "migrations" / "tenant",
    BASE / "services" / "signature-service" / "migrations" / "tenant",
    BASE / "services" / "notification-service" / "migrations" / "tenant",
    BASE / "services" / "knowledge-service" / "migrations" / "tenant",
]


def _dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def _sql_files(directory: Path) -> list[Path]:
    """Return sorted, numerically-prefixed .sql files in *directory*.

    Returns an empty list when the directory does not exist so that services
    without a given sub-dir (e.g. no global migrations) are silently skipped.
    """
    if not directory.is_dir():
        return []
    return sorted(
        f for f in directory.glob("*.sql")
        if re.match(r"^\d+_", f.name)
    )


def _servicio_de(migrations_dir: Path) -> str:
    """…/services/<svc>/migrations/<ámbito> → `<svc>`."""
    return migrations_dir.parent.parent.name


def _mapa_servicios(dirs: list[Path]) -> dict[str, list[str]]:
    """`{archivo: [servicios que lo declaran]}` para el import del libro único.

    Es lo que permite devolverle el servicio a las entradas antiguas, que
    guardaban el nombre desnudo. Cuatro nombres los declaran DOS servicios
    (`002_batch_jobs.sql` en archive/document; `005`, `006` y `007` de
    dead-letter en notification/signature): la ambigüedad no se puede resolver
    leyendo el registro, y `orpycamcp_common.migrations` la resuelve avisando en
    vez de en silencio.
    """
    mapa: dict[str, list[str]] = {}
    for d in dirs:
        for path in _sql_files(d):
            mapa.setdefault(path.name, []).append(_servicio_de(d))
    return mapa


# ---------------------------------------------------------------------------
# Migraciones globales — se anotan en el libro único public.schema_migrations
# ---------------------------------------------------------------------------

async def apply_global_migrations(conn: asyncpg.Connection) -> None:
    aplicadas = await ledger.applied(conn, ledger.ESQUEMA_GLOBAL)
    for migrations_dir in GLOBAL_MIGRATIONS:
        servicio = _servicio_de(migrations_dir)
        for path in _sql_files(migrations_dir):
            if (servicio, path.name) in aplicadas:
                print(f"  [global] skip  {servicio}/{path.name}")
                continue
            print(f"  [global] apply {servicio}/{path.name}")
            # En transacción, igual que los runners (C19): sin DDL huérfano del libro.
            async with conn.transaction():
                await conn.execute(path.read_text())
                await ledger.record(conn, ledger.ESQUEMA_GLOBAL, servicio, path.name)
            # Alimentar el conjunto en caliente: se leyó UNA vez antes del bucle.
            aplicadas.add((servicio, path.name))


# ---------------------------------------------------------------------------
# Schema del tenant — creado por tenant-service; aquí lo garantizamos
# ---------------------------------------------------------------------------

async def provision_schema(conn: asyncpg.Connection, slug: str) -> None:
    schema = f"tenant_{slug.replace('-', '_')}"
    print(f"  [schema] CREATE SCHEMA IF NOT EXISTS {schema}")
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    # El rol de la aplicación necesita USAR el esquema nuevo: los privilegios por
    # omisión cubren sus tablas, no el esquema en sí (ver migración 008).
    if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app'"):
        await conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO orpycamcp_app")
        print(f"  [schema] GRANT USAGE ON {schema} TO orpycamcp_app")


# ---------------------------------------------------------------------------
# Migraciones por tenant — mismo libro único, con `schema_name = tenant_{slug}`.
#
# Antes vivían en `tenant_{slug}._migrations`, invisible para el registro que usa
# `services/*/migrations/runner.py` (`public._tenant_migrations`): provisionar
# con un ejecutor y actualizar con el otro reaplicaba TODAS las migraciones.
# ---------------------------------------------------------------------------

async def apply_tenant_migrations(conn: asyncpg.Connection, slug: str) -> None:
    schema = f"tenant_{slug.replace('-', '_')}"
    await conn.execute(f"SET search_path = \"{schema}\"")
    aplicadas = await ledger.applied(conn, schema)
    for migrations_dir in TENANT_MIGRATIONS:
        servicio = _servicio_de(migrations_dir)
        for path in _sql_files(migrations_dir):
            if (servicio, path.name) in aplicadas:
                print(f"  [{schema}] skip  {servicio}/{path.name}")
                continue
            print(f"  [{schema}] apply {servicio}/{path.name}")
            async with conn.transaction():
                await conn.execute(path.read_text())
                await ledger.record(conn, schema, servicio, path.name)
            aplicadas.add((servicio, path.name))
    # Restaurar search_path al terminar
    await conn.execute("SET search_path = public")


# ---------------------------------------------------------------------------
# Registro del tenant en public.tenants
# ---------------------------------------------------------------------------

async def register_tenant(
    conn: asyncpg.Connection, slug: str, name: str, code: str
) -> None:
    existing = await conn.fetchrow(
        "SELECT id, slug FROM public.tenants WHERE slug = $1", slug
    )
    if existing:
        print(f"  [tenant] ya existe — slug={slug}, id={existing['id']}")
        # Una entidad dada de alta por `POST /api/v1/tenants` nace `pending`
        # (solo registro, sin esquema): esta provisión es la que la completa.
        activated = await conn.execute(
            "UPDATE public.tenants SET provisioning_status = 'active', updated_at = now() "
            "WHERE slug = $1 AND provisioning_status <> 'active'",
            slug,
        )
        if activated != "UPDATE 0":
            print(f"  [tenant] provisioning_status pending -> active — slug={slug}")
        return
    row = await conn.fetchrow(
        """
        INSERT INTO public.tenants (slug, name, code, status)
        VALUES ($1, $2, $3, 'active')
        RETURNING id
        """,
        slug, name, code,
    )
    print(f"  [tenant] registrado — slug={slug}, id={row['id']}")


# ---------------------------------------------------------------------------
# Cliente de servicio de inteligencia (ADR-045 §4)
#
# Confidencial y solo client_credentials. Su token se presenta al proveedor,
# nunca a Docuboto: no se añade a ALLOWED_AZP. El secreto se guarda cifrado;
# este paso imprime el par (issuer, client_id) y nunca el secreto.
# Reejecutarlo no rota el secreto si la fila ya existe.
# ---------------------------------------------------------------------------

FINALIDAD_CLIENTE = "service_client"
SLOT_CLIENTE = 1


def esquema_de(slug: str) -> str:
    """`tenant_{slug}` con guiones pasados a guion bajo. El slug ya está acotado."""
    if not re.match(r"^[a-z0-9-]+$", slug):
        raise ValueError("slug fuera de forma")
    return "tenant_" + slug.replace("-", "_")


def id_cliente_inteligencia(slug: str) -> str:
    """Identificador estable del cliente de esa entidad en el realm."""
    esquema_de(slug)
    return f"intelligence-{slug}"


def cuerpo_cliente_servicio(client_id: str, secret: str) -> dict[str, Any]:
    """Representación de Keycloak: cuenta de servicio, sin flujos de usuario."""
    return {
        "clientId": client_id,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "bearerOnly": False,
        "standardFlowEnabled": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": True,
        "secret": secret,
    }


def aad_credencial(slug: str, finalidad: str, slot: int) -> bytes:
    """AAD atada a la entidad, la finalidad y la ranura. Una fila copiada no descifra."""
    return f"tenant:{slug}:intelligence:{finalidad}:{slot}".encode()


def issuer_publico(keycloak_url: str, realm: str, issuer: str | None) -> str:
    """Issuer que el proveedor registra. Si no hay uno público, se deriva de la URL."""
    if issuer and issuer.strip():
        return issuer.strip().rstrip("/")
    return f"{keycloak_url.rstrip('/')}/realms/{realm}"


def _error_keycloak(metodo: str, ruta: str, status: int) -> RuntimeError:
    """El mensaje nombra el fallo y no el cuerpo: el cuerpo puede hacer eco del secreto."""
    return RuntimeError(f"Keycloak respondió {status} en {metodo} {ruta}")


async def _json_o_fallo(
    http: Any,
    metodo: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    ok: tuple[int, ...] = (200,),
) -> Any:
    resp = await http.request(metodo, url, headers=headers, params=params, json=json)
    if resp.status_code not in ok:
        raise _error_keycloak(metodo, url, resp.status_code)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


async def token_admin(
    http: Any, *, keycloak_url: str, admin_user: str, admin_password: str
) -> str:
    """Token de la API de administración. No es un token de la API de Docuboto."""
    url = f"{keycloak_url.rstrip('/')}/realms/master/protocol/openid-connect/token"
    resp = await http.request(
        "POST",
        url,
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": admin_user,
            "password": admin_password,
        },
    )
    if resp.status_code != 200:
        raise _error_keycloak("POST", url, resp.status_code)
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("Keycloak no devolvió access_token de administración")
    return token


async def asegurar_cliente_servicio(
    http: Any,
    *,
    keycloak_url: str,
    realm: str,
    token: str,
    client_id: str,
    secret_nuevo: str,
) -> str:
    """Crea el cliente si falta y devuelve el secreto que Keycloak conserva.

    Si el cliente ya existe, no se le pone `secret_nuevo`: se lee el que ya tiene.
    En los dos casos se dejan los flujos de usuario apagados.
    """
    base = keycloak_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    lista_url = f"{base}/admin/realms/{realm}/clients"

    async def buscar() -> dict[str, Any] | None:
        lista = await _json_o_fallo(
            http, "GET", lista_url, headers=headers, params={"clientId": client_id}
        )
        encontrados = [c for c in lista if c.get("clientId") == client_id]
        return encontrados[0] if encontrados else None

    cliente = await buscar()
    if cliente is None:
        alta = f"{base}/admin/realms/{realm}/clients"
        resp = await http.request(
            "POST",
            alta,
            headers=headers,
            json=cuerpo_cliente_servicio(client_id, secret_nuevo),
        )
        if resp.status_code == 409:
            cliente = await buscar()
        elif resp.status_code not in (201, 204):
            raise _error_keycloak("POST", alta, resp.status_code)
        else:
            cliente = await buscar()
        if cliente is None:
            raise RuntimeError(f"Keycloak no muestra el cliente {client_id} tras crearlo")

    uuid = cliente.get("id")
    if not uuid:
        raise RuntimeError("Keycloak devolvió el cliente sin id")
    flags = cuerpo_cliente_servicio(client_id, secret_nuevo)
    flags.pop("secret")
    cliente.update(flags)
    cliente.pop("secret", None)
    put_url = f"{base}/admin/realms/{realm}/clients/{uuid}"
    await _json_o_fallo(http, "PUT", put_url, headers=headers, json=cliente, ok=(200, 204))

    secreto_url = f"{base}/admin/realms/{realm}/clients/{uuid}/client-secret"
    secreto = await _json_o_fallo(http, "GET", secreto_url, headers=headers)
    valor = (secreto or {}).get("value")
    if not valor:
        raise RuntimeError("Keycloak no devolvió el secreto del cliente de servicio")
    return valor


async def aprovisionar_cliente_inteligencia(
    conn: Any,
    slug: str,
    *,
    key: bytes,
    keycloak_url: str,
    realm: str,
    admin_user: str,
    admin_password: str,
    issuer: str | None = None,
    http: Any | None = None,
) -> str:
    """Crea el cliente y guarda el secreto, o no hace nada si la fila ya existe.

    Devuelve la línea que se imprime: issuer y client_id, sin el secreto.
    """
    from orpycamcp_common.kek import seal

    client_id = id_cliente_inteligencia(slug)
    publicado = issuer_publico(keycloak_url, realm, issuer)
    esquema = esquema_de(slug)
    await conn.execute(f'SET search_path = "{esquema}"')
    try:
        fila = await conn.fetchrow(
            "SELECT client_id FROM intelligence_credentials WHERE purpose = $1 AND slot = $2",
            FINALIDAD_CLIENTE,
            SLOT_CLIENTE,
        )
        if fila is not None:
            guardado = fila["client_id"]
            if guardado != client_id:
                raise RuntimeError(
                    f"la fila service_client guarda {guardado}, no {client_id}"
                )
            return f"[intelligence] ya existía — issuer={publicado} client_id={client_id}"

        import httpx

        secreto_nuevo = secrets.token_urlsafe(32)
        propio = http is None
        cliente_http = http if http is not None else httpx.AsyncClient(timeout=30.0)
        try:
            try:
                token = await token_admin(
                    cliente_http,
                    keycloak_url=keycloak_url,
                    admin_user=admin_user,
                    admin_password=admin_password,
                )
                secreto = await asegurar_cliente_servicio(
                    cliente_http,
                    keycloak_url=keycloak_url,
                    realm=realm,
                    token=token,
                    client_id=client_id,
                    secret_nuevo=secreto_nuevo,
                )
            except RuntimeError:
                raise
            except httpx.HTTPError:
                raise RuntimeError(
                    "Keycloak no respondió. El esquema del tenant ya quedó aplicado."
                ) from None
        finally:
            if propio:
                await cliente_http.aclose()

        ciphertext, nonce = seal(
            secreto.encode(),
            key=key,
            aad=aad_credencial(slug, FINALIDAD_CLIENTE, SLOT_CLIENTE),
        )
        await conn.execute(
            """
            INSERT INTO intelligence_credentials
                (purpose, slot, client_id, ciphertext, nonce, kek_version)
            VALUES ($1, $2, $3, $4, $5, 1)
            """,
            FINALIDAD_CLIENTE,
            SLOT_CLIENTE,
            client_id,
            ciphertext,
            nonce,
        )
    finally:
        await conn.execute("SET search_path = public")
    return f"[intelligence] cliente de servicio — issuer={publicado} client_id={client_id}"


def _exigir_entorno(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        print(f"ERROR: {nombre} no está definido", file=sys.stderr)
        sys.exit(1)
    return valor


def _kek_credencial() -> bytes:
    from orpycamcp_common.kek import decode_aes256

    cruda = _exigir_entorno("INTELLIGENCE_CREDENTIAL_KEK")
    try:
        return decode_aes256(cruda, "INTELLIGENCE_CREDENTIAL_KEK")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main(slug: str, name: str, code: str) -> None:
    raw_dsn = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not raw_dsn:
        print("ERROR: DATABASE_URL no está definido", file=sys.stderr)
        sys.exit(1)

    dsn = _dsn(raw_dsn)
    print("\nConectando a PostgreSQL...")
    conn = await asyncpg.connect(dsn=dsn)

    try:
        # Mismo cerrojo global que los runners (C19, ADR-041): provisionar un tenant
        # mientras arranca un migrate-<svc> ya no los deja pisarse.
        async with ledger.cerrojo(conn):
            print("\n── Libro de migraciones ──────────────────────────────────────")
            await ledger.ensure_ledger(conn)
            # Import único de los tres registros antiguos. Este ejecutor ve los
            # directorios de todos los servicios, así que puede atribuir cada nombre
            # desnudo; un nombre que no reconoce es una migración retirada del
            # código y merece aviso (de ahí `ignorar_desconocidos` por defecto).
            for aviso in await ledger.importar_libros_antiguos(
                conn,
                servicios_por_archivo_global=_mapa_servicios(GLOBAL_MIGRATIONS),
                servicios_por_archivo_tenant=_mapa_servicios(TENANT_MIGRATIONS),
            ):
                print(aviso)

            print("\n── Migraciones globales (public schema) ──────────────────────")
            await apply_global_migrations(conn)

            print(f"\n── Schema del tenant: tenant_{slug.replace('-', '_')} ─────")
            await provision_schema(conn, slug)

            print("\n── Migraciones por tenant ────────────────────────────────────")
            await apply_tenant_migrations(conn, slug)

            print("\n── Registro en public.tenants ────────────────────────────────")
            await register_tenant(conn, slug, name, code)

            print("\n── Cliente de servicio de inteligencia ───────────────────────")
            try:
                linea = await aprovisionar_cliente_inteligencia(
                    conn,
                    slug,
                    key=_kek_credencial(),
                    keycloak_url=_exigir_entorno("KEYCLOAK_URL"),
                    realm=os.environ.get("KEYCLOAK_REALM", "orpycamcp").strip() or "orpycamcp",
                    admin_user=_exigir_entorno("KEYCLOAK_ADMIN"),
                    admin_password=_exigir_entorno("KEYCLOAK_ADMIN_PASSWORD"),
                    issuer=os.environ.get("KEYCLOAK_ISSUER"),
                )
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                print(
                    "El esquema del tenant ya quedó aplicado. Vuelva a correr el mismo "
                    "comando cuando Keycloak responda: este paso no rota un secreto ya guardado.",
                    file=sys.stderr,
                )
                raise SystemExit(1) from None
            print(f"  {linea}")

            print(f"\n✓ Tenant '{slug}' inicializado correctamente.\n")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicializar tenant en OrpycaMCP")
    parser.add_argument("--slug", required=True, help="Slug único del tenant (ej: icetex)")
    parser.add_argument("--name", required=True, help="Nombre completo (ej: 'ICETEX')")
    parser.add_argument("--code", required=True, help="Código para radicados (ej: 'ICETEX')")
    args = parser.parse_args()

    # Validaciones básicas
    if not re.match(r"^[a-z0-9-]+$", args.slug):
        print("ERROR: slug solo puede contener minúsculas, números y guiones", file=sys.stderr)
        sys.exit(1)
    if not re.match(r"^[A-Z0-9_]+$", args.code):
        print("ERROR: code solo puede contener mayúsculas, números y guiones bajos", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(args.slug, args.name, args.code))
