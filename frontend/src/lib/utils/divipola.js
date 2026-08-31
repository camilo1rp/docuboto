// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Acceso al catálogo geográfico DIVIPOLA (E01, RF-RAD-05).
 *
 * Vive aparte del componente por una razón concreta: los 33 departamentos no
 * cambian mientras la página está abierta, y cada apertura de un modal montaba
 * una instancia nueva que volvía a pedirlos. El caché tiene que ser de la
 * PÁGINA, no del componente.
 *
 * Solo se cachea el ÉXITO. Guardar un fallo dejaría la cascada rota hasta
 * recargar, cuando el siguiente intento podría funcionar.
 */
let cacheDepartamentos = null;

async function pedir(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(String(res.status));
  return await res.json();
}

/** Los 33 departamentos, una sola vez por carga de página. */
export async function cargarDepartamentos() {
  if (cacheDepartamentos) return cacheDepartamentos;
  const datos = await pedir("/radicar/api/divipola");
  cacheDepartamentos = datos;
  return datos;
}

/**
 * Los municipios de UN departamento. No se cachean: son 33 listas y quien
 * compone un radicado toca una o dos.
 * @param {string} departamentoCodigo
 */
export async function cargarMunicipios(departamentoCodigo) {
  return await pedir(
    `/radicar/api/divipola?departamento=${encodeURIComponent(departamentoCodigo)}`,
  );
}

/** Vacía el caché. Existe para las pruebas: sin esto, un caso hereda la carga
 * del anterior y el que simula el catálogo caído nunca lo ve caído. */
export function _reiniciarCacheDivipola() {
  cacheDepartamentos = null;
}
