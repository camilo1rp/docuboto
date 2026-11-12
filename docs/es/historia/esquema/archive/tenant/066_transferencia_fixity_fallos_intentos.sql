-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 066 (B4): contador de recepciones fallidas por fixity.
--
-- El evento PREMIS de una fixity fallida usaba un sello de tiempo en su `dedup_key`: dos
-- reintentos del MISMO fallo (p. ej. el reintento tras un 5xx de storage) producían dos
-- eventos distintos. Ahora la clave es `fixity-recepcion-fallo:{transferencia}:{n}`, con `n`
-- el número de intento fallido, incrementado en transacción propia junto con el asiento
-- `*_failed` (la tx de `recibir` ya está revertida cuando se registra el fallo).
--
-- Idempotente. search_path = tenant_{slug}.

ALTER TABLE transferencias
    ADD COLUMN IF NOT EXISTS fixity_fallos_intentos INT NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF to_regclass(format('%I.transferencias', current_schema())) IS NOT NULL
       AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ck_transferencias_fixity_fallos_intentos'
           AND conrelid = to_regclass(format('%I.transferencias', current_schema()))
    ) THEN
        ALTER TABLE transferencias
            ADD CONSTRAINT ck_transferencias_fixity_fallos_intentos
            CHECK (fixity_fallos_intentos >= 0);
    END IF;
END;
$$;

-- Rollback manual (no automatizado):
-- ALTER TABLE transferencias DROP COLUMN IF EXISTS fixity_fallos_intentos;
