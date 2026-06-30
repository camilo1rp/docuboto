# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"
    redis_url: str = "redis://redis:6379/0"
    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_from: str = "noreply@orpycamcp.local"
    smtp_max_attempts: int = 3
    smtp_retry_backoff_seconds: float = 1.0
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    # Retention job (ADR-021 Fase 4 — MVP-1)
    # How many days to keep rows in notification_processed_events before purging.
    # 7 days is generous: the actual redelivery / reclaim window is minutes.
    # ge=1 guards against a misconfigured 0/negative TTL purging in-flight markers.
    retention_dedup_days: int = Field(default=7, ge=1)
    # How often (in seconds) the worker loop triggers the retention sweep.
    retention_interval_seconds: int = 86400  # 24 h

    # DLQ buffer retention (ADR-021 Fase 4 — MVP-2).
    # How many days to keep Redis DLQ entries before XTRIM MINID pruning.
    # The authoritative copy lives in Postgres (evento_dead_letter + audit_log),
    # so 30 days is a very generous operational buffer.
    retention_dlq_days: int = Field(default=30, ge=1)

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


settings = Settings()
