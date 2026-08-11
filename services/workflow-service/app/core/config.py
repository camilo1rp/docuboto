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

    # ── Presupuesto de conexiones a Postgres ──────────────────────────────
    # Configurables porque el reparto NO es una constante del código: depende
    # de cuántos servicios y réplicas haya en el despliegue. La aritmética
    # completa está escrita en `infra/docker-compose.prod.yml`, junto al
    # `max_connections` del servidor, que es donde tiene sentido cuadrarla.
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    # Tope por CONSULTA. Sin él, una consulta atascada retiene su conexión
    # indefinidamente y el pool se agota sin que nada lo delate. 120 s es muy
    # superior a cualquier consulta sana de este sistema; lo que legítimamente
    # tarde más debe pasar su propio `timeout=` en la llamada (es lo que hace
    # `orpycamcp_common.audit.verify_chain`, que recorre el registro entero).
    db_command_timeout: float = 120.0
    redis_url: str = "redis://redis:6379/0"
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"
    service_port: int = 8006
    log_level: str = "info"

    # DLQ buffer retention (ADR-021 Fase 4 — MVP-2).
    # How many days to keep Redis DLQ entries before XTRIM MINID pruning.
    # The authoritative copy lives in Postgres (evento_dead_letter + audit_log),
    # so 30 days is a very generous operational buffer.
    retention_dlq_days: int = Field(default=30, ge=1)
    # How often (in seconds) the worker loop triggers the DLQ trim sweep.
    retention_interval_seconds: int = 86400  # 24 h
    # Dead-letter Postgres retention (ADR-021 Fase 4 — MVP-3).
    # Grace period (in days) after which rows with status 'resolved' or 'discarded'
    # are eligible for deletion from evento_dead_letter.  'pending' rows are NEVER
    # purged — they represent unresolved failures awaiting human decision.
    # 90 days gives forensic slack; legal copy lives in audit_log (immutable).
    retention_deadletter_days: int = Field(default=90, ge=1)

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


settings = Settings()
