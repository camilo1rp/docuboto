// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
import { describe, it, expect } from "vitest";
import { mensajeError, MENSAJES_TRD } from "./apiError.js";

describe("errores de C-4 en español", () => {
  it("cada código del contrato tiene mensaje y ninguno es el código crudo (control positivo)", () => {
    for (const cod of [
      "productora_ya_declarada",
      "productora_solo_en_ratificacion",
      "productora_solo_en_serie",
      "trd_serie_sin_productora",
      "trd_jerarquia_dos_niveles",
      "trd_padre_no_existe",
      "trd_version_inmutable",
      "trd_retencion_minima",
      "catalogo_dependencias_no_disponible",
      "organigrama_con_ciclo",
      "dependencia_ciclo",
      "tipo_documental_no_publicable",
      "dependencia_inactiva",
    ]) {
      const m = mensajeError({ error: cod, status: 409 }, "x");
      expect(m).not.toBe(cod);
      expect(m).not.toBe("x");
      expect(m).toMatch(/[a-záéíóú]{4}/i);
    }
    expect(Object.keys(MENSAJES_TRD)).toContain("organigrama_con_ciclo");
  });

  it("los que nombran dependencias prefieren el detail del backend (con los códigos)", () => {
    expect(
      mensajeError(
        { error: "organigrama_con_ciclo", detail: "Ciclo entre 3 -> 5 -> 3", status: 409 },
        "x",
      ),
    ).toBe("Ciclo entre 3 -> 5 -> 3");
    expect(
      mensajeError(
        {
          error: "dependencia_ciclo",
          detail: "La dependencia 5 es descendiente de 3",
          status: 409,
        },
        "x",
      ),
    ).toMatch(/dependencia 5/);
    // Sin detail cae al texto propio.
    expect(mensajeError({ error: "dependencia_ciclo", status: 409 }, "x")).toMatch(/ciclo/i);
  });

  it("también en el sobre anidado {detail:{error,...}}", () => {
    expect(mensajeError({ detail: { error: "tipo_documental_no_publicable" } }, "x")).toMatch(
      /publicable/,
    );
  });
});
