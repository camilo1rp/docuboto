// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * apiError.sin-fugas.test.js — `mensajeError` nunca muestra un código ni un
 * estado HTTP del servidor (CR-001 UI-02, ADR-057 §7).
 *
 * La regla del plan: ningún mensaje casa con `snake_case` ni con un 4xx/5xx.
 * Cuando el catálogo no conoce el código, la persona ve un mensaje genérico con
 * una referencia para el administrador.
 */
import { describe, it, expect } from "vitest";
import * as apiError from "./apiError.js";

const { mensajeError, PARECE_CODIGO } = apiError;

const POR_DEFECTO = "No se pudo guardar el radicado.";

/** Respuestas que el servidor puede devolver y que no se pueden mostrar tal cual. */
const CRUDAS = [
  ["código plano desconocido", { error: "tramite_bloqueado_por_regla" }],
  ["código anidado desconocido", { detail: { error: "algo_nuevo_del_backend", status: 409 } }],
  ["detail que es un código", { detail: "radicado_not_found" }],
  ["detail con estado HTTP", { detail: "Upstream returned 502" }],
  ["detail anidado que es un código", { detail: { detail: "quota_exceeded" } }],
  ["cuerpo que es un código", "internal_error"],
  ["cuerpo con estado HTTP", "Error 500"],
];

describe("mensajeError — sin códigos ni estados crudos", () => {
  it.each(CRUDAS)("%s → mensaje genérico con referencia", (_, cuerpo) => {
    const texto = mensajeError(cuerpo, POR_DEFECTO);
    expect(texto).not.toMatch(PARECE_CODIGO);
    expect(texto).toMatch(
      /^No se pudo completar la acción\. Si continúa, informe al administrador la referencia .+\.$/,
    );
  });

  it("la referencia es el request_id cuando viene", () => {
    const texto = mensajeError({ error: "x_y", request_id: "a1b2c3" }, POR_DEFECTO);
    expect(texto).toContain("la referencia a1b2c3.");
  });

  it("un detail en español que nombra un campo se muestra con el campo en palabras", () => {
    const texto = mensajeError(
      { detail: "Falta el campo obligatorio 'monto_contrato'." },
      POR_DEFECTO,
    );
    expect(texto).toBe("Falta el campo obligatorio 'monto contrato'.");
  });

  it("sin cuerpo se usa el texto de la pantalla", () => {
    expect(mensajeError(null, POR_DEFECTO)).toBe(POR_DEFECTO);
  });

  it("ningún mensaje del catálogo parece un código", () => {
    const catalogos = Object.entries(apiError).filter(
      ([nombre, valor]) => nombre.startsWith("MENSAJES_") && valor && typeof valor === "object",
    );
    expect(catalogos.length).toBeGreaterThan(5);
    const malos = catalogos.flatMap(([nombre, mapa]) =>
      Object.entries(mapa)
        .filter(([, texto]) => PARECE_CODIGO.test(texto))
        .map(([codigo]) => `${nombre}.${codigo}`),
    );
    expect(malos).toEqual([]);
  });

  it("cada código del catálogo produce un mensaje sin fugas", () => {
    const codigos = Object.entries(apiError)
      .filter(([nombre]) => nombre.startsWith("MENSAJES_"))
      .flatMap(([, mapa]) => Object.keys(mapa));
    for (const codigo of codigos) {
      expect(mensajeError({ error: codigo }, POR_DEFECTO), codigo).not.toMatch(PARECE_CODIGO);
    }
  });
});
