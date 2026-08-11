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
    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_from: str = "noreply@orpycamcp.local"
    smtp_max_attempts: int = 3
    smtp_retry_backoff_seconds: float = 1.0
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    # Token de servicio interno (D-02): protege `POST /api/v1/notifications/send`,
    # que es un endpoint SERVICIO-A-SERVICIO (signature-service lo llama para
    # despachar los OTP de 2FA; los demás eventos entran por el stream, no por
    # HTTP). Fail-closed: vacío ⇒ el endpoint rechaza toda llamada (401). Mismo
    # secreto que `signature-service.notification_internal_token`. Ver
    # `app/core/internal_auth.py`.
    internal_service_token: str = ""

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

    # SSRF de webhooks salientes (hallazgo Media, auditoría de seguridad — no
    # bloqueante). `WebhookCreate.url` (app/schemas/webhook.py) validaba forma,
    # no destino: cualquier admin podía suscribir un webhook a un servicio
    # interno de la red Docker o a un rango no enrutable, dirigiendo 3
    # reintentos × evento contra la propia red. Kill-switch INVERTIDO respecto
    # a los demás flags de este archivo: por defecto (False) la validación es
    # ESTRICTA (bloquea loopback/link-local/privado/nombres de servicio
    # Docker, ver app/core/webhook_security.py). Ponerlo en True SOLO para
    # desarrollo local (apuntar un webhook a un receptor en la máquina del
    # desarrollador o a MailHog). Producción debe dejarlo en False — fail
    # closed por defecto, no al revés.
    webhook_allow_private_targets: bool = False

    # evento_dead_letter terminal-row retention (ADR-021 Fase 4 — MVP-3, Fase 5 Inc.2).
    # Grace period (days) after resolved_at before a resolved/discarded dead-letter
    # row is purged. 'pending' rows are NEVER purged regardless of age (they await
    # a human decision). ge=1 guards against a misconfigured 0/negative TTL that
    # would widen the purge window to include very recent closures.
    retention_deadletter_days: int = Field(default=90, ge=1)

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


settings = Settings()
