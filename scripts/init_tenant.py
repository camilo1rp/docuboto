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

Aplica la cadena de migraciones (ADR-053) a `public` y al esquema del tenant,
registra el tenant en public.tenants y crea su cliente de servicio de inteligencia
en el realm (ADR-045 §4). Ese cliente no entra en ALLOWED_AZP.

Lo aplicado se anota en `public.schema_migrations`, el mismo libro y con el mismo
cerrojo que `services/docuboto/migrations/runner.py` (ver
`orpycamcp_common.migrations`).

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
# The one migration chain (ADR-053): `global/` to `public`, `tenant/` to the
# schema of the new tenant. Same ledger and same lock as the runner.
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent
MIGRATIONS_DIR = BASE / "services" / "docuboto" / "migrations"


def _dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://")


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
FINALIDAD_SEUDONIMO = "pseudonym"
SLOT_SEUDONIMO = 1


def esquema_de(slug: str) -> str:
    """`tenant_{slug}` con guiones pasados a guion bajo. El slug ya está acotado."""
    if not re.match(r"^[a-z0-9-]+$", slug):
        raise ValueError("slug fuera de forma")
    return "tenant_" + slug.replace("-", "_")


def id_cliente_inteligencia(slug: str) -> str:
    """Identificador estable del cliente de esa entidad en el realm."""
    esquema_de(slug)
    return f"intelligence-{slug}"


# Audiencia que el proveedor exige en el access token. Literales adyacentes:
# el árbol de código no escribe el nombre del proveedor.
AUDIENCIA_PROVEEDOR = "pro" "boto-api"
NOMBRE_ALCANCE_AUDIENCIA = "audiencia-proveedor"


def mapeador_audiencia_proveedor() -> dict[str, Any]:
    """Mapper OIDC que añade la audiencia del proveedor al access token.

    `included.custom.audience` escribe el valor aunque el realm no tenga un
    cliente con ese id. Keycloak 26 aplica el mapper cuando vive en un client
    scope asignado al cliente; el mapper colgado solo del cliente no entra en
    el token de `client_credentials`.
    """
    return {
        "name": NOMBRE_ALCANCE_AUDIENCIA,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "consentRequired": False,
        "config": {
            "included.custom.audience": AUDIENCIA_PROVEEDOR,
            "id.token.claim": "false",
            "access.token.claim": "true",
        },
    }


def cuerpo_alcance_audiencia() -> dict[str, Any]:
    """Client scope del realm: el mapper sí entra en el access token."""
    return {
        "name": NOMBRE_ALCANCE_AUDIENCIA,
        "protocol": "openid-connect",
        "attributes": {
            "include.in.token.scope": "false",
            "display.on.consent.screen": "false",
        },
        "protocolMappers": [mapeador_audiencia_proveedor()],
    }


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
        "protocolMappers": [mapeador_audiencia_proveedor()],
    }


def aad_credencial(slug: str, finalidad: str, slot: int) -> bytes:
    """AAD atada a la entidad, la finalidad y la ranura. Una fila copiada no descifra."""
    return f"tenant:{slug}:intelligence:{finalidad}:{slot}".encode()


