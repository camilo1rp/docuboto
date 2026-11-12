-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- PERM_CONOCIMIENTO (ADR-037, tanda C-1): quién puede consultar la base de conocimiento
-- (acceso y circulación restringida, Ley 1581 art. 4 lit. f).
--
-- Hasta hoy las cuatro rutas de knowledge-service (/search, /antecedentes, /rag y
-- /suggestions/{id}/aceptar) no pedían permiso: cualquier autenticado de la entidad
-- las usaba. Nivel 1 (Leer) = buscar, antecedentes y respuestas con citas; nivel 2
-- (Editar) = aceptar una sugerencia propia. Además exige que la entidad tenga
-- activado el asistente (public.tenants.asistente_habilitado) y respeta el nivel de
-- reserva (clearance), que sigue en el WHERE.
--
-- Para no producir un fail-closed mudo, el permiso se concede a todo grupo que exista
-- al aplicar la migración Y que ya tenga algún permiso con nivel >= 1 (un grupo vacío
-- no estaba usando nada; recibir acceso a la IA sin pedirlo sería un permiso nuevo, no
-- la continuidad). El nivel sale del propio grupo: quien solo tiene permisos de lectura
-- queda en 1 (consulta, no acepta); quien escribe queda en 2. En un tenant
-- nuevo la migración corre durante la provisión, antes de que haya grupos: no concede
-- nada y el administrador lo reparte a conciencia. ROOT no necesita fila (is_root).

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('PERM_CONOCIMIENTO',
   'Usar la base de conocimiento: búsqueda semántica, antecedentes y respuestas con citas (nivel 1); aceptar una sugerencia propia (nivel 2). Además exige que la entidad tenga activado el asistente y respeta el nivel de reserva.')
ON CONFLICT (nombre) DO NOTHING;

INSERT INTO auth_group_permissions (group_id, permission_id, crud)
SELECT g.id, p.id, LEAST(2, mx.max_crud)
  FROM auth_groups g
  CROSS JOIN auth_permissions p
  CROSS JOIN LATERAL (
        SELECT max(gp.crud) AS max_crud FROM auth_group_permissions gp WHERE gp.group_id = g.id
       ) mx
 WHERE p.nombre = 'PERM_CONOCIMIENTO'
   AND mx.max_crud >= 1   -- un grupo sin permisos (o todos en 0) no gana uno por esta vía
ON CONFLICT (group_id, permission_id) DO NOTHING;
