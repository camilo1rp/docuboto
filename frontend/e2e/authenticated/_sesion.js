// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Las rutas del estado de sesión viven aquí y no en el propio setup porque
 * Playwright prohíbe que una spec importe otro fichero de test — y las specs
 * necesitan esa ruta para declarar `storageState`. El helper no es un test, así
 * que es el sitio.
 */
/**
 * Identidades de la suite, y por qué son VARIAS.
 *
 * La cuota del gateway es por persona (120 peticiones/min). Una pantalla cuesta
 * unas 7 peticiones, así que la suite entera —58 casos en minuto y medio— hace
 * del orden de 400 peticiones: con una sola identidad las concentra todas en un
 * cubo y se come su propio 429 a mitad de la corrida. En producción esas mismas
 * 400 las reparten veinte personas.
 *
 * Repartirlas NO es esquivar el límite: es dejar de pedirle a una identidad que
 * represente a una oficina. Subir el tope solo para las pruebas taparía
 * precisamente el eje que existe para que la cuota sea de cada quien.
 *
 * Los tres usuarios `e2eN` están en `infra/keycloak/realm-dev.json` y comparten
 * **el mismo grupo** que `operador` —«Demostración», los 25 permisos—, así que
 * repartir la carga no cambia el sujeto de la prueba. Si fueran perfiles
 * distintos, un fallo de permisos se leería como un fallo de la pantalla.
 */
export const IDENTIDADES = ["e2e1", "e2e2", "e2e3"];

/**
 * Identidad RESTRINGIDA: `consulta`, del grupo «Consulta (solo lectura, nivel 1)»
 * de `scripts/seed_demo.py` — PERM_RADI y USUA_PERM_EXPEDIENTE con nivel 1, SIN
 * USUA_PERM_ADMIN. Es el segundo rol que la suite necesitaba: con tres
 * identidades del mismo grupo, un control que debía desaparecer y un backend que
 * debía negarse eran indistinguibles de que no hubiera control ninguno.
 * No forma parte de `IDENTIDADES` (el reparto de cuota) a propósito: su sujeto de
 * prueba es justo que NO es como `operador`.
 */
export const RESTRINGIDA = "consulta";

/** Dónde queda el estado de sesión de cada identidad. */
export function estadoDe(usuario) {
  return `playwright/.auth/${usuario}.json`;
}

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
 * Quedaba una **cuarta** copia que este mismo texto no contaba: la de
 * `asistente-dock.spec.js`, que era la que funcionaba y por eso nadie la
 * miró al unificar. Retirada (2026-09-19): hoy ninguna spec inicia sesión
 * por su cuenta — lo hace `_auth.setup.js` una sola vez y las demás heredan
 * `ESTADO_SESION`. Este helper lo usan el setup y la prueba del propio login.
 *
 * Se localiza por los **id del formulario de Keycloak** (`#username`,
 * `#password`, `#kc-login`), que es la copia que sí funcionaba
 * (`asistente-dock.spec.js`, retirada con el asistente en ADR-046 §6). No es una preferencia de estilo: el nombre
 * accesible de esa página lo fija un tema de terceros que puede cambiar de
 * idioma —hoy sale en inglés— mientras los id son su contrato estable.
 *
 * @param {import('@playwright/test').Page} page
 * @param {{ destino?: RegExp }} [opciones] `destino` es la URL a la que se
 *   espera llegar tras entrar; por defecto `/dashboard`.
 */
export async function iniciarSesion(page, { destino = /\/dashboard/, usuario } = {}) {
  // `usuario` permite entrar como una de las identidades de reparto
  // (`IDENTIDADES`). Sin él manda `E2E_USERNAME`, que sigue siendo quien decide
  // con qué cuenta se prueba el inicio de sesión en sí.
  const username = usuario ?? process.env.E2E_USERNAME;
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
