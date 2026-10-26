// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Guardián: la regla de datos mínimos del front es la del backend.
 *
 * `REQUERIDOS_POR_TIPO` vive en `services/document-service/app/core/datos_minimos.py`
 * (fuente única). Si alguien cambia allí un campo y no aquí, la pantalla ofrecería
 * «Radicar» ante un 422 (o lo negaría ante un 201). Dos listas escritas aparte
 * divergen: este test las cruza leyendo la del backend.
 */
import { describe, it, expect } from "vitest";
import { REQUERIDOS_POR_TIPO, faltantes } from "./datosMinimos.js";
// `process.getBuiltinModule`: `node:fs` resuelve al sustituto del navegador bajo Vitest.
const { readFileSync } = process.getBuiltinModule("fs");

const PY = readFileSync("../services/document-service/app/core/datos_minimos.py", "utf-8");

/** Extrae `{tipo: [campos]}` del literal Python (sin ejecutarlo). */
function leerDiccionarioPython(fuente) {
  const m = fuente.match(/REQUERIDOS_POR_TIPO[^=]*=\s*\{([\s\S]*?)\n\}/);
  if (!m) return null;
  /** @type {Record<string, string[]>} */
  const salida = {};
  for (const fila of m[1].matchAll(/"([A-Z])":\s*\(([^)]*)\)/g)) {
    salida[fila[1]] = [...fila[2].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
  }
  return salida;
}

describe("datos mínimos: paridad front ↔ backend", () => {
  const backend = leerDiccionarioPython(PY);

  it("control positivo: se leyeron los tres tipos con sus campos", () => {
    expect(backend).not.toBeNull();
    expect(Object.keys(backend).sort()).toEqual(["E", "I", "S"]);
    expect(backend.S).toEqual(["doc_class", "origin_dept_code"]);
  });

  it("el diccionario del front es idéntico al del backend", () => {
    expect(REQUERIDOS_POR_TIPO).toEqual(backend);
  });

  it("faltantes() usa esa regla: Salida exige origen y no destino", () => {
    expect(faltantes("S", { doc_class: "OFI" })).toEqual(["origin_dept_code"]);
    expect(faltantes("S", { doc_class: "OFI", origin_dept_code: 10 })).toEqual([]);
    expect(faltantes("I", { doc_class: "OFI", origin_dept_code: 10 })).toEqual(["dest_dept_code"]);
    expect(faltantes("S", { doc_class: "  ", origin_dept_code: 10 })).toEqual(["doc_class"]);
  });
});
