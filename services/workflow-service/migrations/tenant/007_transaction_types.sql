-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E05, RF-FLU-01): catálogo de transacciones de trámite.
--
-- Cada transacción de trámite tiene un código, el permiso atómico que exige
-- (enforcement delegado a E08/gateway) y el efecto opcional sobre el estado del
-- paso activo. El motor genérico (POST /workflows/{id}/transactions) registra
-- siempre un evento append-only y aplica `target_step_status` si está definido.

CREATE TABLE IF NOT EXISTS transaction_types (
    code                VARCHAR(40)  NOT NULL PRIMARY KEY,
    nombre              TEXT         NOT NULL,
    descripcion         TEXT,
    requires_permission TEXT,                 -- permiso atómico (informativo en workflow)
    target_step_status  VARCHAR(20),          -- estado a aplicar al paso activo (o NULL)
    activo              BOOLEAN      NOT NULL DEFAULT true
);

INSERT INTO transaction_types (code, nombre, requires_permission, target_step_status) VALUES
    ('informar',          'Informar (copia)',          'PERM_INFORMAR',      NULL),
    ('borrar_informado',  'Borrar informado',          'PERM_INFORMAR',      NULL),
    ('nrr',               'No requiere respuesta',     'PERM_TRAMITAR',      NULL),
    ('agendar',           'Agendar',                   'PERM_AGENDAR',       NULL),
    ('no_agendar',        'Quitar de agenda',          'PERM_AGENDAR',       NULL),
    ('change_folder',     'Cambiar de carpeta',        'PERM_TRAMITAR',      NULL),
    ('marcar_leido',      'Marcar como leído',         'PERM_TRAMITAR',      'in_progress'),
    ('validate_trd_send', 'Validar TRD para envío',    'PERM_TRAMITAR',      NULL),
    ('solicitar_firma',   'Solicitar firma',           'PERM_FIRMA',         NULL),
    ('vobo',              'Visto bueno',               'PERM_VOBO',          NULL),
    ('cerrar_exp',        'Cerrar expediente',         'PERM_CERRAR_EXP',    NULL),
    ('anular',            'Anular',                    'PERM_ANULAR',        'cancelled')
ON CONFLICT (code) DO NOTHING;
