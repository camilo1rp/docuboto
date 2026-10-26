// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/pinar/api/planes/[id] — Editar el plan en borrador (AREA-12-03).
 *
 * PATCH → `PATCH /pinar/planes/{id}` (USUA_PERM_ADMIN en el backend). Solo con
 * el plan en `borrador` (409 `plan_no_editable` en otro caso, reenviado tal
 * cual). Allowlist = `PlanUpdate`: omitir un campo es «no lo toques»; el
 * `acto_administrativo` NO se edita aquí, se declara al aprobar.
 */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

const FECHA = /^\d{4}-\d{2}-\d{2}$/;

export const PATCH = mutacion({
  method: "PATCH",
  path: (p) => (UUID_RE.test(p.id ?? "") ? `/planes/${p.id}` : null),
  campos: ["vision", "vigencia_desde", "vigencia_hasta"],
  validar: (c) => {
    for (const k of ["vigencia_desde", "vigencia_hasta"]) {
      if (c[k] != null && !FECHA.test(String(c[k]))) return "Las fechas van como AAAA-MM-DD.";
    }
    return Object.keys(c).length ? null : "No hay nada que modificar.";
  },
});
