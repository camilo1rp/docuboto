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

    # ---------------------------------------------------------------------
    # Propuestas firmadas (gate de escritura)
    # ---------------------------------------------------------------------
    # Clave HMAC con la que se firma cada propuesta. **Sin ella el servicio no
    # arranca** (ver `app/main.py`): una clave vacía produce firmas que cualquiera
    # reproduce, y el gate quedaría decorativo sin que nada lo aparentara — el
    # mismo modo de fallo que los secretos con valor de ejemplo del bloque 11.
    mcp_proposal_secret: str = ""
    # Clave anterior, para que una rotación no invalide las propuestas vivas.
    mcp_proposal_secret_previous: str = ""
    # Ventana de la propuesta. Corta a propósito: lo justo para leer la tarjeta y
    # decidir. Alargarla amplía la ventana en la que un token filtrado sirve.
    mcp_proposal_ttl_seconds: int = 120


settings = Settings()
