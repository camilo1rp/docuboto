// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Resolución de números de radicado a UUID.
 *
 * El defecto que cierra: la carga masiva de expedientes ofrecía «Números de
 * radicado a vincular» y los mandaba en `tracking_numbers` — que en el contrato
 * de archive-service es una lista de ETIQUETAS paralela a `radicado_ids`, no
 * una forma de referirse a un radicado. Resultado: expediente creado, ningún
 * radicado dentro, y la pantalla anunciando «Completado · 0 con error».
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { resolverRadicado, resolverRadicados } from "./radicadoRef.js";

const UUID_A = "11111111-2222-4333-8444-555555555555";
const UUID_B = "66666666-7777-4888-8999-aaaaaaaaaaaa";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(porNumero) {
  const fn = vi.fn(async (url) => {
    const numero = decodeURIComponent(String(url).split("/by-tracking/")[1] ?? "");
    const id = porNumero[numero];
    if (!id) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => ({ id, tracking_number: numero }) };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("resolverRadicado", () => {
  it("un número de radicado se resuelve a su UUID", async () => {
    stubFetch({ "2026-DEMO-E-000017": UUID_A });
    expect(await resolverRadicado("2026-DEMO-E-000017", "t", "demo")).toBe(UUID_A);
  });

  it("un UUID se devuelve tal cual, sin llamar a la API", async () => {
    const fn = stubFetch({});
    expect(await resolverRadicado(UUID_A, "t", "demo")).toBe(UUID_A);
    expect(fn).not.toHaveBeenCalled();
  });

  it("un número que no existe —o que no se puede ver— da null", async () => {
    // El backend responde 404 tanto si no existe como si excede el clearance
    // (no-read-up): aquí los dos casos son el mismo, que es justo el punto.
    stubFetch({});
    expect(await resolverRadicado("2026-DEMO-E-999999", "t", "demo")).toBeNull();
  });

  it("si la red falla, no inventa un identificador", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("red caída");
      }),
    );
    expect(await resolverRadicado("2026-DEMO-E-000017", "t", "demo")).toBeNull();
  });
});

describe("resolverRadicados — las dos listas van ALINEADAS", () => {
  it("devuelve ids y números en el mismo orden", async () => {
    // Es la razón de que esta función exista en vez de un `map` suelto: el
    // backend cruza `radicado_ids[i]` con `tracking_numbers[i]`, así que
    // desalinearlas etiquetaría cada radicado con el número del siguiente.
    stubFetch({ "2026-DEMO-E-000017": UUID_A, "2026-DEMO-S-000013": UUID_B });
    const r = await resolverRadicados(["2026-DEMO-E-000017", "2026-DEMO-S-000013"], "t", "demo");
    expect(r.ids).toEqual([UUID_A, UUID_B]);
    expect(r.numeros).toEqual(["2026-DEMO-E-000017", "2026-DEMO-S-000013"]);
    expect(r.noResueltos).toEqual([]);
  });

  it("lo que no resuelve se reporta, NO se descarta en silencio", async () => {
    // Descartarlo sería el defecto original con otra forma: un expediente al
    // que le falta un documento se crea igual y nadie se entera.
    stubFetch({ "2026-DEMO-E-000017": UUID_A });
    const r = await resolverRadicados(["2026-DEMO-E-000017", "2026-DEMO-E-999999"], "t", "demo");
    expect(r.ids).toEqual([UUID_A]);
    expect(r.numeros).toEqual(["2026-DEMO-E-000017"]);
    expect(r.noResueltos).toEqual(["2026-DEMO-E-999999"]);
  });

  it("las dos listas siempre tienen la misma longitud", async () => {
    stubFetch({ "2026-DEMO-E-000017": UUID_A });
    const r = await resolverRadicados(["2026-DEMO-E-000017", "no-existe", "tampoco"], "t", "demo");
    expect(r.ids.length).toBe(r.numeros.length);
  });
});
