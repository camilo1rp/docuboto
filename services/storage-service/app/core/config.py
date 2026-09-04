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
    # razonable para documentos administrativos digitalizados. También es el
    # perfil usado en INGESTA (ver `preservation_pdfa_ingest_mode` abajo): en
    # captura no se conoce todavía serie/expediente, así que no hay perfil
    # por-serie que aplicar (E10 C2b follow-up, `app/services/pdfa_ingest_service.py`).
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
    # subprocess, `app/services/pdfa/subprocess_verapdf.py`). `verapdf_binary`
    # DEBE ser una ruta absoluta a un ejecutable existente para que la
    # factory (`app/services/pdfa/factory.py`) active el validador real —
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
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @property
    def allowed_mime_set(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.allowed_mime_types.split(",") if m.strip())

    @property
    def allowed_system_mime_set(self) -> frozenset[str]:
        return frozenset(
            m.strip() for m in self.allowed_system_mime_types.split(",") if m.strip()
        )


settings = Settings()
