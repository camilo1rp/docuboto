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

    # El MCP es cliente del api-gateway (nunca de la BD ni de los servicios).
    gateway_url: str = "http://api-gateway:8080"
    request_timeout: float = 10.0
    service_port: int = 8009
    log_level: str = "info"

    # Contexto de sesión para el transporte stdio (un cliente local lo provee por
    # entorno; en HTTP streamable vendría por cabeceras). El token Keycloak y el
    # tenant se propagan al gateway, que resuelve autenticación/RBAC/tenant.
    mcp_bearer_token: str = ""  # token estático (sin refresco); precedencia
    mcp_tenant_slug: str = ""

    # OAuth por sesión (stdio): el mcp-server obtiene y refresca el token Keycloak
    # vía auth-service (gateway), en vez de recibir un bearer estático.
    mcp_refresh_token: str = ""
    mcp_auth_username: str = ""
    mcp_auth_password: str = ""

    # Asistente conversacional (E18/ADR-019): el mcp-server llama a Claude API
    # DIRECTO (no vía gateway); cada tool se ejecuta vía el dispatcher existente,
    # que re-propaga el token del usuario al gateway (el asistente corre AS-THE-USER).
    # Vacío = degradación honesta (503 assistant_unavailable, RF-FIR-15); nunca
    # una respuesta fingida ni un 500 crudo. NO poner una key real aquí — se
    # inyecta por entorno (ver docker-compose.yml).
    anthropic_api_key: str = ""
    assistant_model: str = "claude-opus-4-8"
    assistant_max_tokens: int = 16000
    assistant_max_iterations: int = 8  # tope del loop tool_use manual
    assistant_history_ttl_seconds: int = 1800  # inactividad → se descarta el historial
    assistant_max_turns: int = 20  # tope de turnos de usuario por conversación

    # ---------------------------------------------------------------------
    # Propuestas firmadas (gate de escritura)
    # ---------------------------------------------------------------------
    # Clave HMAC con la que se firma cada propuesta. **Sin ella el servicio no
    # arranca** (ver `app/main.py`): una clave vacía produce firmas que cualquiera
    # reproduce, y el gate quedaría decorativo sin que nada lo aparentara — el
    # mismo modo de fallo que los secretos con valor de ejemplo del bloque 11.
    # ── Transcripción de voz (STT, ADR-020) ────────────────────────────────
    # `disabled` por defecto y a propósito: no existe un «stub» honesto de
    # transcripción — cualquiera tendría que inventarse lo que la persona dijo, y
    # ese texto acabaría en un documento oficial. Ver `app/services/stt.py`.
    stt_provider: str = "disabled"
    # Token del gate D-02 de whisper-service. Sin él, la transcripción responde
    # 403: ese servicio dejó de estar abierto a cualquiera de la red interna.
    whisper_internal_token: str = ""
    # `whisper-service` corre bajo el perfil `assistant` de Compose y arranca
    # desactivado: es un servicio pesado y una instalación mínima no lo necesita.
    whisper_url: str = "http://whisper-service:8012"
    # Un minuto de audio tarda unos segundos en CPU; el margen cubre un dictado
    # largo sin dejar la petición colgada para siempre.
    stt_timeout: float = 120.0
    # Tope del audio aceptado. Un dictado para el compositor del asistente son
    # segundos, no una grabación de una reunión: el límite protege la memoria del
    # servicio y hace explícito para qué es esto.
    stt_max_bytes: int = 10 * 1024 * 1024

    mcp_proposal_secret: str = ""
    # Clave anterior, para que una rotación no invalide las propuestas vivas.
    mcp_proposal_secret_previous: str = ""
    # Ventana de la propuesta. Corta a propósito: lo justo para leer la tarjeta y
    # decidir. Alargarla amplía la ventana en la que un token filtrado sirve.
    mcp_proposal_ttl_seconds: int = 120


settings = Settings()
