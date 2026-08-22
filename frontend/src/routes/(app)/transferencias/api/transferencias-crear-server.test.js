// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Crear una transferencia acepta el CÓDIGO del expediente.
 *
 * El proxy exigía un UUID y devolvía 400 con cualquier otra cosa. El código
 * (`EXP-2026-0020`) es la identidad que el sistema muestra y que va impresa en
 * la carátula; el UUID no aparece en ninguna pantalla. Se resuelve aquí, igual
 * que en la carga masiva de expedientes.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

const UUID_EXP = "7ce8f33d-0dd9-4ecc-9785-423f731032a8";
const LOCALS = { accessToken: "tok", user: { tenant_slug: "demo", sub: "u-1" } };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Backend que resuelve `EXP-2026-0020` y acepta la creación. */
function backend({ resuelve = true } = {}) {
  const llamadas = [];
  const fn = vi.fn(async (url, init) => {
    const u = String(url);
    llamadas.push({ url: u, body: init?.body ? JSON.parse(init.body) : null });
    if (u.includes("/expedientes/search")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          hits: resuelve ? [{ id: UUID_EXP, code: "EXP-2026-0020" }] : [],
        }),
      };
    }
    return {
      ok: true,
      status: 201,
      json: async () => ({ id: "tr-1", estado: "preparacion" }),
    };
  });
  vi.stubGlobal("fetch", fn);
  return llamadas;
}

async function crear(cuerpo) {
  const { POST } = await import("./+server.js");
  return POST({
    locals: LOCALS,
    request: new Request("http://x", { method: "POST", body: JSON.stringify(cuerpo) }),
  });
}

describe("POST /transferencias/api", () => {
  it("un código de expediente se resuelve y viaja como UUID", async () => {
    const llamadas = backend();
    const res = await crear({ tipo: "primaria", expediente_id: "EXP-2026-0020" });

    expect(res.status).toBe(201);
    const alBackend = llamadas.find((c) => c.url.includes("/transferencias"));
    expect(alBackend.body.expediente_id).toBe(UUID_EXP);
  });

  it("un código que no existe da 404 con el código a la vista", async () => {
    // Gemelo: no se puede resolver a medias ni mandar el texto crudo al
    // backend, que respondería un error sobre un campo que la persona no usó.
    backend({ resuelve: false });
    const res = await crear({ tipo: "primaria", expediente_id: "EXP-NO-EXISTE" });

    expect(res.status).toBe(404);
    const cuerpo = await res.json();
    expect(cuerpo.error).toBe("expediente_no_resuelto");
    expect(cuerpo.detail).toContain("EXP-NO-EXISTE");
  });

  it("un UUID sigue aceptándose sin consultar nada", async () => {
    // Las integraciones que ya mandan el identificador no pagan una búsqueda.
    const llamadas = backend();
    const res = await crear({ tipo: "primaria", expediente_id: UUID_EXP });

    expect(res.status).toBe(201);
    expect(llamadas.some((c) => c.url.includes("/expedientes/search"))).toBe(false);
  });
});
