-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: se distribuye bajo la Licencia Pública General Affero de GNU
-- (AGPL) versión 3 o posterior. Véase LICENSE y <https://www.gnu.org/licenses/>.

-- ============================================================================
-- E19 Increment 1 — Cuentas de correo para radicación.
--
-- Sustituye la cuenta ÚNICA en variables de entorno (IMAP_HOST/IMAP_USER/...)
-- por un catálogo por entidad. No es una comodidad: el Acuerdo AGN 060/2001
-- art. 13 dice que las entidades «reglamentarán su utilización y asignarán
-- responsabilidades de acuerdo con la cantidad de cuentas habilitadas», y el
-- art. 3 deja el control en la unidad de correspondencia. Una sola cuenta
-- global en el entorno no puede expresar ni la asignación ni el control.
--
-- Dos tipos, porque las entidades trabajan de las dos formas:
--   · institucional — buzón de la ventanilla, COMPARTIDO por varios
--     funcionarios (`email_cuenta_usuario` dice quiénes). Es el que necesita
--     reclamación y aviso de recarga: dos personas mirando la misma bandeja.
--   · personal — el correo del propio funcionario. Su credencial NO se guarda
--     (ver más abajo); se pide al entrar y vive en la sesión, como en el
--     `lock.php` del Orfeo legacy.
--
-- CARPETA parametrizable, y esto es una capacidad NUEVA: el Orfeo legacy
-- abría siempre INBOX (`imap_open` sin sufijo de buzón). La práctica real de
-- las entidades es que una persona revisa el correo y ARRASTRA a una carpeta
-- lo que sí es para radicar, dejando fuera publicidad y spam. Esa carpeta es
-- un filtro humano previo, y por eso `carpeta_origen` es configurable en vez
-- de estar fijada a INBOX.
-- ============================================================================

-- ============================================================================
-- Aplicación OAuth2 registrada por la ENTIDAD ante su proveedor de correo.
--
-- **Por qué es de la entidad y no del despliegue, ni del alta del tenant.**
--
-- Google exige verificación y una evaluación de seguridad CASA anual —cara y
-- por un tercero acreditado— a cualquier aplicación que pida el scope
-- `https://mail.google.com/`, que es el que cubre IMAP. Pero exime
-- explícitamente a las aplicaciones internas: «The app is only used by people
-- in your Google Workspace or Cloud Identity organization. The project must be
-- owned by the organization, and its OAuth Consent Screen must be configured
-- for internal use.» Si cada entidad registra la suya dentro de su propia
-- organización, no pasa por ese proceso. Con una aplicación única de OrpycaMCP
-- para todos, sí habría que pasarlo — y OrpycaMCP es software que cada
-- institución despliega por su cuenta, no un servicio central que pueda
-- sostener esa acreditación. En Microsoft el equivalente es una aplicación
-- *single-tenant* en el Azure AD de la entidad, sin consentimiento cruzado.
--
-- Además es lo correcto en soberanía: la entidad ve qué aplicación accede a su
-- correo, con qué permisos, y puede revocarla sin depender de nadie.
--
-- **No va en el alta del tenant**, y es deliberado: cuando se crea la entidad
-- nadie sabe todavía qué correo usará, quien la da de alta no suele tener los
-- permisos de administrador de Azure o Google Cloud a mano, y muchas entidades
-- trabajarán contra IMAP de hosting sin necesitar nada de esto. Exigirlo en el
-- alta bloquearía la creación de la institución por un dato que puede tardar
-- semanas. Se registra al configurar la primera cuenta OAuth2, desde
-- `/admin/correo`.
--
-- Una fila por proveedor y entidad: la misma aplicación sirve para todas sus
-- cuentas (PQRS, notificaciones judiciales, facturación…).
-- ============================================================================

CREATE TABLE IF NOT EXISTS email_app_oauth (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proveedor           VARCHAR(20)  NOT NULL,

    client_id           VARCHAR(255) NOT NULL,
    -- Cifrado con el mismo esquema que las contraseñas de cuenta (AESGCM con
    -- AAD tenant+fila): es un secreto, aunque su nombre diga «client».
    secreto_cifrado     BYTEA        NOT NULL,
    secreto_nonce       BYTEA        NOT NULL,

    -- Directorio de Azure AD ("tenant" en vocabulario de Microsoft — NO es el
    -- tenant de OrpycaMCP; el nombre colisiona y por eso se llama así aquí).
    azure_directory_id  VARCHAR(255),

    -- A dónde vuelve el proveedor tras el consentimiento. Se guarda porque debe
    -- coincidir EXACTAMENTE con la registrada en la consola del proveedor: una
    -- diferencia de una barra final rompe el flujo con un error que no explica
    -- nada.
    redirect_uri        VARCHAR(500) NOT NULL,

    notas               TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by          UUID,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT chk_email_app_proveedor
        CHECK (proveedor IN ('gmail', 'microsoft')),
    -- Microsoft necesita el directorio; Google no lo usa. Escrito en las dos
    -- direcciones para que un valor puesto en el proveedor equivocado sea un
    -- error y no un campo que nadie mira.
    CONSTRAINT chk_email_app_azure_directory
        CHECK (
            (proveedor = 'microsoft' AND azure_directory_id IS NOT NULL) OR
            (proveedor = 'gmail'     AND azure_directory_id IS NULL)
        )
);

