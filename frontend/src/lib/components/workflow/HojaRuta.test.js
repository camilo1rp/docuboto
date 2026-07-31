// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * HojaRuta.test.js — trazabilidad de flujo de un radicado (E05 §Fase 1).
 *
 * Cubre: render del timeline combinado (history+events), estado vacío,
 * y que el fallo de UNO de los tres endpoints (Promise.allSettled) no tumbe
 * el resto ni deje el componente en blanco.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import HojaRuta from "./HojaRuta.svelte";

const RAD_ID = "11111111-1111-1111-1111-111111111111";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("HojaRuta", () => {
  it("combina history + events en un timeline y distingue el paso actual (aria-current='step')", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input) => {
      const path = String(input);
      if (path.endsWith("/history")) {
        return jsonResponse({
          radicado_id: RAD_ID,
          tracking_number: "2026-ICETEX-E-000001",
          steps: [
            {
              id: "step-1",
              action: "assign",
              to_dept: "Recepción",
              from_dept: null,
              status: "completed",
              created_at: "2026-01-01T10:00:00Z",
              notes: null,
            },
            {
              id: "step-2",
              action: "transfer",
              to_dept: "Jurídica",
              from_dept: "Recepción",
              status: "active",
              created_at: "2026-01-02T10:00:00Z",
              notes: "Revisión legal",
            },
          ],
        });
      }
      if (path.endsWith("/events")) {
        return jsonResponse([]);
      }
      if (path.endsWith("/current")) {
        return jsonResponse({ id: "step-2", to_dept: "Jurídica", status: "active" });
      }
      throw new Error(`unexpected fetch: ${path}`);
    });

    try {
      render(HojaRuta, { props: { radicadoId: RAD_ID } });

      await waitFor(() =>
        screen.getByText("Jurídica", { selector: ".op-hoja-ruta__current-dept" }),
      );

      // Timeline: ambos pasos presentes.
      expect(screen.getByText("Asignación")).toBeInTheDocument();
      expect(screen.getByText("Traslado")).toBeInTheDocument();

      // Paso actual marcado por texto/icono, no solo color (A11y).
      expect(screen.getByText("Paso actual")).toBeInTheDocument();
      const currentItem = document.querySelector('[aria-current="step"]');
      expect(currentItem).not.toBeNull();

      // Timeline es una lista semántica <ol>.
      expect(document.querySelector("ol.op-hoja-ruta__timeline")).not.toBeNull();

      // Número de radicado en fuente monoespaciada.
      expect(screen.getByText("2026-ICETEX-E-000001")).toHaveClass("tracking-number");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("estado vacío: sin pasos ni eventos, sin paso activo, muestra EmptyState (no revienta)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input) => {
      const path = String(input);
      if (path.endsWith("/history")) {
        return jsonResponse({ radicado_id: RAD_ID, tracking_number: "x", steps: [] });
      }
      if (path.endsWith("/events")) {
        return jsonResponse([]);
      }
      if (path.endsWith("/current")) {
        return jsonResponse({ error: "no_active_step", detail: "...", status: 404 }, 404);
      }
      throw new Error(`unexpected fetch: ${path}`);
    });

    try {
      render(HojaRuta, { props: { radicadoId: RAD_ID } });

      await waitFor(() => screen.getByText("Sin paso activo"));
      expect(screen.getByText("Sin movimientos registrados")).toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("un endpoint caído (events falla) no tumba el resto: history se muestra igual y se avisa del error", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input) => {
      const path = String(input);
      if (path.endsWith("/history")) {
        return jsonResponse({
          radicado_id: RAD_ID,
          tracking_number: "2026-ICETEX-E-000001",
          steps: [
            {
              id: "step-1",
              action: "assign",
              to_dept: "Recepción",
              from_dept: null,
              status: "completed",
              created_at: "2026-01-01T10:00:00Z",
              notes: null,
            },
          ],
        });
      }
      if (path.endsWith("/events")) {
        return jsonResponse({ error: "connection_error", detail: "caído" }, 503);
      }
      if (path.endsWith("/current")) {
        return jsonResponse({ error: "no_active_step" }, 404);
      }
      throw new Error(`unexpected fetch: ${path}`);
    });

    try {
      render(HojaRuta, { props: { radicadoId: RAD_ID } });

      // El paso de history sigue visible pese al fallo de events.
      await waitFor(() => screen.getByText("Asignación"));
      // Se avisa explícitamente qué parte falló (no se finge vacío).
      expect(screen.getByText("No se pudieron cargar los eventos de trámite.")).toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("residuo (revisión UX): si history Y events fallan, NO afirma 'sin trazabilidad' además de los errores", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input) => {
      const path = String(input);
      if (path.endsWith("/history")) {
        return jsonResponse({ error: "connection_error", detail: "caído" }, 503);
      }
      if (path.endsWith("/events")) {
        return jsonResponse({ error: "connection_error", detail: "caído" }, 503);
      }
      if (path.endsWith("/current")) {
        return jsonResponse({ error: "no_active_step" }, 404);
      }
      throw new Error(`unexpected fetch: ${path}`);
    });

    try {
      render(HojaRuta, { props: { radicadoId: RAD_ID } });

      await waitFor(() =>
        expect(screen.getByText("No se pudo cargar el historial de pasos.")).toBeInTheDocument(),
      );
      expect(screen.getByText("No se pudieron cargar los eventos de trámite.")).toBeInTheDocument();
      // No se finge que "no hay trazabilidad" cuando en realidad no se pudo saber.
      expect(screen.queryByText("Sin movimientos registrados")).toBeNull();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("mapea tipo_tx del catálogo (no códigos crudos) — A9, revisión UX", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input) => {
      const path = String(input);
      if (path.endsWith("/history")) {
        return jsonResponse({ radicado_id: RAD_ID, tracking_number: "x", steps: [] });
      }
      if (path.endsWith("/events")) {
        return jsonResponse([
          {
            id: "evt-1",
            tipo_tx: "marcar_leido",
            comentario: null,
            to_dept: "Jurídica",
            from_dept: null,
            ts: "2026-01-03T10:00:00Z",
          },
        ]);
      }
      if (path.endsWith("/current")) {
        return jsonResponse({ error: "no_active_step" }, 404);
      }
      throw new Error(`unexpected fetch: ${path}`);
    });

    try {
      render(HojaRuta, { props: { radicadoId: RAD_ID } });

      await waitFor(() => screen.getByText("Marcar como leído"));
      expect(screen.queryByText("marcar_leido")).toBeNull();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
