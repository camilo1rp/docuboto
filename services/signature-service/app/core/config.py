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

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # Proveedor de firma (ADR-016: pluggable). 'nativa' = firma electrónica
    # (hash + identidad); un proveedor PKI XAdES/PAdES/TSA real se conecta aquí.
    signer_provider: str = "nativa"
    service_port: int = 8008
    log_level: str = "info"

    # Redis URL for stream consumption (document.radicado.reclassified events).
    redis_url: str = "redis://redis:6379/0"

    # Server-side HMAC secret — makes firma_blob non-reproducible from public data
    # (RF-FIR-12, D-01). Must be at least 32 random bytes; rotate with a migration
    # of existing firmas if compromised. NEVER commit the real value.
    signature_secret: str = "change-me-in-production"

    # DLQ buffer retention (ADR-021 Fase 4 — MVP-2).
    # How many days to keep Redis DLQ entries before XTRIM MINID pruning.
    # The authoritative copy lives in Postgres (evento_dead_letter + audit_log),
    # so 30 days is a very generous operational buffer.
    retention_dlq_days: int = Field(default=30, ge=1)
    # How often (in seconds) the worker loop triggers the DLQ trim sweep.
    retention_interval_seconds: int = 86400  # 24 h


settings = Settings()
