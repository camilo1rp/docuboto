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

    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # Proveedor de firma (ADR-016: pluggable). 'nativa' = firma electrónica
    # (hash + identidad); un proveedor PKI XAdES/PAdES/TSA real se conecta aquí.
    signer_provider: str = "nativa"
    service_port: int = 8008
    log_level: str = "info"

    # Keycloak — JWT validation (RF-FIR-11, D-01)
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"
    jwks_cache_ttl: int = 300  # seconds

    # Issuer (`iss`) expected in every token. Override via KEYCLOAK_ISSUER when
    # Keycloak's public hostname (KC_HOSTNAME_URL) differs from the internal URL
    # used to resolve JWKS: tokens carry the public issuer while backends call
    # Keycloak on the Docker-internal address (e.g. http://keycloak:8080).
    keycloak_issuer: str | None = None

    @property
    def keycloak_jwks_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/certs"

    @property
    def expected_issuer(self) -> str:
        """Issuer (`iss`) all tokens must carry. Override via KEYCLOAK_ISSUER."""
        return self.keycloak_issuer or f"{self.keycloak_url}/realms/{self.keycloak_realm}"

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

    # Server-side HMAC secret — makes firma_blob non-reproducible from public data
    # (RF-FIR-12, D-01). Must be at least 32 random bytes; rotate with a migration
    # of existing firmas if compromised. NEVER commit the real value.
    signature_secret: str = "change-me-in-production"


settings = Settings()
