# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La configuración del proceso (ADR-047): una clase `Settings` por familia.

Había nueve `core/config.py`, uno por servicio, con la base de datos, el pool,
Redis y el emisor de identidad repetidos en cada uno. Aquí `CommonSettings` lleva lo común y
cada dominio tiene su `Settings` con lo suyo; las instancias se llaman como el
dominio (`docuboto.config.archive`, `docuboto.config.storage`, …).

Los nombres de las variables de entorno no cambian (son el contrato con quien
despliega): la familia es la clase, no un prefijo nuevo que obligaría a
renombrar cada variable en los manifiestos y en `infra/.env.example`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from orpycamcp_common import kek as _kek
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    """Lo que comparten todas las familias: base de datos, pool, Redis, log."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"
    # Presupuesto de conexiones del proceso (un pool; ver `infra/docker-compose.prod.yml`).
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    # Tope por CONSULTA: sin él, una consulta atascada retiene su conexión
    # indefinidamente y el pool se agota sin que nada lo delate.
    db_command_timeout: float = 120.0
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "info"

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


common = CommonSettings()


def _decode_aes256(b64: str, label: str) -> bytes:
    """Decodifica un base64 a exactamente 32 bytes (AES-256) o lanza
    ``RuntimeError`` explícito — criterio único compartido por todas las claves
    AES del servicio. Vive en ``orpycamcp_common.kek`` (mismo esquema que usa
    notification-service para el secreto de los webhooks)."""
    return _kek.decode_aes256(b64, label)


class AuthSettings(CommonSettings):

    # --- Emisor de identidad (ADR-048) ---
    # `iss` de todo JWT que firma el proceso y base de las URL del descubrimiento
    # OIDC. Es la URL pública del API (`https://{API_PUBLIC_HOSTNAME}` en prod):
    # el proveedor PPI la declara como emisor y baja de ahí la JWKS.
    identity_issuer: str = "http://localhost:8000"
    # `aud` de los tokens de persona. Un token de cuenta de servicio lleva el
    # audience de su fila (p. ej. el del proveedor) y no entra a este API.
    identity_user_audience: str = "docuboto"
    # AES-256 (32 bytes, base64) que sella la clave privada de firma de los JWT en
    # `public.auth_jwt_keys`. Vacía: el proceso no emite ni verifica tokens
    # (fail-closed); nunca se cae a una clave implícita.
    identity_key_encryption_key: str = ""
    # Vidas: access 15 min; refresh 12 h absolutas desde el inicio de sesión (la
    # rotación no las alarga); token de servicio 5 min.
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 43200
    service_token_ttl_s: int = 300
    # Tras rotar, la clave anterior sigue en la JWKS este tiempo: más que la vida
    # del token más largo firmado con ella.
    jwt_key_overlap_s: int = 3600

    # --- Política de contraseñas (la que tenía el realm) ---
    password_min_length: int = 12
    password_history: int = 5
    login_max_failed: int = 5
    login_lockout_seconds: int = 900

    @property
    def identity_key_bytes(self) -> bytes:
        if not self.identity_key_encryption_key:
            raise RuntimeError(
                "IDENTITY_KEY_ENCRYPTION_KEY no configurada: el proceso no firma tokens"
            )
        return _decode_aes256(self.identity_key_encryption_key, "IDENTITY_KEY_ENCRYPTION_KEY")

    # --- TOTP (RFC 6238, E06) — 2FA para la firma personal, junto a auth_users ---
    # Clave AES-256 (32 bytes, base64) para cifrar en reposo el secreto TOTP
    # (auth/core/totp.py, AESGCM). Vacía en dev por defecto: enroll falla
    # explícitamente (ver totp_encryption_key_bytes) hasta configurarla — el
    # secreto TOTP NUNCA se cifra con un valor implícito/derivado. Generar con
    # p. ej. `python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`.
    totp_encryption_key: str = ""
    # Label ``issuer`` embebido en el otpauth:// URI (apps autenticadoras lo
    # muestran junto al nombre de cuenta).
    totp_issuer: str = "OrpycaMCP"
    # Máximo de verificaciones fallidas (activate + /internal/totp/verify
    # comparten el mismo contador de la fila) antes de bloquear con locked_until.
    totp_max_failed_attempts: int = 5
    # Duración del bloqueo tras agotar totp_max_failed_attempts.
    totp_lockout_seconds: int = 900
    # Ventana de tolerancia de reloj (RFC 6238): acepta timesteps en
    # [T-skew, T+skew]. 1 = ±30s con periodo=30s.
    totp_skew_steps: int = 1
    # (Sin `totp_requerido_por_nivel`: era una clave reservada que nadie leía. El
    # forzado de TOTP por nivel vive en signature-service, que consulta
    # `GET /internal/totp/status` y rechaza el OTP-correo cuando el actor ya
    # tiene TOTP activo en un acto de nivel >= 2 — AREA-04-02.)

    # --- Firma electrónica PERSONAL PKI por-usuario (E17/F4, Incremento 1) ---
    # Clave AES-256 (32 bytes, base64) para cifrar en reposo la clave privada
    # de firma personal (auth/core/signing_keys.py, AESGCM) — SEPARADA de
    # totp_encryption_key (secreto distinto, propósito distinto) y del
    # signature_secret HMAC de signature-service (servicio distinto). Vacía en
    # dev por defecto: enroll/sign-digest fallan explícitamente (fail-closed,
    # ver signing_key_encryption_key_bytes) hasta configurarla.
    signing_key_encryption_key: str = ""
    # --- Rotación de la KEK de custodia (E17/F4) --- Llavero de KEKs
    # ADICIONALES (versiones >= 2). `signing_key_encryption_key` (arriba) es
    # SIEMPRE la versión 1 (congelada, retrocompat byte). Formato: JSON en env
    # con {version: base64-32B}, p. ej. `{"2": "<base64>"}`. NUNCA redefinir la
    # versión 1 aquí (reservada al campo legacy). Cada valor se valida a 32
    # bytes AES-256 fail-closed (ver signing_key_keyring).
    signing_key_encryption_keys: dict[int, str] = {}
    # Versión de KEK que cifra las ESCRITURAS nuevas (enroll) y a la que el
    # re-wrap converge. Default 1 = comportamiento actual exacto. Para rotar:
    # añadir la KEK v2 al llavero, fijar esto a 2, y correr el job de re-wrap.
    signing_key_active_version: int = 1
    # Ventana de retención (días) tras la cual el job de ops purga el MATERIAL
    # privado de una credencial REVOCADA (E17/F4). Solo se anula la privada
    # (irreversible); lo público queda para verificación. Colchón operacional
    # contra revocaciones masivas erróneas, NO reversibilidad criptográfica
    # (imposible por diseño: re-enroll genera par nuevo).
    signing_key_purge_after_days: int = Field(default=30, ge=1)
    # Job periódico de mantenimiento de claves de firma (purga de revocadas
    # vencidas + re-wrap a la KEK activa). Kill-switch e intervalo; el job es
    # fail-safe: sin auditoría disponible no muta, sin llavero válido no re-envuelve.
    signing_key_maintenance_enabled: bool = True
    signing_key_maintenance_interval_seconds: int = Field(default=21600, ge=60)
    # --- Anclaje de la sub-CA de usuarios (E17/F4, Incremento 2 — cierra la
    # Baja #4) --- Ruta al certificado PÚBLICO de la sub-CA que emite los
    # certificados de firma personal (``scripts/gen-user-cert.sh`` en
    # producción). Montada READ-ONLY junto al contenedor de auth-service —
    # JAMÁS la ``ca.key`` de ninguna CA (ni la de usuarios ni ninguna otra):
    # este servicio solo necesita el cert público para verificar
    # ``leaf.verify_directly_issued_by(sub_ca)`` en ``activar_certificado``,
    # nunca para firmar/emitir. Vacía por defecto: la activación de un
    # certificado falla explícitamente (503) hasta que se configure — ver
    # ``app.core.signing_keys.load_user_ca_certificate``.
    signing_key_ca_users_cert_path: str = ""

    def signing_key_encryption_key_bytes(self) -> bytes:
        """Decodifica ``signing_key_encryption_key`` (base64) a 32 bytes
        crudos (AES-256) — la KEK **versión 1**. Lanza ``RuntimeError``
        explícito si falta o no mide 32 bytes — nunca cae a una clave derivada
        o implícita (RF-SEG), mismo criterio que :meth:`totp_encryption_key_bytes`."""
        if not self.signing_key_encryption_key:
            raise RuntimeError(
                "signing_key_encryption_key no configurada — requerida para /me/signing-key/*"
            )
        return _decode_aes256(self.signing_key_encryption_key, "signing_key_encryption_key")

    # --- Llavero de KEKs para la rotación de custodia (E17/F4) ---
    # La rotación NO cambia el AAD (tenant+user); solo re-envuelve la privada
    # bajo una KEK nueva. Fail-closed en cada método: nunca se firma ni se
    # arranca un re-wrap con una versión de KEK que no resuelva a 32 bytes.
    def signing_key_keyring(self) -> dict[int, bytes]:
        """Llavero completo {version: 32B}. La v1 sale del campo legacy; las
        v>=2 del JSON ``signing_key_encryption_keys``. Fail-closed por entrada;
        la clave 1 en el JSON está prohibida (reservada al campo legacy)."""
        self.signing_key_encryption_key_bytes()  # v1 ausente => su propio error
        return _kek.build_keyring(
            self.signing_key_encryption_key,
            self.signing_key_encryption_keys,
            v1_label="signing_key_encryption_key",
            extra_label="signing_key_encryption_keys",
        )

    def signing_key_for_version(self, version: int) -> bytes:
        """KEK de una versión concreta (la de la fila al descifrar). Fail-closed
        si esa versión no está cargada — NUNCA se firma con otra KEK."""
        ring = self.signing_key_keyring()
        if version not in ring:
            raise RuntimeError(
                f"KEK versión {version} no cargada (fail-closed) — no se puede "
                "descifrar/firmar esta credencial"
            )
        return ring[version]

    def signing_key_active_key(self) -> tuple[int, bytes]:
        """(versión_activa, KEK_activa) para cifrar escrituras nuevas. Lanza si
        la versión activa no tiene KEK cargada."""
        v = self.signing_key_active_version
        return v, self.signing_key_for_version(v)

    def totp_encryption_key_bytes(self) -> bytes:
        """Decodifica ``totp_encryption_key`` (base64) a 32 bytes crudos (AES-256).

        Lanza ``RuntimeError`` explícito si falta o no mide 32 bytes — nunca cae
        a una clave derivada o implícita (RF-SEG). El caller (TotpService) deja
        que esto se traduzca en un 500 hasta que el operador configure la clave.
        """
        import base64
        import binascii

        if not self.totp_encryption_key:
            raise RuntimeError(
                "totp_encryption_key no configurada — requerida para /me/totp/*"
            )
        try:
            key = base64.b64decode(self.totp_encryption_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("totp_encryption_key no es base64 válido") from exc
        if len(key) != 32:
            raise RuntimeError("totp_encryption_key debe decodificar a 32 bytes (AES-256)")
        return key


auth = AuthSettings()


class TenantSettings(CommonSettings):
    """Sin ajustes propios: lo común basta."""


tenant = TenantSettings()


class DocumentSettings(CommonSettings):

    # --- Barrido de fondo (AREA-09-14 / AREA-09-15) ---
    # Un bucle por réplica: purga los binarios huérfanos de importaciones fallidas
    # que storage no pudo borrar en el momento, reanuda los lotes de radicados
    # interrumpidos (reinicio del servicio) y retira los lotes terminados antiguos.
    # Varias réplicas conviven: cada lote se toma con un advisory lock.
    barrido_enabled: bool = True
    barrido_intervalo_segundos: int = 300
    barrido_retraso_inicial_segundos: int = 20
    # Reintentos de purga de un binario antes de pasarlo a 'agotado' (revisión manual).
    purga_huerfanos_max_intentos: int = 20
    # Un lote 'pending'/'processing' más joven que esto no se reanuda: lo está
    # arrancando su propia petición.
    # B10: tope de callbacks postales en cola por tenant mientras la entidad está suspendida;
    # pasado el tope se vuelve al 503 + Retry-After (contrapresión hacia el operador).
    postal_diferidos_max: int = 10000
    # B10: intentos del drenaje antes de descartar (con asiento) una fila que no se puede
    # aplicar por un error no HTTP que se repite; sin tope bloqueaba la cola entera del tenant.
    postal_diferido_max_intentos: int = 10
    batch_reanudar_despues_segundos: int = 120
    # Retención de lotes COMPLETADOS sin errores (0 = no se purgan). Los que
    # terminaron con errores se conservan: alguien tiene que revisarlos.
    batch_retencion_dias: int = 90

    # Zona horaria con la que se decide el AÑO del número de radicado y la fecha
    # base del plazo de respuesta.
    #
    # Se tomaba en UTC. Colombia es UTC-5 todo el año, así que entre las 19:00 y
    # las 23:59 del 31 de diciembre el servidor ya estaba en el año siguiente: un
    # radicado con fecha impresa 31/12/2026 recibía el número 2027-…-000001. El
    # número y la fecha del acuse se contradicen, y el consecutivo del año que
    # cierra queda cortado antes de tiempo — sobre un número que es inmutable por
    # Ley 594.
    #
    # Es del DESPLIEGUE, no de la entidad, y se dice aquí para no repetir el
    # error de declarar que algo «lo decide la entidad» y leerlo de una variable
    # de entorno. Cuando el registro de entidades guarde su propia zona, este
    # valor pasa a ser el respaldo.
    tz_radicacion: str = "America/Bogota"

    # --- E19: cuentas de correo en base de datos ---
    #
    # Hubo una ruta heredada —las variables `IMAP_*`— que configuraba UNA sola
    # cuenta para todo el despliegue: no expresaba lo que exige el Acuerdo AGN
    # 060/2001 art. 13 ("asignarán responsabilidades de acuerdo con la cantidad
    # de cuentas habilitadas") y dejaba la contraseña en claro en el entorno.
    # Retirada: las cuentas viven en `email_cuenta`, una por buzón, con la
    # credencial cifrada y su responsable declarado.
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
    # Entidad cuyos buzones sondea este proceso. Vacío = fail-closed: sin
    # declararlo el worker no arranca, porque un sondeo que adivinara la entidad
    # radicaría en el archivo equivocado.
    email_sondeo_tenant_slug: str = ""

    # Interoperabilidad pública ANÓNIMA (E11): OAI-PMH y CMIS bajo /api/v1/public/,
    # que el gateway declara público sin autenticación.
    #
    # **False por defecto, y no es una precaución genérica.** El único recorte que
    # aplican esos endpoints es `nivel_seguridad = 1`, y el nivel 1 es el DEFAULT
    # de la columna y del schema de entrada: la reserva depende de
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

    # --- Antirrobots de la ventanilla pública (C16, ADR-038) ---
    #
    # Clave HMAC con la que el servidor firma los retos ALTCHA que entrega a la
    # ventanilla anónima. Es del DESPLIEGUE, no de la entidad (la entidad va atada
    # en el `salt` del reto). Nunca llega al navegador. Vacía = el servicio NO
    # arranca (`main.lifespan`); rotarla solo invalida los retos vivos (<= TTL).
    # Generar con `openssl rand -hex 32`. NUNCA commitear el valor real.
    altcha_hmac_key: str = ""
    # Vigencia de un reto: lo que tarda una persona en llenar el formulario más la
    # resolución. Pasado el tiempo el widget pide otro solo.
    altcha_ttl_segundos: int = Field(default=300, ge=60, le=900)
    # Tope de la búsqueda del cliente (SHA-256 x ~maxnumber/2): costo del anónimo.
    altcha_maxnumber: int = Field(default=100_000, ge=1_000, le=1_000_000)
    # Holgura de reloj entre réplicas al fijar el TTL del registro de «reto usado».
    altcha_skew_segundos: int = Field(default=60, ge=0, le=300)


document = DocumentSettings()


class ArchiveSettings(CommonSettings):

    # --- Reconciliación del sellado del índice electrónico (residual Ac.
    # AGN 001/2024 art. 4.3.2.4) — ver app/jobs/indice_reconciliation.py ---
    # Kill-switch: True (default) arranca el loop de reconciliación en el
    # lifespan de archive/main.py.
    # ── Alerta de disposición vencida (E04, RF-TRD-06 R.4.8 / RF-TRD-08) ──
    # «Alerta a usuarios autorizados cuando vence la fecha de confirmación sin
    # confirmar». El canal es el CORREO y no una campana in-app: E16 no tiene
    # canal persistente —el store del frontend son toasts efímeros— y un toast
    # no es una alerta, porque solo lo ve quien ya estaba mirando, que es
    # justo a quien la alerta no va dirigida.
    disposicion_alerta_enabled: bool = True
    # El intervalo del bucle ES la cadencia del aviso, a propósito: así no hay
    # un «último enviado» por tenant que mantener sincronizado, y el aviso se
    # repite mientras el vencimiento siga sin decidir. Semanal por defecto --
    # un vencimiento no caduca, y un correo diario sobre lo mismo se convierte
    # en ruido que se filtra a la papelera, que es peor que no avisar.
    disposicion_alerta_interval_seconds: int = 604800
    # Días que un vencimiento puede llevar sin decisión antes de que se avise.
    # No es un plazo legal: es el margen para que la ventanilla trabaje sin que
    # el sistema le respire encima.
    disposicion_alerta_dias_sin_decidir: int = 30

    indice_reconciliation_enabled: bool = True
    # Cadencia (segundos) entre barridas del loop.
    indice_reconciliation_interval_seconds: int = 300
    # Índices 'pendiente_firma' procesados como máximo por tenant y barrida.
    indice_reconciliation_batch_size: int = 50
    # Gracia antes de que el barrido trate un sobre de revelación sin referencia como huérfano
    # (cubre la ventana entre subirlo y confirmar la fila).
    sobre_huerfano_gracia_horas: int = 1
    # Reintentos del job antes de marcar `reconcile_agotado=TRUE` (permanece
    # 'pendiente_firma', requiere intervención manual — POST /indice/firmar).
    indice_reconciliation_max_intentos: int = 8
    # Backoff exponencial: `min(cap, base * 2**intentos)`.
    indice_reconciliation_backoff_base_seconds: int = 60
    indice_reconciliation_backoff_cap_seconds: int = 86400
    # Umbral (días desde hoy) bajo el cual el calendario de un FONDO ACUMULADO se
    # considera vencido (ADR-043, enmienda de ADR-026 D4.4): si `fecha_extrema_final +
    # total < hoy + margen` el contenido NO recibe COMPLIANCE sino el hold
    # `valoracion_pendiente`. Ya no es un clamp de `retain_until`. Debe SUPERAR el piso
    # de storage (`PRESERVATION_MIN_RETENTION_DAYS`, 365): con exactamente el piso, un
    # día de bisiesto en la aritmética de años dejaría una retención natural por debajo.
    worm_piso_margen_dias: int = 400
    # Valoración de la TVD (ADR-043): expedientes cuyo hold `valoracion_pendiente` se
    # levanta de forma síncrona al registrar el acto; el resto lo aplica la fase
    # `run_once_valoracion_holds` en lotes de `valoracion_hold_batch_size` por barrida.
    valoracion_sync_apply_max: int = 25
    valoracion_hold_batch_size: int = 50
    # Intento síncrono ACOTADO en el cierre (`IndexService.firmar_indice_close`)
    # antes de degradar a 'pendiente_firma' por fallo transitorio — un `seal_absent`
    # degrada de inmediato SIN consumir estos reintentos (ver el docstring del
    # método). 2 intentos × ~1s es el tope por diseño: el cierre del expediente
    # no debe alargarse peligrosamente por este control compensatorio.
    close_sign_max_sync_attempts: int = 2
    close_sign_sync_delay_seconds: float = 1.0

    # Interoperabilidad pública ANÓNIMA (E11 INT-06): OAI-PMH multinivel de
    # expedientes bajo /api/v1/public/archive/, sin autenticación.
    #
    # False por defecto, mismo criterio y mismo nombre que en document-service —
    # deliberadamente el MISMO nombre de variable, para que una entidad que decide
    # publicar lo active en los dos servicios con el mismo valor y no se quede a
    # medias sin darse cuenta. Aquí el recorte también es `nivel_seguridad = 1`,
    # que es el default de la columna.
    public_interop_enabled: bool = False

    # --- Firma XAdES del acta de transferencia (H-H, E06/F4) ---
    # Kill-switch del loop de reconciliación DEDICADO del acta (segunda fase
    # de app/jobs/indice_reconciliation.py) -- independiente de
    # `indice_reconciliation_enabled`.
    acta_reconciliation_enabled: bool = True
    # Cadencia (segundos) entre barridas del loop del acta -- MAYOR que la
    # del índice por diseño (900s vs 300s): el sellado del acta es aditivo
    # (el acta ya es íntegra y su SHA está anclado en audit_log desde
    # `recibir()`), menos urgente que el índice electrónico.
    acta_reconciliation_interval_seconds: int = 900
    # Intento síncrono ACOTADO al recibir la transferencia
    # (`TransferenciaService._sellar_acta_post_commit`) antes de degradar a
    # 'pendiente_firma' -- mismo criterio que `close_sign_max_sync_attempts`
    # (un `seal_absent` degrada de inmediato SIN consumir estos reintentos).
    acta_sign_max_sync_attempts: int = 2
    acta_sign_sync_delay_seconds: float = 1.0

    # --- Firma PERSONAL PKI por-usuario del acta (E17/F4, Incremento 1) ---
    # Gate de CONFORMIDAD (nunca bloquea `recibir()`, ver
    # `TransferenciaService.firmar_personal`/`acta_verificacion`): decide si
    # la ausencia de la firma personal del receptor degrada la
    # `completitud` reportada por `GET /acta/verificacion` a
    # `firma_personal_pendiente` -- espeja `firma_personal_require_xades`
    # de signature-service (mismos tres valores, mismo criterio por
    # `nivel_seguridad` del expediente).
    #   "clasificados" (default, ADR-054 -- el mismo valor con que la cadena
    #                  de migraciones siembra la clave de la entidad) -- exige
    #                  la firma del receptor para expedientes
    #                  nivel_seguridad>=2 (RESERVADA/CLASIFICADA); nivel 1
    #                  (PÚBLICA) conserva 'sellada' como suficiente.
    #   "off"          -- la firma personal es SIEMPRE opcional; con el sello
    #                  institucional solo, `completitud` queda en 'sellada'.
    #   "todos"        -- exige la firma del receptor para TODA transferencia.
    # Es solo el RESPALDO de la entidad que no define la clave (ver
    # `archive.core.params_entidad`).
    acta_firma_personal_require: Literal["off", "clasificados", "todos"] = "clasificados"

    # --- Renovación de retención WORM para series CT (E10 debt) ---
    # Ventana de antelación (días) antes del vencimiento de `worm_retain_until`
    # en la que un objeto de serie CT se vuelve elegible para renovación (ver
    # `IndexRepository.list_worm_renovacion_pending`/`app/jobs/
    # indice_reconciliation.py::run_once_worm_renovacion`) -- ventana rodante,
    # `nueva = now() + total_TRD`, NUNCA una fecha lejana/9999. Debe superar
    # holgadamente el intervalo de barrida + horizonte de backoff de
    # `indice_reconciliation_*` (reutilizados sin duplicar, ver ese job) o el
    # lock COMPLIANCE podría vencer antes de que una renovación tenga éxito.
    worm_renovacion_ventana_dias: int = 400

    # --- AIP índice-only para expedientes 100% físicos (E10 AIP wiring
    # follow-up) --- Kill-switch: True (default) -- cuando un expediente no
    # tiene NINGÚN anexo digital vinculado, `AipWiringService.
    # intentar_empaquetar` empaqueta igual un AIP OAIS cuyo único objeto
    # sustantivo es el índice electrónico firmado (`documentos=[]`,
    # `indice_file_id` -- ver `app.services.aip_builder._is_indice_only_mode`
    # en storage-service). `False` restaura el comportamiento previo
    # (`estado='omitido'`, TERMINAL, sin AIP) -- ventana de transición/seguro
    # operacional para un consumidor que trate `omitido` como "sin AIP".
    aip_indice_only_enabled: bool = True

    # --- C-6 (ADR-042): reempaquetado del AIP al renovar el sello de archivo ---
    # Cadencia de la fase `run_once_aip_reempaquetado` (barrido contra el XML vigente de
    # signature-service) y del repaso de cada AIP sin cambios. `0` apaga la fase.
    aip_reempaquetado_intervalo_s: int = 86400



archive = ArchiveSettings()


def _same_endpoint(a: str, b: str) -> bool:
    """¿Dos endpoints de almacén son el mismo host? Normaliza esquema, mayúsculas y
    barra final: `http://MinIO:9000/` y `minio:9000` son el mismo destino."""

    def _norm(e: str) -> str:
        e = e.strip().lower()
        for pref in ("https://", "http://"):
            if e.startswith(pref):
                e = e[len(pref) :]
        return e.rstrip("/")

    return _norm(a) == _norm(b)


# Modos de retención WORM válidos (espejo de miniopy_async.commonconfig.{GOVERNANCE,COMPLIANCE}).
_VALID_RETENTION_MODES = frozenset({"GOVERNANCE", "COMPLIANCE"})


class StorageSettings(CommonSettings):

    minio_endpoint: str = "minio:9000"
    # Credenciales de MinIO. Default VACÍO a propósito: las anteriores
    # (`orpycamcp` / `orpycamcp_dev`) son las de desarrollo publicadas en este
    # repositorio AGPL y en el propio CLAUDE.md, y un despliegue que no las
    # redefiniera arrancaba con ellas. El arranque las exige — ver `main.py` y
    # `orpycamcp_common.secrets`. `docker-compose.yml` de desarrollo las define
    # explícitamente, así que el flujo local no cambia.
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_use_ssl: bool = False
    presigned_url_expiry: int = 3600  # seconds

    # Entorno de despliegue ("development" | "production"); no hay indicador común
    # en el proyecto, así que storage-service define el suyo (autocontenido). Solo
    # gatea el guard de arranque de preservación WORM (ver `preservation_startup_guard`).
    app_env: str = "development"

    # Export de expediente (E02/export): el ZIP se arma en un fichero temporal en
    # DISCO y se sirve por trozos (B4-5); nunca entero en memoria. Tope en bytes
    # del paquete (declarado y real) y reserva mínima de disco que debe quedar
    # libre además del paquete. `export_tmp_dir` vacío = directorio temporal del SO.
    export_max_bytes: int = 4 * 1024 * 1024 * 1024
    export_min_free_bytes: int = 256 * 1024 * 1024
    export_tmp_dir: str = ""

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
    # `storage/services/pdfa/stub.py`) | "verapdf" (E10 Increment C2b, roadmap —
    # hoy degrada a stub con warning, `storage/services/pdfa/factory.py`).
    pdfa_validator: str = "stub"
    # Perfil PDF/A objetivo: POLÍTICA DECLARADA de la institución (no un
    # detalle implícito del validador). PDF/A-2b (ISO 19005-2) es el default
    # razonable para documentos administrativos digitalizados. También es el
    # perfil usado en INGESTA (ver `preservation_pdfa_ingest_mode` abajo): en
    # captura no se conoce todavía serie/expediente, así que no hay perfil
    # por-serie que aplicar (E10 C2b follow-up, `storage/services/pdfa_ingest_service.py`).
    pdfa_profile: str = "2b"
    # Validación PDF/A en el flujo de INGESTA/captura de anexos (E07, E10
    # Increment C2b follow-up) -- distinto del empaquetado del AIP (que
    # siempre valida, si el gate `preservation_pdfa_validation_enabled` está
    # activo). "advisory" (default): la validación corre ASÍNCRONA
    # best-effort DESPUÉS de responder al cliente (nunca bloquea la
    # radicación -- PDF/A es requisito de PRESERVACIÓN, Ley 594/2000 +
    # Acuerdo AGN 060/2001, no de radicación). "strict" (opt-in): la
    # validación corre SÍNCRONA en el propio `POST /upload` y un
    # `non_conformant` rechaza la subida con 422 -- quien activa este modo
    # asume el coste JVM/subprocess en el hot path. Ambos modos quedan
    # subordinados al gate maestro `preservation_pdfa_validation_enabled`
    # (False -> sin validación en ingesta en absoluto, cualquiera sea el modo).
    preservation_pdfa_ingest_mode: str = "advisory"
    # E10 Increment C2b: `SubprocessVeraPdfValidator` real (veraPDF vía
    # subprocess, `storage/services/pdfa/subprocess_verapdf.py`). `verapdf_binary`
    # DEBE ser una ruta absoluta a un ejecutable existente para que la
    # factory (`storage/services/pdfa/factory.py`) active el validador real —
    # si no, degrada honestamente a stub (dev slim sin Java, ver Dockerfile).
    verapdf_binary: str | None = None
    verapdf_timeout_seconds: int = 60
    verapdf_max_concurrency: int = 1
    verapdf_jvm_max_heap_mb: int = 512
    # `-Xmx` acota solo el heap Java -- metaspace/native/thread-stacks quedan
    # fuera y pueden hacer que el RSS real de la JVM exceda el heap. Esto NO
    # reemplaza el límite de memoria del contenedor (deploy): es un segundo
    # cinturón barato, no la garantía dura.
    verapdf_jvm_max_metaspace_mb: int = 256

    # ── Migración de formato contra la obsolescencia (E10, RF-PRE-04) ──────
    # "none" (default) | "ghostscript". **No hay opción "stub" y es
    # deliberado**: un validador puede declarar honestamente «no evaluado»,
    # pero un migrador de mentira tendría que PRODUCIR un archivo, y el único
    # que puede producir sin la herramienta es uno inventado. Sin convertidor,
    # el endpoint responde 503 en vez de fabricar una copia de preservación.
    format_migrator: str = "none"
    # Ruta ABSOLUTA al binario de Ghostscript. Misma validación que
    # `verapdf_binary`: si no apunta a un ejecutable real, la factory devuelve
    # `None` y no hay migración en este despliegue.
    ghostscript_binary: str | None = None
    ghostscript_timeout_seconds: int = 120
    ghostscript_max_concurrency: int = 1

    # ── Generación de documentos de salida (E23, RF-PRO-03) ───────────────
    # "none" (default) | "weasyprint". **Tampoco hay opción "stub"**, por la
    # misma razón que en el migrador: un renderizador de mentira tendría que
    # PRODUCIR un documento, y el único que puede producir sin la herramienta es
    # uno inventado. Sin renderizador, el endpoint responde 503 en vez de
    # entregar un PDF vacío o degradar a texto plano fingiendo que hizo un oficio.
    render_engine: str = "none"
    render_timeout_seconds: int = 60
    render_max_concurrency: int = 2
    # Perfil de conservación con que NACE el documento (D-2): se genera PDF/A
    # directamente, no PDF más una migración posterior. El barrido de
    # preservación existe para el fondo heredado, no para lo que la entidad
    # produce hoy.
    render_pdfa_profile: str = "pdf/a-2b"

    # ── Barrido periódico de migración (E10, RF-PRE-04) ────────────────────
    #
    # **La política de obsolescencia la declara la ENTIDAD, no este código.**
    # Qué formato está en riesgo es un juicio archivístico que depende del
    # parque documental de cada institución y de su plan de preservación
    # (PINAR); un barrido que lo decidiera solo estaría convirtiendo el archivo
    # de alguien según una lista adivinada por el fabricante.
    #
    # El sitio propio de esa declaración es el **plan de preservación de cada
    # entidad** (`preservacion_plan.formatos_origen`), que se escribe desde
    # `/admin/preservacion`, queda versionado y dice quién lo declaró. Lo de
    # aquí abajo es solo el **valor por defecto de la instalación**, para las
    # entidades cuyo plan no diga nada: en un despliegue de una sola entidad,
    # quien despliega ES la entidad; en uno compartido, cada entidad lo
    # sustituye declarando su plan. El plan siempre gana.
    #
    # De ahí que la lista venga **VACÍA por defecto**: sin política —ni en el
    # plan ni aquí— el job recorre los tenants y no migra nada. Es el mismo
    # criterio que el barrido de disposición —que cuenta y avisa, pero no
    # elimina—, con una diferencia que conviene tener presente: migrar **no
    # destruye**, el original se conserva intacto (Decreto 2609/2012 art. 32).
    # Lo peligroso aquí no es convertir de más, es CREER que se convirtió.
    format_migration_enabled: bool = False
    format_migration_interval_seconds: int = 86400
    # Valor por defecto de la INSTALACIÓN, para las entidades cuyo plan de
    # preservación no declare `formatos_origen`. Ejemplo de una política
    # razonable, que NO se pone por defecto:
    #   FORMAT_MIGRATION_MIME_TYPES=application/msword,application/vnd.ms-excel
    format_migration_mime_types: str = ""
    # Tope por barrida. Convertir es caro (un subproceso por archivo) y el
    # archivo de una entidad puede tener cientos de miles de ficheros: sin tope,
    # la primera barrida se comería la máquina. Lo que no entra en una barrida
    # entra en la siguiente.
    format_migration_batch_size: int = 50

    @property
    def format_migration_mime_list(self) -> list[str]:
        """El valor por defecto de la instalación, ya troceado.

        Vacía = la instalación no aporta nada; solo migran las entidades que lo
        hayan declarado en su propio plan de preservación.
        """
        return [
            t.strip().lower() for t in self.format_migration_mime_types.split(",") if t.strip()
        ]

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

    @field_validator("preservation_pdfa_ingest_mode")
    @classmethod
    def _validate_pdfa_ingest_mode(cls, v: str) -> str:
        valid = frozenset({"advisory", "strict"})
        lowered = v.lower()
        if lowered not in valid:
            raise ValueError(
                f"preservation_pdfa_ingest_mode debe ser uno de {sorted(valid)}, recibido {v!r}"
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
        # AREA-08-02: el validador PDF/A se pidió real y no hay binario utilizable.
        # La factory degrada a stub («no evaluado») solo con un warning de log; en un
        # entorno que no es de desarrollo eso es un control apagado en silencio, así
        # que `main.py::lifespan` lo trata como FATAL igual que el modo no COMPLIANCE.
        if self.preservation_pdfa_validation_enabled and self.pdfa_validator == "verapdf":
            from storage.services.pdfa.factory import _resolve_verapdf_binary

            if _resolve_verapdf_binary(self) is None:
                warnings.append(
                    "PDFA_VALIDATOR=verapdf con PRESERVATION_PDFA_VALIDATION_ENABLED=true, "
                    f"pero VERAPDF_BINARY={self.verapdf_binary!r} no es un ejecutable real: "
                    "la validación PDF/A degradaría a «no evaluado» sin que nadie lo note."
                )
        # AREA-08-04: un «segundo endpoint» que es el mismo del primario no es una
        # réplica geográfica; se declara aquí en vez de etiquetarla cross-site.
        if (
            self.preservation_replica_enabled
            and self.minio_secondary_endpoint
            and _same_endpoint(self.minio_secondary_endpoint, self.minio_endpoint)
        ):
            warnings.append(
                "MINIO_SECONDARY_ENDPOINT apunta al mismo endpoint que MINIO_ENDPOINT: "
                "la réplica se etiqueta «local» (no cross-site); configure un host distinto "
                "o deje el secundario vacío."
            )
        _prod = self.app_env.strip().lower() not in {
            "development",
            "dev",
            "local",
            "test",
            "testing",
            "ci",
        }
        if _prod and self.preservation_enabled:
            if not (self.minio_secondary_endpoint or "").strip():
                warnings.append(
                    "MINIO_SECONDARY_ENDPOINT vacío: el cubo de preservación tiene que "
                    "vivir fuera de este servidor."
                )
            if not self.preservation_replica_enabled:
                warnings.append(
                    "PRESERVATION_REPLICA_ENABLED=false: la copia WORM no sale del servidor."
                )
        return warnings

    # Tope de tamaño de un anexo subido (`POST /upload`, `POST /files/{id}/replace`).
    #
    # No existía ninguno: `await file.read()` copiaba el fichero entero a memoria
    # —la SEGUNDA copia, después de la del gateway— con `memory: 1G` por réplica.
    # 100 MiB cubre con holgura un expediente escaneado completo y deja el ataque
    # acotado. La importación interoperable, que sí maneja paquetes de cientos de
    # megas, no pasa por aquí: va a `document-service/routers/import_.py`, con su
    # propio tope.
    max_upload_bytes: int = 100 * 1024 * 1024

    #: Tope para lo que llega por la VENTANILLA PÚBLICA, muy por debajo del
    #: general. Quien sube por ahí no está autenticado: el límite de 100 MB
    #: pensado para un funcionario convierte a la entidad en almacenamiento
    #: gratuito para cualquiera. 10 MB cubre un PDF escaneado de decenas de
    #: páginas, que es el caso real (una tutela con sus soportes).
    max_public_upload_bytes: int = 10 * 1024 * 1024

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

    # Formatos de los ARTEFACTOS QUE PRODUCE EL SISTEMA, no de los anexos que
    # sube una persona. Son dos listas porque son dos cosas distintas: la de
    # arriba acota lo que entra desde fuera (RF-DIG-04) y esta describe lo que
    # el propio sistema genera y necesita guardar — hoy, el XML del índice
    # electrónico firmado.
    #
    # Estaban fundidas en una sola, y el efecto era que el índice firmado NO SE
    # PODÍA ALMACENAR: `signature-service` lo subía por el endpoint de anexos y
    # storage lo rechazaba con 415 «Formato no admitido: application/xml». La
    # firma XAdES se producía entera —con su sello de tiempo— y se perdía al
    # guardarla; el índice quedaba `pendiente_firma` para siempre y ningún
    # expediente podía cerrarse con índice firmado.
    #
    # La salida NO es añadir `application/xml` a la lista de anexos: eso
    # permitiría a cualquiera subir XML como anexo y abriría una superficie
    # (entidades externas, XXE en cualquier consumidor futuro) que hoy no
    # existe, para resolver un problema que no es de los anexos. Esta lista solo
    # la alcanza el endpoint sellado con `X-Internal-Token`.
    # `message/rfc822` (E19): el mensaje de correo ORIGINAL que se conserva al
    # radicar una comunicación recibida por buzón. La Ley 527/1999 art. 12 exige
    # guardarlo «en el formato en que se haya generado, enviado o recibido», con
    # su origen, destino, fecha y hora — datos que viven en las cabeceras y que
    # una transcripción a texto pierde.
    #
    # Va aquí y no en la lista de anexos por la misma razón que el XML: un `.eml`
    # no es un soporte que aporte el ciudadano, es un artefacto que produce el
    # sistema al preservar. Admitirlo como anexo dejaría subir correos completos
    # —con sus adjuntos incrustados— por la puerta que valida formatos de anexo.
    # `application/pdf` (E23): el PDF/A del oficio de salida que produce
    # `/borradores/{id}/radicar`. Es la TERCERA vez que esta lista se queda
    # corta por el mismo motivo — nació para el XML del índice, se amplió para
    # el `.eml` del correo, y cuando la producción documental empezó a generar
    # PDF nadie volvió a mirarla—, y el efecto era el mismo que con el índice:
    # el documento se combinaba y se renderizaba entero, y storage lo rechazaba
    # al guardarlo con 415 «Formato no admitido: application/pdf». Radicar un
    # borrador con documento NO funcionaba, y el error no nombraba a quien lo
    # producía.
    #
    # Lo que salvó la situación fue el ORDEN de `radicar`: renderizar y subir
    # van ANTES de pedir el consecutivo, así que el fallo no gastó ningún
    # número. La garantía escrita para otra cosa —un fallo de render no consume
    # un radicado— cubrió también ésta.
    #
    # No abre superficie: `application/pdf` ya está en la lista de ANEXOS, así
    # que cualquiera podía subir un PDF por la puerta de usuario. Aquí solo se
    # reconoce que el sistema también los produce.
    allowed_system_mime_types: str = "application/xml,message/rfc822,application/pdf"

    @property
    def allowed_mime_set(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.allowed_mime_types.split(",") if m.strip())

    @property
    def allowed_system_mime_set(self) -> frozenset[str]:
        return frozenset(
            m.strip() for m in self.allowed_system_mime_types.split(",") if m.strip()
        )


storage = StorageSettings()


class WorkflowSettings(CommonSettings):

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



workflow = WorkflowSettings()


class NotificationSettings(CommonSettings):

    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from: str = "noreply@orpycamcp.local"
    smtp_max_attempts: int = 3
    smtp_retry_backoff_seconds: float = 1.0

    # Retention job (ADR-021 Fase 4 — MVP-1)
    # How many days to keep rows in notification_processed_events before purging.
    # 7 days is generous: the actual redelivery / reclaim window is minutes.
    # ge=1 guards against a misconfigured 0/negative TTL purging in-flight markers.
    retention_dedup_days: int = Field(default=7, ge=1)
    # How often (in seconds) the worker loop triggers the retention sweep.
    retention_interval_seconds: int = 86400  # 24 h

    # DLQ buffer retention (ADR-021 Fase 4 — MVP-2).
    # How many days to keep Redis DLQ entries before XTRIM MINID pruning.
    # The authoritative copy lives in Postgres (evento_dead_letter + audit_log),
    # so 30 days is a very generous operational buffer.
    retention_dlq_days: int = Field(default=30, ge=1)

    #: Días que se conservan los streams de DOMINIO antes de recortarlos.
    #:
    #: Llevan el asunto y el nombre del ciudadano de cada radicación, así que sin
    #: caducidad son un almacén paralelo de datos personales fuera de la Tabla de
    #: Retención Documental. 30 días es holgado para cualquier consumidor que
    #: funcione, y acota la ventana de exposición a un mes en vez de para siempre.
    #:
    #: Un consumidor detenido más tiempo que esto pierde los eventos recortados:
    #: por eso el valor es generoso y configurable, no un tope de entradas.
    retention_streams_days: int = Field(default=30, ge=1)

    # SSRF de webhooks salientes (hallazgo Media, auditoría de seguridad — no
    # bloqueante). `WebhookCreate.url` (app/schemas/webhook.py) validaba forma,
    # no destino: cualquier admin podía suscribir un webhook a un servicio
    # interno de la red Docker o a un rango no enrutable, dirigiendo 3
    # reintentos × evento contra la propia red. Kill-switch INVERTIDO respecto
    # a los demás flags de este archivo: por defecto (False) la validación es
    # ESTRICTA (bloquea loopback/link-local/privado/nombres de servicio
    # Docker, ver notification/core/webhook_security.py). Ponerlo en True SOLO para
    # desarrollo local (apuntar un webhook a un receptor en la máquina del
    # desarrollador o a MailHog). Producción debe dejarlo en False — fail
    # closed por defecto, no al revés.
    webhook_allow_private_targets: bool = False

    # evento_dead_letter terminal-row retention (ADR-021 Fase 4 — MVP-3, Fase 5 Inc.2).
    # Grace period (days) after resolved_at before a resolved/discarded dead-letter
    # row is purged. 'pending' rows are NEVER purged regardless of age (they await
    # a human decision). ge=1 guards against a misconfigured 0/negative TTL that
    # would widen the purge window to include very recent closures.
    retention_deadletter_days: int = Field(default=90, ge=1)

    # ── Secreto HMAC de los webhooks, cifrado en reposo ───────────────────
    # El secreto con el que se firma cada entrega se guarda cifrado (AES-256-GCM,
    # AAD = tenant + id del webhook) con una KEK de ESTE servicio, que vive en el
    # entorno y NUNCA en la base: quien lea un respaldo, la base o una inyección
    # no puede forjar entregas. Mismo esquema que la custodia de la firma
    # personal de auth-service (`orpycamcp_common.kek`).
    #
    # `webhook_secret_kek` es la versión 1 (base64 de 32 bytes). `..._keks` es un
    # JSON `{"2": "<base64>"}` con las versiones >= 2 (la 1 no se redefine ahí).
    # `..._active_version` es la KEK con la que se cifra lo nuevo y a la que
    # converge `python -m app.ops.rewrap_webhook_secrets`. Vacía = el servicio NO
    # arranca (ver `app.core.webhook_secrets.validate_kek_at_startup`).
    webhook_secret_kek: str = ""
    webhook_secret_keks: dict[int, str] = {}
    webhook_secret_kek_active_version: int = 1



notification = NotificationSettings()


class SignatureSettings(CommonSettings):

    # Proveedor de firma (ADR-016: pluggable). Selección real por endpoint:
    # /sign-xml usa 'xades_local' (XAdES-B real con el sello institucional,
    # ver app/providers/local.py) SOLO cuando objeto_tipo='indice' Y este
    # valor es 'xades_local'; en cualquier otro caso cae a 'nativa' (HMAC de
    # servicio, ver app/providers/native.py) — incluye el kill-switch
    # operativo de fijar SIGNER_PROVIDER=nativa para deshabilitar XAdES sin
    # tocar código (E06, ADR-016, Inc.1).
    signer_provider: str = "xades_local"

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

    # --- Sello institucional XAdES-B (signature/core/keys.py, RT-16, Inc.1) ---
    # Directorio raíz montado como secreto/volumen Docker de solo lectura, con
    # un subdirectorio por tenant: <signer_cert_dir>/<slug>/seal.{crt,key}.
    # Fallback de desarrollo en <signer_cert_dir>/_dev/ (scripts/gen-dev-signing-cert.sh).
    signer_cert_dir: str = "/run/secrets/signing"
    # Passphrase opcional de la clave privada del sello (vacío = sin cifrar,
    # el caso de scripts/gen-dev-signing-cert.sh). NUNCA commitear el valor real.
    signer_key_password: str = ""
    # Kill-switch del fallback de sello compartido `_dev/` (signature/core/keys.py,
    # RT-16). `False` por defecto (fail-closed): sin sello propio montado para
    # el tenant, `load_seal` lanza `SealNotFoundError` → 503 → el índice queda
    # 'pendiente_firma', en vez de firmar con el par de desarrollo compartido
    # entre TODOS los tenants (aislamiento criptográfico roto, aunque siempre
    # `acreditado=False`). El `docker-compose.yml` de desarrollo lo activa
    # explícitamente (`SIGNER_ALLOW_DEV_SEAL=true`) porque solo provisiona
    # `secrets/signing/_dev/`; producción debe dejarlo en `False`.
    signer_allow_dev_seal: bool = False

    # --- Renovación del ArchiveTimeStamp de las firmas XAdES-LTA (B10 §8, app/jobs/renovacion_lta.py)
    # Un sello de archivo vale mientras viva el certificado de su TSA: se renueva ANTES de que
    # caduque. `margen_dias` es la antelación con que se renueva Y el plazo adicional que la TSA
    # nueva debe ofrecer sobre la anterior (sin él se renovaría a diario con la misma TSA).
    lta_renovacion_margen_dias: int = Field(default=90, ge=1)
    # Cadencia del bucle (s). 0 = el job no se arranca (kill-switch).
    lta_renovacion_intervalo_s: int = Field(default=86400, ge=0)
    lta_renovacion_retraso_inicial_s: int = Field(default=120, ge=0)

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
    # /batch para la firma personal de la cadena (signature/services/cadena_service.py
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

    # --- 2FA por TOTP (RFC 6238), elevando el OTP-correo del Inc.2 ---
    # Kill-switch: True (default) permite que POST /sign y POST /batch acepten
    # totp_code como satisfactor del gate 2FA (signature/services/cadena_service.py
    # CadenaFirmaService._gate_2fa selecciona TOTP sobre challenge_id+otp
    # cuando ambos vienen presentes). En False, totp_code se IGNORA por
    # completo (como si no viniera) — el caller cae al camino OTP-correo o,
    # si tampoco lo trae, al 409 2fa_requerido de siempre. NUNCA se traduce
    # en un bypass del gate en sí (firma_2fa_required / nivel_seguridad >= 2
    # siguen mandando, ver firmar()).
    totp_enabled: bool = True

    # --- XAdES-T: sello de tiempo RFC 3161 del índice (E06 Inc.4, RT-16) ---
    # Kill-switch: True (default) exige un SignatureTimeStamp válido para toda
    # firma xades_local — sin TSA disponible, /sign-xml falla (503), igual que
    # `signer_allow_dev_seal=False` hace fallar cerrado la ausencia de sello
    # institucional (ver app/providers/local.py::LocalSigner.sign). En False,
    # /sign-xml produce XAdES-B sin sello de tiempo, como antes del Inc.4
    # (entornos sin TSA montada).
    signer_timestamp_enabled: bool = True
    # URL de una TSA remota RFC 3161. Vacío (default) = TSA LOCAL in-process
    # (signature/core/tsa.py::_build_local_timestamp_token). Con este valor puesto,
    # sign-xml usa el cliente remoto REAL (signature/core/tsa.py::
    # _request_remote_timestamp: POST HTTP, comprueba nonce, EKU y token) y,
    # si la TSA no responde o el token no es conforme, falla explícitamente
    # (TsaUnavailableError → 503) — NUNCA cae en silencio a la TSA local si el
    # operador fijó una URL. Acreditado = `tsa_url` Y `tsa_acreditada`
    # (signature/core/tsa.py::sello_acreditado).
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
    # Certificados PEM de las CA que emiten el certificado de la TSA (B10 R2-3), separados por
    # coma. Sirven para VERIFICAR la firma de la CRL de la TSA anterior cuando el token no trae la
    # CA. Vacío = solo se usa la que viaje en el token; sin CA no se acepta la CRL.
    tsa_ca_pem_paths: str = ""
    tsa_timeout_seconds: float = 10.0
    # Kill-switch (C-6): consultar el OCSP del AIA del certificado de la TSA anterior cuando su CA no
    # publica CRL. Con `false` el comportamiento es el de antes de C-6 (solo CRL: sin ella no se renueva).
    # Solo se apaga si un respondedor se comporta mal; el OCSP es una vía ADITIVA, no sustituye la CRL.
    tsa_ocsp_enabled: bool = True
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
    # --- Sello de la TSA local (signature/core/keys.py::load_tsa_seal, Inc.4) ---
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

    # --- Gate XAdES de la firma personal (E17/F4, Incremento 3; ADR-054 §1) ---
    # ES UN PARÁMETRO DE LA ENTIDAD: la política la lee
    # `signature.core.params_entidad.modo_xades_de_la_entidad` de `sgd_param_admin`
    # (clave `firma_personal_require_xades`, sembrada en "clasificados" por
    # `migrations/tenant/120_sgd_param_admin.sql`). Este valor del entorno es solo
    # el RESPALDO de la entidad cuya fila falta o vale JSON null.
    # Controla si un certificado personal ausente o inaccesible puede caer al HMAC
    # nativo (constancia de firma simple) o debe RECHAZAR el acto. Tres valores:
    #
    #   "clasificados" (default, ADR-054) — exige XAdES personal para
    #                  nivel_seguridad>=2 (RESERVADA/CLASIFICADA): actor sin cert ->
    #                  422 firma_xades_requerida; auth caído -> 503
    #                  canal_firma_personal_no_disponible (fail-closed). Nivel 1
    #                  (PÚBLICA) conserva la constancia simple. Una entidad sin
    #                  certificados enrolados no puede firmar clasificados hasta
    #                  enrolarlos: el fallo se ve el día uno, no en la auditoría.
    #   "off"          — la entidad lo elige EXPLÍCITAMENTE en /admin/parametros
    #                  (queda en admin_audit): actor sin cert -> HMAC también en
    #                  clasificados. No es fiabilidad presumible (Ley 527/1999 art. 7).
    #   "todos"        — exige XAdES personal para TODA firma de la cadena.
    #
    # El env FIRMA_PERSONAL_REQUIRE_XADES admite además false/0/no -> "off" y
    # true/1/yes -> "todos"; vacío -> "clasificados". Un valor no reconocido ABORTA
    # el arranque (fail-fast), nunca cae a "off" en silencio.
    firma_personal_require_xades: Literal["off", "clasificados", "todos"] = "clasificados"

    @field_validator("firma_personal_require_xades", mode="before")
    @classmethod
    def _coerce_firma_personal_require_xades(cls, v: object) -> str:
        if isinstance(v, bool):
            return "todos" if v else "off"
        if v is None:
            return "clasificados"
        s = str(v).strip().lower()
        if s == "":
            return "clasificados"
        if s in ("false", "0", "no"):
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


signature = SignatureSettings()


class KnowledgeSettings(CommonSettings):

    ppi_provider_url: str = ""
    # KEK que abre la clave de seudónimo y la de retorno. Vacía: no hay
    # consulta al proveedor ni declaración.
    intelligence_credential_kek: str = ""
    # Clave pública fijada para comprobar la declaración. Vacía: no se lee.
    ppi_capabilities_public_key: str = ""
    ppi_capabilities_kid: str = ""
    # Origen público de las dos URL de retorno. Vacío: no se declara la conexión.
    ppi_callback_base_url: str = ""


knowledge = KnowledgeSettings()
