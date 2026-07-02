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
    redis_url: str = "redis://redis:6379/0"
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"
    # Firma del índice al cierre (E15-F3): llamada a signature-service (E06).
    signature_service_url: str = "http://signature-service:8008"
    # Descarga del índice YA FIRMADO (MinIO, vía storage-service) para la
    # verificación XAdES-B real (E06 Inc.1) — signature_client.verify recibe
    # el XML firmado, no el XML en claro (ver app/clients/storage_client.py).
    storage_service_url: str = "http://storage-service:8005"
    service_port: int = 8004
    log_level: str = "info"

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


settings = Settings()
