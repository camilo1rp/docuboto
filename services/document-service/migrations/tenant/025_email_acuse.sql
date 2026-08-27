-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: se distribuye bajo la Licencia Pública General Affero de GNU
-- (AGPL) versión 3 o posterior. Véase LICENSE y <https://www.gnu.org/licenses/>.

-- ============================================================================
-- E19 Increment 3 — Constancia de radicación al remitente.
--
-- **OPT-IN, y el encuadre normativo importa.** La spec original de E19 afirmaba
-- que la Circular Externa AGN 001/2020 obliga a acusar recibo. Consultado el
-- texto: **no lo dice**. Y la Ley 1755/2015 art. 15 exige la constancia «si
-- quien presenta la petición solicita constancia de haberla presentado» — es
-- decir, **a petición del interesado, no de oficio**.
--
-- Así que esto no se activa solo: es una buena práctica que le da al ciudadano
-- el número con el que puede seguir su trámite (y que enlaza con la consulta
-- pública por código, E13). Cada entidad decide, por buzón. Presentarlo como
-- obligación legal sería atribuirle a la norma algo que no dice, y ese fue el
-- defecto que ya se corrigió en la spec.
--
-- **Por qué por CUENTA y no global**: un buzón de PQRS quiere acusar recibo al
-- ciudadano; uno de notificaciones judiciales, donde el remitente es un juzgado
-- que no espera respuesta automática, probablemente no.
-- ============================================================================

ALTER TABLE email_cuenta
    ADD COLUMN IF NOT EXISTS acuse_al_remitente BOOLEAN NOT NULL DEFAULT false;

-- Resultado del intento, por mensaje. Se guarda el fallo además del éxito:
-- «no se envió» y «no se pudo enviar» son cosas distintas, y sin registrar la
-- segunda el operador no tiene forma de saber que el ciudadano nunca recibió su
-- número.
ALTER TABLE email_mensaje
    ADD COLUMN IF NOT EXISTS acuse_enviado_at TIMESTAMPTZ;
ALTER TABLE email_mensaje
    ADD COLUMN IF NOT EXISTS acuse_error TEXT;

COMMENT ON COLUMN email_cuenta.acuse_al_remitente IS
    'Enviar constancia de radicación al remitente. Opt-in: la Ley 1755/2015 '
    'art. 15 exige la constancia a PETICIÓN del interesado, no de oficio.';
