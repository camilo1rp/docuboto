-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migration 006: forma obligatoria de `hash`/`prev_hash` en audit_log.
--
-- QUÉ CIERRA. Las capas 1-3 (privilegios, trigger de fila, trigger de sentencia)
-- impiden MODIFICAR o BORRAR asientos, que es la amenaza clásica. Ninguna
-- impedía **añadir** una fila que no proviene de `audit.append`: un `INSERT`
-- suelto entra sin más y su hash arbitrario rompe la cadena desde ese punto.
--
-- Ocurrió de verdad. Durante la revisión funcional aparecieron 25 filas con
-- `hash` = 'h1'…'h4', 'x1'…'x15' y `prev_hash` vacío, insertadas por SQL en el
-- registro de los tenants `demo` y `otra-entidad` para probar la propia
-- pantalla de auditoría. La consecuencia no es reparable: el registro es
-- append-only por Ley 594, así que esas filas NO se pueden borrar y la cadena
-- de `demo` queda rota desde ahí para siempre. La reverificación lo denuncia
-- —y hace bien—, pero el daño ya está hecho. Un registro legal que solo se
-- protege contra la modificación deja abierta la puerta de la contaminación.
--
-- QUÉ NO CIERRA, dicho explícitamente: esto NO impide que alguien con acceso
-- SQL inserte una fila con un hash bien formado pero falso. Eso solo lo detecta
-- la reverificación de la cadena, que es el control de fondo. Lo que esta
-- restricción elimina es la contaminación ACCIDENTAL —la de los datos de prueba
-- y los scripts improvisados—, que es la que ha ocurrido y la que seguirá
-- ocurriendo mientras nada la rechace.
--
-- POR QUÉ `NOT VALID`. Las 25 filas ya existen y no se pueden borrar. Una
-- restricción validada haría fallar la migración; `NOT VALID` se aplica a toda
-- fila NUEVA y deja en paz a las viejas — que es exactamente el reparto
-- correcto: el pasado es inmutable, el futuro queda protegido. No se valida
-- después a propósito: `VALIDATE CONSTRAINT` fracasaría siempre.
--
-- Verificado contra PostgreSQL 15 real (tabla PARTICIONADA, `relkind='p'`):
-- la restricción se propaga a las particiones — el rechazo llega desde
-- `audit_log_default` — y el control positivo confirma que una fila con hash
-- SHA-256 de 64 hex sigue entrando.
--
-- Idempotente: no hay ADD CONSTRAINT IF NOT EXISTS para CHECK en Postgres.
-- Se comprueba el catálogo antes de añadir. NO se DROPea-y-recrea como en otras
-- migraciones de CHECK: sobre una tabla particionada con particiones vivas, un
-- DROP transitorio abriría una ventana —por breve que sea— en la que el registro
-- legal admite basura, y eso es justo lo que esta migración viene a cerrar.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'chk_audit_log_hash_forma'
           AND t.relname = 'audit_log'
           AND n.nspname = 'public'
    ) THEN
        ALTER TABLE public.audit_log
            ADD CONSTRAINT chk_audit_log_hash_forma
            CHECK (
                hash ~ '^[0-9a-f]{64}$'
                AND (prev_hash IS NULL OR prev_hash ~ '^[0-9a-f]{64}$')
            )
            NOT VALID;
    END IF;
END
$$;

COMMENT ON CONSTRAINT chk_audit_log_hash_forma ON public.audit_log IS
    'Forma SHA-256 (64 hex) de hash/prev_hash. Cuarta capa de la defensa del '
    'registro: las tres anteriores impiden UPDATE/DELETE, esta impide INSERTAR '
    'un asiento que no venga de orpycamcp_common.audit.append. NOT VALID a '
    'propósito — las filas contaminadas anteriores son append-only y no se '
    'pueden borrar.';
