-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: se distribuye bajo la Licencia Pública General Affero de GNU
-- (AGPL) versión 3 o posterior. Véase LICENSE y <https://www.gnu.org/licenses/>.

-- ============================================================================
-- E19 Increment 2 — Espejo local del buzón: la bandeja compartida.
--
-- El modelo lo acertaba ya el Orfeo legacy (`radimail`, con `uniqueid UNIQUE` y
-- `radi_nume_radi`): el correo se materializa en la base ANTES de radicar, y la
-- unicidad la garantiza la base de datos, no una marca del servidor de correo.
-- Lo que Orfeo hacía mal —y es justo lo que esto corrige— es que un correo ya
-- radicado **seguía en la lista** con una «R» roja, sin reserva ni aviso: dos
-- personas podían abrirlo a la vez y radicarlo dos veces.
--
-- IDENTIDAD. Un `uid` de IMAP **no identifica un mensaje por sí solo**: solo es
-- único dentro de una carpeta y mientras el `UIDVALIDITY` de esa carpeta no
-- cambie (RFC 3501 §2.3.1.1). Si el servidor la recrea —una restauración, una
-- migración de buzón— los uid se reutilizan desde cero y el mensaje nº 7 nuevo
-- se confundiría con el nº 7 viejo, dándose por radicado sin estarlo. Por eso:
--   · identidad estable = `message_id` (RFC 5322 §3.6.4), único globalmente y
--     que sobrevive incluso a que el correo cambie de carpeta;
--   · localizador = (`carpeta`, `uidvalidity`, `uid`), para ir a buscarlo.
--
-- FECHA DE RECIBO. Se guarda la del correo, no la del sondeo. La Circular
-- Externa AGN 001/2020 exige «dejando constancia de la fecha y hora de recibo», y
-- de ella dependen los plazos del derecho de petición: un correo que llega el
-- viernes y se sondea el lunes no puede nacer con fecha de lunes.
--
-- EL MENSAJE ORIGINAL se conserva (`crudo_file_id`), no solo su transcripción a
-- texto. La Ley 527/1999 art. 12 exige conservar el mensaje **en el formato en
-- que se recibió**, con su origen, destino, fecha y hora.
-- ============================================================================

CREATE TABLE IF NOT EXISTS email_mensaje (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_id         UUID NOT NULL REFERENCES email_cuenta (id) ON DELETE CASCADE,

    -- Identidad estable. Un correo sin `Message-ID` es raro pero posible (un
    -- cliente mal hecho); en ese caso el sincronizador sintetiza uno a partir de
    -- la terna del localizador, y por eso la columna es NOT NULL: preferimos un
    -- sustituto explícito a un NULL que rompería la unicidad.
    message_id        VARCHAR(998) NOT NULL,

    -- Localizador dentro del buzón.
    carpeta           VARCHAR(255) NOT NULL,
    uidvalidity       BIGINT       NOT NULL,
    uid               BIGINT       NOT NULL,

    -- Cabeceras, para la bandeja. El CUERPO no se guarda aquí: se lee del
    -- mensaje original cuando alguien abre el correo.
    remitente_nombre  VARCHAR(500),
    remitente_email   VARCHAR(320),
    asunto            VARCHAR(2000),
    fecha_recibo      TIMESTAMPTZ  NOT NULL,
    num_adjuntos      INTEGER      NOT NULL DEFAULT 0,

    -- El `.eml` íntegro en storage (Ley 527/1999 art. 12).
    crudo_file_id     UUID,

    estado            VARCHAR(20)  NOT NULL DEFAULT 'pendiente',

    -- ── Reserva ────────────────────────────────────────────────────────────
    -- Lo que Orfeo no tenía. Al abrir un correo queda reservado para ese
    -- funcionario unos minutos; los demás lo ven ocupado y no pueden radicarlo.
    --
    -- Con EXPIRACIÓN, y es deliberado: sin ella, alguien que cierra el navegador
    -- —o se va a comer— dejaría el correo bloqueado para siempre, y la ventanilla
    -- necesitaría un administrador para desatascarlo. Un bloqueo que solo se
    -- libera a mano acaba siendo un bloqueo que nadie usa.
    reservado_por     UUID,
    reservado_hasta   TIMESTAMPTZ,

    -- ── Resultado ──────────────────────────────────────────────────────────
    radicado_id       UUID,
    tracking_number   VARCHAR(50),
    radicado_por      UUID,
    radicado_at       TIMESTAMPTZ,

    -- Descartar es para lo que llegó a la carpeta y no debía radicarse. Exige
    -- motivo: es una decisión sobre una comunicación oficial recibida, y la
    -- unidad de correspondencia responde de ella (Ac. 060/2001 art. 3).
    descartado_por    UUID,
    descartado_at     TIMESTAMPTZ,
    motivo_descarte   TEXT,

    visto_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_email_mensaje_estado
        CHECK (estado IN ('pendiente', 'radicado', 'descartado')),

    -- Un mensaje radicado tiene número y autor; uno pendiente, ninguno de los
    -- dos. Escrito en ambas direcciones: un número sin estado 'radicado' sería
    -- un correo que se radicó y sigue ofreciéndose en la bandeja.
    CONSTRAINT chk_email_mensaje_radicado_forma
        CHECK (
            (estado = 'radicado' AND tracking_number IS NOT NULL AND radicado_por IS NOT NULL)
            OR (estado <> 'radicado' AND tracking_number IS NULL AND radicado_at IS NULL)
        ),

    CONSTRAINT chk_email_mensaje_descartado_forma
        CHECK (
            (estado = 'descartado' AND motivo_descarte IS NOT NULL AND descartado_por IS NOT NULL)
            OR (estado <> 'descartado' AND motivo_descarte IS NULL)
        ),

    -- Reservado por alguien y hasta cuándo van juntos: media pareja sería una
    -- reserva sin dueño o sin caducidad, y las dos se comportan mal.
    CONSTRAINT chk_email_mensaje_reserva_par
        CHECK ((reservado_por IS NULL) = (reservado_hasta IS NULL)),

    CONSTRAINT chk_email_mensaje_uid_positivo
        CHECK (uid > 0 AND uidvalidity > 0)
);

-- **La defensa contra la doble radicación, y va en la base de datos.**
-- Un mismo correo no puede entrar dos veces en la bandeja de una cuenta. Es el
-- equivalente del `uniqueid UNIQUE` del Orfeo legacy, pero sobre el
-- `Message-ID`, que sobrevive a que cambie el `UIDVALIDITY`.
CREATE UNIQUE INDEX IF NOT EXISTS ux_email_mensaje_identidad
    ON email_mensaje (cuenta_id, message_id);

-- La bandeja: pendientes de una cuenta, el más antiguo primero (FIFO, como la
-- cola de anulaciones — una ventanilla atiende por orden de llegada).
CREATE INDEX IF NOT EXISTS ix_email_mensaje_bandeja
    ON email_mensaje (cuenta_id, fecha_recibo)
    WHERE estado = 'pendiente';

-- Para localizar el mensaje en el buzón al moverlo tras radicar.
CREATE INDEX IF NOT EXISTS ix_email_mensaje_localizador
    ON email_mensaje (cuenta_id, carpeta, uidvalidity, uid);
