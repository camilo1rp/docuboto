-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (RF-FIR-13, cierre de fuga de OTP — seguimiento de
-- seguridad de E06 Inc.2): marca las notificaciones cuyo `body` fue
-- REDACTADO a propósito antes de persistirse.
--
-- Contexto: notification-service recibe el body REAL (p. ej. el código OTP
-- del 2FA por correo, RF-FIR-13) y SIEMPRE lo envía por SMTP tal cual, pero
-- cuando el emisor marca el envío como sensible (`NotificationSend.sensible`)
-- la fila persistida en esta tabla lleva
-- `body = '[contenido sensible omitido]'`, NUNCA el valor real. Esta columna
-- documenta esa redacción intencional (no es pérdida de datos) y permite
-- filtrar/auditar los envíos sensibles sin volver a exponer su contenido.
--
-- IDEMPOTENTE: ADD COLUMN IF NOT EXISTS + DEFAULT false no rompe filas
-- existentes (todo envío previo a esta migración era, por definición,
-- no-sensible bajo el invariante nuevo).
--
-- Rollback:
--   ALTER TABLE notifications DROP COLUMN IF EXISTS sensible;

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS sensible BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN notifications.sensible IS
    'true cuando body fue redactado a propósito antes de persistir (RF-FIR-13, p. ej. OTP de 2FA); el correo se envió con el body real por SMTP pero esta fila NUNCA lo contuvo.';
