// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (app)/admin/+layout.server.js — Espejo UX del RBAC de /admin (AREA-12-07).
 *
 * Forzar `/admin/usuarios` sin `USUA_PERM_ADMIN` redirige a /403 (la ruta
 * existía y nada la usaba). El permiso exigido es el de cada área
 * (`$lib/utils/adminGate.js`), no uno único: TRD, terceros, colas y
 * preservación tienen el suyo. El backend sigue re-validando cada llamada.
 *
 * Si `/auth/me` degradó (`permisosDegradados`), las listas vienen vacías sin que
 * nadie haya perdido nada: no se expulsa a quien solo sufre una caída momentánea
 * — el backend responderá 403 por sí mismo.
 */
import { redirect } from "@sveltejs/kit";
import { ROUTES } from "$lib/utils/config.js";
import { permisosRequeridosAdmin } from "$lib/utils/adminGate.js";

/** @type {import('./$types').LayoutServerLoad} */
export async function load({ parent, url }) {
  const requeridos = permisosRequeridosAdmin(url.pathname);
  if (!requeridos) return {};

  const { user, entidadNoOperativa } = await parent();
  // Entidad no operativa (ADR-035): el layout (app) pinta su pantalla; no se
  // expulsa a /403 a quien no perdió ningún permiso.
  if (entidadNoOperativa || user?.permisosDegradados) return {};

  const tiene = user?.permissions ?? [];
  if (!requeridos.some((p) => tiene.includes(p))) {
    redirect(303, ROUTES.forbidden);
  }
  return {};
}
