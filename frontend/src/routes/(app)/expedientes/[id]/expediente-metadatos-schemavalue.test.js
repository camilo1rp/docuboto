// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id] — los metadatos de la serie TRD se leen con su plantilla
 * (B6-25): etiqueta (`title`) y valor legible, nunca `[object Object]`.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ invalidateAll: vi.fn() }));

const { default: ExpedientePage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const SERIE = "22222222-2222-2222-2222-222222222222";
const EXPEDIENTE = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "2026-ICETEX-EXP-000001",
  name: "Expediente de prueba",
  description: null,
  status: "open",
  trd_serie_id: SERIE,
  radicados: [],
  metadata: { sede: { ciudad: "Pasto" }, reserva: "Ley 1712" },
};
const PLANTILLA = {
  activo: true,
  json_schema: {
    properties: {
      sede: { type: "object", title: "Sede", properties: { ciudad: { title: "Ciudad" } } },
      reserva: { type: "string", title: "Motivo de reserva" },
    },
  },
};

const data = () => ({
  expediente: EXPEDIENTE,
  facetas: {
    indice: { data: null, error: null },
    eventos: { data: [], error: null },
    ubicacion: { data: [], error: null },
  },
  trdSeries: [],
});
const resp = (b, status = 200) => ({ ok: status < 400, status, json: async () => b });

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

describe("/expedientes/[id] — metadatos con SchemaValue", () => {
  it("pinta el title de la plantilla y el valor anidado, sin [object Object]", async () => {
    initSession({ sub: "u", name: "A", roles: [], tenant_slug: "icetex", permissions: [] });
    globalThis.fetch = vi.fn(async (url) =>
      String(url).includes("/expedientes/api/metadata-templates") ? resp([PLANTILLA]) : resp({}),
    );
    const { container } = render(ExpedientePage, { props: { data: data() } });
    await waitFor(() => expect(screen.getByText("Motivo de reserva")).toBeTruthy());
    expect(screen.getByText("Sede")).toBeTruthy();
    expect(screen.getByText("Pasto")).toBeTruthy();
    expect(container.textContent).not.toContain("[object Object]");
  });

  it("si la plantilla no carga: avisa y muestra los datos con su clave", async () => {
    initSession({ sub: "u", name: "A", roles: [], tenant_slug: "icetex", permissions: [] });
    globalThis.fetch = vi.fn(async (url) =>
      String(url).includes("/expedientes/api/metadata-templates")
        ? resp({ error: "x" }, 503)
        : resp({}),
    );
    const { container } = render(ExpedientePage, { props: { data: data() } });
    await waitFor(() =>
      expect(screen.getByText(/No se pudo cargar la plantilla de la serie/)).toBeTruthy(),
    );
    expect(screen.getByText("reserva")).toBeTruthy();
    expect(container.textContent).not.toContain("[object Object]");
  });
});
