# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # Proveedor de firma (ADR-016: pluggable). Selección real por endpoint:
    # /sign-xml usa 'xades_local' (XAdES-B real con el sello institucional,
    # ver app/providers/local.py) SOLO cuando objeto_tipo='indice' Y este
    # valor es 'xades_local'; en cualquier otro caso cae a 'nativa' (HMAC de
    # servicio, ver app/providers/native.py) — incluye el kill-switch
    # operativo de fijar SIGNER_PROVIDER=nativa para deshabilitar XAdES sin
    # tocar código (E06, ADR-016, Inc.1).
    signer_provider: str = "xades_local"
    service_port: int = 8008
    log_level: str = "info"

    # Redis URL for stream consumption (document.radicado.reclassified events)
    # y publicación (firma.indice.firmado — app/core/events.py, Inc.1).
    redis_url: str = "redis://redis:6379/0"

    # Server-side HMAC secret — makes firma_blob non-reproducible from public data
    # (RF-FIR-12, D-01). Must be at least 32 random bytes; rotate with a migration
    # of existing firmas if compromised. NEVER commit the real value.
    signature_secret: str = "change-me-in-production"

    # --- Sello institucional XAdES-B (app/core/keys.py, RT-16, Inc.1) ---
    # Directorio raíz montado como secreto/volumen Docker de solo lectura, con
    # un subdirectorio por tenant: <signer_cert_dir>/<slug>/seal.{crt,key}.
    # Fallback de desarrollo en <signer_cert_dir>/_dev/ (scripts/gen-dev-signing-cert.sh).
    signer_cert_dir: str = "/run/secrets/signing"
    # Passphrase opcional de la clave privada del sello (vacío = sin cifrar,
    # el caso de scripts/gen-dev-signing-cert.sh). NUNCA commitear el valor real.
    signer_key_password: str = ""
    # Kill-switch del fallback de sello compartido `_dev/` (app/core/keys.py,
    # RT-16). `False` por defecto (fail-closed): sin sello propio montado para
    # el tenant, `load_seal` lanza `SealNotFoundError` → 503 → el índice queda
    # 'pendiente_firma', en vez de firmar con el par de desarrollo compartido
    # entre TODOS los tenants (aislamiento criptográfico roto, aunque siempre
    # `acreditado=False`). El `docker-compose.yml` de desarrollo lo activa
    # explícitamente (`SIGNER_ALLOW_DEV_SEAL=true`) porque solo provisiona
    # `secrets/signing/_dev/`; producción debe dejarlo en `False`.
    signer_allow_dev_seal: bool = False

    # URL del storage-service (E06 Inc.1 → E07): destino del XML firmado del
    # índice electrónico (app/clients/storage_client.py).
    storage_service_url: str = "http://storage-service:8005"

    # DLQ buffer retention (ADR-021 Fase 4 — MVP-2).
    # How many days to keep Redis DLQ entries before XTRIM MINID pruning.
    # The authoritative copy lives in Postgres (evento_dead_letter + audit_log),
    # so 30 days is a very generous operational buffer.
    retention_dlq_days: int = Field(default=30, ge=1)
    # How often (in seconds) the worker loop triggers the DLQ trim sweep.
    retention_interval_seconds: int = 86400  # 24 h
    # Dead-letter Postgres retention (ADR-021 Fase 4 — MVP-3).
    # Grace period (in days) after which rows with status 'resolved' or 'discarded'
    # are eligible for deletion from evento_dead_letter.  'pending' rows are NEVER
    # purged — they represent unresolved failures awaiting human decision.
    # 90 days gives forensic slack; legal copy lives in audit_log (immutable).
    retention_deadletter_days: int = Field(default=90, ge=1)

    # --- 2FA por OTP-correo en la firma personal (E06 Inc.2, RF-FIR-13) ---
    # Kill-switch: True (default) exige challenge_id+otp en POST /sign y POST
    # /batch para la firma personal de la cadena (app/services/cadena_service.py
    # CadenaFirmaService.firmar). El sello institucional del índice (/sign-xml,
    # RF-FIR-14, Inc.1) NO pasa por este gate — vive en otro router y no invoca
    # CadenaFirmaService.firmar, así que queda exento por frontera de endpoint.
    # NOTA: este flag deja de ser un kill-switch total desde CadenaFirmaService
    # .firmar() — cuando la solicitud es RESERVADA/CLASIFICADA (nivel_seguridad
    # >= 2) el gate 2FA se exige SIEMPRE, sin importar este valor; el flag solo
    # sigue siendo un interruptor real para PUBLICA (nivel_seguridad=1) o en
    # entornos de prueba.
    firma_2fa_required: bool = True
    # Vigencia del OTP desde su emisión (POST /requests/{id}/challenge).
    otp_ttl_seconds: int = Field(default=300, ge=1)
    # Máximo de verificaciones fallidas antes de bloquear el challenge.
    otp_max_intentos: int = Field(default=5, ge=1)
    # Longitud del OTP numérico (zero-padded, generado con secrets — CSPRNG).
    # app/schemas/cadena.py deriva su regex de validación de ESTE mismo valor
    # (no un literal "6"), para que emisión y verificación nunca diverjan.
    otp_longitud: int = Field(default=6, ge=4, le=10)
    # Ventana mínima entre reemisiones de OTP para el mismo (solicitud,
    # firmante, documento) — evita el reenvío-como-bombardeo de correos.
    otp_reenvio_cooldown_seconds: int = Field(default=30, ge=0)
    # Llamadas DIRECTAS servicio-a-servicio (Q3, mismo patrón que Inc.1
    # storage_client.py) — NO a través del api-gateway.
    notification_service_url: str = "http://notification-service:8007"
    auth_service_url: str = "http://auth-service:8001"


settings = Settings()
