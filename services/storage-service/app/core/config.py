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

    # Réplica del AIP empaquetado (E10 Increment C1b, RF-FIR-15). Feature
    # flag/kill-switch: con False, `empaquetar_aip` se comporta EXACTAMENTE
    # igual que antes de este incremento (sin réplica alguna).
    preservation_replica_enabled: bool = False
    preservation_replica_bucket_suffix: str = "preservation-replica"
    # Segundo endpoint MinIO opcional (réplica CROSS-SITE real, capacidad de
    # producción). Si NO se configura, la réplica es LOCAL: un segundo bucket
    # del MISMO MinIO primario — honestamente NO es copia geográfica (ver
    # `aip_replica_service.ReplicaResult.detalle`). Credenciales secretas:
    # nunca se loguean.
    minio_secondary_endpoint: str | None = None
    minio_secondary_access_key: str | None = None
    minio_secondary_secret_key: str | None = None
    minio_secondary_use_ssl: bool = False

    # Validación PDF/A del AIP en ingesta (E10 Increment C2a, RF-PRE-02, ISO
    # 19005, OAIS). Gate maestro/kill-switch: con False (default),
    # `empaquetar_aip` NO invoca validador alguno — se comporta exactamente
    # igual que antes de este incremento.
    preservation_pdfa_validation_enabled: bool = False
    # "stub" (default, HONESTO: siempre `outcome=not_evaluated`, ver
    # `app/services/pdfa/stub.py`) | "verapdf" (E10 Increment C2b, roadmap —
    # hoy degrada a stub con warning, `app/services/pdfa/factory.py`).
    pdfa_validator: str = "stub"
    # Perfil PDF/A objetivo: POLÍTICA DECLARADA de la institución (no un
    # detalle implícito del validador). PDF/A-2b (ISO 19005-2) es el default
    # razonable para documentos administrativos digitalizados.
    pdfa_profile: str = "2b"
    # Reservados para E10 Increment C2b (SubprocessVeraPdfValidator real,
    # veraPDF vía subprocess) — SIN uso en este incremento.
    verapdf_binary: str | None = None
    verapdf_timeout_seconds: int = 60
    verapdf_max_concurrency: int = 1
    verapdf_jvm_max_heap_mb: int = 512

    @field_validator("pdfa_validator")
    @classmethod
    def _validate_pdfa_validator(cls, v: str) -> str:
        valid = frozenset({"stub", "verapdf"})
        lowered = v.lower()
        if lowered not in valid:
            raise ValueError(
                f"pdfa_validator debe ser uno de {sorted(valid)}, recibido {v!r}"
            )
        return lowered

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

    # Token de servicio interno (D-02, mismo patrón que knowledge-service):
    # exigido en X-Internal-Token por los endpoints que sirven BYTES/URL de un
    # archivo (`GET /files/{id}/download` presigned, `GET /files/{id}/content`,
    # `GET /files/{id}` metadata) — una presigned URL o los bytes crudos
    # entregados sin mediación saltarían el gate de clearance de
    # document-service (RF-SEG-08). `/upload` NO se gatea: el frontend sube
    # directo. Fail-closed: vacío (default) rechaza TODAS las llamadas a esos
    # endpoints con 401 — mismo criterio que `internal_service_token` de
    # knowledge-service. NUNCA loguear el valor real.
    internal_service_token: str = ""

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
