// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxies de retenciones legales (C-5 / C7): sin sesión 401, allowlist de campos,
 * y el levantamiento envía el motivo CONFIRMADO. Mutaciones que deben caer:
 * reenviar el body íntegro; no exigir motivo_tipo en levantar.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, POST } from "./api/legal-holds/+server.js";
import { POST as LEVANTAR } from "./api/legal-holds/[holdId]/levantar/+server.js";

const ID = "7ce8f33d-0dd9-4ecc-9785-423f731032a8";
const HOLD = "11111111-2222-4333-8444-555555555555";
const LOCALS = { accessToken: "t", user: { tenant_slug: "demo", sub: "u1" } };
const req = (body) => ({ json: async () => body });

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue({ status: 200, json: async () => [] });
});

describe("legal-holds proxies", () => {
  it("sin sesión: 401 sin tocar el backend", async () => {
    expect((await GET({ locals: {}, params: { id: ID } })).status).toBe(401);
    expect((await POST({ locals: {}, params: { id: ID }, request: req({}) })).status).toBe(401);
    expect(
      (await LEVANTAR({ locals: {}, params: { id: ID, holdId: HOLD }, request: req({}) })).status,
    ).toBe(401);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("GET reenvía a /legal-holds (control positivo)", async () => {
    await GET({ locals: LOCALS, params: { id: ID } });
    expect(global.fetch.mock.calls[0][0]).toMatch(new RegExp(`/expedientes/${ID}/legal-holds$`));
  });

  it("POST fijar: allowlist, la retención de sistema no se puede fijar", async () => {
    const ok = await POST({
      locals: LOCALS,
      params: { id: ID },
      request: req({
        motivo_tipo: "judicial",
        fundamento: "Orden del juzgado 12",
        acta_ref: "Oficio 55",
        fijado_por: "intruso",
      }),
    });
    expect(ok.status).toBe(200);
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).not.toHaveProperty("fijado_por");
    global.fetch.mockClear();
    const mal = await POST({
      locals: LOCALS,
      params: { id: ID },
      request: req({
        motivo_tipo: "valoracion_pendiente",
        fundamento: "x".repeat(12),
        acta_ref: "a",
      }),
    });
    expect(mal.status).toBe(422);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("levantar: exige el motivo confirmado y lo reenvía tal cual", async () => {
    const mal = await LEVANTAR({
      locals: LOCALS,
      params: { id: ID, holdId: HOLD },
      request: req({ fundamento: "Levantada por el despacho", acta_ref: "x" }),
    });
    expect(mal.status).toBe(422);
    await LEVANTAR({
      locals: LOCALS,
      params: { id: ID, holdId: HOLD },
      request: req({
        motivo_tipo: "fiscal",
        fundamento: "Levantada por el despacho",
        acta_ref: "x",
      }),
    });
    expect(JSON.parse(global.fetch.mock.calls[0][1].body).motivo_tipo).toBe("fiscal");
    expect(global.fetch.mock.calls[0][0]).toMatch(/levantar$/);
  });
});
