// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * grupos-server.test.js — Proxies BFF de /admin/grupos (D-05).
 *
 * Cubre: guard 401, allowlist campo a campo (anti mass-assignment), UUID +
 * encodeURIComponent en ids de ruta, reenvío de Authorization/X-Tenant-Slug/
 * X-User-Id, y el rango de `crud`/`max_level`.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listGet, POST as createPost } from "./+server.js";
import { GET as permissionsGet } from "./permissions/+server.js";
import { GET as securityLevelsGet } from "./security-levels/+server.js";
import { GET as usersPickableGet } from "./users-pickable/+server.js";
import { POST as memberPost, DELETE as memberDelete } from "./[id]/members/[userId]/+server.js";
import { PUT as permissionPut } from "./[id]/permissions/[permissionId]/+server.js";
import { PUT as clearancePut } from "./[id]/clearance/+server.js";

const GROUP_ID = "11111111-1111-1111-1111-111111111111";
const USER_ID = "22222222-2222-2222-2222-222222222222";
const PERMISSION_ID = "33333333-3333-3333-3333-333333333333";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "44444444-4444-4444-4444-444444444444" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

function noFetchExpected(msg) {
  return vi.fn(() => {
    throw new Error(msg ?? "fetch no debió invocarse");
  });
}

// ---------------------------------------------------------------------------
// GET /admin/grupos/api (lista) + POST (crear)
// ---------------------------------------------------------------------------
describe("grupos/api GET (lista)", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await listGet({ locals: {} });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("reenvía Authorization/X-Tenant-Slug/X-User-Id", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init?.headers;
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await listGet({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/auth/groups");
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });
});

describe("grupos/api POST (crear)", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await createPost({ locals: {}, request: { json: async () => ({ name: "x" }) } });
    expect(res.status).toBe(401);
  });

  it("sin name: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ name: "   " }) },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("allowlist: solo reenvía name/description, descarta campos ajenos", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "g-1", ...capturedBody }), { status: 201 });
    });
    await createPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          name: "  Radicadores  ",
          description: "  Grupo operativo  ",
          id: "id-forzado",
          created_at: "hoy",
        }),
      },
    });
    expect(capturedBody).toEqual({ name: "Radicadores", description: "Grupo operativo" });
  });

  it("fallo de red del gateway: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ name: "X" }) },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

// ---------------------------------------------------------------------------
// Catálogos auxiliares (permisos / security-levels / users-pickable)
// ---------------------------------------------------------------------------
describe("grupos/api catálogos auxiliares — guard 401 y reenvío", () => {
  it.each([
    ["permissions", permissionsGet, "/auth/permissions"],
    ["security-levels", securityLevelsGet, "/auth/security-levels"],
    ["users-pickable", usersPickableGet, "/auth/users/pickable"],
  ])("%s: sin locals.accessToken: 401, sin llamar al gateway", async (_name, handler) => {
    globalThis.fetch = noFetchExpected();
    const res = await handler({ locals: {} });
    expect(res.status).toBe(401);
  });

  it.each([
    ["permissions", permissionsGet, "/auth/permissions"],
    ["security-levels", securityLevelsGet, "/auth/security-levels"],
    ["users-pickable", usersPickableGet, "/auth/users/pickable"],
  ])("%s: reenvía al gateway correcto", async (_name, handler, expectedPath) => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await handler({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(expectedPath);
  });
});

// ---------------------------------------------------------------------------
// [id]/members/[userId] — alta/baja de membresía
// ---------------------------------------------------------------------------
describe("grupos/api/[id]/members/[userId]", () => {
  it("POST: sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await memberPost({ locals: {}, params: { id: GROUP_ID, userId: USER_ID } });
    expect(res.status).toBe(401);
  });

  it.each([
    ["id inválido", { id: "no-es-uuid", userId: USER_ID }],
    ["userId inválido", { id: GROUP_ID, userId: "no-es-uuid" }],
  ])("POST: %s → 400 validation_error, sin llamar al gateway", async (_desc, params) => {
    globalThis.fetch = noFetchExpected();
    const res = await memberPost({ locals: SESSION_LOCALS, params });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("POST: encodeURIComponent en ambos ids de la URL hacia el gateway", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init?.headers;
      return new Response(JSON.stringify({ status: "ok" }), { status: 201 });
    });
    const res = await memberPost({
      locals: SESSION_LOCALS,
      params: { id: GROUP_ID, userId: USER_ID },
    });
    expect(res.status).toBe(201);
    expect(capturedUrl).toContain(
      `/auth/groups/${encodeURIComponent(GROUP_ID)}/members/${encodeURIComponent(USER_ID)}`,
    );
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
  });

  it("DELETE: id inválido → 400, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await memberDelete({
      locals: SESSION_LOCALS,
      params: { id: "bad", userId: USER_ID },
    });
    expect(res.status).toBe(400);
  });

  it("DELETE: 204 del gateway se reenvía tal cual", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await memberDelete({
      locals: SESSION_LOCALS,
      params: { id: GROUP_ID, userId: USER_ID },
    });
    expect(res.status).toBe(204);
  });
});

