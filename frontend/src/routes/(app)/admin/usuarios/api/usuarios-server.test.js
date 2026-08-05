// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * usuarios-server.test.js — Proxies BFF de /admin/usuarios/api (D-05, E08).
 *
 * Cubre: guard 401 en los 5 handlers, allowlist anti mass-assignment de
 * POST /admin/usuarios/api (username/email/keycloak_sub/is_root — nunca
 * id/is_active ni campos ajenos como tenant_slug), cabeceras X-User-Id/
 * X-Tenant-Slug tomadas de locals (nunca del body), escapado de [id]/[urdId]
 * en la URL del gateway, y propagación de códigos de error.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listUsers, POST as createUser } from "./+server.js";
import { GET as getPermissions } from "./[id]/permissions/+server.js";
import { GET as listUrd, POST as createUrd } from "./[id]/urd/+server.js";
import { DELETE as deleteUrd } from "./[id]/urd/[urdId]/+server.js";

const USER_ID = "44444444-4444-4444-4444-444444444444";
const URD_ID = "55555555-5555-5555-5555-555555555555";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/usuarios/api", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listUsers({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("propaga el status de error del gateway (no lo colapsa en 500)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "forbidden", status: 403 }), { status: 403 }),
    );
    const res = await listUsers({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(403);
  });
});

describe("POST /admin/usuarios/api", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await createUser({
      locals: {},
      request: { json: async () => ({ username: "ana" }) },
    });
    expect(res.status).toBe(401);
  });

  it("sin username: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin username");
    });
    const res = await createUser({ locals: SESSION_LOCALS, request: { json: async () => ({}) } });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("allowlist: solo username/email/keycloak_sub/is_root viajan; is_active/id/tenant_slug NUNCA (mass-assignment)", async () => {
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "u-1" }), { status: 201 });
    });

    const res = await createUser({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          username: "ana",
          email: "ana@example.com",
          keycloak_sub: "kc-1",
          is_root: true,
          // Campos ajenos que un cliente malicioso podría intentar colar:
          id: "otro-id",
          is_active: false,
          tenant_slug: "otro-tenant",
          role: "SUPERADMIN",
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      username: "ana",
      email: "ana@example.com",
      keycloak_sub: "kc-1",
      is_root: true,
    });
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
  });

  it("is_root no-booleano se normaliza a false (nunca se infiere truthy de un string)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "u-1" }), { status: 201 });
    });
    await createUser({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ username: "ana", is_root: "true" }) },
    });
    expect(capturedBody.is_root).toBe(false);
  });

  it("X-User-Id viaja de locals.user.sub, no del body aunque el cliente mande otro", async () => {
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "u-1" }), { status: 201 });
    });
    await createUser({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ username: "ana", sub: "usuario-suplantado" }) },
    });
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await createUser({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ username: "ana" }) },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("GET /admin/usuarios/api/[id]/permissions", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getPermissions({ locals: {}, params: { id: USER_ID } });
    expect(res.status).toBe(401);
  });

  it("id no-UUID: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con id inválido");
    });
    const res = await getPermissions({ locals: SESSION_LOCALS, params: { id: "no-es-un-uuid" } });
    expect(res.status).toBe(400);
  });

  it("escapa el id al componer la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({}), { status: 200 });
    });
    await getPermissions({ locals: SESSION_LOCALS, params: { id: USER_ID } });
    expect(capturedUrl).toContain(`/auth/users/${USER_ID}/permissions`);
  });

  it("propaga un 403 del gateway tal cual (no lo colapsa en 500)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "forbidden", status: 403 }), { status: 403 }),
    );
    const res = await getPermissions({ locals: SESSION_LOCALS, params: { id: USER_ID } });
    expect(res.status).toBe(403);
  });
});

describe("GET /admin/usuarios/api/[id]/urd", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listUrd({ locals: {}, params: { id: USER_ID } });
    expect(res.status).toBe(401);
  });

  it("id no-UUID: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con id inválido");
    });
    const res = await listUrd({ locals: SESSION_LOCALS, params: { id: "../etc" } });
    expect(res.status).toBe(400);
  });
});

describe("POST /admin/usuarios/api/[id]/urd", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await createUrd({
      locals: {},
      params: { id: USER_ID },
      request: { json: async () => ({ depe_id: 1, rol_id: URD_ID }) },
    });
    expect(res.status).toBe(401);
  });

  it("depe_id fuera de rango: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con depe_id inválido");
    });
    const res = await createUrd({
      locals: SESSION_LOCALS,
      params: { id: USER_ID },
      request: { json: async () => ({ depe_id: 0, rol_id: URD_ID }) },
    });
    expect(res.status).toBe(400);
  });

  it("rol_id no-UUID: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con rol_id inválido");
    });
    const res = await createUrd({
      locals: SESSION_LOCALS,
      params: { id: USER_ID },
      request: { json: async () => ({ depe_id: 1, rol_id: "no-es-un-uuid" }) },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist + escapado del id de ruta: solo depe_id/rol_id/es_principal viajan", async () => {
    let capturedUrl;
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "urd-1" }), { status: 201 });
    });

    const res = await createUrd({
      locals: SESSION_LOCALS,
      params: { id: USER_ID },
      request: {
        json: async () => ({
          depe_id: 42,
          rol_id: URD_ID,
          es_principal: true,
          // Campos ajenos que un cliente malicioso podría intentar colar:
          usua_id: "otro-usuario",
          tenant_slug: "otro-tenant",
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedUrl).toContain(`/auth/users/${USER_ID}/urd`);
    expect(capturedBody).toEqual({ depe_id: 42, rol_id: URD_ID, es_principal: true });
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });
});

describe("DELETE /admin/usuarios/api/[id]/urd/[urdId]", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await deleteUrd({ locals: {}, params: { id: USER_ID, urdId: URD_ID } });
    expect(res.status).toBe(401);
  });

  it("urdId no-UUID: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con urdId inválido");
    });
    const res = await deleteUrd({
      locals: SESSION_LOCALS,
      params: { id: USER_ID, urdId: "../../otro-tenant" },
    });
    expect(res.status).toBe(400);
  });

  it("escapa id y urdId al componer la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(null, { status: 204 });
    });
    const res = await deleteUrd({ locals: SESSION_LOCALS, params: { id: USER_ID, urdId: URD_ID } });
    expect(res.status).toBe(204);
    expect(capturedUrl).toContain(`/auth/users/${USER_ID}/urd/${URD_ID}`);
  });

  it("404 del gateway se propaga tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "not_found", status: 404 }), { status: 404 }),
    );
    const res = await deleteUrd({ locals: SESSION_LOCALS, params: { id: USER_ID, urdId: URD_ID } });
    expect(res.status).toBe(404);
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await deleteUrd({ locals: SESSION_LOCALS, params: { id: USER_ID, urdId: URD_ID } });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
