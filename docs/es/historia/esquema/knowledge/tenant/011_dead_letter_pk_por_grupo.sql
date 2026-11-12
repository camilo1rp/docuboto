-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (AREA-10-07, ADR-021): la PK de la tabla COMPARTIDA
-- evento_dead_letter pasa de (event_id) a (origin_group, event_id).
--
-- workflow, signature, notification y knowledge escriben en la MISMA tabla física
-- (misma base y mismo schema de tenant) y los cuatro leen `orpycamcp.document.events`
-- (o el stream de su dominio). Con PK solo por event_id, cuando un mismo evento
-- agotaba los reintentos en DOS grupos, el segundo INSERT ... ON CONFLICT DO NOTHING
-- era un no-op: ese grupo no dejaba fila ni asiento `evento.dead_letter`, su admin no
-- lo veía (el listado filtra por origin_group) y el XACK ya lo había sacado de la PEL.
-- Perdía en silencio la trazabilidad (Ley 594/2000 arts. 4 y 19).
--
-- IDEMPOTENTE y compartida entre los 4 servicios: corre primero el que llegue antes;
-- los demás ven la PK ya compuesta y no hacen nada. El LOCK evita que dos runners
-- simultáneos se pisen al soltar/crear la restricción.
--
-- Rollback (solo si no hay filas con el mismo event_id en dos grupos):
--   ALTER TABLE evento_dead_letter DROP CONSTRAINT evento_dead_letter_pkey,
--       ADD PRIMARY KEY (event_id);

DO $$
DECLARE
    cols text[];
BEGIN
    LOCK TABLE evento_dead_letter IN ACCESS EXCLUSIVE MODE;

    SELECT array_agg(a.attname::text ORDER BY k.ord)
      INTO cols
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
     WHERE c.conrelid = to_regclass('evento_dead_letter')
       AND c.contype = 'p';

    IF cols IS DISTINCT FROM ARRAY['origin_group', 'event_id'] THEN
        -- Una fila legada sin grupo quedaría fuera de la PK; el centinela 'unknown'
        -- es invisible a todas las consultas por servicio (fail-closed).
        UPDATE evento_dead_letter SET origin_group = 'unknown' WHERE origin_group IS NULL;
        ALTER TABLE evento_dead_letter ALTER COLUMN origin_group SET NOT NULL;
        ALTER TABLE evento_dead_letter DROP CONSTRAINT IF EXISTS evento_dead_letter_pkey;
        ALTER TABLE evento_dead_letter ADD PRIMARY KEY (origin_group, event_id);
    END IF;
END $$;

COMMENT ON TABLE evento_dead_letter IS
    'Copia durable (Postgres) de los eventos aparcados en el dead-letter de Redis. '
    'Tabla COMPARTIDA por los consumidores del tenant: la PK (origin_group, event_id) '
    'deja que cada grupo aparque el mismo evento una vez; repetir el mismo par es '
    'idempotente (ON CONFLICT (origin_group, event_id) DO NOTHING). El INSERT y el '
    'audit_log se escriben en la misma transacción (R-4, ADR-021).';
