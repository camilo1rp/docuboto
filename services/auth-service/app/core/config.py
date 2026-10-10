# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from orpycamcp_common import kek as _kek
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _decode_aes256(b64: str, label: str) -> bytes:
    """Decodifica un base64 a exactamente 32 bytes (AES-256) o lanza
    ``RuntimeError`` explícito — criterio único compartido por todas las claves
    AES del servicio. Vive en ``orpycamcp_common.kek`` (mismo esquema que usa
    notification-service para el secreto de los webhooks)."""
    return _kek.decode_aes256(b64, label)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database — asyncpg native DSN (no +asyncpg driver prefix)
    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"

    # ── Presupuesto de conexiones a Postgres ──────────────────────────────
    # Configurables porque el reparto NO es una constante del código: depende
    # de cuántos servicios y réplicas haya en el despliegue. La aritmética
    # completa está escrita en `infra/docker-compose.prod.yml`, junto al
    # `max_connections` del servidor, que es donde tiene sentido cuadrarla.
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    # Tope por CONSULTA. Sin él, una consulta atascada retiene su conexión
    # indefinidamente y el pool se agota sin que nada lo delate. 120 s es muy
    # superior a cualquier consulta sana de este sistema; lo que legítimamente
    # tarde más debe pasar su propio `timeout=` en la llamada (es lo que hace
    # `orpycamcp_common.audit.verify_chain`, que recorre el registro entero).
    db_command_timeout: float = 120.0

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Keycloak — used for JWKS validation and admin operations
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"
    keycloak_client_id: str = "auth-service"
    keycloak_client_secret: str = "change-me-in-production"

    # Keycloak client that issues tokens via Resource Owner Password Credentials.
    # Must have directAccessGrantsEnabled=true in realm config.
    keycloak_gateway_client_id: str = "api-gateway"
    keycloak_gateway_client_secret: str = "change-me-in-production"

    # Issuer (claim `iss`) esperado en los tokens. Por defecto se deriva de
    # `keycloak_realm_url`, pero debe poder sobreescribirse: en despliegues con
    # Docker, los tokens se emiten con la URL pública de Keycloak (p. ej.
    # http://localhost:19180/realms/orpycamcp) mientras el servicio resuelve
    # Keycloak por la URL interna (http://keycloak:8080). Si difieren, fija aquí
    # el issuer público para que la validación del `iss` no rechace tokens válidos.
    keycloak_issuer: str | None = None

    # Clientes del realm (claim `azp`) cuyos tokens se aceptan como credencial de
    # API, separados por comas. Lista VACÍA = no se acepta ninguno (fail-closed).
    # Añade aquí cualquier cliente real nuevo del realm (p. ej. un cliente de
    # servicio para integraciones) o sus tokens darán 401.
    allowed_azp: str = "orpycamcp-frontend,api-gateway"

    @property
    def allowed_azp_set(self) -> frozenset[str]:
        return frozenset(c.strip() for c in self.allowed_azp.split(",") if c.strip())

    # JWKS public key cache TTL (seconds)
    jwks_cache_ttl: int = 600

    # Service
    service_port: int = 8001
    log_level: str = "info"

    # --- TOTP (RFC 6238, E06) — 2FA para la firma personal, junto a auth_users ---
    # Clave AES-256 (32 bytes, base64) para cifrar en reposo el secreto TOTP
    # (app/core/totp.py, AESGCM). Vacía en dev por defecto: enroll falla
    # explícitamente (ver totp_encryption_key_bytes) hasta configurarla — el
    # secreto TOTP NUNCA se cifra con un valor implícito/derivado. Generar con
    # p. ej. `python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`.
    totp_encryption_key: str = ""
    # Label ``issuer`` embebido en el otpauth:// URI (apps autenticadoras lo
    # muestran junto al nombre de cuenta).
    totp_issuer: str = "OrpycaMCP"
    # Máximo de verificaciones fallidas (activate + /internal/totp/verify
    # comparten el mismo contador de la fila) antes de bloquear con locked_until.
    totp_max_failed_attempts: int = 5
    # Duración del bloqueo tras agotar totp_max_failed_attempts.
    totp_lockout_seconds: int = 900
    # Ventana de tolerancia de reloj (RFC 6238): acepta timesteps en
    # [T-skew, T+skew]. 1 = ±30s con periodo=30s.
    totp_skew_steps: int = 1
    # (Sin `totp_requerido_por_nivel`: era una clave reservada que nadie leía. El
    # forzado de TOTP por nivel vive en signature-service, que consulta
    # `GET /internal/totp/status` y rechaza el OTP-correo cuando el actor ya
    # tiene TOTP activo en un acto de nivel >= 2 — AREA-04-02.)

    # --- Firma electrónica PERSONAL PKI por-usuario (E17/F4, Incremento 1) ---
    # Clave AES-256 (32 bytes, base64) para cifrar en reposo la clave privada
    # de firma personal (app/core/signing_keys.py, AESGCM) — SEPARADA de
    # totp_encryption_key (secreto distinto, propósito distinto) y del
    # signature_secret HMAC de signature-service (servicio distinto). Vacía en
    # dev por defecto: enroll/sign-digest fallan explícitamente (fail-closed,
    # ver signing_key_encryption_key_bytes) hasta configurarla.
    signing_key_encryption_key: str = ""
    # --- Rotación de la KEK de custodia (E17/F4) --- Llavero de KEKs
    # ADICIONALES (versiones >= 2). `signing_key_encryption_key` (arriba) es
    # SIEMPRE la versión 1 (congelada, retrocompat byte). Formato: JSON en env
    # con {version: base64-32B}, p. ej. `{"2": "<base64>"}`. NUNCA redefinir la
    # versión 1 aquí (reservada al campo legacy). Cada valor se valida a 32
    # bytes AES-256 fail-closed (ver signing_key_keyring).
    signing_key_encryption_keys: dict[int, str] = {}
    # Versión de KEK que cifra las ESCRITURAS nuevas (enroll) y a la que el
    # re-wrap converge. Default 1 = comportamiento actual exacto. Para rotar:
    # añadir la KEK v2 al llavero, fijar esto a 2, y correr el job de re-wrap.
    signing_key_active_version: int = 1
    # Ventana de retención (días) tras la cual el job de ops purga el MATERIAL
    # privado de una credencial REVOCADA (E17/F4). Solo se anula la privada
    # (irreversible); lo público queda para verificación. Colchón operacional
    # contra revocaciones masivas erróneas, NO reversibilidad criptográfica
    # (imposible por diseño: re-enroll genera par nuevo).
    signing_key_purge_after_days: int = Field(default=30, ge=1)
    # Token compartido D-02 para POST /internal/signing-key/sign-digest —
    # SOLO signature-service lo llama, servicio-a-servicio, NUNCA vía gateway
    # (misma familia de gate que app.core.internal_auth en otros servicios).
    # Vacío (default) hace que la ruta responda 503 fail-closed en vez de
    # aceptar cualquier token. NUNCA commitear el valor real.
    signing_key_internal_token: str = ""
    # Job periódico de mantenimiento de claves de firma (purga de revocadas
    # vencidas + re-wrap a la KEK activa). Kill-switch e intervalo; el job es
    # fail-safe: sin auditoría disponible no muta, sin llavero válido no re-envuelve.
    signing_key_maintenance_enabled: bool = True
    signing_key_maintenance_interval_seconds: int = Field(default=21600, ge=60)
    # --- Anclaje de la sub-CA de usuarios (E17/F4, Incremento 2 — cierra la
    # Baja #4) --- Ruta al certificado PÚBLICO de la sub-CA que emite los
    # certificados de firma personal (``scripts/gen-user-cert.sh`` en
    # producción). Montada READ-ONLY junto al contenedor de auth-service —
    # JAMÁS la ``ca.key`` de ninguna CA (ni la de usuarios ni ninguna otra):
    # este servicio solo necesita el cert público para verificar
    # ``leaf.verify_directly_issued_by(sub_ca)`` en ``activar_certificado``,
    # nunca para firmar/emitir. Vacía por defecto: la activación de un
    # certificado falla explícitamente (503) hasta que se configure — ver
    # ``app.core.signing_keys.load_user_ca_certificate``.
    signing_key_ca_users_cert_path: str = ""

    def signing_key_encryption_key_bytes(self) -> bytes:
        """Decodifica ``signing_key_encryption_key`` (base64) a 32 bytes
        crudos (AES-256) — la KEK **versión 1**. Lanza ``RuntimeError``
        explícito si falta o no mide 32 bytes — nunca cae a una clave derivada
        o implícita (RF-SEG), mismo criterio que :meth:`totp_encryption_key_bytes`."""
        if not self.signing_key_encryption_key:
            raise RuntimeError(
                "signing_key_encryption_key no configurada — requerida para /me/signing-key/*"
            )
        return _decode_aes256(self.signing_key_encryption_key, "signing_key_encryption_key")

    # --- Llavero de KEKs para la rotación de custodia (E17/F4) ---
    # La rotación NO cambia el AAD (tenant+user); solo re-envuelve la privada
    # bajo una KEK nueva. Fail-closed en cada método: nunca se firma ni se
    # arranca un re-wrap con una versión de KEK que no resuelva a 32 bytes.
    def signing_key_keyring(self) -> dict[int, bytes]:
        """Llavero completo {version: 32B}. La v1 sale del campo legacy; las
        v>=2 del JSON ``signing_key_encryption_keys``. Fail-closed por entrada;
        la clave 1 en el JSON está prohibida (reservada al campo legacy)."""
        self.signing_key_encryption_key_bytes()  # v1 ausente => su propio error
        return _kek.build_keyring(
            self.signing_key_encryption_key,
            self.signing_key_encryption_keys,
            v1_label="signing_key_encryption_key",
            extra_label="signing_key_encryption_keys",
        )

    def signing_key_for_version(self, version: int) -> bytes:
        """KEK de una versión concreta (la de la fila al descifrar). Fail-closed
        si esa versión no está cargada — NUNCA se firma con otra KEK."""
        ring = self.signing_key_keyring()
        if version not in ring:
            raise RuntimeError(
                f"KEK versión {version} no cargada (fail-closed) — no se puede "
                "descifrar/firmar esta credencial"
            )
        return ring[version]

    def signing_key_active_key(self) -> tuple[int, bytes]:
        """(versión_activa, KEK_activa) para cifrar escrituras nuevas. Lanza si
        la versión activa no tiene KEK cargada."""
        v = self.signing_key_active_version
        return v, self.signing_key_for_version(v)

    def totp_encryption_key_bytes(self) -> bytes:
        """Decodifica ``totp_encryption_key`` (base64) a 32 bytes crudos (AES-256).

        Lanza ``RuntimeError`` explícito si falta o no mide 32 bytes — nunca cae
        a una clave derivada o implícita (RF-SEG). El caller (TotpService) deja
        que esto se traduzca en un 500 hasta que el operador configure la clave.
        """
        import base64
        import binascii

        if not self.totp_encryption_key:
            raise RuntimeError(
                "totp_encryption_key no configurada — requerida para /me/totp/*"
            )
        try:
            key = base64.b64decode(self.totp_encryption_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("totp_encryption_key no es base64 válido") from exc
        if len(key) != 32:
            raise RuntimeError("totp_encryption_key debe decodificar a 32 bytes (AES-256)")
        return key

    @property
    def keycloak_realm_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}"

    @property
    def expected_issuer(self) -> str:
        """Issuer (`iss`) que debe traer todo token. Override vía KEYCLOAK_ISSUER."""
        return self.keycloak_issuer or self.keycloak_realm_url

    def issuer_config_warnings(self) -> list[str]:
        """Fail-fast visible del contrato de issuer (complementa la nota de
        infra/.env.example). Devuelve avisos a emitir en el arranque; lista vacía
        significa configuración coherente.

        Caso roto clásico: ``KEYCLOAK_URL`` apunta a un host interno de Docker
        (p. ej. ``keycloak``) y no se definió ``KEYCLOAK_ISSUER``. Entonces
        ``expected_issuer`` queda con la URL interna, que NUNCA coincide con el
        ``iss`` público de los tokens del navegador → el servicio rechaza todos
        los tokens (fail-closed) sin pista visible. Aquí lo hacemos explícito.

        No dispara en dev (allí ``KEYCLOAK_ISSUER`` sí está definido) ni en prod
        single-host (el host es un FQDN con puntos, no un nombre de servicio).
        """
        from urllib.parse import urlparse

        warnings: list[str] = []
        host = urlparse(self.keycloak_url).hostname or ""
        looks_internal = host not in ("localhost", "127.0.0.1") and "." not in host
        if self.keycloak_issuer is None and looks_internal:
            warnings.append(
                f"KEYCLOAK_ISSUER sin definir y KEYCLOAK_URL apunta a un host interno "
                f"('{host}'): el `iss` esperado sera '{self.expected_issuer}', que no "
                f"coincidira con el issuer publico de los tokens del navegador y se "
                f"rechazaran TODOS los tokens (fail-closed). Define KEYCLOAK_ISSUER con "
                f"la URL publica del realm."
            )
        return warnings

    @property
    def keycloak_token_url(self) -> str:
        return f"{self.keycloak_realm_url}/protocol/openid-connect/token"

    @property
    def keycloak_logout_url(self) -> str:
        return f"{self.keycloak_realm_url}/protocol/openid-connect/logout"

    @property
    def keycloak_jwks_url(self) -> str:
        return f"{self.keycloak_realm_url}/protocol/openid-connect/certs"

    @property
    def keycloak_userinfo_url(self) -> str:
        return f"{self.keycloak_realm_url}/protocol/openid-connect/userinfo"


settings = Settings()