-- Una sola aplicación por proveedor y entidad.
CREATE UNIQUE INDEX IF NOT EXISTS ux_email_app_oauth_proveedor
    ON email_app_oauth (proveedor);


CREATE TABLE IF NOT EXISTS email_cuenta (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Etiqueta para el operador: «Ventanilla PQRS», «Notificaciones judiciales».
    nombre                VARCHAR(200) NOT NULL,
    tipo                  VARCHAR(20)  NOT NULL,
    proveedor             VARCHAR(20)  NOT NULL,

    -- Conexión. host/puerto se derivan del proveedor para gmail/microsoft, pero
    -- se guardan explícitos: un preset que cambie no debe reescribir en silencio
    -- la configuración de una cuenta que ya funcionaba.
    host                  VARCHAR(255) NOT NULL,
    puerto                INTEGER      NOT NULL DEFAULT 993,
    usa_ssl               BOOLEAN      NOT NULL DEFAULT true,
    direccion             VARCHAR(320) NOT NULL,

    -- Con qué se AUTENTICA, cuando no coincide con la dirección. NULL = usar la
    -- dirección, que es el caso más común.
    --
    -- No son lo mismo y confundirlos rompe la conexión: la dirección es la
    -- identidad del buzón (va en el radicado y en la cadena XOAUTH2), mientras
    -- que el login depende del servidor — Exchange admite `DOMINIO\usuario`,
    -- algunos Zimbra y paneles de hosting quieren solo la parte local. El Orfeo
    -- legacy ya se había topado con esto y lo resolvía a la brava, cortando por
    -- el «@» según el proveedor (`configRadiMail.php`); aquí se declara, porque
    -- adivinar acierta hasta que no acierta y el fallo es «credenciales
    -- rechazadas», que manda a buscar el problema a la contraseña.
    usuario_login         VARCHAR(320),

    auth_tipo             VARCHAR(20)  NOT NULL,

    -- Credencial cifrada en reposo con AESGCM (mismo patrón que la clave de
    -- firma personal y el secreto TOTP de auth-service: AAD = tenant + id de
    -- la fila, así que un ciphertext movido a otra entidad no descifra).
    -- NULL para las cuentas personales: su contraseña NUNCA toca la base de
    -- datos. Guardar la credencial personal de un funcionario convertiría al
    -- custodio en titular de su correo entero, que excede con mucho lo que
    -- hace falta para radicar.
    secreto_cifrado       BYTEA,
    secreto_nonce         BYTEA,

    -- OAuth2. Obligatorio para Microsoft 365 (Exchange Online retiró la
    -- autenticación básica de IMAP a finales de 2022) y para Google Workspace
    -- (desde 2025-05-01 no acepta usuario+contraseña, ni contraseñas de
    -- aplicación). Sin esto, la ingesta simplemente no puede conectarse a las
    -- dos plataformas que usa la mayoría de las entidades.
    --
    -- Dos credenciales distintas, que se confunden a menudo:
    --   · La de la APLICACIÓN (client_id/client_secret) identifica a OrpycaMCP
    --     ante Google o Microsoft. Es de la ENTIDAD y la comparten todas sus
    --     cuentas del mismo proveedor → tabla `email_app_oauth`.
    --   · La AUTORIZACIÓN del buzón (refresh token) dice que esa aplicación
    --     puede leer ESTE buzón concreto. Es por cuenta → aquí.
    app_oauth_id          UUID,
    oauth_refresh_cifrado BYTEA,
    oauth_refresh_nonce   BYTEA,

    -- Carpetas. `carpeta_origen` es el filtro humano descrito arriba.
    -- `carpeta_radicados` es a dónde se mueve el correo una vez radicado: sale
    -- de la vista de todos los operadores a la vez, es reversible y se ve desde
    -- cualquier cliente de correo — a diferencia de la marca \Seen, que es
    -- compartida, la puede quitar cualquiera y no distingue «lo leí» de «lo
    -- radiqué».
    carpeta_origen        VARCHAR(255) NOT NULL DEFAULT 'INBOX',
    carpeta_radicados     VARCHAR(255) NOT NULL DEFAULT 'Radicados',

    -- Valores por defecto de la radicación que produce esta cuenta.
    dependencia_destino   VARCHAR(255),
    tipo_documental_id    UUID,

    -- 'asistida': el correo espera en la bandeja a que un funcionario lo revise
    -- y lo radique — así queda el «funcionario responsable del trámite» que
    -- exige el art. 2 del Acuerdo 060/2001. 'automatica': se radica al llegar,
    -- para entidades que confían en el filtro de carpeta.
    modo                  VARCHAR(20)  NOT NULL DEFAULT 'asistida',

    activa                BOOLEAN      NOT NULL DEFAULT true,

    -- Dueño de una cuenta personal. NULL en las institucionales.
    propietario_user_id   UUID,

    -- Diagnóstico de la última conexión: sin esto, la única forma de saber si
    -- una cuenta funciona es esperar a que no radique nada y no saber por qué.
    ultimo_sondeo_at      TIMESTAMPTZ,
    ultimo_error          TEXT,

    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by            UUID,
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT chk_email_cuenta_tipo
        CHECK (tipo IN ('institucional', 'personal')),
    CONSTRAINT chk_email_cuenta_proveedor
        CHECK (proveedor IN ('imap', 'gmail', 'microsoft')),
    CONSTRAINT chk_email_cuenta_auth
        CHECK (auth_tipo IN ('password', 'oauth2')),
    CONSTRAINT chk_email_cuenta_modo
        CHECK (modo IN ('asistida', 'automatica')),
    CONSTRAINT chk_email_cuenta_puerto
        CHECK (puerto BETWEEN 1 AND 65535),

    -- Una cuenta personal SIEMPRE tiene dueño; una institucional NUNCA.
    -- Escrito en ambas direcciones a propósito: una institucional con dueño
    -- sería una personal disfrazada, sin la protección de no guardar la
    -- credencial.
    CONSTRAINT chk_email_cuenta_propietario
        CHECK (
            (tipo = 'personal'      AND propietario_user_id IS NOT NULL) OR
            (tipo = 'institucional' AND propietario_user_id IS NULL)
        ),

    -- La credencial de una cuenta personal no se guarda: ni cifrada.
    CONSTRAINT chk_email_cuenta_personal_sin_secreto
        CHECK (tipo <> 'personal' OR (secreto_cifrado IS NULL AND oauth_refresh_cifrado IS NULL)),

    -- Una cuenta OAuth2 necesita la aplicación de su entidad; una de contraseña
    -- no debe apuntar a ninguna. Sin esta regla, una cuenta podría quedar en
    -- OAuth2 sin aplicación y el fallo aparecería en el primer sondeo, lejos de
    -- donde se cometió.
    CONSTRAINT chk_email_cuenta_app_segun_auth
        CHECK (
            (auth_tipo = 'oauth2'   AND app_oauth_id IS NOT NULL) OR
            (auth_tipo = 'password' AND app_oauth_id IS NULL)
        ),

    -- Y una cuenta con contraseña necesita la contraseña, salvo que sea
    -- personal (cuya credencial se pide en cada sesión y nunca se guarda).
    CONSTRAINT chk_email_cuenta_password_tiene_secreto
        CHECK (
            auth_tipo <> 'password'
            OR tipo = 'personal'
            OR secreto_cifrado IS NOT NULL
        ),

    CONSTRAINT fk_email_cuenta_app_oauth
        FOREIGN KEY (app_oauth_id) REFERENCES email_app_oauth (id) ON DELETE RESTRICT,

    -- Ciphertext y nonce viajan juntos o no viajan: media pareja es un secreto
    -- que no se puede descifrar y que nadie descubre hasta el primer sondeo.
    CONSTRAINT chk_email_cuenta_secreto_par
        CHECK ((secreto_cifrado IS NULL) = (secreto_nonce IS NULL)),
    CONSTRAINT chk_email_cuenta_oauth_par
        CHECK ((oauth_refresh_cifrado IS NULL) = (oauth_refresh_nonce IS NULL)),

    -- Las dos carpetas no pueden ser la misma, o mover el correo tras radicarlo
    -- lo dejaría donde estaba y volvería a ofrecerse indefinidamente.
    CONSTRAINT chk_email_cuenta_carpetas_distintas
        CHECK (carpeta_origen <> carpeta_radicados)
);

