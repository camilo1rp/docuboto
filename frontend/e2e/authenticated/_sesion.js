// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Iniciar sesión contra el Keycloak real — UN solo sitio que sepa hacerlo.
 *
 * Estaba copiado en tres archivos, y dos de las copias usaban
 * `getByLabel(/contraseña|password/i)`, que en el tema por defecto de Keycloak
 * **casa con dos elementos**: el `<input>` y el botón de mostrar/ocultar, cuyo
 * `aria-label` es «Show password». Playwright falla en modo estricto, así que
 * los cuatro tests de esas dos copias caían antes de llegar a comprobar nada
 * — y ninguno se había ejecutado nunca desde que se escribieron.
 *
 * Se localiza por los **id del formulario de Keycloak** (`#username`,
 * `#password`, `#kc-login`), que es la copia que sí funcionaba
 * (`asistente-dock.spec.js`). No es una preferencia de estilo: el nombre
 * accesible de esa página lo fija un tema de terceros que puede cambiar de
 * idioma —hoy sale en inglés— mientras los id son su contrato estable.
 *
 * @param {import('@playwright/test').Page} page
 * @param {{ destino?: RegExp }} [opciones] `destino` es la URL a la que se
 *   espera llegar tras entrar; por defecto `/dashboard`.
 */
export async function iniciarSesion(page, { destino = /\/dashboard/ } = {}) {
  const username = process.env.E2E_USERNAME;
  const password = process.env.E2E_PASSWORD;
  if (!username || !password) {
    throw new Error(
      "Faltan E2E_USERNAME/E2E_PASSWORD. No tienen valor por defecto a propósito: " +
        "un default funcional acabaría commiteado y sería una cuenta conocida más.",
    );
  }

  await page.goto("/login");
  await page.fill("#username", username);
  await page.fill("#password", password);
  await Promise.all([page.waitForURL(destino), page.click("#kc-login")]);
}

/** ¿Hay credenciales para los casos que necesitan identidad? */
export function hayCredenciales() {
  return Boolean(process.env.E2E_USERNAME && process.env.E2E_PASSWORD);
}
