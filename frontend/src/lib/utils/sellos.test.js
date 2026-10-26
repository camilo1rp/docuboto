// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
import { describe, it, expect } from "vitest";
import {
  textoUltimoAviso,
  textoObjeto,
  textoDias,
  huellaCorta,
  SITUACIONES,
  SITUACION_LABELS,
} from "./sellos.js";

describe("sellos de archivo en español", () => {
  it("ultimo_aviso: cada código del contrato tiene frase propia", () => {
    expect(textoUltimoAviso("tsa_por_vencer")).toMatch(/por vencer/);
    expect(textoUltimoAviso("sin_revocacion_tsa")).toMatch(/revocación/);
    expect(textoUltimoAviso("tsa_revocada")).toMatch(/revocada/);
    expect(textoUltimoAviso("rechazada:algoritmo_no_vigente")).toBe(
      "Renovación rechazada: algoritmo_no_vigente",
    );
    expect(textoUltimoAviso("rechazada:")).toBe("Renovación rechazada");
    expect(textoUltimoAviso(null)).toBe("—");
  });
  it("un aviso desconocido se muestra tal cual, no se pierde", () => {
    expect(textoUltimoAviso("algo_nuevo")).toBe("algo_nuevo");
  });
  it("objeto reservado: «Reservado», sin tipo ni id aunque vinieran", () => {
    expect(textoObjeto({ objeto_reservado: true, objeto_tipo: "aip", objeto_id: "X" })).toBe(
      "Reservado",
    );
    expect(textoObjeto({ objeto_reservado: false, objeto_tipo: "aip", objeto_id: "X" })).toBe(
      "Paquete AIP · X",
    );
    expect(textoObjeto({ objeto_reservado: false })).toBe("—");
  });
  it("días: negativo = venció, singular/plural", () => {
    expect(textoDias(-3)).toBe("Venció hace 3 días");
    expect(textoDias(-1)).toBe("Venció hace 1 día");
    expect(textoDias(0)).toBe("Vence hoy");
    expect(textoDias(1)).toBe("1 día");
    expect(textoDias(30)).toBe("30 días");
  });
  it("huella corta y todas las situaciones etiquetadas", () => {
    expect(huellaCorta("a".repeat(64))).toBe("aaaaaaaa…aaaaaaaa");
    expect(huellaCorta("abc")).toBe("abc");
    for (const s of SITUACIONES) expect(SITUACION_LABELS[s]).toBeTruthy();
  });
});
