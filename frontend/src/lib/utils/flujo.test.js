// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Presentación de pasos y eventos de trámite.
 *
 * Los valores que se fijan aquí NO son de invención: salen del backend.
 *  - `flow_steps.status`: pending (DEFAULT del esquema), in_progress,
 *    completed, cancelled, returned.
 *  - `flow_events.tipo_tx`: los doce del catálogo configurable
 *    `workflow_transaction_types`, MÁS seis eventos de sistema que emite el
 *    propio flujo (assign, transfer, complete, devolucion, reassign, rollback).
 *
 * HojaRuta traducía `active` —que no existe— y no `pending` —que es el más
 * común—, así que cada paso recién asignado se veía como «Oficina Jurídica
 * pending». Y de los eventos solo cubría los del catálogo, de modo que asignar,
 * trasladar, completar y devolver salían con su código crudo.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { PASO_ESTADO_LABEL, PASO_ESTADO_CHIP, etiquetaPaso, etiquetaEventoFlujo } from "./flujo.js";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

const ESTADOS_REALES = ["pending", "in_progress", "completed", "cancelled", "returned"];
const EVENTOS_SISTEMA = ["assign", "transfer", "complete", "devolucion", "reassign", "rollback"];

describe("estados de paso", () => {
  it.each(ESTADOS_REALES)("«%s» tiene etiqueta y chip", (estado) => {
    expect(PASO_ESTADO_LABEL[estado]).toBeTruthy();
    expect(PASO_ESTADO_CHIP[estado]).toBeTruthy();
  });

  it("ninguno se queda en inglés", () => {
    for (const e of ESTADOS_REALES) expect(etiquetaPaso(e)).not.toBe(e);
  });

  it("no inventa un estado «active» que el backend no tiene", () => {
    // Estaba en el mapa de HojaRuta y no existe en `flow_steps.status`. Un mapa
    // con entradas fantasma da la impresión de cobertura que no hay.
    expect(PASO_ESTADO_LABEL.active).toBeUndefined();
  });

  it("ante un estado nuevo devuelve el crudo, no un hueco", () => {
    expect(etiquetaPaso("en_revision")).toBe("en_revision");
    expect(etiquetaPaso(null)).toBe("—");
  });
});

describe("eventos de trámite", () => {
  it.each(EVENTOS_SISTEMA)("el evento de sistema «%s» se traduce", (tipo) => {
    expect(etiquetaEventoFlujo(tipo)).not.toBe(tipo);
  });

  it("también traduce los del catálogo sin necesitar la API", () => {
    expect(etiquetaEventoFlujo("vobo")).toBe("Visto bueno");
  });

  it("el nombre que manda la API GANA a la reserva local", () => {
    // El catálogo es configurable por entidad: si alguien renombra «vobo» a
    // «Aprobación jurídica», la pantalla debe decir eso y no la copia local.
    const catalogo = [{ code: "vobo", nombre: "Aprobación jurídica" }];
    expect(etiquetaEventoFlujo("vobo", catalogo)).toBe("Aprobación jurídica");
  });

  it("un tipo desconocido devuelve su código, no un hueco", () => {
    expect(etiquetaEventoFlujo("tramite_exotico")).toBe("tramite_exotico");
  });
});

describe("los componentes usan la fuente única", () => {
  for (const c of [
    "components/workflow/HojaRuta.svelte",
    "components/workflow/WorkflowCard.svelte",
  ]) {
    it(`${c} importa de $lib/utils/flujo.js`, () => {
      const fuente = readFileSync(path.join(RAIZ, "lib", c), "utf-8");
      expect(fuente).toMatch(/from "\$lib\/utils\/flujo\.js"/);
      // Y ninguno vuelve a escribir el mapa a mano.
      expect(fuente).not.toMatch(/pending:\s*"Pendiente"/);
      expect(fuente).not.toMatch(/active:\s*"Activo"/);
    });
  }
});
