// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/+page.server.js — Guarda SSR del dashboard de administración (Fase 0).
 *
 * Esta pantalla es solo un índice de tarjetas hacia subrutas de administración
 * (D-05, ADR-020): no llama al gateway ni expone datos remotos, así que no
 * necesita `fetch` ni `locals.accessToken` propios. Existe como
 * `+page.server.js` por dos motivos:
 *   1. Simetría con el resto de `(app)/**`: la carga de `user`/permisos vive
 *      en `(app)/+layout.server.js` (guard de sesión), y este archivo es el
 *      punto donde una futura tarjeta con datos agregados (p. ej. conteo de
 *      colas de errores) añadiría su propio `fetch` sin reestructurar la ruta.
 *   2. Deja claro que la lista de tarjetas (`ADMIN_AREAS`, en `+page.svelte`)
 *      es un catálogo estático de la UI, no datos del backend — evita que
 *      alguien intente "cargarlas" del gateway más adelante.
 */

export async function load() {
  return {};
}
