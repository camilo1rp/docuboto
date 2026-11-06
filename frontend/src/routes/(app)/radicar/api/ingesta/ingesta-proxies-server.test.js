// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ingesta-proxies-server.test.js — proxies SSR de ingesta de preparación en radicar.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as crearPost } from "./+server.js";
import { POST as extractPost } from "./extract/+server.js";
import { GET as estadoGet, DELETE as borrarDelete } from "./[ref]/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};
const FILE_ID = "6f1d2c3b-8a9e-4f70-b1c2-d3e4f5a6b7c8";

function jsonRequest(body) {
  return { json: async () => body };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function forbidFetch() {
  return vi.fn(() => {
    throw new Error("fetch no debió invocarse");
  });
}

describe("radicar/api/ingesta POST", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await crearPost({ locals: {}, request: jsonRequest({ file_id: FILE_ID }) });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("file_id inválido: 422, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await crearPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({ file_id: "no-es-uuid", extra: true }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: solo file_id viaja al gateway", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ staging_ref: "staging-1" }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      await crearPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({ file_id: FILE_ID, extra: "no" }),
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe("http://app:8000/api/v1/knowledge/ingesta");
      expect(JSON.parse(opts.body)).toEqual({ file_id: FILE_ID });
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("caída del servicio: 503 connection_error", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    try {
      const res = await crearPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({ file_id: FILE_ID }),
      });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("radicar/api/ingesta/extract POST", () => {
  it("choice sin opciones: 422, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await extractPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({
          external_ref: "staging-1.file",
          fields: [{ key: "direction", kind: "choice", choices: [] }],
        }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("reenvía external_ref y fields", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ values: { direction: "E" } }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      await extractPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({
          external_ref: "staging-1.file",
          forged: true,
          fields: [
            {
              key: "direction",
              kind: "choice",
              label: "Dirección",
              choices: [{ value: "E", label: "Entrada" }],
            },
          ],
        }),
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe("http://app:8000/api/v1/knowledge/ingesta/extract");
      expect(JSON.parse(opts.body)).toEqual({
        external_ref: "staging-1.file",
        fields: [
          {
            key: "direction",
            kind: "choice",
            label: "Dirección",
            choices: [{ value: "E", label: "Entrada" }],
          },
        ],
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("radicar/api/ingesta/[ref]", () => {
  it("GET consulta el estado con la referencia escapada", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ ingest_state: "indexed" }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      const res = await estadoGet({ locals: SESSION_LOCALS, params: { ref: "staging-1.file" } });
      expect(res.status).toBe(200);
      expect(fetchMock.mock.calls[0][0]).toBe(
        "http://app:8000/api/v1/knowledge/ingesta/staging-1.file",
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("DELETE sin token: 401", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await borrarDelete({ locals: {}, params: { ref: "staging-1" } });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
