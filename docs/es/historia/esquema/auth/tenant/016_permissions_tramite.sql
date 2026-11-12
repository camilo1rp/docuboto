-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Los SEIS permisos de trámite que el código exige y que NADIE podía tener.
--
-- Hallazgo de la verificación extremo a extremo del plan MCP funcional
-- (2026-08-16), con un tenant real levantado: `tenant_demo.auth_permissions`
-- traía 20 filas y ninguna de estas. No es una omisión menor:
--
--   * `PERM_TRAMITAR` gatea `assign`, `transfer`, `complete`, `devolver` y
--     `rollback` en workflow-service, y es el suelo de `execute_transaction`.
--   * Los otros cinco los declaran las filas sembradas de `transaction_types`
--     (`workflow-service/migrations/tenant/007`) en su columna
--     `requires_permission`.
--
-- Un permiso que no está en `auth_permissions` no se puede conceder a ningún
-- grupo, así que `resolve_effective_permissions` nunca lo devuelve y el gate
-- niega a todo el mundo salvo a ROOT, que hace bypass. Resultado: **los 12 tipos
-- de transacción sembrados y las cinco escrituras de trámite eran ROOT-only**,
-- falla cerrado —el lado correcto— pero es capacidad que existe en el código y
-- que ninguna entidad podía repartir entre sus funcionarios.
--
-- Es el mismo defecto que `RADI_CREAR`, que abrió este plan: una cadena de
-- permiso inventada que solo vivía en el código que la exigía. Aquel se cazó
-- porque alguien fue a usar la tool; estos, porque se levantó el stack. El
-- guardián que debía cazarlos —`test_todo_permiso_del_catalogo_existe_en_el_
-- sistema`— llevaba desde su primer commit **saltándose** en cada corrida: vive
-- en la imagen de mcp-server, que no monta el repositorio, así que no
-- encontraba las migraciones y hacía `skip`. Se movió a `shared/tests/` (el
-- único carril de CI que ve el repositorio entero) y allí ya no puede saltar.
--
-- Los nombres NO se inventan aquí: se toman literalmente de lo que el código y
-- las migraciones de workflow-service ya exigen. Cambiar el nombre en el gate
-- habría sido la corrección de menos riesgo aparente y la peor: dejaría las
-- filas de `transaction_types` de las entidades ya provisionadas apuntando a un
-- permiso que vuelve a no existir.

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('PERM_TRAMITAR',   'Tramitar: asignar, trasladar, completar pasos, devolver y revertir'),
  ('PERM_VOBO',       'Dar visto bueno a un documento en trámite'),
  ('PERM_ANULAR',     'Ejecutar la transacción de anulación en el flujo'),
  ('PERM_CERRAR_EXP', 'Ejecutar la transacción de cierre de expediente en el flujo'),
  ('PERM_INFORMAR',   'Ejecutar la transacción de informar en el flujo'),
  ('PERM_AGENDAR',    'Ejecutar la transacción de agendar en el flujo')
ON CONFLICT (nombre) DO NOTHING;
