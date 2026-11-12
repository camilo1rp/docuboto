-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Una cuenta automática radica sin que nadie complete el formulario. Una Entrada
-- exige tipo documental y dependencia de destino (Ac. AGN 060/2001 §5.2). Sin
-- esos dos datos en la cuenta, el sondeo reserva el correo y la radicación
-- falla siempre, y el mensaje queda retenido hasta que caduca la reserva.

ALTER TABLE email_cuenta
    ADD COLUMN IF NOT EXISTS dest_dept_code INT,
    ADD COLUMN IF NOT EXISTS doc_class VARCHAR(50);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'chk_email_cuenta_automatica_con_minimos'
           AND t.relname = 'email_cuenta'
           AND n.nspname = current_schema()
    ) THEN
        -- NOT VALID: un buzón automático que ya exista, sin estos datos, no
        -- tumba la migración. Los INSERT y UPDATE siguientes sí se comprueban.
        ALTER TABLE email_cuenta
            ADD CONSTRAINT chk_email_cuenta_automatica_con_minimos
            CHECK (
                modo <> 'automatica'
                OR (dest_dept_code IS NOT NULL AND doc_class IS NOT NULL)
            ) NOT VALID;
    END IF;
END
$$;

COMMENT ON COLUMN email_cuenta.dest_dept_code IS
    'Código de la dependencia que recibe lo que radica una cuenta automática.';
COMMENT ON COLUMN email_cuenta.doc_class IS
    'Código del tipo documental con el que radica una cuenta automática.';
