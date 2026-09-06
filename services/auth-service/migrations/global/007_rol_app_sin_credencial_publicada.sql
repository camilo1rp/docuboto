-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Software libre: se distribuye bajo la Licencia Pública General Affero de GNU
-- (AGPL) versión 3 o posterior, conservando los términos de la GPL del Orfeo
-- original del que deriva. SIN GARANTÍA ALGUNA. Véase LICENSE.

-- ===========================================================================
-- El rol de aplicación no puede llevar una contraseña publicada
-- ===========================================================================
--
-- La migración 003 crea `orpycamcp_app` con `LOGIN PASSWORD
-- 'CHANGE_IN_PRODUCTION'`, y ese literal está en un repositorio público bajo
-- AGPL. La nota decía «DEBE cambiarse antes del primer despliegue productivo»,
-- pero **ningún servicio usa ese rol** —el `DATABASE_URL` de todos apunta al rol
-- de arranque— así que nadie tiene un motivo operativo para cambiarla: no falla
-- nada si se deja como está. Una credencial que solo protege un aviso escrito no
-- protege nada.
--
-- No es un rol inofensivo: tiene INSERT sobre `public.audit_log`, que es la
-- tabla GLOBAL con la traza de todas las entidades. Poder insertar ahí es poder
-- fabricar asientos de auditoría a nombre de cualquiera.
--
-- Se le quita la capacidad de conectar en vez de cambiarle la contraseña por
-- otra: cualquier valor que se escriba aquí vuelve a estar publicado el día que
-- se hace push. El rol sigue existiendo con sus permisos —para que el
-- endurecimiento previsto se pueda activar sin rehacerlo— pero el operador tiene
-- que habilitarlo a mano, con un secreto suyo:
--
--     ALTER ROLE orpycamcp_app LOGIN PASSWORD '<secreto del despliegue>';
--
-- Idempotente y sin efecto sobre un despliegue que YA lo habilitó con su propia
-- contraseña: solo actúa si la contraseña vigente es exactamente la publicada.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        RETURN;
    END IF;

    -- Se retira el LOGIN sin condiciones, y es deliberado. Se comprobó que
    -- NINGÚN servicio conecta con este rol —`grep orpycamcp_app` sobre el
    -- repositorio entero solo devuelve su propia migración, y en la pila viva
    -- todas las conexiones son del rol de arranque—, así que quitárselo no deja
    -- a nadie fuera. Un despliegue que sí lo hubiera cableado con su propio
    -- secreto lo vuelve a habilitar con una línea, y el aviso se la da.
    --
    -- Se evitó la alternativa «cambiarlo solo si la contraseña sigue siendo la
    -- publicada»: comparar hashes de Postgres es frágil —md5 y SCRAM no se
    -- comparan igual— y una condición que no se puede justificar es peor que
    -- ninguna, porque decide en silencio por un motivo que nadie puede releer.
    ALTER ROLE orpycamcp_app NOLOGIN;

    -- El COMMENT va DENTRO del bloque, no como sentencia suelta: `COMMENT ON
    -- ROLE` sobre un rol inexistente aborta la migración entera, y «el rol no
    -- existe» es un estado alcanzable (una base donde 003 no llegó a aplicarse).
    -- Se detectó probando ese caso contra Postgres real, no leyendo el fichero.
    EXECUTE $c$
        COMMENT ON ROLE orpycamcp_app IS
            'Rol de minimo privilegio para audit_log (INSERT+SELECT). Sin LOGIN: '
            'el operador debe darle una contrasena propia antes de usarlo. Hoy '
            'ningun servicio conecta con el; ver la auditoria de segunda pasada.'
    $c$;

    RAISE NOTICE
        'orpycamcp_app queda SIN LOGIN: llevaba la contrasena publicada en el '
        'repositorio y ningun servicio lo usa. Para activarlo con un secreto '
        'propio: ALTER ROLE orpycamcp_app LOGIN PASSWORD ''<secreto>'';';
END;
$$;
