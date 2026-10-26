// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * una-sola-puerta-de-sesion.test.js — ninguna spec teclea credenciales por su
 * cuenta.
 *
 * Historia del defecto que vigila: el inicio de sesión contra Keycloak estuvo
 * copiado en cuatro archivos. Tres se unificaron en `_sesion.js` y el texto de
 * aquel helper declaró el trabajo terminado — pero **contaba tres copias y
 * había cuatro**. La cuarta (`asistente-dock.spec.js`) se quedó fuera
 * precisamente porque era la que funcionaba, así que nada la delató: un
 * guardián de entonces habría encontrado cero culpables y una copia viva.
 *
 * Por qué importa más allá del duplicado: cada copia es un formulario de
 * Keycloak localizado a mano, y el tema de esa página lo fija un tercero. Las
 * dos copias que localizaban por etiqueta accesible llevaban desde que se
 * escribieron cayendo antes de comprobar nada. Y con `storageState` la sesión
 * se abre **una vez** para toda la suite: una copia suelta vuelve a pagar un
 * login por test.
 *
 * Excepciones DECLARADAS, no descubiertas: `_auth.setup.js` es quien abre la
 * sesión, y `login-flow.spec.js` es la prueba del propio inicio de sesión —la
 * única que debe ejercitar el formulario, y por eso se aísla del estado
 * guardado—. Las dos llaman al helper; ninguna reimplementa el formulario.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const SUITE = resolve(AQUI, "../../e2e/authenticated");

/** Los id del formulario de Keycloak: tocarlos es reimplementar el login. */
const TECLEA_CREDENCIALES = /#username|#password|#kc-login/;

/** Quien SÍ puede hablar con el formulario, con su motivo escrito. */
const PUEDEN = new Set([
  "_sesion.js", // el helper: el único sitio que sabe hacerlo
  "_auth.setup.js", // abre la sesión una vez para toda la suite
  "login-flow.spec.js", // prueba el inicio de sesión: es su objeto
]);

const ficheros = readdirSync(SUITE).filter((f) => f.endsWith(".js"));

describe("una sola puerta de sesión en la suite autenticada", () => {
  it("el censo encuentra specs que mirar (control de vacuidad)", () => {
    // Sin esto, «ninguna spec teclea credenciales» y «no miré ninguna spec» se
    // leen igual — que es exactamente cómo sobrevivió la cuarta copia.
    const specs = ficheros.filter((f) => f.endsWith(".spec.js"));
    expect(specs.length).toBeGreaterThanOrEqual(3);
  });

  it("solo las tres excepciones declaradas tocan el formulario de Keycloak", () => {
    const culpables = ficheros.filter(
      (f) => !PUEDEN.has(f) && TECLEA_CREDENCIALES.test(readFileSync(join(SUITE, f), "utf8")),
    );
    expect(culpables, `Reimplementan el inicio de sesión: ${culpables.join(", ")}`).toEqual([]);
  });

  it("control positivo: el guardián sabe reconocer una copia", () => {
    // Si esta comprobación cae, el detector no detecta y el test de arriba
    // pasaría en verde con la suite llena de copias.
    expect(TECLEA_CREDENCIALES.test('await page.fill("#password", password);')).toBe(true);
  });

  it("las excepciones existen de verdad y usan el helper", () => {
    // Una lista de excepciones que nombra archivos inexistentes envejece a
    // permiso en blanco: lo que no está no se vigila y nadie se entera.
    for (const nombre of PUEDEN) {
      expect(ficheros, `La excepción ${nombre} ya no existe`).toContain(nombre);
    }
    for (const nombre of ["_auth.setup.js", "login-flow.spec.js"]) {
      expect(readFileSync(join(SUITE, nombre), "utf8")).toMatch(/from "\.\/_sesion\.js"/);
    }
  });
});
