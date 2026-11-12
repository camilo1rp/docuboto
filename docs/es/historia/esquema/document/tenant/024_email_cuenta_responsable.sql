-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: se distribuye bajo la Licencia Pública General Affero de GNU
-- (AGPL) versión 3 o posterior. Véase LICENSE y <https://www.gnu.org/licenses/>.

-- ============================================================================
-- E19 Increment 3 — Funcionario responsable de una cuenta automática.
--
-- Una cuenta en modo `automatica` radica sin que nadie pulse nada, y eso deja
-- dos problemas que resultan ser el mismo:
--
-- 1. **Normativo.** El Acuerdo AGN 060/2001 art. 2 incluye el «Nombre del
--    funcionario responsable del trámite» entre los datos mínimos del registro,
--    y el art. 13 exige que las entidades «asignen responsabilidades» sobre las
--    cuentas habilitadas. Un radicado que nadie firma no cumple ninguno de los
--    dos.
--
-- 2. **Técnico.** Los anexos se suben a storage bajo la autoría de quien radica,
--    y storage exige `PERM_RADI` de un usuario concreto — comprobado: sin
--    identidad responde 403. Un proceso sin identidad no puede meter soportes en
--    el archivo de la entidad, y eso está bien que sea así.
--
-- La misma respuesta resuelve los dos: **la cuenta automática declara bajo qué
-- funcionario radica**. No es un tecnicismo para contentar a storage: es la
-- persona que responde de lo que entre por ese buzón sin revisión previa.
--
-- Las cuentas `asistida` no lo necesitan: ahí el responsable es quien pulsa.
-- ============================================================================

ALTER TABLE email_cuenta
    ADD COLUMN IF NOT EXISTS responsable_user_id UUID;

DO $$
BEGIN
    -- Guard por `current_schema()`: sin él, el nombre de la restricción se
    -- encuentra en el esquema del primer tenant y el segundo se queda SIN ella
    -- —y el fallo es silencioso, porque la migración se marca aplicada igual.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'chk_email_cuenta_automatica_con_responsable'
           AND t.relname = 'email_cuenta'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE email_cuenta
            ADD CONSTRAINT chk_email_cuenta_automatica_con_responsable
            CHECK (modo <> 'automatica' OR responsable_user_id IS NOT NULL);
    END IF;
END
$$;

COMMENT ON COLUMN email_cuenta.responsable_user_id IS
    'Funcionario bajo cuya autoría radica una cuenta en modo automático '
    '(Ac. AGN 060/2001 arts. 2 y 13). NULL en modo asistida: allí responde quien pulsa.';
