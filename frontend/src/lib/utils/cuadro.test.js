// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { aplanarCuadro, textoAviso, motivoSinSeccion } from "./cuadro.js";

const serie = (code, subs = []) => ({ code, name: `Serie ${code}`, subseries: subs });

describe("aplanarCuadro", () => {
  it("la clave es el CAMINO: el mismo codigo bajo dos padres no repite clave", () => {
    const secciones = [
      {
        codigo: 1,
        nombre: "A",
        nivel: "seccion",
        series: [],
        subsecciones: [
          {
            codigo: 9,
            nombre: "Común",
            nivel: "subseccion",
            series: [serie("S1")],
            subsecciones: [],
          },
        ],
      },
      {
        codigo: 2,
        nombre: "B",
        nivel: "seccion",
        series: [],
        subsecciones: [
          {
            codigo: 9,
            nombre: "Común",
            nivel: "subseccion",
            series: [serie("S1")],
            subsecciones: [],
          },
        ],
      },
    ];
    const filas = aplanarCuadro(secciones);
    const claves = filas.map((f) => f.key);
    expect(new Set(claves).size).toBe(claves.length);
    expect(claves).toContain("1/9");
    expect(claves).toContain("2/9");
    expect(claves).toContain("1/9#S1");
    expect(claves).toContain("2/9#S1");
  });

  it("control positivo: profundidad y tipo por nivel, subseries bajo su serie", () => {
    const filas = aplanarCuadro([
      {
        codigo: 1,
        nombre: "A",
        nivel: "seccion",
        series: [serie("S", [{ code: "SS", name: "Sub" }])],
        subsecciones: [],
      },
    ]);
    expect(filas.map((f) => [f.kind, f.depth])).toEqual([
      ["seccion", 0],
      ["serie", 1],
      ["subserie", 2],
    ]);
  });

  it("entrada vacía o ausente no rompe", () => {
    expect(aplanarCuadro([])).toEqual([]);
    expect(aplanarCuadro(undefined)).toEqual([]);
  });
});

describe("textos en español", () => {
  it("cada aviso conocido tiene texto propio y nombra la dependencia y las series", () => {
    const a = textoAviso({
      tipo: "organigrama_reorganizado_tras_convalidacion",
      dependencia_codigo: 12,
      series: ["S1", "S2"],
    });
    expect(a).toMatch(/dependencia 12/);
    expect(a).toMatch(/S1, S2/);
    expect(textoAviso({ tipo: "productora_inactiva", dependencia_codigo: 3 })).toMatch(/inactiva/);
    expect(textoAviso({ tipo: "productora_desconocida", dependencia_codigo: 3 })).toMatch(
      /no existe/,
    );
    expect(textoAviso({ tipo: "entidad_sin_nombre" })).toMatch(/sin nombre/);
  });
  it("un aviso desconocido no se pierde", () => {
    expect(textoAviso({ tipo: "nuevo_tipo" })).toMatch(/nuevo_tipo/);
  });
  it("motivos de sin_seccion en español, desconocido se devuelve tal cual", () => {
    expect(motivoSinSeccion("sin_productora")).toMatch(/productora/);
    expect(motivoSinSeccion("otro")).toBe("otro");
  });
});
