// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El sobre doble de los errores del gateway.
 *
 * Los routers levantan `HTTPException(detail={"error", "detail", "status"})` y
 * FastAPI serializa eso DENTRO de su propio `detail`. Los proxies BFF reenvían
 * el cuerpo tal cual, así que el navegador recibe `{detail: {error, …}}` y no
 * `{error, …}`. Todo el frontend comparaba el campo de primer nivel.
 *
 * Encontrado navegando: un radicado recién radicado decía «No se pudo cargar el
 * paso actual» en vez de «Sin paso activo», porque `no_active_step` nunca se
 * reconocía. El mismo supuesto estaba en cinco sitios más.
 */
import { describe, it, expect } from "vitest";
import { codigoError, mensajeError } from "./apiError.js";

const ANIDADO = {
  detail: { error: "no_active_step", detail: "No active flow step found", status: 404 },
};
const PLANO = { error: "connection_error", detail: "No se pudo conectar." };

describe("codigoError", () => {
  it("lee la forma ANIDADA, que es la que produce el backend", () => {
    expect(codigoError(ANIDADO)).toBe("no_active_step");
  });

  it("lee también la forma PLANA, que fabrican los proxies BFF", () => {
    // Las dos existen de verdad; quedarse con una sola volvería a partir el
    // manejo de errores por la mitad.
    expect(codigoError(PLANO)).toBe("connection_error");
  });

  it("devuelve null cuando no hay código, sin reventar", () => {
    for (const v of [null, undefined, "texto", 42, {}, { detail: "un string" }]) {
      expect(codigoError(v)).toBeNull();
    }
  });
});

describe("mensajeError", () => {
  it("saca el mensaje humano del sobre anidado", () => {
    expect(mensajeError(ANIDADO, "por defecto")).toBe("No active flow step found");
  });

  it("NUNCA devuelve un objeto", () => {
    // Esta es la aserción central. `/radicar` hacía `err?.detail ?? "…"` y,
    // como `detail` es un objeto, el aviso de error salía literalmente como
    // «[object Object]» — la persona veía que algo falló y nada más.
    for (const v of [ANIDADO, PLANO, {}, { detail: {} }, { detail: { detail: {} } }, null]) {
      expect(typeof mensajeError(v, "por defecto")).toBe("string");
    }
    expect(String(mensajeError(ANIDADO, "x"))).not.toMatch(/\[object Object\]/);
  });

  it("cae en el valor por defecto cuando no hay nada legible", () => {
    expect(mensajeError({}, "Error al crear el radicado")).toBe("Error al crear el radicado");
    expect(mensajeError(null, "Error al crear el radicado")).toBe("Error al crear el radicado");
  });

  it("prefiere el mensaje al código cuando existen los dos", () => {
    expect(mensajeError(PLANO, "x")).toBe("No se pudo conectar.");
  });
});
