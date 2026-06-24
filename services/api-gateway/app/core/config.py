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

    redis_url: str = "redis://redis:6379/0"

    auth_service_url: str = "http://auth-service:8001"
    tenant_service_url: str = "http://tenant-service:8002"
    document_service_url: str = "http://document-service:8003"
    archive_service_url: str = "http://archive-service:8004"
    storage_service_url: str = "http://storage-service:8005"
    workflow_service_url: str = "http://workflow-service:8006"
    notification_service_url: str = "http://notification-service:8007"
    signature_service_url: str = "http://signature-service:8008"

    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    rate_limit_per_ip: int = 100
    rate_limit_global: int = 1000
    proxy_timeout: float = 30.0
    service_port: int = 8080
    log_level: str = "info"


settings = Settings()
