// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * catalogos-server.test.js — Proxies /admin/catalogos/api[/[catalogo][, /[id]]] (D-05, E14).
 *
 * Cubre: guard 401, whitelist de nombre de catálogo (rechaza segmentos con
 * caracteres fuera de [a-z0-9-]), allowlist anti mass-assignment de POST/PATCH
 * (PATCH nunca reenvía `codigo`, es inmutable), encodeURIComponent de
 * [catalogo]/[id] en la URL del gateway.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listCatalogos } from "./+server.js";
import { GET as listItems, POST as createItem } from "./[catalogo]/+server.js";
import { PATCH as updateItem, DELETE as deleteItem } from "./[catalogo]/[id]/+server.js";

const ITEM_ID = "44444444-4444-4444-4444-444444444444";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/catalogos/api", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listCatalogos({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("propaga la lista del gateway", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(["tipos-anexo"]), { status: 200 }));
    const res = await listCatalogos({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(["tipos-anexo"]);
  });
});

describe("GET /admin/catalogos/api/[catalogo]", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listItems({ locals: {}, params: { catalogo: "tipos-anexo" }, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("nombre de catálogo con caracteres fuera de la whitelist: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listItems({
      locals: SESSION_LOCALS,
      params: { catalogo: "../../etc/passwd" },
      url: new URL("http://x/"),
    });
    expect(res.status).toBe(400);
  });

  it("propaga 404 del backend si el catálogo no existe en su whitelist", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "unknown_catalog", status: 404 }), { status: 404 }),
    );
    const res = await listItems({
      locals: SESSION_LOCALS,
      params: { catalogo: "no-existe" },
      url: new URL("http://x/"),
    });
    expect(res.status).toBe(404);
  });
});

describe("POST /admin/catalogos/api/[catalogo]", () => {
  it("allowlist: solo codigo/nombre/activo viajan (mass-assignment)", async () => {
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: ITEM_ID }), { status: 201 });
    });

    const res = await createItem({
      locals: SESSION_LOCALS,
      params: { catalogo: "tipos-anexo" },
      request: {
        json: async () => ({
          codigo: "PDF",
          nombre: "Documento PDF",
          id: "otro-id",
          activo: true,
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedUrl).toContain("/catalogos/tipos-anexo");
    expect(capturedBody).toEqual({ codigo: "PDF", nombre: "Documento PDF", activo: true });
  });
});

describe("PATCH /admin/catalogos/api/[catalogo]/[id]", () => {
  it("id no-UUID: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateItem({
      locals: SESSION_LOCALS,
      params: { catalogo: "tipos-anexo", id: "no-es-uuid" },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(400);
  });

  it("codigo NUNCA viaja en el PATCH (inmutable) aunque el cliente lo envíe", async () => {
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: ITEM_ID, nombre: "Nuevo" }), { status: 200 });
    });

    const res = await updateItem({
      locals: SESSION_LOCALS,
      params: { catalogo: "tipos-anexo", id: ITEM_ID },
      request: { json: async () => ({ nombre: "Nuevo", codigo: "OTRO", activo: false }) },
    });

    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/catalogos/tipos-anexo/${ITEM_ID}`);
    expect(capturedBody).toEqual({ nombre: "Nuevo", activo: false });
    expect(capturedBody.codigo).toBeUndefined();
  });
});

describe("DELETE /admin/catalogos/api/[catalogo]/[id]", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await deleteItem({ locals: {}, params: { catalogo: "tipos-anexo", id: ITEM_ID } });
    expect(res.status).toBe(401);
  });

  it("204 del gateway se propaga sin cuerpo", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await deleteItem({ locals: SESSION_LOCALS, params: { catalogo: "tipos-anexo", id: ITEM_ID } });
    expect(res.status).toBe(204);
  });
});
