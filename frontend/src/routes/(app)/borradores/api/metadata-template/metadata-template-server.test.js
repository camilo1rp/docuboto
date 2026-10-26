// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi, afterEach } from "vitest";

import { GET } from "./+server.js";

const original = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = original;
});

const locals = { accessToken: "t", user: { tenant_slug: "demo" } };
const pedir = (qs) =>
  GET({ locals, url: new URL(`http://x/borradores/api/metadata-template${qs}`) });

describe("borradores/api/metadata-template", () => {
  it("sin tipo_documental responde 400 y no llama al gateway", async () => {
    const f = vi.fn();
    globalThis.fetch = f;
    for (const qs of ["", "?tipo_documental=", "?tipo_documental=%20"]) {
      const r = await pedir(qs);
      expect(r.status).toBe(400);
    }
    expect(f).not.toHaveBeenCalled();
  });

  it("con tipo reenvía la consulta", async () => {
    const f = vi.fn(async () => ({ status: 200, json: async () => [] }));
    globalThis.fetch = f;
    const r = await pedir("?tipo_documental=OFI");
    expect(r.status).toBe(200);
    expect(String(f.mock.calls[0][0])).toContain("tipo_documental=OFI");
  });
});
