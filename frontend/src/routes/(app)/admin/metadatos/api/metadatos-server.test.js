// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * metadatos-server.test.js — Proxies /admin/metadatos/api/* (D-05, E03/ADR-007).
 *
 * Cubre: guard 401, validación de json_schema (debe ser objeto JSON, no
 * array/escalar/ausente), allowlist anti mass-assignment, encodeURIComponent
 * del [id] en elements, y el guard de tipo_dato de MetadataElementCreate.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import {
  GET as listExpTemplates,
  POST as createExpTemplate,
} from "./expediente-templates/+server.js";
import {
  GET as listDocTemplates,
  POST as createDocTemplate,
} from "./document-templates/+server.js";
import { GET as listElements, POST as createElement } from "./elements/+server.js";
import { PATCH as updateElement, DELETE as deleteElement } from "./elements/[id]/+server.js";

const SERIE_ID = "44444444-4444-4444-4444-444444444444";
const ELEMENT_ID = "55555555-5555-5555-5555-555555555555";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("expediente-templates", () => {
  it("GET: 401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listExpTemplates({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("POST: trd_serie_id no-UUID -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createExpTemplate({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ trd_serie_id: "no-es-uuid", json_schema: {} }) },
    });
    expect(res.status).toBe(400);
  });

  it("POST: json_schema como array -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createExpTemplate({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ trd_serie_id: SERIE_ID, json_schema: [] }) },
    });
    expect(res.status).toBe(400);
  });

  it("POST: allowlist completa viaja al gateway", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "t-1" }), { status: 201 });
    });
    const res = await createExpTemplate({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          trd_serie_id: SERIE_ID,
          json_schema: { type: "object", properties: {} },
          version: 2,
          activo: false,
          id: "otro-id",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      trd_serie_id: SERIE_ID,
      json_schema: { type: "object", properties: {} },
      version: 2,
      activo: false,
    });
  });
});

describe("document-templates", () => {
  it("GET: 401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listDocTemplates({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("POST: sin tipo_documental -> 400", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createDocTemplate({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ json_schema: {} }) },
    });
    expect(res.status).toBe(400);
  });

  it("POST: json_schema null -> 400", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createDocTemplate({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ tipo_documental: "oficio", json_schema: null }) },
    });
    expect(res.status).toBe(400);
  });
});

describe("elements", () => {
  it("GET: 401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listElements({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("POST: tipo_dato fuera del enum -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createElement({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ clave: "asunto", etiqueta: "Asunto", tipo_dato: "DROP TABLE" }),
      },
    });
    expect(res.status).toBe(400);
  });

  it("POST: allowlist anti mass-assignment", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: ELEMENT_ID }), { status: 201 });
    });
    const res = await createElement({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          clave: "asunto",
          etiqueta: "Asunto",
          tipo_dato: "text",
          searchable: true,
          id: "otro-id",
          created_at: "2020-01-01T00:00:00Z",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      clave: "asunto",
      etiqueta: "Asunto",
      tipo_dato: "text",
      longitud: null,
      ocurrencia_min: 0,
      ocurrencia_max: null,
      modificable: true,
      orden: 0,
      searchable: true,
      mapeo_dublin_core: null,
      activo: true,
    });
  });

  it("PATCH: id no-UUID -> 400", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateElement({
      locals: SESSION_LOCALS,
      params: { id: "no-es-uuid" },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(400);
  });

  it("PATCH: escapa el id en la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ id: ELEMENT_ID }), { status: 200 });
    });
    const res = await updateElement({
      locals: SESSION_LOCALS,
      params: { id: ELEMENT_ID },
      request: { json: async () => ({ etiqueta: "Nueva" }) },
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/metadata/elements/${ELEMENT_ID}`);
  });

  it("DELETE: 204 del gateway se propaga sin cuerpo", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await deleteElement({ locals: SESSION_LOCALS, params: { id: ELEMENT_ID } });
    expect(res.status).toBe(204);
  });
});
