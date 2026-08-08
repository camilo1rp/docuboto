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

    redis_url: str = "redis://redis:6379/0"

    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    service_port: int = 8002
    log_level: str = "info"

    # Token de servicio interno (D-02) que sella el registro de instituciones
    # (`routers/tenants.py`, sobre `public.tenants`). Fail-closed: vacío —el
    # default— rechaza TODAS las llamadas a esas rutas. Es el estado correcto
    # por omisión: hoy no hay ningún consumidor legítimo por HTTP, el alta la
    # hace `scripts/init_tenant.py` escribiendo directo a la BD. Ver
    # `app/core/internal_auth.py` para el razonamiento completo.
    internal_service_token: str = ""


settings = Settings()
