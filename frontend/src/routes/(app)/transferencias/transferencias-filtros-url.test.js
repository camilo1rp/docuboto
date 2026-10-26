// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * `/transferencias?estado=…` filtra de verdad.
 *
 * El enlace ya existía —el panel principal y los avisos lo producen— y la
 * pantalla lo ignoraba: pedía la lista COMPLETA al gateway y pintaba los
 * desplegables en «todos». Quien llegaba por el enlace veía otra cosa de la que
 * pidió, y sin ninguna señal de que el filtro se hubiera perdido.
 *
 * Se resuelve en el `load` y no al montar para que la PRIMERA pintada ya venga
 * filtrada; y con allowlist, para que un valor inventado en la URL se descarte
 * aquí en vez de viajar al gateway y volver como un error sobre un campo que
 * la persona no escribió.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

const LOCALS = { user: { tenant_slug: "demo" } };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Registra las URL pedidas al gateway y responde listas vacías. */
function gateway() {
  const urls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      urls.push(String(url));
      return { ok: true, status: 200, json: async () => [] };
    }),
  );
  return urls;
}

/** El espía se instala ANTES de importar el `load`, no después. */
async function cargarConEspia(href) {
  const urls = gateway();
  const { load } = await import("./+page.server.js");
  const resultado = await load({
    cookies: { get: () => "tok" },
    parent: async () => LOCALS,
    url: new URL(href),
  });
  return { urls, resultado };
}

describe("/transferencias — los filtros vienen de la URL", () => {
  it("el estado de la URL viaja al gateway y vuelve como semilla", async () => {
    const { urls, resultado } = await cargarConEspia(
      "http://x/transferencias?estado=enviada&tipo=secundaria",
    );

    const listado = urls.find((u) => u.includes("/transferencias"));
    expect(listado).toContain("estado=enviada");
    expect(listado).toContain("tipo=secundaria");
    expect(resultado.filtros).toEqual({ estado: "enviada", tipo: "secundaria" });
  });

  it("sin filtros en la URL se pide la lista completa, como siempre", async () => {
    const { urls, resultado } = await cargarConEspia("http://x/transferencias");

    const listado = urls.find((u) => u.includes("/transferencias"));
    expect(listado).not.toContain("estado=");
    expect(resultado.filtros).toEqual({ estado: "", tipo: "" });
  });

  it("un estado inventado se descarta en vez de viajar al gateway", async () => {
    // Sin la allowlist, `?estado=' OR 1=1` acabaría en la consulta del backend
    // y la respuesta sería un error sobre un campo que nadie escribió.
    const { urls, resultado } = await cargarConEspia("http://x/transferencias?estado=inventado");

    const listado = urls.find((u) => u.includes("/transferencias"));
    expect(listado).not.toContain("inventado");
    expect(resultado.filtros.estado).toBe("");
  });
});
