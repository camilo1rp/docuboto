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

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # E21: grupo de consumidores PROPIO sobre orpycamcp.document.events. Distinto
    # del grupo de workflow-service (auto-distribución) — Redis Streams entrega una
    # copia independiente del stream a cada consumer-group, así que ambos servicios
    # consumen el mismo stream sin competir entre sí.
    knowledge_ingest_group: str = "knowledge-ingest"

    # Proveedor de embeddings (ADR-006: pluggable, local por defecto).
    embedding_provider: str = "stub"
    embedding_dim: int = 64
    service_port: int = 8011
    log_level: str = "info"

    # Token de ingesta (D-02 / RF-SEG-08): cadena compartida que deben enviar los
    # servicios internos en X-Internal-Token para invocar /ingest. Si está vacío el
    # endpoint rechaza TODAS las llamadas (fail-closed).  Nunca exponer en logs.
    internal_service_token: str = ""


settings = Settings()
