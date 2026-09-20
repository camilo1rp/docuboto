// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * _auth.setup.js — iniciar sesión UNA vez y guardar el estado.
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
 * Lo que NO debe usar este estado: `login-flow.spec.js`, que prueba el inicio de
 * sesión y tiene que empezar sin ninguna. Lo declara por su cuenta.
 */
import { test as setup, expect } from "@playwright/test";

import { ESTADO_SESION, iniciarSesion } from "./_sesion.js";

setup("iniciar sesión una vez para toda la suite", async ({ page }) => {
  await iniciarSesion(page);

  // No basta con que la navegación termine: si el canje del código falla, la
  // aplicación devuelve a `/login` y el estado guardado sería el de una sesión
  // que no existe — y las 51 pruebas caerían después, lejos de la causa.
  await expect(page).toHaveURL(/\/dashboard/);

  await page.context().storageState({ path: ESTADO_SESION });
});
