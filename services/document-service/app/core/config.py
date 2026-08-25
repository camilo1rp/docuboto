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
    storage_service_url: str = "http://storage-service:8005"
    # Token compartido D-02 (remediación no-read-up en anexos, RF-SEG-08):
    # enviado en X-Internal-Token al pedir bytes de un anexo a storage-service
    # (`app/clients/storage_client.py`). Mismo secreto que
    # `storage-service.INTERNAL_SERVICE_TOKEN`. Vacío (default) hace que
    # storage-service rechace la llamada con 401 (fail-closed) — NUNCA
    # commitear el valor real.
    storage_internal_token: str = ""
    # D-02 (E10 debt, cableado del AIP al cierre): secreto que ESTE servicio
    # exige en X-Internal-Token en `POST /api/v1/internal/documentos/anexos`
    # (`app/routers/internal.py`) — el ÚNICO llamador legítimo es
    # archive-service (`app/clients/document_client.py`), que resuelve el
    # inventario TOTAL de anexos de un lote de radicados para empaquetar el
    # AIP OAIS (acto de sistema, sin gate de clearance). Vacío (default) hace
    # que este servicio rechace la llamada con 401 (fail-closed) — NUNCA
    # commitear el valor real. Mismo patrón que
    # `storage-service.INTERNAL_SERVICE_TOKEN`.
    internal_service_token: str = ""
    tenant_service_url: str = "http://tenant-service:8002"
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"

    service_port: int = 8003
    log_level: str = "info"

    # Ingesta de correo (E19). Si imap_host está vacío, la ingesta queda
    # deshabilitada y el endpoint de poll responde 503.
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True
    imap_mailbox: str = "INBOX"
    # Entidad DUEÑA del buzón. El proceso tiene UN solo buzón configurado, pero
    # el endpoint de poll acepta el tenant de la cabecera: sin este campo, un
    # usuario de la entidad A dispara la ingesta y los correos del buzón —que
    # pueden ser de la entidad B— acaban radicados en A y marcados como leídos.
    # No es una fuga de lectura: es una apropiación irreversible de documentos
    # ajenos, porque el correo ya no vuelve a aparecer como no leído.
    #
    # Vacío por defecto = fail-closed: la ingesta responde 503 aunque IMAP esté
    # configurado. Es deliberado — un buzón sin dueño declarado no tiene forma
    # de saber en qué entidad debe radicar.
    imap_tenant_slug: str = ""

    # --- E19 Increment 1: cuentas de correo en base de datos ---
    #
    # Las variables `imap_*` de arriba configuran UNA sola cuenta para todo el
    # despliegue. Eso no expresa lo que exige el Acuerdo AGN 060/2001 art. 13
    # ("asignarán responsabilidades de acuerdo con la cantidad de cuentas
    # habilitadas") y deja la contraseña en claro en el entorno. Quedan como
    # ruta heredada mientras las entidades migran a `email_cuenta`.
    #
    # Clave maestra (AES-256 en base64) con la que se cifran en reposo las
    # credenciales de las cuentas institucionales. Vacía por defecto y
    # FAIL-CLOSED: crear una cuenta con contraseña falla explícitamente hasta
    # configurarla — nunca se cae a una clave derivada, que daría la apariencia
    # de custodia sin la custodia. Mismo criterio que
    # `signing_key_encryption_key` y `totp_encryption_key` de auth-service.
    email_encryption_key: str = ""

    # Sondeo programado de los buzones (E19 Increment 3). **Apagado por
    # defecto**: un proceso que se conecta solo a buzones institucionales y crea
    # documentos oficiales con número inmutable no debe encenderse porque sí al
    # desplegar. La entidad lo activa cuando ha configurado sus cuentas y ha
    # decidido cuáles radican solas.
    email_sondeo_enabled: bool = False
    email_sondeo_intervalo_segundos: int = 300
    # Entidad cuyos buzones sondea este proceso. Igual que `imap_tenant_slug`,
    # vacío = fail-closed: sin declararlo el worker no arranca, porque un sondeo
    # que adivinara la entidad radicaría en el archivo equivocado.
    email_sondeo_tenant_slug: str = ""

    # Interoperabilidad pública ANÓNIMA (E11): OAI-PMH y CMIS bajo /api/v1/public/,
    # que el gateway declara público sin autenticación.
    #
    # **False por defecto, y no es una precaución genérica.** El único recorte que
    # aplican esos endpoints es `nivel_seguridad = 1`, y el nivel 1 es el DEFAULT
    # de la columna (migración 016) y del schema de entrada: la reserva depende de
    # que un funcionario clasifique documento a documento, así que la polaridad
    # efectiva es «público salvo que alguien se acuerde». Las PQRS ciudadanas
    # entran por `routers/public.py` heredando ese nivel, con el asunto escrito por
    # el propio ciudadano. Y `cmisselector=content` no sirve metadatos: sirve los
    # BYTES del anexo.
    #
    # Publicar el registro de correspondencia de una entidad es una decisión de esa
    # entidad —el esquema de publicación del art. 12 de la Ley 1712—, no un efecto
    # secundario de desplegar el software. Este interruptor es el control provisional
    # hasta que exista el opt-in por documento (columna `publicable` con acto
    # explícito auditado); cuando exista, esto pasa a ser una segunda barrera.
    public_interop_enabled: bool = False


settings = Settings()
