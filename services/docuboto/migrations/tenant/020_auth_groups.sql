-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Groups, permissions and memberships.

-- ── auth_groups ───────────────────────────────────────────────────────────────

CREATE TABLE auth_groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(120) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT auth_groups_name_key UNIQUE (name),
    CONSTRAINT auth_groups_pkey PRIMARY KEY (id)
);

-- ── auth_permissions ──────────────────────────────────────────────────────────

CREATE TABLE auth_permissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nombre character varying(80) NOT NULL,
    descripcion text,
    CONSTRAINT auth_permissions_nombre_key UNIQUE (nombre),
    CONSTRAINT auth_permissions_pkey PRIMARY KEY (id)
);

INSERT INTO auth_permissions (nombre, descripcion) VALUES
    ('PERM_AGENDAR', 'Ejecutar la transacción de agendar en el flujo'),
    ('PERM_ANULAR', 'Ejecutar la transacción de anulación en el flujo'),
    ('PERM_APRO_ANULAR', 'RESERVADO — hoy NO restringe nada. Aprobar una anulación exige PERM_PANU_CODI.'),
    ('PERM_BORRAR_ANEXO', 'RESERVADO — hoy NO restringe nada: ningún gate del sistema lo consulta.'),
    ('PERM_CERRAR_EXP', 'Ejecutar la transacción de cierre de expediente en el flujo'),
    ('PERM_CONOCIMIENTO', 'Usar la base de conocimiento: búsqueda semántica, antecedentes y respuestas con citas (nivel 1); aceptar una sugerencia propia (nivel 2). Además exige que la entidad tenga activado el asistente y respeta el nivel de reserva.'),
    ('PERM_DESCARGAEXP', 'RESERVADO — hoy NO restringe nada. Descargar un expediente exige USUA_PERM_EXPEDIENTE.'),
    ('PERM_DLQ_ADMIN', 'Inspeccionar y reprocesar el dead-letter de eventos'),
    ('PERM_DUE_DATE', 'Corregir el vencimiento de un radicado (nivel 3). Acortar se permite; ampliar se niega al asignado del paso y si la fecha pasa del doble del término inicial.'),
    ('PERM_FIRMA', 'Firma de documentos'),
    ('PERM_INFORMAR', 'Ejecutar la transacción de informar en el flujo'),
    ('PERM_NRR', 'RESERVADO — hoy NO restringe nada. La transacción «no requiere respuesta» exige PERM_TRAMITAR.'),
    ('PERM_PANU_CODI', 'Anulación nivel jefe'),
    ('PERM_RADI', 'Permiso base de radicación'),
    ('PERM_RADI_ENTRADA', 'RESERVADO — hoy NO restringe nada: los tres tipos de radicado exigen solo PERM_RADI.'),
    ('PERM_RADI_INTERNO', 'RESERVADO — hoy NO restringe nada: los tres tipos de radicado exigen solo PERM_RADI.'),
    ('PERM_RADI_SALIDA', 'Gestionar ENVÍOS POSTALES de salida (no restringe radicar Salidas: eso exige solo PERM_RADI).'),
    ('PERM_RECLASIFICAR', 'Reclasificar el nivel de seguridad de un radicado'),
    ('PERM_RESPUESTA_VINCULADA', 'Crear respuesta vinculada (Salida RE:, legado USUA_PERM_RESPUESTA)'),
    ('PERM_SOL_ANULAR', 'Solicitar anulación de radicado (legado SGD_PANU_CODI crud=1)'),
    ('PERM_TIPIF_ANEXO', 'RESERVADO — hoy NO restringe nada: ningún gate del sistema lo consulta.'),
    ('PERM_TRAMITAR', 'Tramitar: asignar, trasladar, completar pasos, devolver y revertir'),
    ('PERM_VOBO', 'Dar visto bueno a un documento en trámite'),
    ('SGD_PERM_ESTADISTICA', 'Acceso a estadísticas'),
    ('USUA_PERM_ADMIN', 'Administración de usuarios, grupos y permisos'),
    ('USUA_PERM_EXPEDIENTE', 'Gestión de expedientes'),
    ('USUA_PERM_ROOT', 'Bypass total de permisos'),
    ('USUA_PERM_TRD', 'Administración de TRD');

-- ── auth_group_permissions ────────────────────────────────────────────────────

CREATE TABLE auth_group_permissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    group_id uuid NOT NULL,
    permission_id uuid NOT NULL,
    crud smallint DEFAULT 0 NOT NULL,
    CONSTRAINT auth_group_permissions_crud_check CHECK (((crud >= 0) AND (crud <= 5))),
    CONSTRAINT auth_group_permissions_group_id_permission_id_key UNIQUE (group_id, permission_id),
    CONSTRAINT auth_group_permissions_pkey PRIMARY KEY (id),
    CONSTRAINT auth_group_permissions_group_id_fkey FOREIGN KEY (group_id) REFERENCES auth_groups(id) ON DELETE CASCADE,
    CONSTRAINT auth_group_permissions_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES auth_permissions(id) ON DELETE CASCADE
);

CREATE INDEX ix_auth_group_permissions_group ON auth_group_permissions USING btree (group_id);

-- ── auth_memberships ──────────────────────────────────────────────────────────

CREATE TABLE auth_memberships (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    group_id uuid NOT NULL,
    CONSTRAINT auth_memberships_pkey PRIMARY KEY (id),
    CONSTRAINT auth_memberships_user_id_group_id_key UNIQUE (user_id, group_id),
    CONSTRAINT auth_memberships_group_id_fkey FOREIGN KEY (group_id) REFERENCES auth_groups(id) ON DELETE CASCADE,
    CONSTRAINT auth_memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

CREATE INDEX ix_auth_memberships_user ON auth_memberships USING btree (user_id);

-- ── urd_usuaroldep ────────────────────────────────────────────────────────────

CREATE TABLE urd_usuaroldep (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    depe_id integer NOT NULL,
    rol_id uuid NOT NULL,
    es_principal boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT urd_usuaroldep_pkey PRIMARY KEY (id),
    CONSTRAINT urd_usuaroldep_user_id_depe_id_key UNIQUE (user_id, depe_id),
    CONSTRAINT urd_usuaroldep_rol_id_fkey FOREIGN KEY (rol_id) REFERENCES auth_groups(id),
    CONSTRAINT urd_usuaroldep_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ux_urd_principal ON urd_usuaroldep USING btree (user_id) WHERE (es_principal = true);
