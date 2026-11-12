-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (B9-S7): una firma NATIVA solo existe con `key_version = 1`.
--
-- La 024 retiró el esquema HMAC 0 del código y quitó el DEFAULT, pero la base seguía
-- admitiendo una fila `provider = 'nativa'` con `key_version = 0`: justo la fila que
-- ningún código sabe verificar. El CHECK lo cierra donde nace.
--
-- ── Entorno de pruebas, sin backfill ────────────────────────────────────────
--
-- Se BORRA la fila sintética v0 que el desarrollo sembró para ejercitar el motivo
-- `esquema_hmac_retirado` (`d4e62969-824d-4802-9e40-b402d977b6fc`); no se
-- reetiqueta ni se reescribe: una firma es un hecho fechado y esta nunca fue real.
-- Es la única fila nativa con 0 (comprobado). Si otro entorno tuviera filas nativas
-- con 0, el ADD CONSTRAINT falla —ruidosamente— en vez de reescribirlas.
--
-- Idempotente.

DELETE FROM firma
 WHERE id = 'd4e62969-824d-4802-9e40-b402d977b6fc'
   AND provider = 'nativa'
   AND key_version = 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'firma_nativa_exige_key_version'
           AND conrelid = 'firma'::regclass
    ) THEN
        ALTER TABLE firma
            ADD CONSTRAINT firma_nativa_exige_key_version
            CHECK (provider <> 'nativa' OR key_version = 1);
    END IF;
END $$;
