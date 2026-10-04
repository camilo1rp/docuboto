// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


/**
 * estado-acuse.test.js — el proxy de cambio de estado deja pasar el acuse (AREA-09-10).
 * El backend es quien EXIGE el acuse para «entregado»; aquí solo se comprueba que el
 * proxy no lo pierda (una allowlist que olvida un campo lo recorta en silencio).
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({ API_V1: "http://gw/api/v1" }));

const { POST } = await import("./+server.js");

function evento(cuerpo) {
  return {
    locals: { accessToken: "t", user: { tenant_slug: "demo", sub: "u1" } },
    params: { id: "s1" },
    request: { json: async () => cuerpo },
  };
}

describe("/envios/api/[id]/estado — acuse de recibo", () => {
  it("reenvía acuse_file_id y acuse_sha256 al backend", async () => {
    const f = vi.fn().mockResolvedValue({ status: 200, json: async () => ({}) });
    vi.stubGlobal("fetch", f);
    await POST(
      evento({ estado: "entregado", acuse_file_id: "f-1", acuse_sha256: "a".repeat(64) }),
    );
    const cuerpo = JSON.parse(f.mock.calls[0][1].body);
    expect(cuerpo.acuse_file_id).toBe("f-1");
    expect(cuerpo.acuse_sha256).toBe("a".repeat(64));
  });

  it("sin acuse manda null y deja que el backend responda 422 para «entregado»", async () => {
    const f = vi.fn().mockResolvedValue({
      status: 422,
      json: async () => ({ detail: "exige el acuse" }),
    });
    vi.stubGlobal("fetch", f);
    const res = await POST(evento({ estado: "entregado" }));
    const cuerpo = JSON.parse(f.mock.calls[0][1].body);
    expect(cuerpo.acuse_file_id).toBeNull();
    expect(res.status).toBe(422);
  });
});
