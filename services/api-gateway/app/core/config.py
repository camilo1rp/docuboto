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

    # Orígenes autorizados a llamar al gateway desde un navegador (CSV).
    #
    # Antes estaba en duro `allow_origins=["*"]` **junto con**
    # `allow_credentials=True`. Esa pareja no es "permisiva": Starlette, cuando
    # hay credenciales, no puede responder `*` — así que refleja el `Origin` que
    # venga, y el resultado es que CUALQUIER página de internet podía llamar al
    # gateway con las credenciales del visitante. Es la definición de CSRF.
    #
    # Vacío por defecto = **no se instala el middleware CORS**. No es una
    # regresión: el frontend habla con el gateway desde el servidor (SSR), no
    # desde el navegador, así que el despliegue normal no necesita CORS. Quien
    # sí lo necesite (una integración de terceros, un SPA en otro dominio) tiene
    # que enumerar los orígenes, que es exactamente la decisión que antes se
    # tomaba sola. Ejemplo:
    #   CORS_ALLOW_ORIGINS=https://sgd.entidad.gov.co,https://portal.entidad.gov.co
    #
    # El comodín `*` se acepta pero **desactiva las credenciales** (ver
    # `app/main.py`): un API público de solo lectura es un caso legítimo; un
    # comodín con credenciales no lo es en ningún escenario.
    cors_allow_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    # Redes de los intermediarios de CONFIANZA (CSV de CIDR o direcciones), en
    # orden indiferente. Solo se cree lo que estas redes escriben en
    # `X-Forwarded-For`; ver `app/core/client_ip.py` para la regla completa.
    #
    # Vacío por defecto **a propósito**: sin declarar los proxies, la cabecera se
    # ignora entera y se usa la IP del socket. Un default permisivo aquí es dejar
    # que el cliente elija su propia identidad de red — y esa identidad acaba
    # sellada en el `audit_log`.
    #
    # En un despliegue típico son la red Docker (donde vive el SSR, que reenvía
    # la IP del navegador) y el reverse proxy que termina TLS. Ejemplo:
    #   TRUSTED_PROXIES=172.20.0.0/16,10.0.0.5
    trusted_proxies: str = ""

    # Tope propio para el alta ciudadana de PQRS (`POST /public/{tenant}/pqrs`),
    # muy por debajo del general: ese endpoint crea radicados SIN autenticar y
    # consume números de radicado que **no se reutilizan**, así que un bucle
    # trivial agujerea la numeración oficial de la entidad de forma permanente.
    rate_limit_pqrs_per_ip: int = 5

    # TTL (segundos) de la caché de `GET /auth/validate` en el gateway. Corto a
    # propósito: no hay invalidación por revocación, así que este número ES la
    # ventana durante la cual un token revocado seguiría siendo aceptado. Se acota
    # además al `expires_in` del propio token, para no resucitar uno caducado.
    auth_cache_ttl: int = 15
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
