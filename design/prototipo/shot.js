// Captura las pantallas del prototipo para revisión visual.
// Uso: node design/prototipo/shot.js [ruta|all]
const path = require("path");
const { chromium } = require(path.join(
  __dirname,
  "../../frontend/node_modules/playwright-core",
));

const SHOTS = [
  ["escritorio", "#/escritorio"],
  ["bandeja", "#/documentos"],
  ["radicar", "#/documentos"],
  ["expedientes", "#/documentos"],
  ["distribucion", "#/distribucion"],
  ["alertas", "#/alertas"],
  ["capacidades", "#/capacidades"],
  ["inteligencia", "#/inteligencia"],
  ["actividad", "#/actividad"],
  ["sistema", "#/sistema"],
];

(async () => {
  const browser = await chromium.launch({
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  const page = await browser.newPage({
    viewport: { width: 1512, height: 950 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
  });
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  const file = "file://" + path.join(__dirname, "index.html");

  const only = process.argv[2] || "all";
  const list = only === "all" ? SHOTS : SHOTS.filter((s) => s[0] === only);

  for (const [name, hash] of list) {
    await page.goto(file + hash, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    if (name === "radicar") {
      await page.evaluate(() => {
        document.querySelector('[data-tab="radicar"]')?.click();
      });
      await page.waitForTimeout(600);
    }
    if (name === "expedientes") {
      await page.evaluate(() => {
        document.querySelector('[data-tab="expedientes"]')?.click();
      });
      await page.waitForTimeout(600);
    }
    await page.screenshot({
      path: path.join(__dirname, `preview-${name}.png`),
      fullPage: false,
    });
  }

  // Estado de interacción: paleta + barra de deshacer + modo oscuro
  await page.goto(file + "#/documentos", { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    document.querySelector('[data-act="doc-tab"][data-tab="bandeja"]')?.click();
  });
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    document.querySelector('[data-act="toggle-select"][data-id="r1"]')?.click();
    document.querySelector('[data-act="toggle-select"][data-id="r2"]')?.click();
    document.querySelector('[data-act="bulk-tag"]')?.click();
  });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(__dirname, "preview-undo.png") });

  await page.evaluate(() => document.querySelector('[data-act="palette-open"]')?.click());
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(__dirname, "preview-palette.png") });

  await page.evaluate(() => {
    document.querySelector('[data-act="palette-close"]')?.click();
    document.querySelector('[data-act="theme-toggle"]')?.click();
  });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(__dirname, "preview-dark.png") });

  console.log(errors.length ? "ERRORES:\n" + errors.join("\n") : "Sin errores de consola.");
  await browser.close();
})();
