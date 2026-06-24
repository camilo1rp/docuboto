-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E16): metadatos de reintento del envío SMTP.
--
-- attempts  = número de intentos de envío realizados (>=1).
-- last_error = último error SMTP si el envío terminó en 'failed'.
-- El envío reintenta con backoff hasta SMTP_MAX_ATTEMPTS antes de marcar 'failed'.

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 1;

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS last_error TEXT;
