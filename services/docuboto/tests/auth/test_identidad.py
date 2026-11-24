# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El emisor de identidad del proceso (ADR-048), sin Postgres.

Firma y verificación ES256, rotación con solapamiento, JWKS y las rutas OIDC. La política de contraseñas está en `test_passwords.py`. El login, el refresh y el bloqueo contra una base
real los prueba `integration/test_identidad_integration.py`.
"""

from __future__ import annotations

import base64
import time
import uuid

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from orpycamcp_common.database import get_conn as get_db_conn

from auth.core import passwords, security
from auth.services.auth_service import AuthError, AuthService, emitir_token_servicio
from docuboto.config import auth as settings
from tests.auth.app import app
from tests.auth.fakes_identidad import ConexionDeClaves, configurar, cuenta

pytestmark = pytest.mark.asyncio

AUDIENCIA_PROVEEDOR = "pro" "boto-api"


@pytest.fixture(autouse=True)
def _emisor(monkeypatch: pytest.MonkeyPatch) -> None:
    configurar(monkeypatch)


async def _token_de_persona(conn: ConexionDeClaves, **extra: object) -> str:
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": settings.identity_user_audience,
        "tenant_slug": "demo",
        "preferred_username": "OPERADOR",
        "principal_type": "user",
        **extra,
    }
    return await security.firmar(conn, claims, vida_s=settings.access_token_ttl_s)


# ── firma y verificación ─────────────────────────────────────────────────────


async def test_la_primera_firma_crea_la_clave_y_el_token_verifica() -> None:
    conn = ConexionDeClaves()
    token = await _token_de_persona(conn)
    assert len(conn.claves) == 1
    cabecera = jwt.get_unverified_header(token)
    assert cabecera["alg"] == "ES256"
    assert cabecera["kid"] == conn.claves[0]["kid"]
    claims = await security.verificar(conn, token, audience=settings.identity_user_audience)
    assert claims["iss"] == "https://api.test"
    assert claims["exp"] - claims["iat"] == settings.access_token_ttl_s
    assert claims["jti"]


async def test_la_privada_se_guarda_sellada() -> None:
    conn = ConexionDeClaves()
    await _token_de_persona(conn)
    assert b"PRIVATE KEY" not in conn.claves[0]["private_key_cifrado"]
    assert "d" not in conn.claves[0]["public_jwk"]  # la JWK pública no lleva la privada


async def test_otra_audiencia_no_verifica() -> None:
    conn = ConexionDeClaves()
    token = await _token_de_persona(conn)
    with pytest.raises(jwt.InvalidAudienceError):
        await security.verificar(conn, token, audience=AUDIENCIA_PROVEEDOR)


async def test_otro_emisor_no_verifica(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = ConexionDeClaves()
    token = await _token_de_persona(conn)
    monkeypatch.setattr(settings, "identity_issuer", "https://otro.test")
    with pytest.raises(jwt.InvalidIssuerError):
        await security.verificar(conn, token, audience=settings.identity_user_audience)


async def test_un_token_firmado_por_otra_clave_no_verifica() -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    conn = ConexionDeClaves()
    await _token_de_persona(conn)
    kid = conn.claves[0]["kid"]
    ajena = ec.generate_private_key(ec.SECP256R1())
    ahora = int(time.time())
    falso = jwt.encode(
        {"sub": "x", "aud": "docuboto", "iss": "https://api.test", "iat": ahora, "exp": ahora + 60},
        ajena,
        algorithm="ES256",
        headers={"kid": kid},
    )
    with pytest.raises(jwt.InvalidSignatureError):
        await security.verificar(conn, falso, audience="docuboto")


async def test_hs256_con_la_clave_publica_no_cuela() -> None:
    """El ataque clásico de confusión de algoritmo: solo se acepta ES256."""
    conn = ConexionDeClaves()
    await _token_de_persona(conn)
    kid = conn.claves[0]["kid"]
    falso = jwt.api_jws.PyJWS().encode(
        b'{"sub":"x","aud":"docuboto","iss":"https://api.test","iat":1,"exp":9999999999}',
        b"cualquier-cosa",
        algorithm="HS256",
        headers={"kid": kid},
    )
    with pytest.raises(jwt.InvalidAlgorithmError):
        await security.verificar(conn, falso, audience="docuboto")


async def test_sin_kek_no_se_firma(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "identity_key_encryption_key", "")
    with pytest.raises(RuntimeError, match="IDENTITY_KEY_ENCRYPTION_KEY"):
        await _token_de_persona(ConexionDeClaves())


# ── rotación y JWKS ──────────────────────────────────────────────────────────


async def test_rotar_sigue_aceptando_la_clave_anterior_durante_el_solapamiento() -> None:
    conn = ConexionDeClaves()
    viejo = await _token_de_persona(conn)
    kid_viejo = conn.claves[0]["kid"]
    kid_nuevo = await security.rotar(conn)
    assert kid_nuevo != kid_viejo
    nuevo = await _token_de_persona(conn)
    assert jwt.get_unverified_header(nuevo)["kid"] == kid_nuevo
    await security.verificar(conn, viejo, audience=settings.identity_user_audience)
    publicadas = {k["kid"] for k in (await security.jwks(conn))["keys"]}
    assert publicadas == {kid_viejo, kid_nuevo}


async def test_pasado_el_solapamiento_la_clave_retirada_sale_de_la_jwks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = ConexionDeClaves()
    viejo = await _token_de_persona(conn)
    kid_viejo = conn.claves[0]["kid"]
    await security.rotar(conn)
    monkeypatch.setattr(settings, "jwt_key_overlap_s", 0)
    security.olvidar()
    publicadas = {k["kid"] for k in (await security.jwks(conn))["keys"]}
    assert kid_viejo not in publicadas
    with pytest.raises(jwt.InvalidTokenError):
        await security.verificar(conn, viejo, audience=settings.identity_user_audience)


async def test_la_jwks_es_publica_y_es256() -> None:
    conn = ConexionDeClaves()
    await _token_de_persona(conn)
    (clave,) = (await security.jwks(conn))["keys"]
    assert clave["kty"] == "EC" and clave["crv"] == "P-256"
    assert clave["alg"] == "ES256" and clave["use"] == "sig"
    assert set(clave) >= {"kid", "x", "y"} and "d" not in clave


# ── validate: lo que el middleware acepta ────────────────────────────────────


async def test_validate_acepta_un_access_token_de_persona() -> None:
    conn = ConexionDeClaves()
    sub = str(uuid.uuid4())
    token = await _token_de_persona(conn, sub=sub)
    r = await AuthService().validate(token, conn)  # type: ignore[arg-type]
    assert r.valid and r.user_id == sub and r.tenant_slug == "demo"
    assert r.username == "OPERADOR"
    assert 0 < (r.expires_in or 0) <= settings.access_token_ttl_s


async def test_validate_rechaza_un_token_de_servicio() -> None:
    """Un token de cuenta de servicio no es credencial del API de Docuboto."""
    conn = ConexionDeClaves()
    servicio = await emitir_token_servicio(conn, cuenta())  # type: ignore[arg-type]
    assert not (await AuthService().validate(servicio.access_token, conn)).valid  # type: ignore[arg-type]
    # Ni aunque la cuenta declare la audiencia de Docuboto: falta principal_type=user.
    propia = await emitir_token_servicio(
        conn, cuenta(audience=settings.identity_user_audience)  # type: ignore[arg-type]
    )
    assert not (await AuthService().validate(propia.access_token, conn)).valid  # type: ignore[arg-type]


async def test_validate_rechaza_basura_sin_lanzar() -> None:
    r = await AuthService().validate("no-es-un-jwt", ConexionDeClaves())  # type: ignore[arg-type]
    assert not r.valid


# ── cuentas de servicio ──────────────────────────────────────────────────────


async def test_el_token_de_servicio_es_el_que_espera_el_proveedor() -> None:
    """`sub` = `client_id` y `aud` del proveedor: así lo reconoce como servicio."""
    conn = ConexionDeClaves()
    t = await emitir_token_servicio(conn, cuenta())  # type: ignore[arg-type]
    claims = await security.verificar(conn, t.access_token, audience=AUDIENCIA_PROVEEDOR)
    assert claims["sub"] == claims["client_id"] == claims["azp"] == "intelligence-demo"
    assert claims["principal_type"] == "service"
    assert claims["tenant_slug"] == "demo"
    assert t.refresh_token is None
    assert t.expires_in == settings.service_token_ttl_s


async def test_client_credentials_exige_el_secreto(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = ConexionDeClaves([cuenta("cli-1", secret_hash=passwords.cifrar("s3creto-largo"))])

    async def _operativa(*_a: object) -> None:
        return None

    monkeypatch.setattr("auth.services.auth_service.veredicto_entidad", _operativa)
    ok = await AuthService().client_credentials(conn, "cli-1", "s3creto-largo")  # type: ignore[arg-type]
    assert ok.access_token
    with pytest.raises(AuthError) as e:
        await AuthService().client_credentials(conn, "cli-1", "otro")  # type: ignore[arg-type]
    assert e.value.code == "invalid_client"
    with pytest.raises(AuthError):
        await AuthService().client_credentials(conn, "no-existe", "x")  # type: ignore[arg-type]


async def test_una_cuenta_sin_secreto_no_sirve_en_oauth_token() -> None:
    """La de inteligencia no tiene secreto: solo la usa el propio proceso."""
    conn = ConexionDeClaves([cuenta()])
    with pytest.raises(AuthError) as e:
        await AuthService().client_credentials(conn, "intelligence-demo", "")  # type: ignore[arg-type]
    assert e.value.code == "invalid_client"


async def test_una_cuenta_inactiva_no_obtiene_token() -> None:
    conn = ConexionDeClaves(
        [cuenta("cli-2", secret_hash=passwords.cifrar("s3creto-largo"), is_active=False)]
    )
    with pytest.raises(AuthError):
        await AuthService().client_credentials(conn, "cli-2", "s3creto-largo")  # type: ignore[arg-type]


# ── rutas OIDC ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def oidc(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    conn = ConexionDeClaves([cuenta("cli-3", secret_hash=passwords.cifrar("s3creto-largo"))])

    async def _dep():  # noqa: ANN202
        yield conn

    async def _operativa(*_a: object) -> None:
        return None

    async def _sin_traza(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("auth.services.auth_service.veredicto_entidad", _operativa)
    monkeypatch.setattr("auth.routers.oidc.AuditRepository.log", _sin_traza)
    app.dependency_overrides[get_db_conn] = _dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.pop(get_db_conn, None)


async def test_descubrimiento_apunta_a_este_emisor(oidc) -> None:  # noqa: ANN001
    ac, _ = oidc
    r = await ac.get("/.well-known/openid-configuration")
    assert r.status_code == 200
    doc = r.json()
    assert doc["issuer"] == "https://api.test"
    assert doc["jwks_uri"] == "https://api.test/.well-known/jwks.json"
    assert doc["token_endpoint"] == "https://api.test/oauth/token"
    assert doc["grant_types_supported"] == ["client_credentials"]


async def test_jwks_por_http(oidc) -> None:  # noqa: ANN001
    ac, conn = oidc
    await _token_de_persona(conn)
    r = await ac.get("/.well-known/jwks.json")
    assert r.status_code == 200
    assert [k["kid"] for k in r.json()["keys"]] == [conn.claves[0]["kid"]]


async def test_oauth_token_con_basic_y_con_formulario(oidc) -> None:  # noqa: ANN001
    ac, conn = oidc
    basic = base64.b64encode(b"cli-3:s3creto-largo").decode()
    r = await ac.post(
        "/oauth/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    cuerpo = r.json()
    assert cuerpo["token_type"] == "Bearer"
    claims = await security.verificar(conn, cuerpo["access_token"], audience=AUDIENCIA_PROVEEDOR)
    assert claims["sub"] == "cli-3"

    r = await ac.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "cli-3",
            "client_secret": "s3creto-largo",
        },
    )
    assert r.status_code == 200, r.text


async def test_oauth_token_errores_con_forma_oauth(oidc) -> None:  # noqa: ANN001
    ac, _ = oidc
    r = await ac.post("/oauth/token", data={"grant_type": "password"})
    assert r.status_code == 400 and r.json() == {"error": "unsupported_grant_type"}
    r = await ac.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "cli-3", "client_secret": "mal"},
    )
    assert r.status_code == 401 and r.json() == {"error": "invalid_client"}
