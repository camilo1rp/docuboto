-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- AREA-01-N2: el borrador hereda la clasificación del radicado que responde.
--
-- Hasta ahora `borrador` no tenía nivel: cualquier `PERM_RADI` de nivel 1
-- listaba, leía y editaba los borradores de TODO el tenant, incluidos los que
-- responden a radicados reservados y cuyo cuerpo ya arrastra datos del padre
-- (la combinación se hace con el clearance del creador y después cualquiera lo
-- leía). El no-read-up (RF-SEG-08) va en el WHERE de get/list/update.
--
-- La columna es la INSTANTÁNEA del nivel del padre al crear (sobrevive a que
-- el padre desaparezca: `radicado_padre_id` es ON DELETE SET NULL). El gate usa
-- además el nivel VIGENTE del padre (GREATEST), de modo que reclasificar el
-- padre hacia arriba cierra también sus borradores. Sin padre queda en 1.

ALTER TABLE borrador
    ADD COLUMN IF NOT EXISTS nivel_seguridad SMALLINT NOT NULL DEFAULT 1;

ALTER TABLE borrador
    DROP CONSTRAINT IF EXISTS ck_borrador_nivel_seguridad;
ALTER TABLE borrador
    ADD CONSTRAINT ck_borrador_nivel_seguridad CHECK (nivel_seguridad BETWEEN 1 AND 3);

-- Relleno desde el padre (idempotente: solo toca lo que difiere).
UPDATE borrador b
   SET nivel_seguridad = r.nivel_seguridad
  FROM radicados r
 WHERE r.id = b.radicado_padre_id
   AND b.nivel_seguridad IS DISTINCT FROM r.nivel_seguridad;

COMMENT ON COLUMN borrador.nivel_seguridad IS
    'Nivel de seguridad heredado del radicado padre al crear (RF-SEG-08). El '
    'acceso exige nivel <= clearance del llamante, en el WHERE.';
