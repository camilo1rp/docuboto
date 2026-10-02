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
    // `no_active_step` ya no sirve de muestra: tiene mensaje propio (ADR-028,
    // ver tenencia.test.js). Aquí interesa el sobre, no el código.
    const otro = {
      detail: { error: "document_not_found", detail: "Radicado no encontrado", status: 404 },
    };
    expect(mensajeError(otro, "por defecto")).toBe("Radicado no encontrado");
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

describe("mensajeError — la validación de esquema también se lee", () => {
  it("un 422 de Pydantic muestra lo que el backend explicó", () => {
    // Medido reclasificando un radicado: el backend decía exactamente qué
    // faltaba y con qué fundamento legal, y la pantalla mostraba «Error 422 al
    // reclasificar el nivel de seguridad». La persona veía que no se podía, sin
    // ninguna vía para averiguar qué le faltaba.
    const cuerpo = {
      detail: [
        {
          type: "value_error",
          loc: ["body"],
          msg: "Value error, fundamento_juridico es obligatorio al reclasificar a nivel_seguridad >= 2 (Ley 1712/2014 art. 19/28)",
        },
      ],
    };
    const texto = mensajeError(cuerpo, "Error 422 al reclasificar el nivel de seguridad.");
    expect(texto).toContain("fundamento_juridico es obligatorio");
    expect(texto).toContain("Ley 1712/2014");
    expect(texto).not.toContain("Error 422");
  });

  it("quita el «Value error,» que antepone Pydantic", () => {
    // Es ruido del framework: quien lee el aviso no tiene por qué saber con qué
    // biblioteca se validó su formulario.
    const texto = mensajeError(
      { detail: [{ msg: "Value error, el plazo debe ser positivo" }] },
      "x",
    );
    expect(texto).toBe("el plazo debe ser positivo");
  });

  it("varios campos inválidos se enumeran, no se recorta al primero", () => {
    // Quedarse con uno deja a la persona corrigiendo de uno en uno sin saber
    // cuántos faltan.
    const texto = mensajeError(
      {
        detail: [
          { msg: "Value error, falta el fundamento" },
          { msg: "Value error, falta la causal" },
        ],
      },
      "x",
    );
    expect(texto).toContain("falta el fundamento");
    expect(texto).toContain("falta la causal");
  });

  it("una lista sin mensajes legibles cae al texto por defecto", () => {
    // Gemelo: la corrección no puede inventar un mensaje donde no lo hay.
    expect(mensajeError({ detail: [] }, "por defecto")).toBe("por defecto");
    expect(mensajeError({ detail: [{ type: "value_error" }] }, "por defecto")).toBe("por defecto");
    expect(mensajeError({ detail: [{ msg: "field required" }] }, "por defecto")).toBe(
      "por defecto",
    );
  });

  it("el sobre anidado del proyecto sigue teniendo prioridad", () => {
    // Control de no-regresión: la forma que ya funcionaba no puede romperse por
    // añadir la nueva.
    const cuerpo = { detail: { error: "no_activo", detail: "No hay paso activo.", status: 409 } };
    expect(mensajeError(cuerpo, "x")).toBe("No hay paso activo.");
  });
});
