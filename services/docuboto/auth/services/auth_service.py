# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El proveedor de identidad del proceso (ADR-048).

Personas: contraseña argon2id contra `auth_users`, access JWT de 15 min y un
refresh opaco de 12 h (30 min de inactividad) que rota en cada uso; presentar
uno ya usado revoca toda la familia. Máquinas: `client_credentials` contra `public.auth_service_accounts`.

La entidad se comprueba al emitir (login y refresh): una entidad sin registro o
sin schema no emite tokens. El `sub` del access token es `auth_users.id`, así
que validar no toca la base salvo para leer las claves públicas (en caché).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from jwt import PyJWTError
from orpycamcp_common.tenant import slug_valido, tenant_schema, veredicto_entidad

from auth.core import passwords, security
from auth.repositories.identity_repository import IdentityRepository
from auth.schemas.auth import TokenResponse, ValidateResponse
from docuboto.config import auth as settings

logger = logging.getLogger(__name__)

PRINCIPAL_USER = "user"
PRINCIPAL_SERVICE = "service"


class AuthError(Exception):
    """Respuesta de negocio del emisor: `status_code` y `code` van al cuerpo."""

    def __init__(self, status_code: int, code: str, detail: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail or code
        super().__init__(code)


def _hash_refresh(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _slug_del_refresh(token: str) -> str:
    slug, _, resto = token.partition(".")
    if not resto or not slug_valido(slug):
        raise AuthError(401, "invalid_refresh_token")
    return slug


@asynccontextmanager
async def _en_entidad(conn: asyncpg.Connection, slug: str) -> AsyncIterator[asyncpg.Connection]:
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    try:
        yield conn
    finally:
        await conn.execute("SET search_path = public")


async def _exigir_entidad(conn: asyncpg.Connection, slug: str) -> None:
    veto = await veredicto_entidad(conn, slug)
    if veto is not None:
        raise AuthError(veto[0], veto[1], veto[2])


async def emitir_token_servicio(conn: asyncpg.Connection, cuenta: Any) -> TokenResponse:
    """Token de una cuenta de servicio. `sub` = `client_id`: así lo reconoce el proveedor."""
    client_id = str(cuenta["client_id"])
    claims = {
        "sub": client_id,
        "client_id": client_id,
        "azp": client_id,
        "aud": str(cuenta["audience"]),
        "tenant_slug": str(cuenta["tenant_slug"]),
        "scope": " ".join(cuenta["scopes"] or []),
        "principal_type": PRINCIPAL_SERVICE,
    }
    token = await security.firmar(conn, claims, vida_s=settings.service_token_ttl_s)
    return TokenResponse(
        access_token=token, refresh_token=None, expires_in=settings.service_token_ttl_s
    )


class AuthService:
    # ------------------------------------------------------------------
    # Personas
    # ------------------------------------------------------------------

    async def _emitir(
        self,
        conn: asyncpg.Connection,
        user: Any,
        slug: str,
        *,
        family_id: uuid.UUID,
        expires_at: datetime,
    ) -> TokenResponse:
        claims = {
            "sub": str(user["id"]),
            "aud": settings.identity_user_audience,
            "tenant_slug": slug,
            "preferred_username": user["username"],
            "email": user["email"] or "",
            "principal_type": PRINCIPAL_USER,
        }
        access = await security.firmar(conn, claims, vida_s=settings.access_token_ttl_s)
        refresh = f"{slug}.{secrets.token_urlsafe(32)}"
        await IdentityRepository(conn).insert_refresh(
            user_id=user["id"],
            family_id=family_id,
            token_hash=_hash_refresh(refresh),
            expires_at=expires_at,
        )
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.access_token_ttl_s,
        )

    async def login(
        self, conn: asyncpg.Connection, username: str, password: str, tenant_slug: str
    ) -> TokenResponse:
        if not slug_valido(tenant_slug):
            await passwords.coincide_async(None, password)
            raise AuthError(401, "invalid_credentials")
        await _exigir_entidad(conn, tenant_slug)
        async with _en_entidad(conn, tenant_slug):
            repo = IdentityRepository(conn)
            user = await repo.user_by_username(username)
            if user is None or not user["is_active"]:
                await passwords.coincide_async(None, password)
                raise AuthError(401, "invalid_credentials")
            if user["locked_until"] is not None and user["locked_until"] > datetime.now(UTC):
                raise AuthError(423, "account_locked", "La cuenta está bloqueada temporalmente.")
            if not await passwords.coincide_async(user["password_hash"], password):
                await repo.register_failure(
                    user["id"],
                    max_failed=settings.login_max_failed,
                    lockout_s=settings.login_lockout_seconds,
                )
                raise AuthError(401, "invalid_credentials")
            async with conn.transaction():
                await repo.register_success(user["id"])
                return await self._emitir(
                    conn,
                    user,
                    tenant_slug,
                    family_id=uuid.uuid4(),
                    expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_s),
                )

    async def refresh(self, conn: asyncpg.Connection, refresh_token: str) -> TokenResponse:
        slug = _slug_del_refresh(refresh_token)
        await _exigir_entidad(conn, slug)
        rechazo: AuthError | None = None
        async with _en_entidad(conn, slug):
            repo = IdentityRepository(conn)
            async with conn.transaction():
                fila = await repo.lock_refresh(
                    _hash_refresh(refresh_token), idle_s=settings.refresh_token_idle_s
                )
                if fila is None:
                    raise AuthError(401, "invalid_refresh_token")
                user = await repo.user_by_id(fila["user_id"])
                if fila["used_at"] is not None or fila["revoked_at"] is not None:
                    # Reuso: alguien más tiene la cadena. Se corta la familia entera.
                    await repo.revoke_family(fila["family_id"])
                    rechazo = AuthError(401, "refresh_token_reused")
                elif fila["vencido"]:
                    rechazo = AuthError(401, "invalid_refresh_token")
                elif user is None or not user["is_active"]:
                    await repo.revoke_family(fila["family_id"])
                    rechazo = AuthError(401, "invalid_refresh_token")
                else:
                    await repo.mark_refresh_used(fila["id"])
                    return await self._emitir(
                        conn,
                        user,
                        slug,
                        family_id=fila["family_id"],
                        expires_at=fila["expires_at"],
                    )
        assert rechazo is not None
        raise rechazo

    async def logout(self, conn: asyncpg.Connection, refresh_token: str) -> None:
        """Revoca la familia del refresh. Idempotente: un token desconocido no es error."""
        try:
            slug = _slug_del_refresh(refresh_token)
        except AuthError:
            return
        try:
            async with _en_entidad(conn, slug):
                await IdentityRepository(conn).revoke_family_of(_hash_refresh(refresh_token))
        except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError):
            return

    async def change_password(
        self, conn: asyncpg.Connection, user_id: uuid.UUID, current: str, new: str
    ) -> None:
        """La persona cambia su contraseña. `conn` ya está en el schema de su entidad."""
        repo = IdentityRepository(conn)
        user = await repo.user_by_id(user_id)
        if user is None or not await passwords.coincide_async(user["password_hash"], current):
            raise AuthError(401, "invalid_credentials")
        await self.set_password(conn, user_id, new)

    async def set_password(self, conn: asyncpg.Connection, user_id: uuid.UUID, new: str) -> None:
        """Fija la contraseña tras la política y cierra las sesiones abiertas."""
        repo = IdentityRepository(conn)
        anteriores = await repo.recent_password_hashes(user_id, settings.password_history)
        try:
            passwords.exigir_politica(new, anteriores=anteriores)
        except passwords.PoliticaIncumplida as exc:
            raise AuthError(422, exc.codigo, exc.detalle) from exc
        cifrada = passwords.cifrar(new)
        async with conn.transaction():
            await repo.set_password(user_id, cifrada)
            await repo.revoke_user_sessions(user_id)

    async def validate(self, token: str, conn: asyncpg.Connection) -> ValidateResponse:
        """Verifica un access token de persona. Solo lee las claves públicas."""
        try:
            claims = await security.verificar(
                conn, token, audience=settings.identity_user_audience
            )
        except PyJWTError:
            return ValidateResponse(valid=False)
        if claims.get("principal_type") != PRINCIPAL_USER:
            return ValidateResponse(valid=False)
        return ValidateResponse(
            valid=True,
            user_id=str(claims["sub"]),
            username=str(claims.get("preferred_username") or ""),
            tenant_slug=claims.get("tenant_slug") or None,
            expires_in=max(0, int(claims["exp"]) - int(time.time())),
        )

    # ------------------------------------------------------------------
    # Máquinas
    # ------------------------------------------------------------------

    async def client_credentials(
        self, conn: asyncpg.Connection, client_id: str, client_secret: str
    ) -> TokenResponse:
        cuenta = await IdentityRepository(conn).service_account(client_id)
        guardado = cuenta["secret_hash"] if cuenta is not None else None
        if not await passwords.coincide_async(guardado, client_secret) or not (
            cuenta and cuenta["is_active"]
        ):
            raise AuthError(401, "invalid_client")
        await _exigir_entidad(conn, str(cuenta["tenant_slug"]))
        return await emitir_token_servicio(conn, cuenta)
