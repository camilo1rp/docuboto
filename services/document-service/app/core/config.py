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
    storage_service_url: str = "http://storage-service:8005"
    # Token compartido D-02 (remediación no-read-up en anexos, RF-SEG-08):
    # enviado en X-Internal-Token al pedir bytes de un anexo a storage-service
    # (`app/clients/storage_client.py`). Mismo secreto que
    # `storage-service.INTERNAL_SERVICE_TOKEN`. Vacío (default) hace que
    # storage-service rechace la llamada con 401 (fail-closed) — NUNCA
    # commitear el valor real.
    storage_internal_token: str = ""
    # D-02 (E10 debt, cableado del AIP al cierre): secreto que ESTE servicio
    # exige en X-Internal-Token en `POST /api/v1/internal/documentos/anexos`
    # (`app/routers/internal.py`) — el ÚNICO llamador legítimo es
    # archive-service (`app/clients/document_client.py`), que resuelve el
    # inventario TOTAL de anexos de un lote de radicados para empaquetar el
    # AIP OAIS (acto de sistema, sin gate de clearance). Vacío (default) hace
    # que este servicio rechace la llamada con 401 (fail-closed) — NUNCA
    # commitear el valor real. Mismo patrón que
    # `storage-service.INTERNAL_SERVICE_TOKEN`.
    internal_service_token: str = ""
    tenant_service_url: str = "http://tenant-service:8002"
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    service_port: int = 8003
    log_level: str = "info"

    # Ingesta de correo (E19). Si imap_host está vacío, la ingesta queda
    # deshabilitada y el endpoint de poll responde 503.
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True
    imap_mailbox: str = "INBOX"


settings = Settings()
