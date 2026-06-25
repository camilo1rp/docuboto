# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database — asyncpg native DSN (no +asyncpg driver prefix)
    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"

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

    # JWKS public key cache TTL (seconds)
    jwks_cache_ttl: int = 600

    # Service
    service_port: int = 8001
    log_level: str = "info"

    @property
    def keycloak_realm_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}"

    @property
    def expected_issuer(self) -> str:
        """Issuer (`iss`) que debe traer todo token. Override vía KEYCLOAK_ISSUER."""
        return self.keycloak_issuer or self.keycloak_realm_url

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
