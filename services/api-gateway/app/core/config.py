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

    #: Tope por IP y minuto. Es un freno ANTI-AVALANCHA, no el regulador del uso
    #: normal — para eso está `rate_limit_per_usuario`.
    #:
    #: Estaba en 100 y era el único eje, lo que lo convertía en un defecto
    #: operativo medido: una pantalla del sistema cuesta **7 peticiones al
    #: gateway** (media sobre tablero, bandeja, búsqueda, expedientes y
    #: borradores), así que 100/min son **14 pantallas por minuto**. Un solo
    #: funcionario las agota en un rato, y en una entidad pública lo normal es una
    #: oficina entera tras un NAT: con 50 personas compartiendo IP saliente, el
    #: sistema daba una pantalla cada 36 segundos **para todos juntos**.
    rate_limit_per_ip: int = 1200

    #: Tope por USUARIO AUTENTICADO y minuto — el que regula el uso normal.
    #:
    #: Existe porque el eje IP no puede distinguir a cincuenta funcionarios detrás
    #: de una misma IP saliente, y el castigo recaía sobre inocentes: el primero
    #: que trabajara deprisa dejaba a los demás con 429. A 7 peticiones por
    #: pantalla, 120 son ~17 pantallas por minuto y por persona.
    rate_limit_per_usuario: int = 120

    #: Tope por ENTIDAD y minuto. Entre la persona y la plataforma.
    #:
    #: Sin este escalón, el techo de una entidad era el cubo global de TODAS:
    #: 4000/120 ≈ 33 personas de una sola entidad trabajando a la vez dejaban a
    #: las demás en 429, sin abuso ninguno. En multi-tenant eso es aislamiento de
    #: disponibilidad, y el recurso compartido tiene que estar particionado.
    rate_limit_per_tenant: int = 3000

    #: Freno anti-catástrofe de la plataforma entera. NO es el regulador de nadie:
    #: para eso están los cubos por persona y por entidad.
    #:
    #: Con `per_ip` a 1200 y el global a 4000, bastaban 3,3 IPs para saturar la
    #: plataforma — antes hacían falta 10. Un global que se alcanza con el tráfico
    #: legítimo de unas pocas entidades no protege: denega.
    rate_limit_global: int = 20_000

    #: Tope propio para el login (`POST /auth/token` y `/refresh`), por IP.
    #:
    #: Existe porque son las dos únicas rutas ANÓNIMAS caras que heredaban el cubo
    #: general: cada petición es un password-grant contra Keycloak, con hash de
    #: contraseña (PBKDF2, decenas de miles de iteraciones) sobre el componente que
    #: no tiene réplicas. Al subir el cubo general de 100 a 1200 por el problema
    #: del NAT, eso pasó a ser amplificación de CPU x12 — una puerta que estaba
    #: cerrada y que abrió un cambio hecho para otra cosa.
    #:
    #: `bruteForceProtected` de Keycloak mitiga el bloqueo POR CUENTA, no el costo
    #: ni el rociado de una contraseña sobre muchas cuentas distintas.
    rate_limit_login_per_ip: int = 20

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

    #: Tope para la cosecha anónima de interoperabilidad (OAI-PMH, CMIS).
    #:
    #: Son lecturas, pero cada una escribe un asiento de auditoría, y esa
    #: escritura toma el cerrojo que serializa la cadena de la entidad. Sin tope
    #: propio, repetirlas ponía a competir por ese cerrojo a todas las escrituras
    #: de auditoría de la entidad, radicaciones incluidas.
    rate_limit_cosecha_per_ip: int = 30

    # TTL (segundos) de la caché de `GET /auth/validate` en el gateway. Corto a
    # propósito: no hay invalidación por revocación, así que este número ES la
    # ventana durante la cual un token revocado seguiría siendo aceptado. Se acota
    # además al `expires_in` del propio token, para no resucitar uno caducado.
    auth_cache_ttl: int = 15
    # Secreto EXCLUSIVO del gateway con el que se sella (HMAC-SHA256) cada entrada
    # de esa caché. Sin él la caché queda apagada (fail-closed): una entrada sin
    # integridad la forja quien tenga solo la contraseña de Redis.
    auth_cache_mac_secret: str = ""
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

    # Tope propio para las rutas ANÓNIMAS (`/api/v1/public/`), muy por debajo del
    # general. El de 512 MiB está dimensionado para el ZIP de importación, que es
    # una carga autenticada y de administración; aplicárselo también a la puerta
    # ciudadana significaba dejar que un anónimo hiciera escribir medio giga en el
    # disco del contenedor de destino por cada petición, solo para que el servicio
    # lo rechazara después. La radicación externa admite 25 MiB de anexos: 32 MiB
    # deja holgura para el resto del multipart sin habilitar nada más.
    max_public_body_bytes: int = 32 * 1024 * 1024

    # Tope para la SUBIDA de anexos. El destino acepta 100 MiB y rechaza el
    # resto con 413, pero ese rechazo llega cuando el cuerpo entero ya se ha
    # escrito en el disco del contenedor: el análisis del multipart vuelca la
    # parte a un fichero temporal ANTES de que el endpoint pueda mirar su tamaño.
    # Con el tope general de 512 MiB, cada petición rechazada costaba medio giga
    # de disco, y el limitador permite cien por minuto. El gateway lo corta antes
    # de reenviar, con el mismo número que el destino admite más un margen para
    # el resto del multipart.
    max_upload_body_bytes: int = 110 * 1024 * 1024

    # ¿Se publica el catálogo agregado de la API sin autenticar?
    #
    # `/api/v1/openapi.json` describe la superficie ENTERA: los siete servicios
    # que agrega, sus esquemas y los endpoints `/internal` que el ruteo retira a
    # propósito. Servido a internet, es el mapa que un atacante tendría que
    # levantar a mano. La plantilla de nginx solo restringe `/health/ready` por
    # IP, así que en producción quedaba abierto.
    #
    # Por omisión NO. En desarrollo se enciende desde el compose, que es donde
    # la documentación navegable hace falta.
    openapi_publico: bool = False


settings = Settings()
