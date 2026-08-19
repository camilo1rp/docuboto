// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * asistente-dock.spec.js — abrir el asistente no puede matar la pestaña.
 *
 * Este test existe por un defecto concreto: pulsar «Abrir asistente
 * conversacional» **crasheaba el renderizador de Chrome**. No una pantalla en
 * blanco ni un aviso: la ventana entera dejaba de responder y había que
 * matarla. La causa era un bloque reactivo que leía `messagesEl` —asignada por
 * `bind:this`— y por tanto dependía de ella, así que cada asignación lo
 * reprogramaba y el ciclo consumía memoria hasta el crash.
 *
 * Por qué va en la suite AUTENTICADA y no en jsdom: **jsdom no reproduce el
 * defecto**. Los tests unitarios del dock pasaban —los diez que ya existían y
 * los que se añadieron después— mientras Chrome moría. Un bucle de
 * actualizaciones solo se manifiesta con un motor de render de verdad. Es el
 * argumento entero de esta suite: hay defectos que solo existen en un
 * navegador.
 *
 * Requiere la pila completa (`docker compose --profile dev --profile assistant
 * up -d`) y credenciales por entorno:
 *
 *   E2E_USERNAME=jefe E2E_PASSWORD='...' \
 *     npx playwright test --config=playwright.authenticated.config.js
 */
import { test, expect } from "@playwright/test";

const BOTON = 'button[aria-label="Abrir asistente conversacional"]';

async function iniciarSesion(page) {
  const username = process.env.E2E_USERNAME;
  const password = process.env.E2E_PASSWORD;
  test.skip(!username || !password, "Faltan E2E_USERNAME/E2E_PASSWORD");

  await page.goto("/login");
  await page.fill("#username", username);
  await page.fill("#password", password);
  await Promise.all([page.waitForURL(/\/dashboard/), page.click("#kc-login")]);

  // Esperar a que la aplicación HIDRATE, no solo a que llegue el HTML. El
  // botón del asistente está en el HTML servido desde el primer byte, pero su
  // `on:click` no existe hasta que Svelte toma el control: pulsarlo antes no
  // hace nada y el test falla por un motivo que no es el que investiga.
  await expect(page.locator('button[aria-label="Abrir asistente conversacional"]')).toBeEnabled();
  await page.waitForFunction(() => {
    const b = document.querySelector('button[aria-label="Abrir asistente conversacional"]');
    return b !== null && b.getAttribute("aria-pressed") !== null;
  });
  await page.waitForLoadState("networkidle");
}

test.describe("Asistente conversacional — apertura", () => {
  test("abrir el dock deja la página viva y muestra el marco de alcance", async ({ page }) => {
    await iniciarSesion(page);

    await page.click(BOTON);

    // 1. El panel aparece.
    const dock = page.locator("aside.assistant-dock");
    await expect(dock).toBeVisible();

    // 2. Y —esto es lo que fallaba— la página SIGUE RESPONDIENDO. Si el
    //    renderizador está en un bucle, este `evaluate` nunca vuelve y el test
    //    cae por tiempo, que es como se manifiesta un crash por memoria.
    await expect
      .poll(async () => page.evaluate(() => document.readyState), { timeout: 10_000 })
      .toBe("complete");

    // 3. El marco de alcance (E18): el asistente DECLARA lo que puede hacer
    //    antes de que nadie le pida nada.
    await expect(dock).toContainText(/Puedes consultar|Consultando qué puedes hacer/i);
  });

  test("abrir y cerrar repetidamente no degrada la página", async ({ page }) => {
    await iniciarSesion(page);

    const dock = page.locator("aside.assistant-dock");
    for (let i = 0; i < 3; i += 1) {
      await page.click(BOTON);
      await expect(dock).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(dock).toBeHidden();
    }

    // Gemelo: una corrección que solo convergiera la primera vez dejaría el
    // dock inservible a partir de la segunda apertura, y el defecto volvería
    // disfrazado de «a veces no abre».
    await expect
      .poll(async () => page.evaluate(() => document.readyState), { timeout: 10_000 })
      .toBe("complete");
  });
});
