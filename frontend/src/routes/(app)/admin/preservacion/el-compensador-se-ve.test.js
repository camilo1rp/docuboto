// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * Una cola vacía no dice si nunca hubo cola.
 *
 * El trabajo de reconciliación resetea el contador de intentos al firmar —a
 * propósito, para no dejar basura en la cola de pendientes— y con eso borra el
 * único rastro de que hubo que reparar. Durante dos días el sellado del cierre
 * falló SIEMPRE y esta pantalla dijo «sin pendientes» con toda la razón.
 *
 * Se comprueba sobre la FUENTE porque lo que hay que garantizar es que el
 * número viaje entero de punta a punta: lo emite archive-service en una
 * cabecera, tiene que estar declarado en la lista del gateway (si no, se cae
 * en el trayecto y nada falla), el proxy lo reenvía, la carga lo expone y la
 * pantalla lo pinta. Un eslabón que se olvide no rompe nada: solo hace
 * desaparecer el dato, que es el modo de fallo caro.
 */
import { describe, it, expect } from "vitest";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const { readFileSync } = process.getBuiltinModule("fs");
const AQUI = dirname(fileURLToPath(import.meta.url));
const RAIZ = resolve(AQUI, "../../../../..");

const leer = (rel) => readFileSync(resolve(RAIZ, rel), "utf-8");

const CABECERA = "X-Sellado-Diferido-Count";

describe("el recuento del compensador llega hasta la pantalla", () => {
  it("archive-service lo emite", () => {
    const fuente = leer("../services/archive-service/app/routers/indice.py");
    expect(fuente).toContain(CABECERA);
    expect(fuente).toContain("count_sellado_diferido");
  });

  it("el gateway lo tiene declarado, o se cae en el trayecto sin avisar", () => {
    const fuente = leer("../services/api-gateway/app/core/proxy.py");
    expect(fuente).toContain(CABECERA.toLowerCase());
  });

  it("el proxy de la pantalla lo reenvía", () => {
    const fuente = leer(
      "src/routes/(app)/admin/preservacion/api/indices/pendientes-firma/+server.js",
    );
    expect(fuente).toContain(CABECERA);
  });

  it("la carga lo expone y distingue «no se sabe» de «cero»", () => {
    const fuente = leer("src/routes/(app)/admin/preservacion/+page.server.js");
    expect(fuente).toContain(CABECERA);
    expect(fuente).toContain("selladoDiferido");
    // `null` y `0` no son lo mismo: decir «0» sin saberlo afirmaría que el
    // compensador nunca actuó.
    expect(fuente).toMatch(/Number\.isFinite\(selladoDiferido\)\s*\?\s*selladoDiferido\s*:\s*null/);
  });

  it("la pantalla solo lo pinta cuando de verdad hubo reparaciones", () => {
    const fuente = leer("src/routes/(app)/admin/preservacion/+page.svelte");
    expect(fuente).toMatch(/selladoDiferido !== null && selladoDiferido > 0/);
    expect(fuente).toContain("reconciliación");
  });
});