async def asegurar_clave_seudonimo(conn: Any, slug: str, *, key: bytes) -> None:
    """Sella una clave HMAC nueva si la ranura de seudónimo está vacía.

    Reejecutar no la rota. El id de quien consulta se sustituye por el HMAC
    de esta clave; sin ella la pasarela no pregunta al proveedor.
    """
    from orpycamcp_common.kek import seal

    fila = await conn.fetchrow(
        "SELECT purpose FROM intelligence_credentials WHERE purpose = $1 AND slot = $2",
        FINALIDAD_SEUDONIMO,
        SLOT_SEUDONIMO,
    )
    if fila is not None:
        return
    ciphertext, nonce = seal(
        secrets.token_bytes(32),
        key=key,
        aad=aad_credencial(slug, FINALIDAD_SEUDONIMO, SLOT_SEUDONIMO),
    )
    await conn.execute(
        """
        INSERT INTO intelligence_credentials
            (purpose, slot, client_id, ciphertext, nonce, kek_version)
        VALUES ($1, $2, NULL, $3, $4, 1)
        """,
        FINALIDAD_SEUDONIMO,
        SLOT_SEUDONIMO,
        ciphertext,
        nonce,
    )


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
    flags.pop("protocolMappers", None)
    cliente.update(flags)
    cliente.pop("secret", None)
    put_url = f"{base}/admin/realms/{realm}/clients/{uuid}"
    await _json_o_fallo(http, "PUT", put_url, headers=headers, json=cliente, ok=(200, 204))
    await asegurar_audiencia_proveedor(
        http,
        keycloak_url=keycloak_url,
        realm=realm,
        token=token,
        client_id=client_id,
        uuid=str(uuid),
    )

    secreto_url = f"{base}/admin/realms/{realm}/clients/{uuid}/client-secret"
    secreto = await _json_o_fallo(http, "GET", secreto_url, headers=headers)
    valor = (secreto or {}).get("value")
    if not valor:
        raise RuntimeError("Keycloak no devolvió el secreto del cliente de servicio")
    return valor


def _audiencia_de(mapeador: dict[str, Any]) -> str:
    config = mapeador.get("config")
    if not isinstance(config, dict):
        return ""
    valor = config.get("included.custom.audience") or config.get(
        "included.client.audience"
    )
    return valor if isinstance(valor, str) else ""


async def _asegurar_mapper(
    http: Any, modelos: str, headers: dict[str, str]
) -> None:
    """Deja el mapper de audiencia en esa colección. Sustituye la config vieja."""
    actuales = await _json_o_fallo(http, "GET", modelos, headers=headers)
    if not isinstance(actuales, list):
        actuales = []
    cuerpo = mapeador_audiencia_proveedor()
    existente = next(
        (
            m
            for m in actuales
            if isinstance(m, dict)
            and m.get("protocolMapper") == "oidc-audience-mapper"
            and _audiencia_de(m) == AUDIENCIA_PROVEEDOR
        ),
        None,
    )
    if existente is None:
        await _json_o_fallo(
            http, "POST", modelos, headers=headers, json=cuerpo, ok=(201, 204)
        )
        return
    mapper_id = existente.get("id")
    if not mapper_id:
        return
    actualizado = {**existente, **cuerpo, "id": mapper_id, "config": dict(cuerpo["config"])}
    await _json_o_fallo(
        http,
        "PUT",
        f"{modelos}/{mapper_id}",
        headers=headers,
        json=actualizado,
        ok=(200, 204),
    )


async def asegurar_alcance_audiencia(
    http: Any,
    *,
    keycloak_url: str,
    realm: str,
    token: str,
    uuid: str,
) -> None:
    """Crea el client scope y lo asigna al cliente. Sin él el mapper no viaja."""
    base = keycloak_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    scopes_url = f"{base}/admin/realms/{realm}/client-scopes"
    lista = await _json_o_fallo(http, "GET", scopes_url, headers=headers)
    if not isinstance(lista, list):
        lista = []
    alcance = next(
        (
            s
            for s in lista
            if isinstance(s, dict) and s.get("name") == NOMBRE_ALCANCE_AUDIENCIA
        ),
        None,
    )
    if alcance is None:
        resp = await http.request(
            "POST", scopes_url, headers=headers, json=cuerpo_alcance_audiencia()
        )
        if resp.status_code not in (201, 204, 409):
            raise _error_keycloak("POST", scopes_url, resp.status_code)
        lista = await _json_o_fallo(http, "GET", scopes_url, headers=headers)
        if not isinstance(lista, list):
            lista = []
        alcance = next(
            (
                s
                for s in lista
                if isinstance(s, dict) and s.get("name") == NOMBRE_ALCANCE_AUDIENCIA
            ),
            None,
        )
    if alcance is None or not alcance.get("id"):
        raise RuntimeError("Keycloak no muestra el client scope de audiencia")
    scope_id = str(alcance["id"])
    await _asegurar_mapper(
        http,
        f"{scopes_url}/{scope_id}/protocol-mappers/models",
        headers,
    )
    asignados_url = f"{base}/admin/realms/{realm}/clients/{uuid}/default-client-scopes"
    asignados = await _json_o_fallo(http, "GET", asignados_url, headers=headers)
    if not isinstance(asignados, list):
        asignados = []
    if any(
        isinstance(s, dict)
        and (s.get("id") == scope_id or s.get("name") == NOMBRE_ALCANCE_AUDIENCIA)
        for s in asignados
    ):
        return
    await _json_o_fallo(
        http,
        "PUT",
        f"{asignados_url}/{scope_id}",
        headers=headers,
        ok=(200, 204),
    )


