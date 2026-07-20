-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 debt "AIP sin-wiring" — cableado del AIP OAIS al
-- cierre del expediente, diseño ratificado por orfeo-architect: ver
-- `.claude/agent-memory/orfeo-architect/project_aip-wiring-e10-diseno.md`).
--
-- Estado de reconciliación del empaquetado del AIP -- artefacto a NIVEL
-- EXPEDIENTE con ciclo de vida INDEPENDIENTE del índice electrónico
-- (`expediente_indice`, migración 029/031): NO se añaden columnas ahí. El
-- AIP embebe el índice YA FIRMADO/protegido como `indice_file_id`, pero su
-- propio empaquetado es un acto DISTINTO, con su propio backoff.
--
-- `estado`:
--   'pendiente'   -- elegible/en curso de reintentos (default; la fila se
--                    crea perezosamente en el primer intento, ver
--                    `AipRepository.get_or_create`).
--   'empaquetado' -- AIP subido y registrado en storage-service, TERMINAL.
--   'omitido'     -- expediente 100% físico (0 anexos digitales en todos
--                    sus radicados vigentes) -- el índice firmado YA cubre
--                    su preservación (WORM, E10 Inc.1); el contrato de
--                    storage-service (`AipCreate.documentos` con
--                    `min_length=1`) rechazaría un Bag sin documentos.
--                    TERMINAL.
--   'agotado'     -- backoff agotado (`indice_reconciliation_max_intentos`,
--                    reutilizado sin duplicar) -- requiere intervención
--                    manual. NO terminal: un archivista puede investigar y
--                    la fila puede reintentarse a mano (roadmap: sin
--                    endpoint de reintento manual en este incremento).
--
-- `aip_worm_object_id`: el `id` del AIP devuelto por `POST /preservacion/aip
-- [-internal]` (storage-service) -- storage-service no expone directamente
-- el `id` de la fila `preservacion_worm_objeto` subyacente en esa respuesta
-- (`AipResponse` solo trae `id`/`bucket`/`object_key`/etc. de la tabla
-- `aip`), así que esta columna guarda la referencia que SÍ está disponible;
-- 1:1 con la fila `preservacion_worm_objeto` creada en la MISMA transacción
-- del lado de storage-service.
--
-- Índice parcial para el barrido de la 6ª fase de reconciliación
-- (`run_once_aip`, `app/jobs/indice_reconciliation.py`): solo se consulta
-- por `proximo_intento_at` cuando `estado='pendiente'`.

CREATE TABLE IF NOT EXISTS expediente_aip (
    expediente_id       UUID PRIMARY KEY REFERENCES expedientes(id),
    estado               TEXT NOT NULL DEFAULT 'pendiente'
                          CHECK (estado IN ('pendiente', 'empaquetado', 'omitido', 'agotado')),
    aip_worm_object_id   UUID,
    empaquetado_at       TIMESTAMPTZ,
    intentos             INT NOT NULL DEFAULT 0,
    proximo_intento_at   TIMESTAMPTZ,
    ultimo_error         TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_expediente_aip_pendiente
    ON expediente_aip (proximo_intento_at)
    WHERE estado = 'pendiente';

-- Rollback (manual, no automatizado por el runner):
-- DROP TABLE IF EXISTS expediente_aip;
