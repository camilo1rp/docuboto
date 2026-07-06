# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Modos de retención WORM válidos (espejo de miniopy_async.commonconfig.{GOVERNANCE,COMPLIANCE}).
_VALID_RETENTION_MODES = frozenset({"GOVERNANCE", "COMPLIANCE"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "orpycamcp"
    minio_secret_key: str = "orpycamcp_dev"
    minio_use_ssl: bool = False
    presigned_url_expiry: int = 3600  # seconds
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"
    service_port: int = 8005
    log_level: str = "info"

    # Entorno de despliegue ("development" | "production"); no hay indicador común
    # en el proyecto, así que storage-service define el suyo (autocontenido). Solo
    # gatea el guard de arranque de preservación WORM (ver `preservation_startup_guard`).
    app_env: str = "development"

    # Preservación WORM del índice electrónico firmado (E10 Increment A, ADR-016/E15).
    # Feature flag/kill-switch: con False, POST /preservacion/proteger-indice responde 503.
    preservation_enabled: bool = False
    preservation_bucket_suffix: str = "preservation"
    # COMPLIANCE = ni siquiera root puede acortar/eliminar la retención (irreversible,
    # el valor legalmente correcto para el índice electrónico). GOVERNANCE existe
    # solo para necesidades operativas explícitas — ver guard de arranque abajo.
    preservation_retention_mode: str = "COMPLIANCE"
    # Piso de seguridad: rechaza `retain_until` que implique menos días que esto,
    # aunque el TRD del llamador diga otra cosa (defensa en profundidad).
    preservation_min_retention_days: int = 365
    # Techo de cordura: una fecha de retención absurdamente lejana suele ser un bug
    # del llamador (unidades mal convertidas), no una decisión TRD deliberada.
    preservation_max_retention_years: int = 100

    @field_validator("preservation_retention_mode")
    @classmethod
    def _validate_retention_mode(cls, v: str) -> str:
        upper = v.upper()
        if upper not in _VALID_RETENTION_MODES:
            raise ValueError(
                f"preservation_retention_mode debe ser uno de {sorted(_VALID_RETENTION_MODES)}, recibido {v!r}"
            )
        return upper

    def preservation_startup_guard(self) -> list[str]:
        """Avisos de arranque sobre la config de preservación WORM.

        En ``app_env=="production"`` con modo distinto de COMPLIANCE, el llamador
        (``main.py::lifespan``) trata estos avisos como FATALES (rechaza el arranque):
        GOVERNANCE es evadible por un usuario con el permiso bypass-governance de
        MinIO, y envenenar/perder la inmutabilidad del índice electrónico firmado
        es irreversible en producción. En development/test son solo un warning
        ruidoso (necesario para poder mockear MinIO sin permisos de bypass reales).
        """
        warnings: list[str] = []
        if self.preservation_retention_mode != "COMPLIANCE":
            warnings.append(
                f"PRESERVATION_RETENTION_MODE={self.preservation_retention_mode} "
                "(no COMPLIANCE): la retención WORM del índice electrónico es evadible "
                "por un usuario con permiso bypass-governance de MinIO."
            )
        return warnings

    # Formatos admitidos en la subida (RF-DIG-04). CSV de MIME types; "*" = todos.
    allowed_mime_types: str = (
        "application/pdf,"
        "application/msword,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.ms-excel,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.ms-powerpoint,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "application/vnd.oasis.opendocument.text,"
        "application/vnd.oasis.opendocument.spreadsheet,"
        "image/jpeg,image/png,image/tiff,image/gif,"
        "text/plain,text/csv,application/zip"
    )

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @property
    def allowed_mime_set(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.allowed_mime_types.split(",") if m.strip())


settings = Settings()
