// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Guardián: la lista de claves admitidas del front es la allowlist del backend
 * (`PARAMS_ADMITIDOS` en tenant-service). Dos listas escritas aparte divergen.
 */
import { describe, it, expect } from "vitest";
import { PARAMS_ADMITIDOS, clavesPorCrear } from "./paramsAdmitidos.js";
// `process.getBuiltinModule`: `node:fs` resuelve al sustituto del navegador bajo Vitest.
const { readFileSync } = process.getBuiltinModule("fs");

const PY = readFileSync("../services/tenant-service/app/routers/config.py", "utf-8");

function leerAllowlist(fuente) {
  const m = fuente.match(/PARAMS_ADMITIDOS[^=]*=\s*frozenset\(\{([\s\S]*?)\}\)/);
  if (!m) return null;
  const sinComentarios = m[1].replace(/#.*$/gm, "");
  return [...sinComentarios.matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
}

describe("parámetros admitidos: paridad front ↔ backend", () => {
  const backend = leerAllowlist(PY);

  it("control positivo: se leyó la allowlist del backend", () => {
    expect(backend).not.toBeNull();
    expect(backend.length).toBeGreaterThanOrEqual(11);
    expect(backend).toContain("entidad_nombre");
  });

  it("el front admite exactamente las mismas claves", () => {
    expect([...PARAMS_ADMITIDOS].sort()).toEqual([...backend].sort());
  });

  it("clavesPorCrear descarta las ya existentes", () => {
    const resto = clavesPorCrear(["entidad_nombre", "otra"]);
    expect(resto).not.toContain("entidad_nombre");
    expect(resto.length).toBe(PARAMS_ADMITIDOS.length - 1);
  });
});