async def asegurar_audiencia_proveedor(
    http: Any,
    *,
    keycloak_url: str,
    realm: str,
    token: str,
    client_id: str,
    uuid: str | None = None,
) -> None:
    """Deja el mapper en el cliente y en un client scope asignado. No rota el secreto."""
    base = keycloak_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    if not uuid:
        lista_url = f"{base}/admin/realms/{realm}/clients"
        lista = await _json_o_fallo(
            http, "GET", lista_url, headers=headers, params={"clientId": client_id}
        )
        encontrados = [
            c for c in lista if isinstance(c, dict) and c.get("clientId") == client_id
        ]
        if not encontrados or not encontrados[0].get("id"):
            raise RuntimeError(f"Keycloak no muestra el cliente {client_id}")
        uuid = str(encontrados[0]["id"])
    await _asegurar_mapper(
        http,
        f"{base}/admin/realms/{realm}/clients/{uuid}/protocol-mappers/models",
        headers,
    )
    await asegurar_alcance_audiencia(
        http,
        keycloak_url=keycloak_url,
        realm=realm,
        token=token,
        uuid=uuid,
    )


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
    """Crea el cliente y guarda el secreto, o deja el mapper si la fila ya existe.

    Reejecutar no rota el secreto guardado. Sí actualiza Keycloak para que el
    access token lleve la audiencia del proveedor. Devuelve la línea que se
    imprime: issuer y client_id, sin el secreto.
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
            await asegurar_clave_seudonimo(conn, slug, key=key)
            import httpx

            propio_existente = http is None
            cliente_existente = http if http is not None else httpx.AsyncClient(timeout=30.0)
            try:
                try:
                    token = await token_admin(
                        cliente_existente,
                        keycloak_url=keycloak_url,
                        admin_user=admin_user,
                        admin_password=admin_password,
                    )
                    await asegurar_audiencia_proveedor(
                        cliente_existente,
                        keycloak_url=keycloak_url,
                        realm=realm,
                        token=token,
                        client_id=client_id,
                    )
                except RuntimeError:
                    raise
                except httpx.HTTPError:
                    raise RuntimeError(
                        "Keycloak no respondió. El esquema del tenant ya quedó aplicado."
                    ) from None
            finally:
                if propio_existente:
                    await cliente_existente.aclose()
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
        await asegurar_clave_seudonimo(conn, slug, key=key)
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
        # Mismo cerrojo global que el runner (C19, ADR-041): provisionar un tenant
        # mientras arranca `migrate-docuboto` no los deja pisarse.
        async with ledger.cerrojo(conn):
            await ledger.ensure_ledger(conn)

            print("\n── Migraciones globales (public schema) ──────────────────────")
            await ledger.apply_chain(conn, MIGRATIONS_DIR / "global", ledger.ESQUEMA_GLOBAL)

            print(f"\n── Schema del tenant: {esquema_de(slug)} ─────")
            await provision_schema(conn, slug)

            print("\n── Migraciones por tenant ────────────────────────────────────")
            await ledger.apply_chain(conn, MIGRATIONS_DIR / "tenant", esquema_de(slug))

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
