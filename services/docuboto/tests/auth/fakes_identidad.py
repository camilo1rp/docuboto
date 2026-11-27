# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un doble de conexión para `public.auth_jwt_keys` y `auth_service_accounts`.

Guarda las filas en memoria y entiende solo el SQL de `auth.core.security` y de
las cuentas de servicio: basta para firmar, verificar y rotar sin Postgres.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from auth.core import security
from docuboto.config import auth as settings

KEK = base64.b64encode(b"\x42" * 32).decode()


def configurar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "identity_key_encryption_key", KEK)
    monkeypatch.setattr(settings, "identity_issuer", "https://api.test")
    security.olvidar()


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> bool:
        return False


class ConexionDeClaves:
    def __init__(self, cuentas: list[dict[str, Any]] | None = None) -> None:
        self.claves: list[dict[str, Any]] = []
        self.cuentas = cuentas or []

    def transaction(self) -> _Tx:
        return _Tx()

    async def execute(self, sql: str, *args: Any) -> str:
        if sql.startswith("INSERT INTO public.auth_jwt_keys"):
            kid, cifrado, nonce, jwk = args
            self.claves.append(
                {
                    "kid": kid,
                    "private_key_cifrado": cifrado,
                    "private_key_nonce": nonce,
                    "public_jwk": json.loads(jwk),
                    "created_at": datetime.now(UTC) + timedelta(microseconds=len(self.claves)),
                    "retired_at": None,
                }
            )
        elif sql.startswith("UPDATE public.auth_jwt_keys SET retired_at"):
            for c in self.claves:
                if c["retired_at"] is None:
                    c["retired_at"] = datetime.now(UTC)
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        assert "FROM public.auth_jwt_keys" in sql
        limite = datetime.now(UTC) - timedelta(seconds=float(args[0]))
        filas = [c for c in self.claves if c["retired_at"] is None or c["retired_at"] > limite]
        return sorted(filas, key=lambda c: c["created_at"], reverse=True)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "FROM public.auth_jwt_keys WHERE retired_at IS NULL" in sql:
            return 1 if any(c["retired_at"] is None for c in self.claves) else None
        return None

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM public.auth_service_accounts WHERE client_id = $1" in sql:
            return next((c for c in self.cuentas if c["client_id"] == args[0]), None)
        if "FROM public.auth_service_accounts" in sql:
            return next(
                (
                    c
                    for c in self.cuentas
                    if c["tenant_slug"] == args[0] and c["audience"] == args[1] and c["is_active"]
                ),
                None,
            )
        raise AssertionError(f"fetchrow no previsto: {sql}")


def cuenta(
    client_id: str = "intelligence-demo",
    *,
    secret_hash: str | None = None,
    audience: str = "pro" "boto-api",
    is_active: bool = True,
) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "secret_hash": secret_hash,
        "tenant_slug": "demo",
        "audience": audience,
        "scopes": ["ppi"],
        "is_active": is_active,
    }


@contextmanager
def como(info: Any) -> Iterator[None]:
    """La petición llega con esta persona ya verificada (lo que deja el middleware).

    `info` es un `UserInfoResponse`; un `user_id` que no es UUID se convierte en
    uno estable, como el `sub` de cualquier token de este emisor.
    """
    from orpycamcp_common.usuario import Usuario, usuario_actual

    from tests.auth.app import app

    try:
        uid = uuid.UUID(str(info.user_id))
    except ValueError:
        uid = uuid.uuid5(uuid.NAMESPACE_URL, str(info.user_id))
    persona = Usuario(id=uid, tenant_slug=info.tenant_slug or "", username=info.username)
    app.dependency_overrides[usuario_actual] = lambda: persona
    try:
        yield
    finally:
        app.dependency_overrides.pop(usuario_actual, None)
