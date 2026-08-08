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

    redis_url: str = "redis://redis:6379/0"

    auth_service_url: str = "http://auth-service:8001"
    tenant_service_url: str = "http://tenant-service:8002"
    document_service_url: str = "http://document-service:8003"
    archive_service_url: str = "http://archive-service:8004"
    storage_service_url: str = "http://storage-service:8005"
    workflow_service_url: str = "http://workflow-service:8006"
    notification_service_url: str = "http://notification-service:8007"
    signature_service_url: str = "http://signature-service:8008"
    mcp_service_url: str = "http://mcp-server:8009"
    knowledge_service_url: str = "http://knowledge-service:8011"

    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    rate_limit_per_ip: int = 100
    rate_limit_global: int = 1000
    proxy_timeout: float = 30.0
    # Timeout largo SOLO para el asistente conversacional (E18): el loop LLM
    # (varias iteraciones Claude + llamadas a tools vía gateway) supera con
    # holgura el proxy_timeout global de 30s. No se sube el global para no
    # relajar el resto de rutas.
    assistant_proxy_timeout: float = 180.0
    service_port: int = 8080
    log_level: str = "info"

    # Tope del cuerpo de una petición que el gateway acepta reenviar.
    #
    # Antes NO existía ninguno, y el gateway además **bufferizaba el cuerpo entero
    # en memoria** (`await request.body()`) antes de mandarlo, con storage haciendo
    # una segunda copia. Con `memory: 1G` por réplica, eso convertía cualquier
    # subida grande —o una deliberada— en un OOM del contenedor, no en un error.
    # Ahora el cuerpo se transmite por trozos y este tope es lo que lo acota.
    #
    # 512 MiB no es un número redondo elegido al azar: es el máximo que ya declara
    # `document-service/app/routers/import_.py` para el ZIP de importación
    # interoperable, la carga legítima más grande del sistema. Bajarlo aquí rompería
    # esa función en silencio; subirlo no habilita nada que el servicio de destino
    # no vaya a rechazar igualmente.
    max_request_body_bytes: int = 512 * 1024 * 1024


settings = Settings()
