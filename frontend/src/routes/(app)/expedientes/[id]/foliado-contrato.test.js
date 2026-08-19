// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La pestaña Foliado leía el contrato del ÍNDICE ELECTRÓNICO.
 *
 * `GET /expedientes/{id}/foliado` responde `ExpedienteIndice`, cuyos ítems son
 * `{folio, radicado_id, tracking_number, added_at}`. La tabla leía
 * `{orden, titulo, document_id, tipologia, fecha_incorporacion, valor_huella}`,
 * que son los de `IndiceItemResponse` — otro esquema, de otra pestaña (E15).
 * Ninguno de esos campos existe en la respuesta, así que la foliación —el orden
 * que da fe de cómo está compuesto el expediente— se mostraba como «undefined»
 * en las dos columnas que importan. Y la clave del `{#each}` era ese mismo
 * `undefined` en todas las filas.
 *
 * Dos esquemas parecidos en el mismo servicio son fáciles de cruzar y el
 * cruce no rompe nada: pinta. Por eso el test compara la vista contra el
 * contrato REAL, campo a campo, en vez de contra una captura de pantalla.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const AQUI = path.dirname(fileURLToPath(import.meta.url));
const VISTA = readFileSync(path.join(AQUI, "+page.svelte"), "utf8");

/** Bloque de la tabla de foliación, acotado para no leer el resto de la página. */
const TABLA = (() => {
  const i = VISTA.indexOf('aria-label="Foliación ordenada del expediente"');
  expect(i).toBeGreaterThan(-1);
  return VISTA.slice(i, VISTA.indexOf("</table>", i));
})();

// Campos que SÍ trae `ExpedienteIndice.items` (archive/schemas/archive.py: IndiceItem).
const DEL_CONTRATO = ["folio", "radicado_id", "tracking_number", "added_at"];
// Campos de `IndiceItemResponse` — otro endpoint, otra pestaña.
const DE_OTRO_CONTRATO = ["orden", "titulo", "document_id", "tipologia", "valor_huella"];

describe("pestaña Foliado — contrato de la respuesta", () => {
  it.each(DEL_CONTRATO)("usa el campo real «%s»", (campo) => {
    expect(TABLA).toContain(`item.${campo}`);
  });

  it.each(DE_OTRO_CONTRATO)("no lee «%s», que pertenece al índice electrónico", (campo) => {
    expect(TABLA).not.toContain(`item.${campo}`);
  });

  it("la clave del each es un campo que existe, no un undefined repetido", () => {
    expect(TABLA).toMatch(/\{#each foliado\.items as item \(item\.radicado_id\)\}/);
  });
});
