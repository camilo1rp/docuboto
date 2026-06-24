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
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "orpycamcp"
    minio_secret_key: str = "orpycamcp_dev"
    minio_use_ssl: bool = False
    presigned_url_expiry: int = 3600  # seconds
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"
    service_port: int = 8005
    log_level: str = "info"

    # Formatos admitidos en la subida (RF-DIG-04). CSV de MIME types; "*" = todos.
    allowed_mime_types: str = (
        "application/pdf,"
        "application/msword,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.ms-excel,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.ms-powerpoint,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "application/vnd.oasis.opendocument.text,"
        "application/vnd.oasis.opendocument.spreadsheet,"
        "image/jpeg,image/png,image/tiff,image/gif,"
        "text/plain,text/csv,application/zip"
    )

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @property
    def allowed_mime_set(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.allowed_mime_types.split(",") if m.strip())


settings = Settings()
