// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * C-5: retenciones legales (C7), valoración TVD (C7) y estado del soporte (C14).
 *
 * Mutaciones que deben caer:
 *  - construirFijar admite `valoracion_pendiente` (la retención del sistema);
 *  - construirLevantar usa el motivo de la fila en vez del confirmado;
 *  - admiteValoracion deja de exigir aprobada|convalidada (borrador/devuelta);
 *  - soporteEstadoInfo devuelve texto para un estado desconocido.
 */
import { describe, it, expect } from "vitest";
import {
  construirFijar,
  construirLevantar,
  esDeSistema,
  estaActivo,
  motivoLabel,
} from "./legalHold.js";
import { admiteValoracion, construirValoracion, soporteEstadoInfo } from "./valoracion.js";

describe("construirFijar", () => {
  const base = {
    motivo_tipo: "judicial",
    fundamento: "Orden del juzgado 12",
    acta_ref: "Oficio 55",
  };
  it("control positivo", () => {
    expect(construirFijar(base)).toEqual({ ok: base });
  });
  it("rechaza la retención de sistema, fundamento corto y acta vacía", () => {
    expect("error" in construirFijar({ ...base, motivo_tipo: "valoracion_pendiente" })).toBe(true);
    expect("error" in construirFijar({ ...base, fundamento: "corto" })).toBe(true);
    expect("error" in construirFijar({ ...base, acta_ref: " " })).toBe(true);
  });
});

describe("construirLevantar", () => {
  it("envía el motivo CONFIRMADO", () => {
    const r = construirLevantar({
      motivo_confirmado: "fiscal",
      fundamento: "Levantada por el despacho",
      acta_ref: "Oficio 9",
    });
    expect(r).toEqual({
      ok: { motivo_tipo: "fiscal", fundamento: "Levantada por el despacho", acta_ref: "Oficio 9" },
    });
  });
  it("sin motivo confirmado o con el de sistema no hay cuerpo", () => {
    const f = { fundamento: "Levantada por el despacho", acta_ref: "x" };
    expect("error" in construirLevantar({ ...f, motivo_confirmado: "" })).toBe(true);
    expect("error" in construirLevantar({ ...f, motivo_confirmado: "valoracion_pendiente" })).toBe(
      true,
    );
  });
});

describe("helpers de retención", () => {
  it("la de valoración es de sistema; levantada no está activa", () => {
    expect(esDeSistema({ motivo_tipo: "valoracion_pendiente" })).toBe(true);
    expect(esDeSistema({ motivo_tipo: "judicial" })).toBe(false);
    expect(estaActivo({ levantado_at: null })).toBe(true);
    expect(estaActivo({ levantado_at: "2026-01-01T00:00:00Z" })).toBe(false);
    expect(motivoLabel("judicial")).toBe("Judicial");
  });
});

describe("valoración TVD", () => {
  it.each([
    ["aprobada", true],
    ["convalidada", true],
    ["borrador", false],
    ["devuelta", false],
    ["derogada", false],
  ])("estado %s → ofrece valoración: %s", (estado, esperado) => {
    expect(admiteValoracion({ estado })).toBe(esperado);
  });
  it("una TRD nunca", () => {
    expect(admiteValoracion({ estado: "aprobada", tipo_instrumento: "TRD" })).toBe(false);
  });
  it("construirValoracion exige acta, fecha real y fundamento >= 20", () => {
    const ok = { acta_comite: "Acta 3", fecha_acta: "2026-03-01", fundamento: "x".repeat(20) };
    expect(construirValoracion(ok)).toEqual({ ok });
    expect("error" in construirValoracion({ ...ok, fecha_acta: "01/03/2026" })).toBe(true);
    expect("error" in construirValoracion({ ...ok, fundamento: "x".repeat(19) })).toBe(true);
    expect("error" in construirValoracion({ ...ok, acta_comite: " " })).toBe(true);
  });
});

describe("soporteEstadoInfo", () => {
  it.each(["vigente", "baja_avalada", "eliminado_por_trd"])("%s tiene texto", (estado) => {
    expect(soporteEstadoInfo({ soporte_estado: estado })?.estado).toBe(estado);
  });
  it("desconocido o ausente: nada", () => {
    expect(soporteEstadoInfo({ soporte_estado: "otro" })).toBeNull();
    expect(soporteEstadoInfo({})).toBeNull();
  });
  it("eliminado por TRD menciona lote y acta", () => {
    const t = soporteEstadoInfo({
      soporte_estado: { estado: "eliminado_por_trd", lote_codigo: "L-1", acta_sha256: "abc" },
    })?.texto;
    expect(t).toMatch(/L-1/);
    expect(t).toMatch(/abc/);
  });
});
