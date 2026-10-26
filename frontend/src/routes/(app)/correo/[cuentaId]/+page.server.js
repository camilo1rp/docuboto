// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /correo/[cuentaId] — Bandeja de un buzón institucional (E19 Increment 2).
 *
 * Carga la lista de correos pendientes y los datos de la cuenta. **No
 * sincroniza**: traer del buzón es un acto que el operador dispara, no un efecto
 * de abrir una pantalla — y aunque sincronizar no radique nada, descargar un
 * buzón entero cada vez que alguien mira la bandeja sería un coste que nadie
 * pidió.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch, params }) {
  const { user } = await parent();

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${cookies.get(COOKIE_ACCESS_TOKEN) ?? ""}`,
    "X-Tenant-Slug": user?.tenant_slug ?? "",
  };
  const userId = user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  const cuentaId = params.cuentaId;

  /**
   * @param {string} url
   * @param {any} vacio
   * @returns {Promise<[any, {detail: string, status: number} | null]>}
   */
  async function traer(url, vacio) {
    try {
      const res = await fetch(url, { headers });
      if (res.ok) return [await res.json().catch(() => vacio), null];
      const cuerpo = await res.json().catch(() => ({}));
      return [vacio, { detail: mensajeError(cuerpo, `Error ${res.status}`), status: res.status }];
    } catch {
      return [vacio, { detail: "No se pudo conectar al servidor.", status: 503 }];
    }
  }

  const [correos, correosError] = await traer(
    `${API_V1}/email/cuentas/${encodeURIComponent(cuentaId)}/bandeja`,
    [],
  );
  // La cuenta se lee del listado general: no hay endpoint de detalle, y pedir
  // uno solo para el nombre añadiría una ruta que nadie más usaría.
  const [cuentas] = await traer(`${API_V1}/email/cuentas`, []);
  const cuenta = Array.isArray(cuentas) ? cuentas.find((c) => c.id === cuentaId) : null;

  // Los dos vocabularios que el funcionario elige al radicar. Antes se
  // escribían a mano, y eso tenía dos costes distintos: la dependencia quedaba
  // como texto libre —sin vínculo con el organigrama, así que el radicado no
  // aparecía al repartir por código y «Jurídica» y «Juridica» eran dos áreas— y
  // el tipo documental escrito a mano no casa con ninguna serie de la TRD, que
  // es la que gobierna cuánto se conserva. Un radicado nacido en ventanilla
  // nunca tuvo ese problema: `/radicar` siempre ofreció selectores.
  const [dependencias] = await traer(`${API_V1}/dependencias?activo=true`, []);
  const [tiposDocumentales] = await traer(`${API_V1}/tipos-documentales?activo=true`, []);

  return {
    cuentaId,
    cuenta: cuenta ?? null,
    correos: Array.isArray(correos) ? correos : [],
    correosError,
    dependencias: Array.isArray(dependencias) ? dependencias : [],
    tiposDocumentales: Array.isArray(tiposDocumentales) ? tiposDocumentales : [],
    // Quién soy, para distinguir «lo tengo yo» de «lo tiene otro» sin exponer la
    // identidad de los demás.
    //
    // **`user_id` y NO `sub`.** El `sub` es el identificador de Keycloak; la
    // autoría que el backend escribe —aquí, `reservado_por`— es la identidad
    // interna del tenant (`auth_users.id`). Son distintos, y compararlos con el
    // equivocado hace que un funcionario **nunca reconozca sus propios
    // correos**: los vería todos como ajenos y, con los botones deshabilitados,
    // no podría ni continuar con el que él mismo abrió. El propio
    // `auth.server.js` lo advierte —«confundirlos es lo que dejó sin funcionar
    // tres pantallas»— y esta iba a ser la cuarta.
    yo: user?.user_id ?? user?.context?.user_id ?? "",
  };
}
