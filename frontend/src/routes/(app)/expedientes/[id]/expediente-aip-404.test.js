// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expediente-aip-404.test.js — Render de /expedientes/[id] (+page.svelte).
 *
 * Regresión de un hallazgo real (revisión UX C1): `loadPreservacion()` hacía
 * `if (!r.ok) throw d`, sin mirar el status — así que el 404 NEUTRO de
 * `GET /expedientes/{id}/aip` (sin AIP empaquetado TODAVÍA, o sin visibilidad
 * — ver `archive_service.py` `get_aip`, mismo criterio no-read-up que
 * `get_indice`) caía siempre en `aip.status = "error"`, y la pantalla decía
 * "No se pudo cargar el AIP" + el `detail` crudo del backend sobre la ficha
 * de un expediente que el usuario SÍ está viendo — un fallo del sistema
 * inventado sobre el caso normal. La rama honesta "Sin AIP empaquetado" ya
 * existía en la plantilla pero era inalcanzable.
 *
 * Este test fija: (a) un 404 en /aip resuelve al estado vacío ("Sin AIP
 * empaquetado…"), NUNCA a "No se pudo cargar"; (b) un 503 real SÍ se
 * presenta como error, para no enmascarar un fallo genuino de otra causa.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ invalidateAll: vi.fn() }));

const { default: ExpedientePage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const EXPEDIENTE = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "2026-ICETEX-EXP-000001",
  name: "Expediente de prueba",
  description: null,
  status: "open",
  radicados: [],
};

function sessionWith(permissions = []) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

/** @param {Partial<import('./$types').PageData>} overrides */
function baseData(overrides = {}) {
  return {
    expediente: EXPEDIENTE,
    facetas: {
      indice: { data: null, error: null },
      eventos: { data: [], error: null },
      ubicacion: { data: [], error: null },
    },
    trdSeries: [],
    ...overrides,
  };
}

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

describe("/expedientes/[id] — panel Preservación, AIP 404 NEUTRO (C1)", () => {
  it('un 404 de /api/aip muestra "Sin AIP empaquetado", NO "No se pudo cargar"', async () => {
    initSession(sessionWith());
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/aip")) {
        return jsonResponse(
          { error: "expediente_not_found", detail: "Expediente ... not found", status: 404 },
          404,
        );
      }
      if (u.includes("/api/preservacion-eventos")) return jsonResponse([]);
      return jsonResponse({});
    });

    render(ExpedientePage, { props: { data: baseData() } });

    await fireEvent.click(screen.getByRole("tab", { name: /preservación/i }));

    await waitFor(() => {
      expect(screen.getByText(/sin aip empaquetado/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/no se pudo cargar el aip/i)).not.toBeInTheDocument();
  });

  it("un 503 real de /api/aip SÍ se presenta como error (no se enmascara)", async () => {
    initSession(sessionWith());
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/aip")) {
        return jsonResponse(
          { error: "connection_error", detail: "No se pudo obtener el paquete.", status: 503 },
          503,
        );
      }
      if (u.includes("/api/preservacion-eventos")) return jsonResponse([]);
      return jsonResponse({});
    });

    render(ExpedientePage, { props: { data: baseData() } });

    await fireEvent.click(screen.getByRole("tab", { name: /preservación/i }));

    await waitFor(() => {
      expect(screen.getByText(/no se pudo cargar el aip/i)).toBeInTheDocument();
    });
  });
});
