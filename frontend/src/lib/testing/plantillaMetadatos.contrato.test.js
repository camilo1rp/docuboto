// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Guardián: el fixture `plantillaMetadatos` tiene EXACTAMENTE las claves de
 * `MetadataTemplateResponse`. Si el backend renombra un campo, este test cae
 * y obliga a actualizar fixture y lectores a la vez.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { plantillaMetadatos } from "./plantillaMetadatos.js";

const AQUI = dirname(fileURLToPath(import.meta.url));
const FUENTE = resolve(AQUI, "../../../../services/document-service/app/schemas/metadata.py");

function camposDe(clase) {
  const src = readFileSync(FUENTE, "utf8");
  const inicio = src.indexOf(`class ${clase}(`);
  expect(inicio).toBeGreaterThan(-1);
  const resto = src.slice(inicio).split("\n").slice(1);
  const campos = [];
  for (const linea of resto) {
    if (/^class /.test(linea)) break;
    const m = /^ {4}([a-z_]+):/.exec(linea);
    if (m) campos.push(m[1]);
  }
  return campos.sort();
}

describe("fixture plantillaMetadatos vs MetadataTemplateResponse", () => {
  it("las claves coinciden con el modelo Pydantic", () => {
    const esperado = camposDe("MetadataTemplateResponse");
    expect(esperado).toContain("json_schema");
    expect(Object.keys(plantillaMetadatos({ json_schema: {} })).sort()).toEqual(esperado);
  });

  it("la clave inventada `schema` no existe en el contrato", () => {
    expect(camposDe("MetadataTemplateResponse")).not.toContain("schema");
  });
});
