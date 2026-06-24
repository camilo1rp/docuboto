-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E16): historial de notificaciones por institución.
--
-- El historial vive en el schema del tenant (tenant_{slug}, ADR-002); sin
-- columna tenant_slug redundante (el schema identifica al tenant). El worker de
-- eventos y el endpoint de envío insertan aquí; los endpoints de consulta leen.

CREATE TABLE IF NOT EXISTS notifications (
    id              UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    recipient_email VARCHAR(320) NOT NULL,
    subject         TEXT         NOT NULL,
    body            TEXT         NOT NULL,
    status          VARCHAR(20)  NOT NULL,  -- 'sent' | 'failed'
    event_type      VARCHAR(100),
    sent_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_notifications_sent_at ON notifications (sent_at DESC);
