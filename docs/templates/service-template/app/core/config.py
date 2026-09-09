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

    # El DATABASE_URL se mantiene en formato SQLAlchemy (postgresql+asyncpg://) por
    # consistencia con el resto del stack; se normaliza a asyncpg puro al usarlo
    # (asyncpg NO entiende el sufijo +asyncpg). Ver ADR-003.
    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    redis_url: str = "redis://redis:6379/0"
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    service_port: int = __PORT__
    log_level: str = "info"


settings = Settings()
