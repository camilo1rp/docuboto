// Verifica interacciones clave del prototipo y captura los modales.
const path = require("path");
const { chromium } = require(path.join(__dirname, "../../frontend/node_modules/playwright-core"));

(async () => {
  const browser = await chromium.launch({
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  const page = await browser.newPage({
    viewport: { width: 1512, height: 950 },
    deviceScaleFactor: 2,
    reducedMotion: "reduce",
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  const file = "file://" + path.join(__dirname, "index.html");
  await page.goto(file + "#/capacidades", { waitUntil: "networkidle" });
  await page.waitForTimeout(400);

  // 1. Modal de cambio de modo
  await page.evaluate(() => document.querySelector('[data-act="mode-pick"][data-mode="regulado"]').click());
  await page.waitForTimeout(300);
  const modeModal = await page.locator(".modal").count();
  await page.screenshot({ path: path.join(__dirname, "preview-modal-modo.png") });

  // 2. Aplicar modo y comprobar que aparece el undo bar
  await page.evaluate(() => document.querySelector('[data-act="mode-apply"]').click());
  await page.waitForTimeout(300);
  const undoBars = await page.locator(".undo-bar").count();
  const modeLabel = await page.locator(".page__actions .chip--lg").first().innerText();

  // 3. Intento de apagar una capacidad de la que dependen otras
  await page.evaluate(() => {
    const sw = document.querySelector('[data-act="toggle-cap"][data-id="radicacion"]');
    if (sw) sw.click();
  });
  await page.waitForTimeout(300);
  const toastText = await page.locator(".toast").last().innerText().catch(() => "");

  // 4. Pre-vuelo de acción reversible
  await page.goto(file + "#/documentos", { waitUntil: "networkidle" });
  await page.evaluate(() => document.querySelector('[data-act="doc-tab"][data-tab="bandeja"]').click());
  await page.waitForTimeout(300);
  await page.evaluate(() => document.querySelector('[data-act="preflight"][data-kind="devolver"]').click());
  await page.waitForTimeout(300);
  const preflight = await page.locator(".consequence__row").count();
  await page.screenshot({ path: path.join(__dirname, "preview-modal-prevuelo.png") });

  // 5. Constancia de acto irreversible
  await page.evaluate(() => {
    document.querySelector('[data-act="modal-close"]')?.click();
    document.querySelector(".btn--ghost[data-act='undo-entry']")?.click();
  });
  await page.waitForTimeout(300);

  // 6. Aplicar sugerencia desde el panel del agente
  await page.evaluate(() => document.querySelector('[data-act="apply-suggestion"]')?.click());
  await page.waitForTimeout(300);
  const afterApply = await page.locator(".undo-bar").count();

  const report = { modeModal, undoBars, modeLabel, toastText, preflight, afterApply };
  console.log(JSON.stringify(report, null, 2));
  console.log(errors.length ? "ERRORES:\n" + errors.join("\n") : "Sin errores de consola.");
  await browser.close();
})();
