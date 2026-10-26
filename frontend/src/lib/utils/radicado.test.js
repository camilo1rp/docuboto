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
 * Los mapas de presentación del radicado son fuente ÚNICA.
 *
 * Vivían dentro de `/bandeja/+page.svelte`, y el resultado fue que la pantalla
 * de éxito de `/radicar` —la primera que ve quien acaba de radicar— mostraba
 * «Estado: registered», el valor crudo del backend en inglés, justo al lado de
 * un «Tipo: Entrada» que sí estaba traducido.
 *
 * El control de «no lo redeclares en tu pantalla» copia el que ya existe para
 * `ANULACION_ESTADO_LABEL` (M12): sin él, la forma natural de resolver una
 * necesidad idéntica en una tercera pantalla es volver a escribir el objeto —y
 * entonces hay dos verdades que se desincronizan en cuanto el backend sume un
 * estado, sin que nada avise.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  RADICADO_TIPO_LABEL,
  RADICADO_ESTADO_LABEL,
  RADICADO_ESTADO_CHIP,
  etiquetaEstadoRadicado,
} from "./radicado.js";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const leer = (rel) => readFileSync(path.join(RAIZ, rel), "utf-8");

// Los cuatro que declara el propio backend (ver la cabecera de bandeja/+page.svelte).
const ESTADOS = ["registered", "distributed", "in_progress", "closed"];

describe("mapas de presentación del radicado", () => {
  it("cubren los cuatro estados, con etiqueta y variante de chip", () => {
    for (const e of ESTADOS) {
      expect(RADICADO_ESTADO_LABEL[e]).toBeTruthy();
      expect(RADICADO_ESTADO_CHIP[e]).toBeTruthy();
    }
  });

  it("ninguna etiqueta se queda en inglés", () => {
    // La aserción que discrimina: es exactamente el defecto encontrado.
    for (const e of ESTADOS) {
      expect(RADICADO_ESTADO_LABEL[e]).not.toBe(e);
    }
  });

  it("cubren los tres tipos de radicado", () => {
    expect(RADICADO_TIPO_LABEL).toMatchObject({ E: "Entrada", S: "Salida", I: "Interno" });
  });
});

describe("etiquetaEstadoRadicado", () => {
  it("traduce los estados conocidos", () => {
    expect(etiquetaEstadoRadicado("in_progress")).toBe("En trámite");
  });

  it("ante un estado nuevo devuelve el valor crudo, no un hueco", () => {
    // Un `en_revision` sin traducir es feo; un espacio en blanco donde debería
    // ir el estado de un documento oficial es peor.
    expect(etiquetaEstadoRadicado("en_revision")).toBe("en_revision");
  });

  it("sin estado muestra una raya, no «undefined»", () => {
    expect(etiquetaEstadoRadicado(null)).toBe("—");
    expect(etiquetaEstadoRadicado(undefined)).toBe("—");
  });
});

describe("las pantallas importan los mapas en vez de redeclararlos", () => {
  for (const pantalla of [
    "routes/(app)/bandeja/+page.svelte",
    "routes/(app)/radicar/+page.svelte",
  ]) {
    it(`${pantalla} los toma de $lib/utils/radicado.js`, () => {
      const fuente = leer(pantalla);
      expect(fuente).toMatch(/from "\$lib\/utils\/radicado\.js"/);
      // Y no vuelve a escribir el objeto a mano.
      expect(fuente).not.toMatch(/registered:\s*"Radicado"/);
    });
  }
});

describe("la pantalla de éxito de /radicar no pinta el estado crudo", () => {
  it("usa el ayudante y no interpola `createdDocument.status` directamente", () => {
    // Este control existe porque el anterior NO discriminaba: revertir el
    // `<dd>` a `{createdDocument.status}` dejaba los diecisiete tests en verde
    // mientras la pantalla volvía a decir «registered». Comprobar que el módulo
    // se importa no es comprobar que se usa donde hacía falta.
    //
    // Es una aserción sobre el fuente a propósito, igual que la de
    // `ANULACION_ESTADO_LABEL` (M12): el estado de éxito es interno al
    // componente y llegar a él exige recorrer los cinco pasos del asistente y
    // simular la respuesta del POST — un montaje tan grande que se rompería por
    // motivos ajenos a lo que aquí se quiere fijar.
    const fuente = readFileSync(path.join(RAIZ, "routes/(app)/radicar/+page.svelte"), "utf-8");
    expect(fuente).toMatch(/etiquetaEstadoRadicado\(createdDocument\.status\)/);
    expect(fuente).not.toMatch(/<dd>\{createdDocument\.status\}<\/dd>/);
  });
});
