-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E01, RF-RAD-05, 2026-08-30): la geografía de terceros y
-- envíos pasa de TEXTO LIBRE a CÓDIGO DIVIPOLA. Cierra el pendiente que la
-- migración `026` declaró.
--
-- Requiere `global/003_divipola.sql`, que el runner aplica antes.
--
-- ── Las dos tablas NO reciben el mismo tratamiento, y es deliberado ─────────
--
-- En `tercero` el código lleva **clave foránea** al catálogo: la ficha viva de
-- un sujeto debe apuntar a un municipio que existe, y si alguien intentara
-- retirar del catálogo un municipio todavía en uso, la base lo impide.
--
-- En `radicado_actor` el código va **sin clave foránea**, a propósito. Esa fila
-- es la copia CONGELADA de a dónde se envió: tiene que sobrevivir a cualquier
-- cambio del catálogo, incluido que el municipio deje de existir. Atarla al
-- catálogo convertiría una reorganización territorial en una corrupción del
-- registro histórico —o, peor, en un borrado en cascada—. Es la misma razón por
-- la que los datos de contacto se copian en vez de leerse por referencia: el
-- envío se hizo con lo que era verdad entonces.
--
-- ── El texto libre se queda, y ahora dice la verdad ─────────────────────────
--
-- `municipio` y `departamento` (TEXT) NO se eliminan: los lee el índice
-- electrónico, la exportación y los reportes. Lo que cambia es su procedencia
-- —la capa de servicio los rellena con el nombre OFICIAL del catálogo cuando
-- hay código— así que dejan de ser lo que alguien tecleó y pasan a ser el
-- nombre canónico. Quien los lee sigue funcionando sin enterarse, y deja de
-- ver cuatro escrituras del mismo municipio.
--
-- Se admite código NULO: una dirección en el extranjero (`pais <> 'CO'`) no
-- tiene DIVIPOLA, y los terceros ya registrados con texto libre siguen siendo
-- válidos. Forzar el código de golpe rompería la ventanilla por un dato de
-- presentación.

ALTER TABLE tercero
    ADD COLUMN IF NOT EXISTS departamento_codigo CHAR(2),
    ADD COLUMN IF NOT EXISTS municipio_codigo    CHAR(5);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tercero_departamento_codigo_fkey'
          AND conrelid = (current_schema() || '.tercero')::regclass
    ) THEN
        ALTER TABLE tercero
            ADD CONSTRAINT tercero_departamento_codigo_fkey
            FOREIGN KEY (departamento_codigo)
            REFERENCES public.divipola_departamento (codigo);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tercero_municipio_codigo_fkey'
          AND conrelid = (current_schema() || '.tercero')::regclass
    ) THEN
        ALTER TABLE tercero
            ADD CONSTRAINT tercero_municipio_codigo_fkey
            FOREIGN KEY (municipio_codigo)
            REFERENCES public.divipola_municipio (codigo);
    END IF;

    -- Un municipio sin su departamento deja la cascada a medias, y un municipio
    -- que no cuelga del departamento elegido es la incoherencia que una cascada
    -- mal programada produce en silencio. El código DIVIPOLA lleva dentro el de
    -- su departamento, así que comprobarlo no cuesta una consulta.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tercero_divipola_coherencia_check'
          AND conrelid = (current_schema() || '.tercero')::regclass
    ) THEN
        ALTER TABLE tercero
            ADD CONSTRAINT tercero_divipola_coherencia_check
            CHECK (
                municipio_codigo IS NULL
                OR (departamento_codigo IS NOT NULL
                    AND left(municipio_codigo, 2) = departamento_codigo)
            );
    END IF;
END $$;

COMMENT ON COLUMN tercero.municipio_codigo IS
    'Código DIVIPOLA (5 dígitos) del municipio. NULL para direcciones fuera de Colombia y para fichas anteriores al catálogo. Es lo que impide que «Bogotá» y «BOGOTA» sean dos municipios.';


ALTER TABLE radicado_actor
    ADD COLUMN IF NOT EXISTS departamento_codigo CHAR(2),
    ADD COLUMN IF NOT EXISTS municipio_codigo    CHAR(5);

COMMENT ON COLUMN radicado_actor.municipio_codigo IS
    'Código DIVIPOLA CONGELADO del envío. SIN clave foránea a propósito: la copia debe sobrevivir a que el catálogo cambie o a que el municipio deje de existir.';

-- El trigger de inmutabilidad se rehace para cubrir también los códigos. Si no
-- se ampliara, quedaría el hueco exacto que la copia congelada existe para
-- cerrar: el nombre del municipio protegido y su código libre de reescritura,
-- de modo que un `UPDATE` podría mover el envío a otro sitio sin tocar ni una
-- letra del texto.
CREATE OR REPLACE FUNCTION reject_radicado_actor_snapshot_update() RETURNS trigger AS $$
BEGIN
    IF NEW.nombre IS DISTINCT FROM OLD.nombre
       OR NEW.entidad IS DISTINCT FROM OLD.entidad
       OR NEW.email IS DISTINCT FROM OLD.email
       OR NEW.telefono IS DISTINCT FROM OLD.telefono
       OR NEW.direccion IS DISTINCT FROM OLD.direccion
       OR NEW.municipio IS DISTINCT FROM OLD.municipio
       OR NEW.departamento IS DISTINCT FROM OLD.departamento
       OR NEW.municipio_codigo IS DISTINCT FROM OLD.municipio_codigo
       OR NEW.departamento_codigo IS DISTINCT FROM OLD.departamento_codigo
       OR NEW.pais IS DISTINCT FROM OLD.pais
       OR NEW.tercero_id IS DISTINCT FROM OLD.tercero_id
    THEN
        RAISE EXCEPTION
            'los datos de contacto de un actor son los USADOS en el envío y no se editan '
            '(E01, RF-RAD-05): cambiarlos daría información errónea sobre un acto ya ocurrido';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- La deduplicación por identidad + destino gana el municipio. «Calle 10 #5-20»
-- existe en medio país: sin el municipio, dos envíos legítimos a esa dirección
-- en dos municipios distintos se rechazaban como duplicado. Relajar un índice
-- único es seguro sobre datos existentes; restringirlo no lo habría sido.
DROP INDEX IF EXISTS ux_radicado_actor_sin_duplicado;
CREATE UNIQUE INDEX IF NOT EXISTS ux_radicado_actor_sin_duplicado
    ON radicado_actor (
        radicado_id, tercero_id, rol,
        coalesce(direccion, ''), coalesce(email, ''), coalesce(municipio_codigo, '')
    );
