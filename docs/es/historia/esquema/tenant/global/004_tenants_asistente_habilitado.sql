-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Global migration 004: bandera por entidad del asistente y la búsqueda semántica
-- (ADR-037, tanda C-1).
--
-- Las entidades que existen AL APLICAR esta migración quedan encendidas (no se les
-- quita nada que ya usaban); toda entidad que se registre después nace APAGADA y su
-- administrador la enciende a conciencia (opt-in; finalidad y deberes del responsable, Ley 1581 art. 4 lit. b y art. 17; enviar datos a un proveedor es una transmisión a un encargado, DUR 1074/2015 [confirmar numerales]). Las dos
-- sentencias juntas expresan esa regla en el esquema, sin heurística: el DEFAULT true
-- rellena las filas presentes y el SET DEFAULT false gobierna las futuras.
--
-- El IF NOT EXISTS evita que una reejecución fuera del libro de migraciones vuelva a
-- encender a quien ya la apagó. El rol de la aplicación ya tiene UPDATE sobre
-- public.tenants (auth-service global/008): no hace falta grant nuevo.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'tenants'
           AND column_name = 'asistente_habilitado'
    ) THEN
        ALTER TABLE public.tenants
            ADD COLUMN asistente_habilitado BOOLEAN NOT NULL DEFAULT true;
        ALTER TABLE public.tenants
            ALTER COLUMN asistente_habilitado SET DEFAULT false;
    END IF;
END $$;
