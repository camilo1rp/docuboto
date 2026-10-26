// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reasignacion-server.test.js — Proxies BFF de /admin/flujos/reasignacion
 * (D-05, RF-FLU-08).
 *
 * Cubre: guard 401, validación de origen/destino, allowlist anti
 * mass-assignment, y — el punto central de esta pantalla — que
 * `POST .../api` (ejecución real) es IMPOSIBLE sin la frase de confirmación
 * exacta, sin importar qué más traiga el body.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as executePost } from "./+server.js";
import { GET as previewGet } from "./preview/+server.js";

const USER_A = "11111111-1111-1111-1111-111111111111";
const USER_B = "22222222-2222-2222-2222-222222222222";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

const url = (path) => new URL(`http://localhost${path}`);

afterEach(() => {
  vi.restoreAllMocks();
});

describe("reasignacion/api POST — sin locals.accessToken", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await executePost({
      locals: {},
      request: { json: async () => ({ to_assigned_to: USER_B, confirm: "REASIGNAR" }) },
    });
    expect(res.status).toBe(401);
  });
});

describe("reasignacion/api POST — confirmación escrita OBLIGATORIA (núcleo de la pantalla)", () => {
  it.each([[undefined], [""], ["reasignar"], ["Sí, confirmo"], [null]])(
    "confirm=%j no coincide con la frase exacta: 422 confirmation_required, sin llamar al gateway",
    async (confirm) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse sin confirmación válida");
      });
      const res = await executePost({
        locals: SESSION_LOCALS,
        request: {
          json: async () => ({
            from_assigned_to: USER_A,
            to_assigned_to: USER_B,
            confirm,
          }),
        },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("confirmation_required");
    },
  );

  it("confirm='REASIGNAR' con espacios alrededor SÍ se acepta (trim)", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ reassigned: 3 }), { status: 200 }),
    );
    const res = await executePost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          from_assigned_to: USER_A,
          to_assigned_to: USER_B,
          confirm: "  REASIGNAR  ",
        }),
      },
    });
    expect(res.status).toBe(200);
  });
});

describe("reasignacion/api POST — validación de origen/destino", () => {
  it("sin from_assigned_to ni from_dept_code: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin origen");
    });
    const res = await executePost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ to_assigned_to: USER_B, confirm: "REASIGNAR" }),
      },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("to_assigned_to ausente/no-UUID: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin destino válido");
    });
    const res = await executePost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          from_dept_code: 12,
          to_assigned_to: "no-es-un-uuid",
          confirm: "REASIGNAR",
        }),
      },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("allowlist: solo reenvía from_assigned_to/from_dept_code/to_assigned_to — nunca el body íntegro", async () => {
    let capturedUrl;
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ reassigned: 7 }), { status: 200 });
    });

    const res = await executePost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          from_assigned_to: USER_A,
          to_assigned_to: USER_B,
          confirm: "REASIGNAR",
          // Campos ajenos que un cliente malicioso podría intentar colar:
          actor: "otro-usuario-id",
          tenant_slug: "otro-tenant",
        }),
      },
    });

    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/workflows/reassign/cascade");
    expect(capturedBody).toEqual({ from_assigned_to: USER_A, to_assigned_to: USER_B });
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });

  it("fallo de red del gateway: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await executePost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          from_dept_code: 12,
          to_assigned_to: USER_B,
          confirm: "REASIGNAR",
        }),
      },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("reasignacion/api/preview GET", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await previewGet({ locals: {}, url: url("/x/api/preview") });
    expect(res.status).toBe(401);
  });

  it("sin origen (ni assigned_to ni dept): 400 validation_error", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin origen");
    });
    const res = await previewGet({
      locals: SESSION_LOCALS,
      url: url("/x/api/preview"),
    });
    expect(res.status).toBe(400);
  });

  it("con ambos assigned_to y dept a la vez: 400 validation_error (exactamente uno)", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con origen ambiguo");
    });
    const res = await previewGet({
      locals: SESSION_LOCALS,
      url: url(`/x/api/preview?assigned_to=${USER_A}&dept_code=12`),
    });
    expect(res.status).toBe(400);
  });

  it("dept_code que no es un entero (p. ej. un nombre): 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con un nombre en lugar del código");
    });
    const res = await previewGet({
      locals: SESSION_LOCALS,
      url: url("/x/api/preview?dept_code=TIC"),
    });
    expect(res.status).toBe(400);
  });

  it("assigned_to no-UUID: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con id inválido");
    });
    const res = await previewGet({
      locals: SESSION_LOCALS,
      url: url("/x/api/preview?assigned_to=no-es-un-uuid"),
    });
    expect(res.status).toBe(400);
  });

  it("lee X-Total-Count de GET /workflows/pending y lo etiqueta como estimado", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "X-Total-Count": "12" },
      });
    });
    const res = await previewGet({
      locals: SESSION_LOCALS,
      url: url(`/x/api/preview?assigned_to=${USER_A}`),
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/workflows/pending?");
    expect(capturedUrl).toContain(`assigned_to=${USER_A}`);
    const body = await res.json();
    expect(body).toEqual({ count: 12, estimated: true });
  });
});
