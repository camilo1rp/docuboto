// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * _auth.setup.js — iniciar sesión una vez POR IDENTIDAD y guardar cada estado.
 *
 * Por qué: la suite de adaptabilidad recorre 17 pantallas × 3 anchos, y con un
 * `beforeEach` que autentica eran **51 vueltas completas por Keycloak** —código
 * PKCE, canje del token, redirección— para 51 pruebas que usan exactamente la
 * misma identidad. Además de tardar decenas de minutos, castiga al servidor de
 * identidad con 51 autenticaciones idénticas y convierte cualquier lentitud
 * suya en un fallo de una prueba que no iba de eso.
 *
 * Playwright lo resuelve con un proyecto de preparación: esto corre primero,
 * guarda cookies y `localStorage` en un fichero, y las pruebas arrancan ya
 * dentro. La sesión de OrpycaMCP vive en una cookie httpOnly del dominio del
 * frontend (D-05), así que el estado la captura sin que el token toque nunca el
 * JavaScript de la página.
 *
 * El fichero NO se versiona (lleva una sesión viva): `playwright/.auth/` está en
 * el `.gitignore`.
 *
 * Lo que NO debe usar estos estados: `login-flow.spec.js`, que prueba el inicio
 * de sesión y tiene que empezar sin ninguna. Lo declara por su cuenta.
 *
 * Son TRES identidades, no una, y el motivo es la cuota del gateway: es por
 * persona (120 peticiones/min) y cada pantalla cuesta unas 7, así que la suite
 * entera en una sola identidad se come su propio 429 a mitad de la corrida — en
 * producción esas mismas peticiones las reparten veinte personas. El reparto no
 * esquiva el límite: deja de pedirle a una identidad que represente a una
 * oficina. Ver `IDENTIDADES` en `_sesion.js`.
 */
import { test as setup, expect } from "@playwright/test";

import { IDENTIDADES, RESTRINGIDA, estadoDe, iniciarSesion } from "./_sesion.js";

// Una sesión por cada identidad de reparto. Son logins de verdad contra
// Keycloak, pero solo TRES para toda la suite: lo que se evita es que cada una de
// las 58 pruebas abra la suya, no el login en sí.
for (const usuario of IDENTIDADES) {
  setup(`iniciar sesión como ${usuario}`, async ({ page }) => {
    await iniciarSesion(page, { usuario });
    await expect(page).toHaveURL(/\/dashboard/);
    await page.context().storageState({ path: estadoDe(usuario) });
  });
}

// La identidad de perfil restringido (AREA-12-07): su estado lo usan las specs de
// RBAC en la interfaz.
setup(`iniciar sesión como ${RESTRINGIDA} (perfil restringido)`, async ({ page }) => {
  await iniciarSesion(page, { usuario: RESTRINGIDA });
  await expect(page).toHaveURL(/\/dashboard/);
  await page.context().storageState({ path: estadoDe(RESTRINGIDA) });
});
