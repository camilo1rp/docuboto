// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { causalesDe } from "./causal.js";

const CATALOGO = [
  { codigo: "DEV-INC", nombre: "Información incompleta", ambito: "devolucion" },
  { codigo: "DEV-NOC", nombre: "No es competencia", ambito: "devolucion" },
  { codigo: "ANU-DUP", nombre: "Radicado duplicado", ambito: "anulacion" },
  { codigo: "POS-DIR", nombre: "Dirección incorrecta", ambito: "devolucion_postal" },
  { codigo: "GEN-OTR", nombre: "Otra", ambito: "general" },
  { codigo: "VIEJA", nombre: "Sin ámbito", ambito: null },
];

describe("causalesDe", () => {
  it("ofrece las del acto pedido y ninguna de otro acto", () => {
    const v = causalesDe(CATALOGO, "devolucion").map((o) => o.value);
    expect(v).toContain("DEV-INC");
    expect(v).toContain("DEV-NOC");
    // Lo que importa: una causal de anulación NO puede ofrecerse como causal de
    // devolución. Ese es el dato incorrecto que el eje `ambito` existe para evitar.
    expect(v).not.toContain("ANU-DUP");
    expect(v).not.toContain("POS-DIR");
  });

  it("incluye siempre las generales y las que aún no declaran ámbito", () => {
    for (const acto of ["devolucion", "anulacion", "devolucion_postal"]) {
      const v = causalesDe(CATALOGO, acto).map((o) => o.value);
      expect(v).toContain("GEN-OTR");
      expect(v).toContain("VIEJA");
    }
  });

  it("etiqueta con código y nombre, y sobrevive a un nombre ausente", () => {
    expect(causalesDe(CATALOGO, "anulacion")[0].label).toBe("ANU-DUP — Radicado duplicado");
    expect(causalesDe([{ codigo: "X" }], "devolucion")[0].label).toBe("X");
  });

  it("descarta filas sin código: no se puede enviar lo que no tiene valor", () => {
    expect(causalesDe([{ nombre: "sin código", ambito: "devolucion" }], "devolucion")).toEqual([]);
  });

  it("tolera un catálogo ausente sin reventar la pantalla", () => {
    expect(causalesDe(undefined, "devolucion")).toEqual([]);
    expect(causalesDe(null, "devolucion")).toEqual([]);
  });
});