// ---------------------------------------------------------------------------
// [id]/permissions/[permissionId] — fija crud
// ---------------------------------------------------------------------------
describe("grupos/api/[id]/permissions/[permissionId]", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await permissionPut({
      locals: {},
      params: { id: GROUP_ID, permissionId: PERMISSION_ID },
      request: { json: async () => ({ crud: 3 }) },
    });
    expect(res.status).toBe(401);
  });

  it.each([[-1], [6], ["no-numero"], [null]])(
    "crud=%s fuera de rango: 400 validation_error, sin llamar al gateway",
    async (bad) => {
      globalThis.fetch = noFetchExpected();
      const res = await permissionPut({
        locals: SESSION_LOCALS,
        params: { id: GROUP_ID, permissionId: PERMISSION_ID },
        request: { json: async () => ({ crud: bad }) },
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("allowlist: solo reenvía { crud } coercionado a número, descarta campos ajenos", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    });
    const res = await permissionPut({
      locals: SESSION_LOCALS,
      params: { id: GROUP_ID, permissionId: PERMISSION_ID },
      request: { json: async () => ({ crud: "3", group_id: "otro-id", extra: true }) },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ crud: 3 });
    expect(capturedUrl).toContain(
      `/auth/groups/${encodeURIComponent(GROUP_ID)}/permissions/${encodeURIComponent(PERMISSION_ID)}`,
    );
  });
});

// ---------------------------------------------------------------------------
// [id]/clearance — fija max_level
// ---------------------------------------------------------------------------
describe("grupos/api/[id]/clearance", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await clearancePut({
      locals: {},
      params: { id: GROUP_ID },
      request: { json: async () => ({ max_level: 2 }) },
    });
    expect(res.status).toBe(401);
  });

  it("id inválido: 400, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await clearancePut({
      locals: SESSION_LOCALS,
      params: { id: "bad" },
      request: { json: async () => ({ max_level: 2 }) },
    });
    expect(res.status).toBe(400);
  });

  it.each([[0], [4], ["no-numero"]])(
    "max_level=%s fuera de rango: 400 validation_error, sin llamar al gateway",
    async (bad) => {
      globalThis.fetch = noFetchExpected();
      const res = await clearancePut({
        locals: SESSION_LOCALS,
        params: { id: GROUP_ID },
        request: { json: async () => ({ max_level: bad }) },
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("204 del gateway se reenvía tal cual, con allowlist de max_level", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(null, { status: 204 });
    });
    const res = await clearancePut({
      locals: SESSION_LOCALS,
      params: { id: GROUP_ID },
      request: { json: async () => ({ max_level: 2, group_id: "otro" }) },
    });
    expect(res.status).toBe(204);
    expect(capturedBody).toEqual({ max_level: 2 });
  });
});

// ---------------------------------------------------------------------------
// Lecturas del drawer (miembros / permisos / clearance)
//
// La pantalla administraba a ciegas: daba de alta y de baja sin poder mostrar
// lo guardado, y enseñaba en su lugar una lista "de esta sesión" con lo que
// ella misma había hecho. Los tres GET existían en auth-service desde el
// espejo de lectura de RBAC; faltaba consumirlos, y un comentario que afirmaba
// que no existían mantuvo el hueco abierto.
// ---------------------------------------------------------------------------

describe("GET /admin/grupos/api/[id]/{members,permissions,clearance}", () => {
  const GID = "11111111-2222-3333-4444-555555555555";

  it("miembros: propaga la lista y el X-Total-Count del backend", async () => {
    const { GET } = await import("./[id]/members/+server.js");
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([{ id: "u-1", username: "ana" }]), {
          status: 200,
          headers: { "X-Total-Count": "7" },
        }),
    );
    const res = await GET({
      locals: SESSION_LOCALS,
      params: { id: GID },
      url: new URL("http://x/"),
    });
    expect(res.status).toBe(200);
    // El total viaja: un grupo grande no puede verse truncado en silencio.
    expect(res.headers.get("X-Total-Count")).toBe("7");
    expect(await res.json()).toEqual([{ id: "u-1", username: "ana" }]);
  });

  it("miembros: 401 sin sesión, sin llamar al gateway", async () => {
    const { GET } = await import("./[id]/members/+server.js");
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await GET({ locals: {}, params: { id: GID }, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("miembros: id que no es UUID se rechaza antes del viaje", async () => {
    const { GET } = await import("./[id]/members/+server.js");
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con un id inválido");
    });
    const res = await GET({
      locals: SESSION_LOCALS,
      params: { id: "../../otro" },
      url: new URL("http://x/"),
    });
    expect(res.status).toBe(400);
  });

  it("permisos: devuelve lo YA concedido con su nivel", async () => {
    // Sin esta lectura todos los selectores mostraban 0, y guardar un permiso
    // cualquiera podía REBAJAR otro que ya estaba concedido con nivel 3.
    const { GET } = await import("./[id]/permissions/+server.js");
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([{ permission_id: "p-1", crud: 3 }]), { status: 200 }),
    );
    const res = await GET({ locals: SESSION_LOCALS, params: { id: GID } });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([{ permission_id: "p-1", crud: 3 }]);
  });

  it("clearance: se puede LEER el nivel vigente, no solo fijar uno nuevo", async () => {
    const { GET } = await import("./[id]/clearance/+server.js");
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ group_id: GID, max_level: 2 }), { status: 200 }),
    );
    const res = await GET({ locals: SESSION_LOCALS, params: { id: GID } });
    expect(res.status).toBe(200);
    expect((await res.json()).max_level).toBe(2);
  });

  it("clearance: un 404 del backend se propaga tal cual, no se disfraza de vacío", async () => {
    const { GET } = await import("./[id]/clearance/+server.js");
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "not_found", status: 404 }), { status: 404 }),
    );
    const res = await GET({ locals: SESSION_LOCALS, params: { id: GID } });
    expect(res.status).toBe(404);
  });
});
