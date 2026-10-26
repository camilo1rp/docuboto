// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Cómo se nombra una ubicación del depósito en pantalla.
 *
 * Vive aquí, y no en la pantalla que lo escribió primero, porque lo necesitan
 * dos: `/archivo-fisico` y `/transferencias`. La segunda pedía el **UUID** de
 * la ubicación escrito a mano —igual que hacía la primera antes de corregirla—,
 * y un identificador que ninguna pantalla muestra no se puede escribir sin
 * abrir la base de datos.
 *
 * La ruta importa: `GAV-03` no dice dónde está, y en un depósito hay muchas
 * gavetas tres. `SED-001 / DEP-001 · Depósito Central 1` sí ubica.
 */

/**
 * @param {{codigo?: string, nombre?: string, ruta?: string}} u
 * @returns {string}
 */
export function etiquetaUbicacion(u) {
  const codigo = u?.codigo ?? "";
  const nombre = u?.nombre ?? "";
  const base = `${codigo} — ${nombre}`;
  return u?.ruta && u.ruta !== codigo ? `${u.ruta} · ${nombre}` : base;
}
