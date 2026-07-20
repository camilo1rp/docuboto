# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"
    redis_url: str = "redis://redis:6379/0"
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "orpycamcp"
    # Firma del índice al cierre (E15-F3): llamada a signature-service (E06).
    signature_service_url: str = "http://signature-service:8008"
    # Descarga del índice YA FIRMADO (MinIO, vía storage-service) para la
    # verificación XAdES-B real (E06 Inc.1) — signature_client.verify recibe
    # el XML firmado, no el XML en claro (ver app/clients/storage_client.py).
    storage_service_url: str = "http://storage-service:8005"
    # Token compartido D-02 (remediación no-read-up en anexos/AIP, RF-SEG-08):
    # storage-service exige X-Internal-Token en `GET /files/{id}/download`
    # (descarga del índice YA FIRMADO) y, desde E10 Increment C, también en
    # `GET /preservacion/aip/{expediente_id}` (lectura mediada del AIP de
    # preservación) — ver app/clients/storage_client.py. Mismo secreto que
    # `storage-service.INTERNAL_SERVICE_TOKEN`. Vacío (default) hace que
    # storage-service rechace la llamada con 401 (fail-closed) — NUNCA
    # commitear el valor real.
    storage_internal_token: str = ""
    # E10 debt (cableado del AIP OAIS al cierre del expediente): resolución
    # cross-servicio de los `file_id` de los anexos de un lote de radicados
    # — document-service es el único que conoce esa tabla. Ver
    # app/clients/document_client.py.
    document_service_url: str = "http://document-service:8003"
    # D-02: mismo secreto que document-service.INTERNAL_SERVICE_TOKEN —
    # enviado en X-Internal-Token al pedir el inventario TOTAL de anexos
    # (`POST /internal/documentos/anexos`). Vacío (default) hace que
    # document-service rechace la llamada con 401 (fail-closed) — NUNCA
    # commitear el valor real.
    document_internal_token: str = ""
    service_port: int = 8004
    log_level: str = "info"

    # --- Reconciliación del sellado del índice electrónico (residual Ac.
    # AGN 001/2024 art. 4.3.2.4) — ver app/jobs/indice_reconciliation.py ---
    # Kill-switch: True (default) arranca el loop de reconciliación en el
    # lifespan de app/main.py.
    indice_reconciliation_enabled: bool = True
    # Cadencia (segundos) entre barridas del loop.
    indice_reconciliation_interval_seconds: int = 300
    # Índices 'pendiente_firma' procesados como máximo por tenant y barrida.
    indice_reconciliation_batch_size: int = 50
    # Reintentos del job antes de marcar `reconcile_agotado=TRUE` (permanece
    # 'pendiente_firma', requiere intervención manual — POST /indice/firmar).
    indice_reconciliation_max_intentos: int = 8
    # Backoff exponencial: `min(cap, base * 2**intentos)`.
    indice_reconciliation_backoff_base_seconds: int = 60
    indice_reconciliation_backoff_cap_seconds: int = 86400
    # Intento síncrono ACOTADO en el cierre (`IndexService.firmar_indice_close`)
    # antes de degradar a 'pendiente_firma' por fallo transitorio — un `seal_absent`
    # degrada de inmediato SIN consumir estos reintentos (ver el docstring del
    # método). 2 intentos × ~1s es el tope por diseño: el cierre del expediente
    # no debe alargarse peligrosamente por este control compensatorio.
    close_sign_max_sync_attempts: int = 2
    close_sign_sync_delay_seconds: float = 1.0
    # D-02: mismo secreto compartido que signature-service.SIGNATURE_INTERNAL_TOKEN
    # — enviado por app/clients/signature_client.py::sign_indice_internal (SIN
    # X-User-Id) hacia signature-service `POST /internal/sign-indice`. Vacío
    # (default) hace que signature-service rechace la llamada con 503
    # (fail-closed, mal-configurado) — NUNCA commitear el valor real.
    signature_internal_token: str = ""

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
    #   "off"          (default) -- la firma personal es SIEMPRE opcional;
    #                  con el sello institucional solo, `completitud` queda
    #                  en 'sellada'.
    #   "clasificados" -- exige la firma del receptor para expedientes
    #                  nivel_seguridad>=2 (RESERVADA/CLASIFICADA); nivel 1
    #                  (PÚBLICA) conserva 'sellada' como suficiente.
    #   "todos"        -- exige la firma del receptor para TODA transferencia.
    acta_firma_personal_require: Literal["off", "clasificados", "todos"] = "off"

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

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


settings = Settings()
