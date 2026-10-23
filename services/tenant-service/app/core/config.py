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

    # Proveedores EFECTIVOS del asistente, para mostrarlos en la tarjeta de encendido
    # (ADR-037: quien enciende debe saber a quién se transmite). Espejo de los ajustes
    # de knowledge-service y mcp-server; mismas variables y mismos defaults (lo vigila
    # `shared/tests/test_proveedores_del_asistente_coinciden.py`).
    embedding_provider: str = "local_st"
    ai_provider: str = "ollama_local"
    assistant_model: str = "claude-opus-4-8"


settings = Settings()
