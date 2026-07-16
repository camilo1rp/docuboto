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
    mcp_user_permissions: str = ""  # CSV
    mcp_user_roles: str = ""  # CSV (ROOT = ve/usa todo)

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


settings = Settings()
