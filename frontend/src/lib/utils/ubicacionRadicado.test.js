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
  ubicacionActual,
  ubicacionTexto,
  ubicacionTerminal,
  ubicadoPorFlujo,
  responsableDe,
  ubicacionEtiqueta,
  responsableTexto,
} from "./ubicacionRadicado.js";

describe("ubicacionRadicado (ADR-029)", () => {
  it("lee ubicacion_dept y NUNCA cae a dest_dept (la de radicación)", () => {
    const doc = { dest_dept: "Oficina de Tecnología", ubicacion_dept: "Control Interno" };
    expect(ubicacionActual(doc)).toBe("Control Interno");
    expect(ubicacionActual({ dest_dept: "Oficina de Tecnología" })).toBeNull();
    expect(ubicacionActual({ ubicacion_dept: "   " })).toBeNull();
    expect(ubicacionActual(null)).toBeNull();
  });

  it("ubicacionTexto usa guion largo sin dato", () => {
    expect(ubicacionTexto({})).toBe("—");
    expect(ubicacionTexto({ ubicacion_dept: "Archivo" })).toBe("Archivo");
  });

  it("ubicacionTerminal solo en cerrado/anulado", () => {
    expect(ubicacionTerminal({ status: "closed", ubicacion_dept: "Archivo" })).toBe(
      "Cerrado en Archivo",
    );
    expect(ubicacionTerminal({ status: "anulado", ubicacion_dept: "Archivo" })).toBe(
      "Anulado en Archivo",
    );
    expect(ubicacionTerminal({ status: "in_progress", ubicacion_dept: "Archivo" })).toBeNull();
    expect(ubicacionTerminal({ status: "closed" })).toBeNull();
  });

  it("ubicadoPorFlujo distingue flujo de radicación", () => {
    expect(ubicadoPorFlujo({ flujo_dept: "Talento Humano" })).toBe(true);
    expect(ubicadoPorFlujo({ flujo_dept: null })).toBe(false);
    expect(ubicadoPorFlujo({})).toBe(false);
  });

  it("responsableDe: Tú, nombre resuelto, o null (nunca el UUID)", () => {
    const doc = { flujo_usuario: "u-1" };
    expect(responsableDe(doc, "u-1", {})).toBe("Tú");
    expect(responsableDe(doc, "u-2", { "u-1": "maria" })).toBe("maria");
    expect(responsableDe(doc, "u-2", {})).toBeNull();
    expect(responsableDe({}, "u-2", { "u-1": "maria" })).toBeNull();
  });
});

describe("ubicacionEtiqueta (ADR-029 §2-3)", () => {
  const base = { doc_type: "E", dest_dept: "Jurídica", ubicacion_dept: "Jurídica" };

  it("por flujo: el nombre, o «Cerrado/Anulado en X»", () => {
    const f = { ...base, flujo_dept: "Archivo", ubicacion_dept: "Archivo" };
    expect(ubicacionEtiqueta(f)).toBe("Archivo");
    expect(ubicacionEtiqueta({ ...f, status: "closed" })).toBe("Cerrado en Archivo");
    expect(ubicacionEtiqueta({ ...f, status: "anulado" })).toBe("Anulado en Archivo");
  });

  it("sin pasos y con destino: «Sin repartir — en X»", () => {
    expect(ubicacionEtiqueta({ ...base, flujo_dept: null })).toBe("Sin repartir — en Jurídica");
  });

  it("Salida/Interno sin destino: dependencia productora; una Entrada no", () => {
    for (const t of ["S", "I"]) {
      expect(ubicacionEtiqueta({ doc_type: t, dest_dept: null, ubicacion_dept: "Jurídica" })).toBe(
        "Sin repartir — en la dependencia productora: Jurídica",
      );
    }
    expect(ubicacionEtiqueta({ doc_type: "E", dest_dept: "", ubicacion_dept: "X" })).toBe(
      "Sin repartir — en X",
    );
  });

  it("terminal sin flujo: «sin pasar por el flujo», sin el «aún en…»", () => {
    expect(ubicacionEtiqueta({ ...base, status: "anulado" })).toBe(
      "Anulado sin pasar por el flujo — en Jurídica",
    );
    expect(ubicacionEtiqueta({ ...base, status: "closed" })).toBe(
      "Cerrado sin pasar por el flujo — en Jurídica",
    );
  });

  it("sin ubicación: null", () => {
    expect(ubicacionEtiqueta({ doc_type: "S" })).toBeNull();
  });
});

describe("responsableTexto", () => {
  const doc = { flujo_dept: "Archivo", flujo_usuario: "u-9", status: "in_progress" };
  it("nombra, dice «Tú» o cae al genérico; nunca el UUID", () => {
    expect(responsableTexto(doc, "u-1", { "u-9": "maria" })).toBe("Responsable: maria");
    expect(responsableTexto(doc, "u-9", {})).toBe("Responsable: Tú");
    expect(responsableTexto(doc, "u-1", {})).toBe("A cargo de un responsable de la dependencia");
  });
  it("null sin pasos, sin poseedor o en terminal", () => {
    expect(responsableTexto({ ...doc, flujo_dept: null }, "u-1", {})).toBeNull();
    expect(responsableTexto({ ...doc, flujo_usuario: null }, "u-1", {})).toBeNull();
    expect(responsableTexto({ ...doc, status: "closed" }, "u-1", {})).toBeNull();
  });
});
