// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * El defecto no era que faltara el dato: era que estaba cargado y nadie lo leía.
 *
 * `+page.server.js` resuelve la serie completa en una faceta propia cuyo
 * comentario dice para qué sirve —«no solo un UUID crudo»— y ese resultado solo
 * llegaba al drawer de Disposición final. La ficha del expediente, lo primero
 * que se ve al abrirlo, seguía mostrando `83a44878-d169-4d4a-…`.
 *
 * Por eso hay dos pruebas de distinta naturaleza: una fija cómo se compone el
 * nombre, y la otra —la que importa— fija que la vista lo USE. Sin la segunda,
 * la primera podría estar en verde con la ficha pintando el UUID igual que antes.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { nombreSerieTrd, opcionesSerieTrd } from "./trd.js";

describe("nombreSerieTrd", () => {
  it("compone código y nombre", () => {
    expect(nombreSerieTrd({ code: "DEM-100", name: "Derechos de Petición" })).toBe(
      "DEM-100 — Derechos de Petición",
    );
  });

  it("se conforma con lo que haya", () => {
    expect(nombreSerieTrd({ code: "DEM-100" })).toBe("DEM-100");
    expect(nombreSerieTrd({ name: "Derechos de Petición" })).toBe("Derechos de Petición");
  });

  it("devuelve null cuando no hay nada que mostrar, para que quien llame decida", () => {
    expect(nombreSerieTrd(null)).toBeNull();
    expect(nombreSerieTrd({})).toBeNull();
    expect(nombreSerieTrd({ code: "   ", name: "" })).toBeNull();
  });
});

describe("la ficha del expediente usa el nombre, no el UUID", () => {
  const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const VISTA = path.join(SRC, "routes", "(app)", "expedientes", "[id]", "+page.svelte");
  const fuente = readFileSync(VISTA, "utf8");

  it("el bloque «Serie TRD» ofrece el nombre resuelto antes que el identificador", () => {
    const i = fuente.indexOf("<dt>Serie TRD</dt>");
    expect(i).toBeGreaterThan(-1);
    const bloque = fuente.slice(i, i + 400);
    expect(bloque).toContain("serieTrdNombre");
    // El UUID sigue como último recurso, y por eso no se prohíbe aquí: lo que
    // se prohíbe es que sea la ÚNICA salida.
    expect(bloque.indexOf("serieTrdNombre")).toBeLessThan(bloque.indexOf("trd_serie_id"));
  });

  it("el nombre se deriva del detalle que el servidor ya resolvió", () => {
    expect(fuente).toMatch(/serieTrdNombre\s*=[\s\S]{0,200}trdSerieDetail/);
  });
});

/**
 * El desplegable que mostraba la misma serie dos veces.
 *
 * Con el circuito de convalidación abierto, DEM-100 existe a la vez como v1
 * convalidada y v2 en borrador. Etiquetadas solo con «código — nombre» son dos
 * filas idénticas y quien clasifica elige a ciegas entre la que rige y la que
 * no. Estaba resuelto en el asistente de radicación y NO en el modal de
 * metadatos del expediente: por eso el criterio es ahora uno solo.
 */
describe("opcionesSerieTrd", () => {
  const SERIES = [
    { id: "b", code: "DEM-100", name: "Derechos de Petición", version: 2, estado: "borrador" },
    { id: "a", code: "DEM-100", name: "Derechos de Petición", version: 1, estado: "convalidada" },
  ];

  it("distingue las versiones de una misma serie", () => {
    const etiquetas = opcionesSerieTrd(SERIES).map((o) => o.label);
    expect(new Set(etiquetas).size).toBe(etiquetas.length);
    expect(etiquetas).toContain("DEM-100 v1 — Derechos de Petición (vigente)");
    expect(etiquetas).toContain("DEM-100 v2 — Derechos de Petición (borrador)");
  });

  it("pone primero la que rige", () => {
    expect(opcionesSerieTrd(SERIES)[0].value).toBe("a");
  });

  it("no esconde las que no rigen: una entidad nueva solo tiene borradores", () => {
    const soloBorradores = [
      { id: "x", code: "A-1", name: "Serie", version: 1, estado: "borrador" },
    ];
    expect(opcionesSerieTrd(soloBorradores)).toHaveLength(1);
  });

  it("un estado desconocido va al final, no entre las vigentes", () => {
    const conRaro = [
      { id: "r", code: "A-1", name: "Serie", version: 9, estado: "inventado" },
      ...SERIES,
    ];
    expect(opcionesSerieTrd(conRaro).at(-1).value).toBe("r");
  });

  it("tolera una lista vacía o ausente", () => {
    expect(opcionesSerieTrd([])).toEqual([]);
    expect(opcionesSerieTrd(undefined)).toEqual([]);
  });
});
