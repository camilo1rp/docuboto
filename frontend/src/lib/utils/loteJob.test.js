// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Los tres estados de un ítem de lote se distinguen en pantalla.
 *
 * Lo que estos tests fijan se vio con un lote real de dos documentos: la
 * pantalla listaba «Error #1 null» y «Error #2 null» mientras el contador de
 * arriba decía «0 con error». Los dos leían el mismo objeto; solo uno lo leía
 * bien. Un ítem recién encolado está en `pending`, no ha fallado, y su
 * `error_message` es `null` — que impreso tal cual se lee «null».
 */
import { describe, it, expect } from "vitest";
import { presentacionDelItem, detalleDelItem } from "./loteJob.js";

describe("presentacionDelItem", () => {
  it("un ítem en cola NO se anuncia como error", () => {
    // Este es el defecto exacto: `success ? OK : Error` convertía toda la
    // espera normal de un lote en una pantalla llena de errores inexistentes.
    const p = presentacionDelItem("pending");
    expect(p.etiqueta).toBe("En cola");
    expect(p.chip).not.toBe("error");
  });

  it("un ítem en curso tampoco", () => {
    expect(presentacionDelItem("processing").chip).not.toBe("error");
  });

  it("el que SÍ falló se marca como error", () => {
    // Gemelo del anterior: la corrección no puede consistir en dejar de avisar.
    expect(presentacionDelItem("failed")).toEqual({ chip: "error", etiqueta: "Error" });
  });

  it("el éxito se marca como éxito", () => {
    expect(presentacionDelItem("success")).toEqual({ chip: "success", etiqueta: "OK" });
  });

  it("un estado desconocido no se disfraza de éxito ni de error", () => {
    expect(presentacionDelItem("algo_nuevo").chip).toBe("neutral");
    expect(presentacionDelItem(null).etiqueta).toBe("—");
  });
});

describe("detalleDelItem", () => {
  it("nunca imprime «null» al usuario", () => {
    const texto = detalleDelItem({
      item_index: 0,
      status: "pending",
      result_id: null,
      error_message: null,
    });
    expect(texto).not.toContain("null");
    expect(texto.length).toBeGreaterThan(0);
  });

  it("un fallo sin motivo lo dice, en vez de dejar el hueco", () => {
    // Un espacio en blanco donde debería estar la causa se lee como «no pasó
    // nada», que es lo contrario de lo ocurrido.
    expect(
      detalleDelItem({ item_index: 0, status: "failed", result_id: null, error_message: null }),
    ).toBe("Falló sin motivo registrado.");
    expect(
      detalleDelItem({ item_index: 0, status: "failed", result_id: null, error_message: "   " }),
    ).toBe("Falló sin motivo registrado.");
  });

  it("un fallo CON motivo muestra el motivo", () => {
    expect(
      detalleDelItem({
        item_index: 0,
        status: "failed",
        result_id: null,
        error_message: "asunto vacío",
      }),
    ).toBe("asunto vacío");
  });

  it("el éxito muestra lo que se creó", () => {
    expect(
      detalleDelItem({
        item_index: 0,
        status: "success",
        result_id: "uuid-1",
        error_message: null,
      }),
    ).toBe("uuid-1");
  });
});
