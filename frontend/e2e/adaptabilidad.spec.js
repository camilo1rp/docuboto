// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * adaptabilidad.spec.js — las pantallas se ajustan al dispositivo que las usa.
 *
 * POR QUÉ ESTE FICHERO EXISTE, que es lo que importa: el defecto lo reportó una
 * persona usando el producto —«las pantallas no usan todo el espacio en
 * escritorio, y algunas muestran tablas recortadas»— y **la suite entera estaba
 * en verde**. No era mala suerte: Vitest corre sobre jsdom, que no calcula
 * layout. Dos mil pruebas no saben cuántos píxeles ocupa nada.
 *
 * Playwright sí: es un navegador de verdad con un motor de maquetación. Aquí se
 * mide lo que jsdom no puede — cuánto ancho ocupa el contenido, si la página se
 * desplaza en horizontal, si una tabla se sale de su contenedor—, en los tres
 * anchos que existen de verdad: un móvil, un portátil y un escritorio.
 *
 * ALCANCE DECLARADO: este carril corre SIN backend, así que solo alcanza las
 * páginas públicas. Las pantallas de trabajo —bandeja, admin, expedientes—, que
 * son las que motivaron el arreglo, se miden con el MISMO helper en
 * `e2e/authenticated/adaptabilidad-app.spec.js`, que necesita la pila arriba.
 * Decirlo importa: un fichero llamado «adaptabilidad» que solo mira tres
 * páginas públicas induce a creer que el eje está cubierto entero.
 */
import { test, expect } from "@playwright/test";

import { ANCHOS, medir } from "./_adaptabilidad.js";

/** Páginas que este carril puede alcanzar sin backend. */
const PUBLICAS = [
  { ruta: "/", nombre: "landing" },
  { ruta: "/403", nombre: "sin permiso" },
  { ruta: "/consulta", nombre: "consulta ciudadana" },
  { ruta: "/radicacion", nombre: "ventanilla pública" },
];

for (const ancho of ANCHOS) {
  test.describe(`adaptabilidad — ${ancho.nombre} (${ancho.width}px)`, () => {
    test.use({ viewport: { width: ancho.width, height: ancho.height } });

    for (const { ruta, nombre } of PUBLICAS) {
      test(`${nombre} no se desborda ni recorta tablas`, async ({ page }) => {
        const res = await page.goto(ruta);
        // Una página que no responde no puede declararse adaptable.
        expect(res?.status(), `${ruta} no respondió`).toBeLessThan(400);
        await page.waitForLoadState("networkidle");

        const m = await medir(page);

        expect(
          m.scrollHorizontal,
          `${ruta} se desplaza en horizontal a ${ancho.width}px ` +
            `(${m.scrollWidth}px de contenido). Culpables: ${m.desbordan.join(", ") || "—"}`,
        ).toBe(false);

        expect(
          m.tablasRecortadas,
          `${ruta}: tabla que no cabe en un contenedor que no se desplaza. Una ` +
            "`<table width:100%>` con muchas columnas no desborda, COMPRIME: el " +
            "contenido se parte y se lee como una tabla rota.",
        ).toEqual([]);
      });
    }
  });
}

test.describe("el título de página mide lo que dice el token", () => {
  test.use({ viewport: { width: 1366, height: 768 } });

  // Esto es lo que ningún guardián de CSS puede comprobar: el guardián vigila
  // que nadie declare su propio tamaño, pero no sabe cuántos píxeles acaba
  // midiendo el título. jsdom no calcula layout; este navegador sí.
  for (const { ruta, nombre } of [
    { ruta: "/403", nombre: "sin permiso" },
    { ruta: "/consulta", nombre: "consulta ciudadana" },
    { ruta: "/radicacion", nombre: "ventanilla pública" },
  ]) {
    test(`${nombre}: el h1 mide el token, no lo que decidiera su pantalla`, async ({ page }) => {
      await page.goto(ruta);
      const medida = await page.evaluate(() => {
        const h1 = document.querySelector("h1");
        if (!h1) return null;
        const token = getComputedStyle(document.documentElement)
          .getPropertyValue("--op-font-title-page")
          .trim();
        return { titulo: getComputedStyle(h1).fontSize, token };
      });

      expect(medida, `${ruta} no tiene h1`).not.toBeNull();
      // Se compara contra el TOKEN y no contra "22px": si el sistema cambia de
      // opinión, este test sigue midiendo lo correcto en vez de fijar un número
      // que habría que perseguir por la suite.
      expect(medida.titulo).toBe(medida.token);
    });
  }
});

test.describe("control positivo del medidor", () => {
  test.use({ viewport: { width: 1366, height: 768 } });

  test("detecta un desbordamiento y una tabla recortada cuando los hay", async ({ page }) => {
    // Sin esto, los doce tests de arriba podrían estar en verde por no medir
    // nada —un selector que dejó de casar, un `evaluate` que devuelve vacío— y
    // se leerían exactamente igual que si todo estuviera bien. Es el mismo
    // motivo por el que la suite de Vitest no vio nunca este defecto: no es que
    // fallara, es que no miraba.
    await page.goto("/403");
    await page.evaluate(() => {
      const ancho = document.createElement("div");
      ancho.className = "prueba-desborde";
      ancho.style.cssText = "width:3000px;height:10px";
      document.body.appendChild(ancho);

      const caja = document.createElement("div");
      caja.style.cssText = "width:200px;overflow:hidden";
      const t = document.createElement("table");
      t.innerHTML = "<tr><td style='width:1200px'>x</td></tr>";
      caja.appendChild(t);
      document.body.appendChild(caja);
    });

    const m = await medir(page);

    expect(m.scrollHorizontal).toBe(true);
    expect(m.desbordan.join(" ")).toContain("prueba-desborde");
    expect(m.tablasRecortadas.length).toBeGreaterThan(0);
  });
});
