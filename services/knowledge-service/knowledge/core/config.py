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

    # Búsqueda de texto completo que respalda `/search` y `/antecedentes` en modo
    # `local` (ADR-046 §2): se consulta como el usuario, con su identidad firmada.
    document_service_url: str = "http://document-service:8003"
    # El mismo secreto que document-service exige en sus rutas internas.
    # Vacío: el retorno no pide bytes.
    document_internal_token: str = ""
    # Bytes de un archivo ya subido, para la ingesta de preparación.
    # Vacío: no se leen. El mismo secreto que storage-service exige.
    storage_service_url: str = "http://storage-service:8005"
    storage_internal_token: str = ""
    service_port: int = 8011
    log_level: str = "info"
    # El consumidor lee los dos streams de dominio. La URL vacía no es un
    # destino: el punto de paso no llama a nadie hasta el perfil intelligence.
    redis_url: str = "redis://redis:6379/0"
    ppi_provider_url: str = ""
    # KEK que abre la clave de seudónimo, la de retorno y el secreto del
    # cliente de servicio. Vacía: no hay consulta al proveedor ni declaración.
    intelligence_credential_kek: str = ""
    # Realm que emite el token de la cuenta de servicio. Vacío: no hay llamada.
    keycloak_url: str = ""
    keycloak_realm: str = "orpycamcp"
    # Clave pública fijada para comprobar la declaración. Vacía: no se lee.
    ppi_capabilities_public_key: str = ""
    ppi_capabilities_kid: str = ""
    # Origen público de las dos URL de retorno. Vacío: no se declara la conexión.
    ppi_callback_base_url: str = ""
    # Vacío: las rutas internas que usa el acto de configuración rechazan
    # toda llamada. No es el token de document-service.
    intelligence_internal_token: str = ""


settings = Settings()
