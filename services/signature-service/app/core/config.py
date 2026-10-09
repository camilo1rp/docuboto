# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator
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

    # document-service: se consulta la huella VIGENTE de un borrador antes de
    # resolver un turno de firma (E23, RF-PRO-11). Misma puerta interna que
    # auth-service: `X-Internal-Token`, sin reenviar la identidad del firmante.
    document_service_url: str = "http://document-service:8003"
    # Secreto de la puerta interna de DOCUMENT-SERVICE, que NO es el de
    # auth-service. Tenerlos separados no es purismo: cada servicio valida el
    # suyo, y reutilizar el de auth aquí hacía que la consulta de la huella
    # respondiera 401 SIEMPRE — el guardián de documento vigente degradaba a
    # 503 y el circuito de aprobación quedaba inoperativo entero, fail-closed
    # pero inservible. No lo cazó ningún test porque todos sustituyen la
    # llamada; apareció al abrir el navegador.
    # Mismo valor que document-service.INTERNAL_SERVICE_TOKEN (D-02).
    document_internal_token: str = ""
    # AREA-04-09 Media-2: huella del índice electrónico VIGENTE de un expediente
    # (`GET /api/v1/internal/expedientes/{ref}/indice/huella`). Mismo secreto que
    # `archive-service.INTERNAL_SERVICE_TOKEN`; vacío = archive responde 401 y
    # abrir/firmar una cadena sobre un expediente falla cerrado (503).
    archive_service_url: str = "http://archive-service:8004"
    archive_internal_token: str = ""

    # Redis URL for stream consumption (document.radicado.reclassified events)
    # y publicación (firma.indice.firmado — app/core/events.py, Inc.1).
    redis_url: str = "redis://redis:6379/0"

    # Server-side HMAC secret — makes firma_blob non-reproducible from public data
    # (RF-FIR-12, D-01). Must be at least 32 random bytes; rotate with a migration
    # of existing firmas if compromised. NEVER commit the real value.
    #
    # Default VACÍO a propósito. El anterior (`"change-me-in-production"`) era un
    # secreto funcional publicado en un repositorio AGPL: un despliegue que se
    # olvidara de definirlo arrancaba y firmaba con una clave que cualquiera
    # puede leer. El arranque ahora lo exige — ver `main.py` y
    # `orpycamcp_common.secrets`.
    signature_secret: str = ""

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
    # Token compartido D-02 (remediación no-read-up en anexos, RF-SEG-08):
    # mismo secreto que `storage-service.INTERNAL_SERVICE_TOKEN`, enviado en
    # X-Internal-Token por `app/clients/storage_client.py` en cualquier
    # llamada a storage-service que quede protegida por ese gate. Hoy
    # `upload_signed_xml` solo usa `POST /upload` (sin gatear), pero se envía
    # de forma preventiva/uniforme con archive-service — si storage-service
    # amplía el gate a `/upload` en el futuro, este cliente ya está listo.
    # Vacío (default) = sin efecto mientras `/upload` no lo exija.
    storage_internal_token: str = ""

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
    # Token compartido D-02: mismo secreto que
    # `notification-service.INTERNAL_SERVICE_TOKEN`, enviado en X-Internal-Token
    # al despachar el OTP por `POST /api/v1/notifications/send` (que ahora está
    # sellado como endpoint servicio-a-servicio). Vacío (default) ⇒ notification
    # responde 401 y el OTP-correo no se entrega — en dev/prod debe configurarse.
    notification_internal_token: str = ""
    auth_service_url: str = "http://auth-service:8001"

    # --- 2FA por TOTP (RFC 6238), elevando el OTP-correo del Inc.2 ---
    # Kill-switch: True (default) permite que POST /sign y POST /batch acepten
    # totp_code como satisfactor del gate 2FA (app/services/cadena_service.py
    # CadenaFirmaService._gate_2fa selecciona TOTP sobre challenge_id+otp
    # cuando ambos vienen presentes). En False, totp_code se IGNORA por
    # completo (como si no viniera) — el caller cae al camino OTP-correo o,
    # si tampoco lo trae, al 409 2fa_requerido de siempre. NUNCA se traduce
    # en un bypass del gate en sí (firma_2fa_required / nivel_seguridad >= 2
    # siguen mandando, ver firmar()).
    totp_enabled: bool = True
    # Timeout agresivo para POST /internal/totp/verify (auth-service) —
    # fail-closed: cualquier excepción (timeout incluido) se traduce en 503
    # canal_2fa_no_disponible + rollback, mismo patrón que get_signer_identity.
    totp_verify_timeout_seconds: float = 3.0

    # --- XAdES-T: sello de tiempo RFC 3161 del índice (E06 Inc.4, RT-16) ---
    # Kill-switch: True (default) exige un SignatureTimeStamp válido para toda
    # firma xades_local — sin TSA disponible, /sign-xml falla (503), igual que
    # `signer_allow_dev_seal=False` hace fallar cerrado la ausencia de sello
    # institucional (ver app/providers/local.py::LocalSigner.sign). En False,
    # /sign-xml produce XAdES-B sin sello de tiempo, como antes del Inc.4
    # (entornos sin TSA montada).
    signer_timestamp_enabled: bool = True
    # URL de una TSA remota RFC 3161. Vacío (default) = TSA LOCAL in-process
    # (app/core/tsa.py::_build_local_timestamp_token). Con este valor puesto,
    # sign-xml usa el cliente remoto REAL (app/core/tsa.py::
    # _request_remote_timestamp: POST HTTP, comprueba nonce, EKU y token) y,
    # si la TSA no responde o el token no es conforme, falla explícitamente
    # (TsaUnavailableError → 503) — NUNCA cae en silencio a la TSA local si el
    # operador fijó una URL. Acreditado = `tsa_url` Y `tsa_acreditada`
    # (app/core/tsa.py::sello_acreditado).
    # El token RFC 3161 debe llevar el atributo firmado `signing-certificate-v2`
    # (ESSCertID, RFC 5035) que nombra el certificado de la TSA. Si lo lleva, su
    # hash se comprueba SIEMPRE. Qué pasa cuando FALTA lo decide una FECHA DE
    # CORTE (antes un booleano, `TSA_ESSCERTID_OBLIGATORIO`, que obligaba a elegir
    # entre rechazar todo el corpus anterior o no exigir nunca nada):
    #   genTime >= corte -> el sello se rechaza (`tsa_sin_esscertid`), como exige
    #                       la RFC 3161: todo token nuevo (local o de una TSA
    #                       remota conforme) lo lleva.
    #   genTime <  corte -> sigue siendo válido y `/verify` lo declara en
    #                       `advertencias` (`tsa_sin_esscertid_anterior_al_corte`):
    #                       los tokens que emitió la TSA local de desarrollo ANTES
    #                       de AREA-04-08 no lo llevan y están embebidos, inmutables,
    #                       en firmas ya emitidas; rechazarlos las daría por inválidas.
    # Es GLOBAL y no por entidad a propósito: la TSA es infraestructura compartida
    # del despliegue, y el corpus de tokens anteriores pertenece al despliegue, no
    # a una entidad. El DEFAULT es la fecha de despliegue de este cambio
    # (2026-10-04T00:00:00Z); `TSA_ESSCERTID_CORTE` admite ISO 8601 y, sin zona
    # horaria, se interpreta como UTC.
    tsa_esscertid_corte: datetime = datetime(2026, 10, 4, tzinfo=UTC)

    @field_validator("tsa_esscertid_corte", mode="after")
    @classmethod
    def _corte_con_zona(cls, v: datetime) -> datetime:
        return v if v.tzinfo is not None else v.replace(tzinfo=UTC)

    tsa_url: str = ""
    # Segundos de espera de la TSA remota (conexión + lectura). Acotado a
    # propósito: `build_timestamp_token` es SÍNCRONO y se invoca desde el mismo
    # hilo que firma, así que una TSA lenta bloquearía el bucle de eventos
    # durante este tiempo. No se hizo asíncrono porque el firmador ya hace
    # criptografía bloqueante en línea (signxml, RSA); lo que sí se hace es
    # acotar la espera para que el bloqueo tenga un techo conocido.
    tsa_timeout_seconds: float = 10.0
    # Declara que la TSA remota configurada en `tsa_url` es de un prestador
    # ACREDITADO (ONAC). Solo tiene efecto con `tsa_url` puesto: la TSA LOCAL
    # es un par autofirmado del propio despliegue y jamás se acredita, mire lo
    # que mire este flag.
    #
    # Es una variable de DESPLIEGUE y no per-tenant, a diferencia del sello
    # institucional, porque la TSA también lo es (`tsa_cert_dir` es única del
    # despliegue). Quien la fija es quien contrató el servicio de sellado, que
    # es el mismo que despliega. No es un dato que ningún usuario pueda tocar.
    tsa_acreditada: bool = False
    # --- Sello de la TSA local (app/core/keys.py::load_tsa_seal, Inc.4) ---
    # Directorio raíz montado como secreto/volumen Docker de solo lectura.
    # A diferencia del sello institucional (por tenant), la TSA es ÚNICA del
    # despliegue (no per-tenant): par principal en <tsa_cert_dir>/tsa.{crt,key},
    # con fallback de desarrollo en <tsa_cert_dir>/_dev/tsa.{crt,key}
    # (scripts/gen-dev-tsa-cert.sh) — mismo patrón de dos niveles que
    # `signer_cert_dir`, solo que aquí el nivel "principal" no está scopeado
    # por slug de tenant.
    tsa_cert_dir: str = "/run/secrets/tsa"
    # Passphrase opcional de la clave privada de la TSA (vacío = sin cifrar).
    tsa_key_password: str = ""
    # Kill-switch del fallback de TSA de DESARROLLO `_dev/` — análogo a
    # `signer_allow_dev_seal` (False por defecto, fail-closed en producción:
    # sin par principal montado, `load_tsa_seal` lanza `TsaSealNotFoundError`
    # en vez de caer al par de pruebas). El `docker-compose.yml` de desarrollo
    # lo activa explícitamente porque solo provisiona `secrets/tsa/_dev/`.
    tsa_allow_dev_seal: bool = False

    # --- XAdES-LT/LTA: material de validación a largo plazo (E06, RT-16) ---
    # Kill-switch: `False` por defecto (comportamiento idéntico a antes de este
    # incremento — la firma se queda en XAdES-T). En `True`, `LocalSigner.sign`
    # intenta escalar a XAdES-LTA cuando el sello institucional del tenant (o
    # `_dev/`) trae también `ca.crt`/`crl.der` (`scripts/gen-dev-ca.sh`) — ver
    # `app.core.keys.load_seal`. LT y LTA se producen SIEMPRE juntos al firmar
    # (nunca se persiste un XML que se quede a medio camino en "solo LT");
    # `verify_xades` sí puede reportar el nivel LT si el `ArchiveTimeStamp` no
    # llega a validar pero el resto del material LT sí.
    signer_lt_enabled: bool = False
    # Con `signer_lt_enabled=True`, este flag decide qué hacer si el tenant NO
    # tiene material CA montado: `False` (default) degrada en silencio-pero-
    # observable a XAdES-T (`firma.lt_downgrade_no_ca` en audit_log, ver
    # `app.routers.signature`); `True` hace fallar `/sign-xml` con 503
    # (`lt_material_required`) — fail-closed para despliegues que exigen
    # conformidad estricta y no toleran ninguna firma sin LT/LTA.
    signer_lt_require: bool = False

    # --- OCSP stapled en XAdES-LT (E06, RF-FIR-15) ---
    # Kill-switch: `False` por defecto (comportamiento idéntico a antes de este
    # incremento — XAdES-LT/LTA sin OCSPValues). En `True`, `LocalSigner.sign`
    # intenta añadir `xades:OCSPValues` (ADITIVO a la CRL, nunca en su lugar)
    # cuando el sello institucional del tenant (o `_dev/`) trae también el par
    # del responder OCSP delegado (`ocsp.crt`/`ocsp.key`,
    # `scripts/gen-dev-ocsp-responder.sh`) — ver `app.core.keys.load_seal`.
    # Solo tiene efecto DENTRO del bloque XAdES-LT (requiere además
    # `signer_lt_enabled=True` y material `ca.crt`/`crl.der` montado): OCSP es
    # material de validación a largo plazo, igual que la CRL que complementa.
    signer_ocsp_enabled: bool = False
    # Con `signer_ocsp_enabled=True`, este flag decide qué hacer si el tenant
    # NO tiene material de responder OCSP montado: `False` (default) degrada
    # en silencio-pero-observable (`firma.ocsp_downgrade_no_responder` en
    # audit_log, ver `app.routers.signature`) — la firma XAdES-LT/LTA se
    # produce igual, solo sin OCSPValues; `True` hace fallar `/sign-xml` con
    # 503 (`ocsp_material_required`) — fail-closed para despliegues que
    # exigen OCSP stapled sin excepción.
    signer_ocsp_require: bool = False
    # Passphrase opcional de la clave privada del responder OCSP (vacío = sin
    # cifrar, el caso de scripts/gen-dev-ocsp-responder.sh). NUNCA commitear
    # el valor real.
    signer_ocsp_key_password: str = ""
    # Vigencia (`nextUpdate - thisUpdate`, en días) de cada respuesta OCSP
    # producida por `app.core.ocsp.build_ocsp_response` — corta a propósito
    # (material de desarrollo, sin responder acreditado real detrás).
    signer_ocsp_validity_days: int = Field(default=7, ge=1)

    # --- Reconciliación del sellado del índice (residual Ac. AGN 001/2024
    # art. 4.3.2.4) --- Token compartido servicio-a-servicio (D-02, mismo
    # patrón que `storage_internal_token`) que protege
    # `POST /internal/sign-indice` (`app/core/internal_auth.py`,
    # `app/routers/signature.py`): archive-service lo usa desde su job de
    # reconciliación (`app/jobs/indice_reconciliation.py`) para pedir el
    # sellado del índice ATRIBUIDO A SISTEMA (`SYSTEM_RECONCILER_ID`,
    # `app/core/constants.py`), sin `X-User-Id`. Mismo secreto que
    # `archive-service.SIGNATURE_INTERNAL_TOKEN`. Vacío (default) hace que la
    # ruta responda 503 fail-closed (mal-configurada) en vez de aceptar
    # cualquier token — NUNCA commitear el valor real.
    signature_internal_token: str = ""

    # --- Firma electrónica PERSONAL PKI por-usuario (E17/F4, Incremento 1) ---
    # Custodia SERVIDOR (postura B, auth-service — app/core/signing_keys.py),
    # marco honesto "no acreditado" (mismo nivel legal art. 7 Ley 527/1999
    # que la firma electrónica simple). CadenaFirmaService.firmar() consulta
    # GET /internal/signing-key/status (auth-service) para decidir entre el
    # camino xades_personal (cert activo) y el fallback nativo HMAC — ver
    # app.core.http_clients.sign_digest_remote / get_signing_key_status_remote.
    # Mismo secreto que auth-service.SIGNING_KEY_INTERNAL_TOKEN (D-02).
    signing_key_internal_token: str = ""
    # Timeout agresivo para las llamadas a /internal/signing-key/* — mismo
    # criterio fail-closed que TOTP_VERIFY_TIMEOUT_SECONDS: cualquier fallo
    # de red/timeout se traduce en 503 canal_2fa_no_disponible, y el actor
    # NUNCA cae en silencio al camino nativo por un simple timeout (eso solo
    # ocurre cuando GET /status responde limpiamente "no_activo"/"revocado").
    signing_key_sign_timeout_seconds: float = 5.0
    # --- Gate XAdES de la firma personal (E17/F4, Incremento 3 — cierra Baja #3 del Inc.2) ---
    # AHORA ES UN PARÁMETRO DE LA ENTIDAD (2026-10-04): la política la lee
    # `app.core.params_entidad.modo_xades_de_la_entidad` de `sgd_param_admin`
    # (clave `firma_personal_require_xades`, sembrada en "off" por la migración
    # 010 de tenant-service). Este valor del entorno es solo el RESPALDO de la
    # entidad que no define la clave.
    # Controla si un certificado personal ausente o inaccesible puede caer a HMAC
    # nativa (comportamiento histórico) o debe RECHAZAR el acto. Tres valores:
    #
    #   "off"          (default) — fail-open a nativa: actor sin cert -> HMAC; auth
    #                  caído -> HMAC. Comportamiento byte-idéntico a Inc.1/2. NO sube
    #                  el aseguramiento de no-repudio; la firma clasificada puede
    #                  degradar en silencio. Postura out-of-the-box.
    #   "clasificados" — exige XAdES personal para nivel_seguridad>=2 (RESERVADA/
    #                  CLASIFICADA): actor sin cert -> 422 firma_xades_requerida;
    #                  auth caído -> 503 canal_firma_personal_no_disponible
    #                  (fail-closed). nivel 1 (PÚBLICA) conserva el fallback nativo.
    #                  RECOMENDADO **una vez completo el enrolamiento de los firmantes
    #                  con clearance>=2**. OJO: la emisión del cert es OFFLINE por-usuario
    #                  (NO autoservicio, a diferencia del 2FA cuyo nivel>=2 SÍ es
    #                  incondicional, ver firma_2fa_required) — habilitarlo antes de
    #                  tener enrolados BLOQUEA la firma clasificada de quien no tenga cert.
    #   "todos"        — exige XAdES personal para TODA firma de la cadena, sin fallback
    #                  HMAC en ningún nivel.
    #
    # Retrocompat: el env FIRMA_PERSONAL_REQUIRE_XADES admite el bool histórico —
    # false/0/no/"" -> "off"; true/1/yes -> "todos". Un valor no reconocido ABORTA
    # el arranque (fail-fast), nunca cae a "off" en silencio.
    firma_personal_require_xades: Literal["off", "clasificados", "todos"] = "off"

    @field_validator("firma_personal_require_xades", mode="before")
    @classmethod
    def _coerce_firma_personal_require_xades(cls, v: object) -> str:
        if isinstance(v, bool):
            return "todos" if v else "off"
        if v is None:
            return "off"
        s = str(v).strip().lower()
        if s in ("false", "0", "no", ""):
            return "off"
        if s in ("true", "1", "yes"):
            return "todos"
        if s in ("off", "clasificados", "todos"):
            return s
        raise ValueError(
            "firma_personal_require_xades debe ser uno de 'off'/'clasificados'/"
            f"'todos' (o un bool legacy true/false), recibido {v!r}"
        )

    # --- Revocación de la firma personal (E17/F4, Incremento 2 — A2) ---
    # Fail-closed (default True): si GET /internal/signing-key/revocation-status
    # (auth-service) no responde o falla, /verify reporta `valida=False`,
    # `motivo="revocacion_no_verificable"` para firmas `xades_personal` — la
    # verificación NUNCA asume "vigente" ante un canal caído. `False`
    # (opt-in fail-open, NO recomendado) degrada a `revocacion_status=None`
    # sin afectar `valida` — reservado para entornos donde auth-service es
    # sabidamente inalcanzable pero se quiere seguir viendo la validez
    # criptográfica pura.
    #
    # RIESGO (Baja de auditoría #2, Incremento 2): `False` (fail-open) es
    # SOLO ante caída del canal — pero mientras auth-service esté
    # inalcanzable, un certificado REVOCADO se reportará `valida=True`
    # (el chequeo de revocación se omite, no falla; la validez criptográfica
    # pura no distingue "revocado" de "vigente"). Ponerlo en `False` implica
    # aceptar esa ventana a cambio de disponibilidad. NO recomendado para
    # `nivel_seguridad>=2` — ese nivel exige 2FA gate SIEMPRE (ver
    # `firma_2fa_required` arriba) y debería exigir igual certeza de
    # revocación.
    verify_personal_revocation_require: bool = True


#: Variables de entorno que ya NO tienen efecto, con la que las sustituye. Una
#: variable retirada que sigue puesta engaña: el operador cree estar exigiendo
#: algo que el servicio ya no lee.
VARIABLES_RETIRADAS: dict[str, str] = {
    "TSA_ESSCERTID_OBLIGATORIO": "TSA_ESSCERTID_CORTE",
}


def variables_retiradas_presentes(environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """(variable_retirada, sustituta) de cada una que aparece en el entorno."""
    env = os.environ if environ is None else environ
    return [(v, nueva) for v, nueva in VARIABLES_RETIRADAS.items() if v in env]


settings = Settings()
