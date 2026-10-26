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
  loteTermino,
  puedeReprocesar,
  itemsFallidos,
  itemsAProcesar,
  motivoDelFallo,
  textoConfirmacion,
  mensajeReproceso,
  asuntoDelItem,
} from "./loteReproceso.js";

describe("loteTermino", () => {
  it.each(["completed", "completado_con_errores", "failed"])("%s es terminal", (s) => {
    expect(loteTermino(s)).toBe(true);
  });
  it.each(["pending", "processing", undefined])("%s no es terminal", (s) => {
    expect(loteTermino(s)).toBe(false);
  });
});

describe("puedeReprocesar", () => {
  it("requiere reprocesable Y PERM_RADI", () => {
    expect(puedeReprocesar({ reprocesable: true }, ["PERM_RADI"])).toBe(true);
    expect(puedeReprocesar({ reprocesable: true }, [])).toBe(false);
    expect(puedeReprocesar({ reprocesable: false }, ["PERM_RADI"])).toBe(false);
    expect(puedeReprocesar(null, ["PERM_RADI"])).toBe(false);
  });
});

describe("itemsFallidos / itemsAProcesar", () => {
  const lote = {
    pending_items: 2,
    items: [
      { item_index: 0, status: "success" },
      { item_index: 1, status: "failed" },
      { item_index: 2, status: "failed" },
    ],
  };
  it("deriva los fallidos de items[] si no hay errores[]", () => {
    expect(itemsFallidos(lote)).toHaveLength(2);
  });
  it("usa errores[] cuando viene", () => {
    expect(itemsFallidos({ errores: [{ item_index: 9 }], items: [] })).toHaveLength(1);
  });
  it("cuenta pending_items; si no, los fallidos", () => {
    expect(itemsAProcesar(lote)).toBe(2);
    expect(itemsAProcesar({ items: lote.items })).toBe(2);
  });
});

describe("motivoDelFallo", () => {
  it("un motivo en español va como motivo principal", () => {
    expect(motivoDelFallo({ error_message: "La dependencia destino no existe." })).toEqual({
      motivo: "La dependencia destino no existe.",
      detalle: "",
    });
  });
  it("un texto técnico pasa a detalle secundario", () => {
    const r = motivoDelFallo({
      error_message: "asyncpg.exceptions.UniqueViolationError: duplicate key",
      error_tipo: "db",
    });
    expect(r.motivo).toMatch(/fallo del sistema/);
    expect(r.detalle).toContain("asyncpg");
  });
  it("sin motivo no imprime null", () => {
    expect(motivoDelFallo({ error_message: null }).motivo).toBe("Falló sin motivo registrado.");
  });
});

describe("textos", () => {
  it("la confirmación dice cuántos y que se conserva el número", () => {
    expect(textoConfirmacion(3)).toMatch(/3 documentos/);
    expect(textoConfirmacion(1)).toMatch(/1 documento /);
    expect(textoConfirmacion(3)).toMatch(/conservan su número/);
  });
  it("409/404 salen en español con tildes", () => {
    expect(mensajeReproceso(409, { error: "lote_en_proceso" })).toMatch(
      /todavía se está procesando/,
    );
    expect(mensajeReproceso(409, { detail: { error: "nada_que_reprocesar" } })).toMatch(
      /No hay documentos pendientes/,
    );
    expect(mensajeReproceso(404, { error: "lote_no_encontrado" })).toMatch(
      /No se encontró el lote/,
    );
  });
});

describe("asuntoDelItem", () => {
  it("subject null (por encima del clearance) dice «Asunto reservado»", () => {
    expect(asuntoDelItem({ subject: null })).toBe("Asunto reservado");
    expect(asuntoDelItem({ subject: "  " })).toBe("Asunto reservado");
    expect(asuntoDelItem({ subject: "Oficio" })).toBe("Oficio");
  });
  it("404 con sobre anidado (detail.error) sale en español", () => {
    expect(
      mensajeReproceso(404, {
        detail: { error: "lote_no_encontrado", detail: "No existe el trabajo solicitado" },
      }),
    ).toMatch(/No se encontró el lote/);
  });
});
