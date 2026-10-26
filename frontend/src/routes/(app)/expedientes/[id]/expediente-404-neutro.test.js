// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expediente-404-neutro.test.js — Fija el contrato del 404 NEUTRO (no-read-up,
 * RF-SEG-08) para las facetas del workspace de expediente.
 *
 * Regresión de un hallazgo real (revisión UX C2): `+page.server.js` convertía
 * CUALQUIER rechazo de una faceta (incl. el 404 "todavía no existe / sin
 * visibilidad" que `archive_service.py` devuelve a propósito para
 * `GET /expedientes/{id}/indice`, ver `get_indice`) en `facetas.<x>.error`,
 * así que la pestaña de índice electrónico —la de mayor valor legal de la
 * vista— mostraba "No se pudo cargar el índice" en el caso NORMAL de "no
 * generado todavía", y la rama honesta "Índice no disponible" quedaba
 * inalcanzable.
 *
 * Este test fija: (a) el 404 de `indice` se resuelve a estado vacío legítimo
 * (`{data: null, error: null}`), no a error; (b) un 500/503 real de esa misma
 * faceta SÍ se conserva como error (para no enmascarar fallos genuinos);
 * (c) el 404 del expediente principal (faceta crítica) sigue propagando un
 * error de página, sin regresión.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { load } from "./+page.server.js";

const EXPEDIENTE = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "2026-ICETEX-EXP-000001",
  name: "Expediente de prueba",
  status: "open",
  radicados: [],
};

const SESSION = {
  cookies: { get: () => "tok-abc" },
  parent: async () => ({
    user: { tenant_slug: "icetex", sub: "22222222-2222-2222-2222-222222222222" },
  }),
  params: { id: EXPEDIENTE.id },
};

/** @param {(path: string) => { status: number, body: any }} responder */
function mockGateway(responder) {
  globalThis.fetch = vi.fn(async (url) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    const { status, body } = responder(path);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    };
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("load de /expedientes/[id] — 404 NEUTRO por faceta (RF-SEG-08)", () => {
  it("resuelve el 404 de /indice a estado vacío, NO a error", async () => {
    mockGateway((path) => {
      if (path.includes("/indice")) {
        return {
          status: 404,
          body: {
            error: "expediente_not_found",
            detail: `Expediente ${EXPEDIENTE.id} not found`,
            status: 404,
          },
        };
      }
      if (path === `/expedientes/${EXPEDIENTE.id}`) return { status: 200, body: EXPEDIENTE };
      return { status: 200, body: [] };
    });

    const data = await load(SESSION);

    expect(data.facetas.indice.error).toBeNull();
    expect(data.facetas.indice.data).toBeNull();
  });

  it("un 503 real en /indice SÍ se conserva como error (no se enmascara)", async () => {
    mockGateway((path) => {
      if (path.includes("/indice")) {
        return {
          status: 503,
          body: { error: "upstream_error", detail: "Service unavailable", status: 503 },
        };
      }
      if (path === `/expedientes/${EXPEDIENTE.id}`) return { status: 200, body: EXPEDIENTE };
      return { status: 200, body: [] };
    });

    const data = await load(SESSION);

    expect(data.facetas.indice.error).not.toBeNull();
    expect(data.facetas.indice.error.status).toBe(503);
  });

  it("el 404 del expediente principal sigue siendo un error de página (sin regresión)", async () => {
    mockGateway((path) => {
      if (path === `/expedientes/${EXPEDIENTE.id}`) {
        return {
          status: 404,
          body: {
            error: "expediente_not_found",
            detail: `Expediente ${EXPEDIENTE.id} not found`,
            status: 404,
          },
        };
      }
      return { status: 200, body: [] };
    });

    await expect(load(SESSION)).rejects.toMatchObject({ status: 404 });
  });

  it("resuelve el 404 de /unidades a estado vacío (mismo criterio que /indice)", async () => {
    mockGateway((path) => {
      if (path.includes("/unidades")) {
        return {
          status: 404,
          body: {
            error: "expediente_not_found",
            detail: `Expediente ${EXPEDIENTE.id} not found`,
            status: 404,
          },
        };
      }
      if (path === `/expedientes/${EXPEDIENTE.id}`) return { status: 200, body: EXPEDIENTE };
      return { status: 200, body: [] };
    });

    const data = await load(SESSION);

    expect(data.facetas.ubicacion.error).toBeNull();
    expect(data.facetas.ubicacion.data).toBeNull();
  });
});
