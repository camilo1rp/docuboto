# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Docuboto firma y verifica sus propios JWT (ADR-048).

Las claves son pares ES256 en `public.auth_jwt_keys`, con la privada sellada
con AES-GCM. Se firma con la más reciente sin `retired_at`; la JWKS publica
también las retiradas hace menos de `jwt_key_overlap_s`, de modo que rotar no
corta las sesiones ni el token de servicio del proveedor.

ES256 y no Ed25519: el proveedor PPI acepta RS256 y ES256, y la decisión del ADR
dejaba abierta esa alternativa.

Las claves públicas y la de firma se guardan en memoria `_TTL_S` segundos; un
`kid` desconocido fuerza una relectura, como mucho cada `_RELECTURA_MIN_S`.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from typing import Any

import asyncpg
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import InvalidTokenError
from jwt.algorithms import ECAlgorithm
from orpycamcp_common.kek import seal, unseal

from docuboto.config import auth as settings

ALG = "ES256"
_TTL_S = 60.0
_RELECTURA_MIN_S = 5.0
#: Cerrojo de la generación de la primera clave (dos peticiones a la vez no
#: deben crear dos claves activas).
_CERROJO = 0x4F52505943414A57

_VIGENTES = """
SELECT kid, public_jwk, private_key_cifrado, private_key_nonce, retired_at
  FROM public.auth_jwt_keys
 WHERE retired_at IS NULL OR retired_at > now() - make_interval(secs => $1)
 ORDER BY created_at DESC
"""


class _Llavero:
    def __init__(self) -> None:
        self.publicas: dict[str, Any] = {}
        self.jwks: list[dict[str, Any]] = []
        self.firma: tuple[str, ec.EllipticCurvePrivateKey] | None = None
        self.leido: float = 0.0
        self.cerrojo = asyncio.Lock()


_llavero = _Llavero()


def olvidar() -> None:
    """Tira lo leído: la próxima operación relee la tabla. Para pruebas y rotación."""
    global _llavero
    _llavero = _Llavero()


def _aad(kid: str) -> bytes:
    return f"jwt-key:{kid}".encode()


def _jwk_publica(kid: str, privada: ec.EllipticCurvePrivateKey) -> dict[str, Any]:
    jwk = json.loads(ECAlgorithm.to_jwk(privada.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": ALG})
    return jwk


async def generar_clave(conn: asyncpg.Connection) -> str:
    """Crea un par nuevo; pasa a ser la clave de firma. Devuelve el `kid`."""
    kid = uuid.uuid4().hex
    privada = ec.generate_private_key(ec.SECP256R1())
    pem = privada.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cifrado, nonce = seal(pem, key=settings.identity_key_bytes, aad=_aad(kid))
    await conn.execute(
        "INSERT INTO public.auth_jwt_keys (kid, private_key_cifrado, private_key_nonce, public_jwk)"
        " VALUES ($1, $2, $3, $4::jsonb)",
        kid,
        cifrado,
        nonce,
        json.dumps(_jwk_publica(kid, privada)),
    )
    return kid


async def rotar(conn: asyncpg.Connection) -> str:
    """Retira la clave de firma vigente y crea otra. La retirada sigue en la JWKS."""
    async with conn.transaction():
        await conn.execute(
            "UPDATE public.auth_jwt_keys SET retired_at = now() WHERE retired_at IS NULL"
        )
        kid = await generar_clave(conn)
    olvidar()
    return kid


async def _leer(conn: asyncpg.Connection) -> None:
    filas = await conn.fetch(_VIGENTES, float(settings.jwt_key_overlap_s))
    if not any(f["retired_at"] is None for f in filas):
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _CERROJO)
            hay = await conn.fetchval(
                "SELECT 1 FROM public.auth_jwt_keys WHERE retired_at IS NULL LIMIT 1"
            )
            if not hay:
                await generar_clave(conn)
        filas = await conn.fetch(_VIGENTES, float(settings.jwt_key_overlap_s))
    publicas: dict[str, Any] = {}
    jwks: list[dict[str, Any]] = []
    firma: tuple[str, ec.EllipticCurvePrivateKey] | None = None
    for f in filas:
        jwk = f["public_jwk"]
        if isinstance(jwk, str):
            jwk = json.loads(jwk)
        publicas[f["kid"]] = ECAlgorithm.from_jwk(json.dumps(jwk))
        jwks.append(dict(jwk))
        if firma is None and f["retired_at"] is None:
            pem = unseal(
                bytes(f["private_key_cifrado"]),
                nonce=bytes(f["private_key_nonce"]),
                key=settings.identity_key_bytes,
                aad=_aad(f["kid"]),
            )
            privada = serialization.load_pem_private_key(pem, password=None)
            assert isinstance(privada, ec.EllipticCurvePrivateKey)
            firma = (f["kid"], privada)
    _llavero.publicas = publicas
    _llavero.jwks = jwks
    _llavero.firma = firma
    _llavero.leido = time.monotonic()


async def _al_dia(conn: asyncpg.Connection, *, kid: str | None = None) -> None:
    ahora = time.monotonic()
    vencido = ahora - _llavero.leido > _TTL_S
    falta = kid is not None and kid not in _llavero.publicas
    if not vencido and not (falta and ahora - _llavero.leido > _RELECTURA_MIN_S):
        return
    async with _llavero.cerrojo:
        ahora = time.monotonic()
        vencido = ahora - _llavero.leido > _TTL_S
        falta = kid is not None and kid not in _llavero.publicas
        if vencido or (falta and ahora - _llavero.leido > _RELECTURA_MIN_S):
            await _leer(conn)


async def jwks(conn: asyncpg.Connection) -> dict[str, Any]:
    """El documento JWKS: la clave de firma y las retiradas aún en solapamiento."""
    await _al_dia(conn)
    return {"keys": list(_llavero.jwks)}


async def firmar(conn: asyncpg.Connection, claims: dict[str, Any], *, vida_s: int) -> str:
    """Firma `claims` añadiendo `iss`, `iat`, `exp` y `jti`."""
    await _al_dia(conn)
    if _llavero.firma is None:
        raise RuntimeError("no hay clave de firma de tokens")
    kid, privada = _llavero.firma
    ahora = int(time.time())
    cuerpo = {
        **claims,
        "iss": settings.identity_issuer,
        "iat": ahora,
        "exp": ahora + vida_s,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(cuerpo, privada, algorithm=ALG, headers={"kid": kid})


async def verificar(conn: asyncpg.Connection, token: str, *, audience: str) -> dict[str, Any]:
    """Claims de un token firmado aquí para `audience`. Lanza `InvalidTokenError`."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("cabecera ilegible") from exc
    if not isinstance(kid, str) or not kid:
        raise InvalidTokenError("sin kid")
    await _al_dia(conn, kid=kid)
    clave = _llavero.publicas.get(kid)
    if clave is None:
        raise InvalidTokenError(f"kid desconocido: {kid!r}")
    return jwt.decode(
        token,
        clave,
        algorithms=[ALG],
        issuer=settings.identity_issuer,
        audience=audience,
        options={"require": ["exp", "iat", "iss", "sub", "aud"]},
    )