-- Una dirección no puede estar dos veces como cuenta institucional: dos filas
-- para el mismo buzón serían dos bandejas del mismo correo, y la reclamación
-- de una no protegería a la otra.
CREATE UNIQUE INDEX IF NOT EXISTS ux_email_cuenta_institucional_direccion
    ON email_cuenta (lower(direccion))
    WHERE tipo = 'institucional';

-- La cuenta personal es única por (dueño, dirección).
CREATE UNIQUE INDEX IF NOT EXISTS ux_email_cuenta_personal
    ON email_cuenta (propietario_user_id, lower(direccion))
    WHERE tipo = 'personal';

CREATE INDEX IF NOT EXISTS ix_email_cuenta_activa
    ON email_cuenta (activa, tipo);


-- ============================================================================
-- Quién puede trabajar cada cuenta institucional.
--
-- Es la «asignación de responsabilidades» del art. 13 hecha dato. Sin esta
-- tabla, «cuenta compartida» significaría «cualquiera con el permiso», y la
-- entidad no podría decir quién responde por la ventanilla de PQRS frente a la
-- de notificaciones judiciales.
-- ============================================================================

CREATE TABLE IF NOT EXISTS email_cuenta_usuario (
    cuenta_id   UUID NOT NULL REFERENCES email_cuenta (id) ON DELETE CASCADE,
    user_id     UUID NOT NULL,
    asignado_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    asignado_by UUID,
    PRIMARY KEY (cuenta_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_email_cuenta_usuario_user
    ON email_cuenta_usuario (user_id);
