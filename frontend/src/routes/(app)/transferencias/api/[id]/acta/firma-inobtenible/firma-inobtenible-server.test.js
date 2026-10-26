// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxies de la firma inobtenible (D-05): sesión, allowlist, forma del cuerpo y
 * `encodeURIComponent` del id.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as declarar } from "./+server.js";
import { POST as revocar } from "./revocar/+server.js";

const LOCALS = { accessToken: "tok", user: { tenant_slug: "icetex", sub: "kc-1" } };
const RAD = "11111111-1111-4111-8111-111111111111";
const ANEXO = "22222222-2222-4222-8222-222222222222";
const FUND = "El receptor falleció el 3 de octubre de 2026.";
const valido = (extra = {}) => ({
  rol: "recibe",
  motivo: "fallecimiento",
  fundamento: FUND,
  referencia_radicado_id: RAD,
  referencia_anexo_id: ANEXO,
  ...extra,
});
const req = (body) => ({ json: async () => body });

afterEach(() => vi.restoreAllMocks());

function espiar(status = 201) {
  const f = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status }));
  globalThis.fetch = f;
  return f;
}
function prohibido() {
  globalThis.fetch = vi.fn(() => {
    throw new Error("no debió llamar al gateway");
  });
}

describe("POST .../acta/firma-inobtenible", () => {
  it("401 sin sesión", async () => {
    prohibido();
    const res = await declarar({ locals: {}, params: { id: "t" }, request: req(valido()) });
    expect(res.status).toBe(401);
  });

  it("reenvía solo la allowlist y escapa el id", async () => {
    const f = espiar();
    const res = await declarar({
      locals: LOCALS,
      params: { id: "a/b" },
      request: req(
        valido({
          referencia_anexo_id: ANEXO,
          totp_code: " 123456 ",
          parte_id: "x",
          declarada_por: "y",
        }),
      ),
    });
    expect(res.status).toBe(201);
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toMatch(/\/transferencias\/a%2Fb\/acta\/firma-inobtenible$/);
    expect(JSON.parse(init.body)).toEqual({
      rol: "recibe",
      motivo: "fallecimiento",
      fundamento: FUND,
      referencia_radicado_id: RAD,
      referencia_anexo_id: ANEXO,
      totp_code: "123456",
    });
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.headers["X-Tenant-Slug"]).toBe("icetex");
  });

  it("omite el TOTP cuando viene vacío (el anexo es obligatorio)", async () => {
    const f = espiar();
    await declarar({
      locals: LOCALS,
      params: { id: "t" },
      request: req(valido({ totp_code: "  " })),
    });
    expect(Object.keys(JSON.parse(f.mock.calls[0][1].body)).sort()).toEqual([
      "fundamento",
      "motivo",
      "referencia_anexo_id",
      "referencia_radicado_id",
      "rol",
    ]);
  });

  it.each([
    ["rol inválido", { rol: "jefe" }],
    ["motivo inválido", { motivo: "vacaciones" }],
    ["fundamento corto", { fundamento: "corto" }],
    ["fundamento de solo blancos", { fundamento: " ".repeat(30) }],
    ["fundamento largo", { fundamento: "x".repeat(2001) }],
    ["radicado que no es UUID (texto libre)", { referencia_radicado_id: "radicado 123" }],
    ["radicado ausente", { referencia_radicado_id: undefined }],
    ["anexo que no es UUID", { referencia_anexo_id: "nope" }],
    ["anexo ausente (obligatorio desde FIRMA-2)", { referencia_anexo_id: undefined }],
    ["anexo vacío", { referencia_anexo_id: "" }],
    ["TOTP demasiado largo", { totp_code: "1".repeat(17) }],
  ])("422 sin llamar al gateway: %s", async (_, cambio) => {
    prohibido();
    const res = await declarar({
      locals: LOCALS,
      params: { id: "t" },
      request: req(valido(cambio)),
    });
    expect(res.status).toBe(422);
  });

  it("relaya el código del backend tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { error: "parte_ya_firmo" } }), { status: 409 }),
    );
    const res = await declarar({ locals: LOCALS, params: { id: "t" }, request: req(valido()) });
    expect(res.status).toBe(409);
    expect((await res.json()).detail.error).toBe("parte_ya_firmo");
  });

  it("503 si el gateway no responde", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("down");
    });
    const res = await declarar({ locals: LOCALS, params: { id: "t" }, request: req(valido()) });
    expect(res.status).toBe(503);
  });
});

describe("POST .../acta/firma-inobtenible/revocar", () => {
  it("401 sin sesión", async () => {
    prohibido();
    const res = await revocar({
      locals: {},
      params: { id: "t" },
      request: req({ rol: "recibe", causa: "otro", fundamento: FUND }),
    });
    expect(res.status).toBe(401);
  });

  it("reenvía solo rol, fundamento y totp_code, escapando el id", async () => {
    const f = espiar();
    await revocar({
      locals: LOCALS,
      params: { id: "a/b" },
      request: req({
        rol: "recibe",
        causa: "otro",
        fundamento: ` ${FUND} `,
        motivo: "x",
        totp_code: " 123456 ",
      }),
    });
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toMatch(/\/transferencias\/a%2Fb\/acta\/firma-inobtenible\/revocar$/);
    expect(JSON.parse(init.body)).toEqual({
      rol: "recibe",
      causa: "otro",
      fundamento: FUND,
      totp_code: "123456",
    });
  });

  it("sin TOTP no lo envía y uno demasiado largo es 422", async () => {
    const f = espiar();
    await revocar({
      locals: LOCALS,
      params: { id: "t" },
      request: req({ rol: "recibe", causa: "otro", fundamento: FUND, totp_code: "  " }),
    });
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({
      rol: "recibe",
      causa: "otro",
      fundamento: FUND,
    });
    prohibido();
    const res = await revocar({
      locals: LOCALS,
      params: { id: "t" },
      request: req({ rol: "recibe", causa: "otro", fundamento: FUND, totp_code: "1".repeat(17) }),
    });
    expect(res.status).toBe(422);
  });

  it.each([
    ["rol inválido", { rol: "x", causa: "otro", fundamento: FUND }],
    ["causa ausente", { rol: "recibe", fundamento: FUND }],
    ["causa fuera del vocabulario", { rol: "recibe", causa: "capricho", fundamento: FUND }],
    ["fundamento corto", { rol: "recibe", causa: "otro", fundamento: "corto" }],
    ["fundamento largo", { rol: "recibe", causa: "otro", fundamento: "x".repeat(2001) }],
  ])("422 sin llamar al gateway: %s", async (_, body) => {
    prohibido();
    const res = await revocar({ locals: LOCALS, params: { id: "t" }, request: req(body) });
    expect(res.status).toBe(422);
  });
});
