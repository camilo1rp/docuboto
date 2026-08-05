// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * claves-server.test.js — Proxy BFF de /admin/seguridad/claves/api (D-05,
 * E17/F4 Incremento 2). Cubre: guard 401, allowlist, confirmación escrita
 * OBLIGATORIA (mismo patrón que reasignacion-server.test.js), escapado del
 * user_id en la URL del gateway, y el mapeo 404 → no_signing_key.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST } from "./+server.js";
import { ADMIN_REVOKE_CONFIRM_PHRASE } from "$lib/utils/signingKeyConfirm.js";

const TARGET_USER = "44444444-4444-4444-4444-444444444444";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("POST /admin/seguridad/claves/api", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await POST({
      locals: {},
      request: {
        json: async () => ({ user_id: TARGET_USER, confirm: ADMIN_REVOKE_CONFIRM_PHRASE }),
      },
    });
    expect(res.status).toBe(401);
  });

  it("sin user_id: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin usuario objetivo");
    });
    const res = await POST({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ confirm: ADMIN_REVOKE_CONFIRM_PHRASE }) },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it.each([[undefined], [""], ["revocar"], ["Sí, confirmo"], [null]])(
    "confirm=%j no coincide con la frase exacta: 422 confirmacion_requerida, sin llamar al gateway",
    async (confirm) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse sin confirmación válida");
      });
      const res = await POST({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ user_id: TARGET_USER, confirm }) },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("confirmacion_requerida");
    },
  );

  it("confirm con espacios alrededor SÍ se acepta (trim)", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ estado: "revocado" }), { status: 200 }),
    );
    const res = await POST({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ user_id: TARGET_USER, confirm: `  ${ADMIN_REVOKE_CONFIRM_PHRASE}  ` }),
      },
    });
    expect(res.status).toBe(200);
  });

  it("allowlist + URL: solo motivo viaja además del user_id (escapado en la ruta), nunca el body íntegro", async () => {
    let capturedUrl;
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ estado: "revocado" }), { status: 200 });
    });

    const res = await POST({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          user_id: TARGET_USER,
          motivo: "salida del funcionario",
          confirm: ADMIN_REVOKE_CONFIRM_PHRASE,
          // Campos ajenos que un cliente malicioso podría intentar colar:
          estado: "activo",
          cert_pem: "no-debería-reenviarse",
        }),
      },
    });

    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/auth/admin/users/${TARGET_USER}/signing-key/revoke`);
    expect(capturedBody).toEqual({ motivo: "salida del funcionario" });
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });

  it("escapa el user_id al componer la URL del gateway (anti path-injection)", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ estado: "revocado" }), { status: 200 });
    });
    await POST({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ user_id: "../../otro-tenant", confirm: ADMIN_REVOKE_CONFIRM_PHRASE }),
      },
    });
    expect(capturedUrl).toContain(encodeURIComponent("../../otro-tenant"));
    expect(capturedUrl).not.toMatch(/users\/\.\.\//);
  });

  it("404 del backend se mapea a no_signing_key con mensaje claro", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: "no_signing_key",
            detail: "No signing-key credential",
            status: 404,
          }),
          { status: 404 },
        ),
    );
    const res = await POST({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ user_id: TARGET_USER, confirm: ADMIN_REVOKE_CONFIRM_PHRASE }),
      },
    });
    expect(res.status).toBe(404);
    expect((await res.json()).error).toBe("no_signing_key");
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await POST({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ user_id: TARGET_USER, confirm: ADMIN_REVOKE_CONFIRM_PHRASE }),
      },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
