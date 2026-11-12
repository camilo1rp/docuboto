-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Ubicación actual del radicado (ADR-029; equivalente a `radi_depe_actu` /
-- `radi_usua_actu` de Orfeo).
--
-- `dest_dept`/`dest_dept_code` son la dependencia DE RADICACIÓN: una instantánea
-- legal inmutable de a quién iba dirigido el documento. Tras repartir o devolver,
-- el radicado está en OTRA dependencia y ninguna columna lo decía.
--
--   flujo_dept_code / flujo_dept / flujo_usuario
--       las escribe el workflow-service (`sincronizar_estado_radicado`) en la MISMA
--       transacción que `status`, y su backfill (workflow 016) para lo existente.
--       document-service NUNCA las escribe: un solo escritor.
--       `flujo_dept` es el NOMBRE del paso (`flow_steps.to_dept`, sin FK);
--       `flujo_dept_code` solo se resuelve si el nombre casa con UNA dependencia
--       activa, si no queda NULL y el nombre es la instantánea. `flujo_usuario` es
--       `auth_users.id` del poseedor del paso (nunca el `sub` de Keycloak).
--
--   ubicacion_dept_code / ubicacion_dept   (columnas GENERADAS, STORED)
--       dónde está AHORA: la del flujo si el flujo la ha movido; si no, la de
--       radicación (destino y, a falta de él, origen). Se calcula por PAR
--       (nombre + código del mismo origen): con un `COALESCE` columna a columna,
--       un nombre de flujo sin código único mostraría el código de OTRA
--       dependencia. Al ser generadas no tienen segundo escritor que desincronice.
--
-- Idempotente (`IF NOT EXISTS`). Los radicados existentes quedan con `flujo_*`
-- NULL —ubicación = radicación— hasta que corra el backfill de workflow-service.

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS flujo_dept_code INTEGER,
    ADD COLUMN IF NOT EXISTS flujo_dept      VARCHAR(100),
    ADD COLUMN IF NOT EXISTS flujo_usuario   UUID;

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS ubicacion_dept_code INTEGER GENERATED ALWAYS AS (
        CASE
            WHEN flujo_dept IS NOT NULL THEN flujo_dept_code
            WHEN dest_dept IS NOT NULL OR dest_dept_code IS NOT NULL THEN dest_dept_code
            ELSE origin_dept_code
        END
    ) STORED;

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS ubicacion_dept VARCHAR(100) GENERATED ALWAYS AS (
        CASE
            WHEN flujo_dept IS NOT NULL THEN flujo_dept
            WHEN dest_dept IS NOT NULL OR dest_dept_code IS NOT NULL THEN dest_dept
            ELSE origin_dept
        END
    ) STORED;

CREATE INDEX IF NOT EXISTS ix_radicados_ubicacion_dept_code ON radicados (ubicacion_dept_code);

COMMENT ON COLUMN radicados.flujo_dept IS
    'Nombre de la dependencia del paso de flujo vigente (o del último cerrado). La escribe SOLO workflow-service (ADR-029).';
COMMENT ON COLUMN radicados.ubicacion_dept IS
    'Dónde está el radicado AHORA (generada): flujo_* si el flujo lo movió, si no la dependencia de radicación (ADR-029).';
