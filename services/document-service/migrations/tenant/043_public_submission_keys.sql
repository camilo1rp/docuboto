-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 043 (AREA-02-07): idempotencia de la radicación pública.
--
-- Un reintento de red del ciudadano (o un doble clic) producía DOS radicados: dos
-- consecutivos oficiales, irrecuperables. El cliente genera una `Idempotency-Key`
-- por formulario; este registro la ata al radicado y se inserta con
-- `ON CONFLICT DO NOTHING` en la MISMA transacción que el radicado: dos peticiones
-- simultáneas con la misma clave se serializan en el índice único (la segunda espera
-- el commit de la primera y devuelve el radicado existente, sin gastar número).
--
-- `huella` es el SHA-256 del contenido validado: la misma clave con OTRO contenido no
-- es un reintento, es un mal uso, y se rechaza (409) en vez de devolver un radicado
-- que no corresponde a lo que se envió.
--
-- Idempotente. Rollback: DROP TABLE IF EXISTS public_submission_keys;

CREATE TABLE IF NOT EXISTS public_submission_keys (
    key          TEXT        PRIMARY KEY,
    radicado_id  UUID        REFERENCES radicados(id) ON DELETE CASCADE,
    huella       TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public_submission_keys IS
    'Idempotency-Key de la ventanilla pública (AREA-02-07): clave del cliente -> radicado.';
