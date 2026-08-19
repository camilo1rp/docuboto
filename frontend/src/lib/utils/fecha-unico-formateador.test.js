// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Una sola forma de formatear una fecha.
 *
 * `new Date("2026-09-19").toLocaleDateString("es-CO")` desplaza la fecha un día
 * hacia atrás en Colombia: la cadena sin hora se lee como medianoche UTC y se
 * convierte a UTC−5. El defecto apareció en el préstamo de una unidad (plazo al
 * 19, en pantalla «18/09/2026») y estaba copiado en diecisiete pantallas, entre
 * ellas los PLAZOS DE RETENCIÓN, donde un día de menos no es un detalle
 * estético sino un vencimiento equivocado.
 *
 * Corregirlo en una pantalla de diecisiete habría dejado nacer la decimoctava
 * — ya pasó en esta misma revisión con el desplegable de series TRD. Por eso el
 * criterio vive en `$lib/utils/fecha.js` y este test impide que se vuelva a
 * escribir suelto.
 */

import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const SRC = resolve(process.cwd(), "src");

// El único archivo autorizado a hablar con la API de internacionalización de
// fechas. Todo lo demás lo consume a través de él.
const FORMATEADOR = "lib/utils/fecha.js";

/** Métodos que convierten un instante a la zona del lector. */
const API_FECHA = /\.toLocale(Date|Time)String\s*\(|\bdateStyle\s*:|\btimeStyle\s*:/;

// Exención LOCAL y declarada, no una lista de archivos permitidos: la línea de
// código lleva su propia marca —en ella misma o en el comentario que la
// precede—, así que la excepción se lee donde está y no en un fichero de
// configuración que nadie mira. Si el código se mueve, la marca se mueve con
// él; si se borra, el guardián vuelve a mirarlo.
const MARCA_EXENTA = "fecha-formateo-propio:";

function archivos(dir) {
  const salida = [];
  for (const entrada of readdirSync(dir)) {
    const ruta = join(dir, entrada);
    if (statSync(ruta).isDirectory()) {
      salida.push(...archivos(ruta));
    } else if (/\.(svelte|js)$/.test(entrada) && !/\.test\.js$/.test(entrada)) {
      salida.push(ruta);
    }
  }
  return salida;
}

describe("el formateo de fechas vive en un solo sitio", () => {
  const todos = archivos(SRC);

  it("el barrido mira de verdad", () => {
    // Un barrido que no encuentra archivos pasaría siempre en verde.
    expect(todos.length).toBeGreaterThan(100);
    expect(todos.some((f) => relative(SRC, f) === FORMATEADOR)).toBe(true);
  });

  it("ninguna pantalla formatea fechas por su cuenta", () => {
    const infractores = todos
      .filter((f) => relative(SRC, f) !== FORMATEADOR)
      .filter((f) => {
        const lineas = readFileSync(f, "utf8").split("\n");
        return lineas.some(
          (linea, i) =>
            API_FECHA.test(linea) &&
            !lineas.slice(Math.max(0, i - 6), i + 1).some((l) => l.includes(MARCA_EXENTA)),
        );
      })
      .map((f) => relative(SRC, f));

    expect(infractores).toEqual([]);
  });

  it("una exención sin marca no cuela", () => {
    // Gemelo: si el filtro de exención estuviera mal escrito y perdonara
    // cualquier línea, este guardián no vigilaría nada.
    const lineas = ['  return d.toLocaleDateString("es-CO");'];
    const infractor = lineas.some(
      (linea, i) =>
        API_FECHA.test(linea) &&
        !lineas.slice(Math.max(0, i - 6), i + 1).some((l) => l.includes(MARCA_EXENTA)),
    );
    expect(infractor).toBe(true);
  });

  it("y el formateador sí la usa — si no, el guardián vigila un archivo vacío", () => {
    const fuente = readFileSync(join(SRC, FORMATEADOR), "utf8");
    expect(API_FECHA.test(fuente)).toBe(true);
  });
});
