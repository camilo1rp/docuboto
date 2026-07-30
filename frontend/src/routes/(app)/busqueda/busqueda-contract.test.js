// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * busqueda-contract.test.js — test de contrato de `GET /api/v1/search`.
 *
 * Regresión de un fallo real: tanto el `load` de la pantalla como el proxy
 * `/busqueda/api` normalizaban la respuesta con `data.items ?? data.results`,
 * pero `SearchResponse` (services/document-service/app/schemas/search.py) devuelve
 * `{ query, total, hits, page, size }`. Ninguno de los dos nombres existía, así que
 * la búsqueda caía siempre en `[]` y la pantalla no mostraba NUNCA resultados —
 * incluyendo la búsqueda global del topbar, que navega aquí.
 *
 * Este test fija la forma real del contrato para que el desajuste no vuelva a
 * pasar desapercibido.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as busquedaProxyGet } from "./api/+server.js";
import { load as busquedaLoad } from "./+page.server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

/** Forma REAL de SearchResponse del document-service. */
const SEARCH_RESPONSE = {
  query: "contrato",
  total: 2,
  page: 1,
  size: 20,
  hits: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      tracking_number: "2026-ICETEX-E-000001",
      doc_type: "entrada",
      subject: "Contrato de prestación",
      sender_name: "Juan Pérez",
      sender_entity: null,
      dest_dept: "Jurídica",
      status: "registered",
      registered_at: "2026-01-15T10:30:00Z",
      rank: 0.87,
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      tracking_number: "2026-ICETEX-S-000002",
      doc_type: "salida",
      subject: "Respuesta a contrato",
      sender_name: null,
      sender_entity: "ICETEX",
      dest_dept: "Financiera",
      status: "closed",
      registered_at: "2026-02-01T08:00:00Z",
      rank: 0.42,
    },
  ],
};

const okResponse = (body) => ({
  ok: true,
  status: 200,
  headers: new Headers(),
  json: async () => body,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("proxy /busqueda/api — normalización de SearchResponse", () => {
  it("mapea `hits` a `results` y respeta `total`", async () => {
    globalThis.fetch = vi.fn(async () => okResponse(SEARCH_RESPONSE));

    const res = await busquedaProxyGet({
      locals: SESSION_LOCALS,
      url: new URL("http://localhost/busqueda/api?q=contrato"),
    });
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.total).toBe(2);
    expect(body.results).toHaveLength(2);
    expect(body.results[0].tracking_number).toBe("2026-ICETEX-E-000001");
  });

  it("no devuelve vacío cuando el backend sí trajo aciertos (regresión del fallo)", async () => {
    globalThis.fetch = vi.fn(async () => okResponse(SEARCH_RESPONSE));

    const res = await busquedaProxyGet({
      locals: SESSION_LOCALS,
      url: new URL("http://localhost/busqueda/api?q=contrato"),
    });

    expect((await res.json()).results).not.toHaveLength(0);
  });

  it("sigue aceptando `items` y `results` por respaldo", async () => {
    for (const campo of ["items", "results"]) {
      globalThis.fetch = vi.fn(async () => okResponse({ [campo]: SEARCH_RESPONSE.hits, total: 2 }));

      const res = await busquedaProxyGet({
        locals: SESSION_LOCALS,
        url: new URL("http://localhost/busqueda/api?q=contrato"),
      });

      expect((await res.json()).results, `respaldo ${campo}`).toHaveLength(2);
    }
  });
});

describe("load de /busqueda — normalización de SearchResponse", () => {
  it("mapea `hits` en la carga SSR inicial", async () => {
    globalThis.fetch = vi.fn(async () => okResponse(SEARCH_RESPONSE));

    // El `load` real lee el token de la cookie httpOnly (D-05) y el usuario del
    // layout padre, no de `locals`.
    const data = await busquedaLoad({
      cookies: { get: () => "tok-abc" },
      parent: async () => ({ user: SESSION_LOCALS.user }),
      url: new URL("http://localhost/busqueda?q=contrato"),
    });

    expect(data.total).toBe(2);
    expect(data.results).toHaveLength(2);
    expect(data.results[0].subject).toBe("Contrato de prestación");
  });
});
