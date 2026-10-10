// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** load SSR de la ventanilla: la política sale del servidor, fail-closed. */
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));

const { load } = await import("./+page.server.js");

const ok = (cuerpo) => ({ ok: true, json: async () => cuerpo });

describe("/radicacion load — política de la entidad", () => {
  it("la toma de /ventanilla/politica", async () => {
    const fetch = vi.fn(async (url) =>
      String(url).endsWith("/politica")
        ? ok({ publicada: true, version: "v3", url: "https://e/p", texto: "t", huella: "h" })
        : ok([{ code: "DP" }]),
    );
    const r = await load({ fetch });
    expect(String(fetch.mock.calls[0][0])).toBe("http://gw/api/v1/public/demo/ventanilla/politica");
    expect(r.politica).toMatchObject({ publicada: true, version: "v3", url: "https://e/p" });
    expect(r.habilitada).toBe(true);
  });

  it("si el servidor no la puede verificar, queda sin publicar y no verificable", async () => {
    const fetch = vi.fn(async (url) =>
      String(url).endsWith("/politica") ? { ok: false, status: 503 } : ok([{ code: "DP" }]),
    );
    const r = await load({ fetch });
    expect(r.politica.publicada).toBe(false);
    expect(r.politica.verificable).toBe(false);
  });
});
