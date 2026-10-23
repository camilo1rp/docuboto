// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expediente-aip-versiones.test.js — pestaña Preservación: lista de `versiones[]`
 * del AIP (C-6, ADR-042), solo lectura. Control positivo (con versiones se
 * muestran) y negativo (AIP sin versiones[]: no se inventa una sección vacía).
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
const data = () => ({
  expediente: EXPEDIENTE,
  facetas: {
    indice: { data: null, error: null },
    eventos: { data: [], error: null },
    ubicacion: { data: [], error: null },
  },
  trdSeries: [],
});
const resp = (body, status = 200) => ({ ok: status < 300, status, json: async () => body });
const AIP = {
  estado: "completo",
  num_documentos: 3,
  created_at: "2026-02-01T10:00:00Z",
  aip_sha256: "a".repeat(64),
};

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

async function abrir(aip) {
  initSession({
    sub: "u-1",
    email: "o@o.com",
    name: "O",
    preferred_username: "o",
    roles: [],
    tenant_slug: "icetex",
    permissions: [],
  });
  globalThis.fetch = vi.fn(async (url) =>
    String(url).includes("/api/aip") ? resp(aip) : resp([]),
  );
  render(ExpedientePage, { props: { data: data() } });
  await fireEvent.click(screen.getByRole("tab", { name: /preservación/i }));
  await waitFor(() => expect(screen.getByText("Generado")).toBeInTheDocument());
}

describe("/expedientes/[id] — versiones del AIP", () => {
  it("muestra las versiones con su motivo en español", async () => {
    await abrir({
      ...AIP,
      versiones: [
        {
          version: 1,
          aip_id: "a1",
          sustituye_a_aip_id: null,
          motivo: "ingesta",
          firma_ordinal: 1,
          created_at: "2026-02-01T10:00:00Z",
        },
        {
          version: 2,
          aip_id: "a2",
          sustituye_a_aip_id: "a1",
          motivo: "renovacion_lta",
          firma_ordinal: 2,
          created_at: "2026-06-01T10:00:00Z",
        },
      ],
    });
    expect(screen.getByText(/Versiones del paquete \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("Renovación del sello de archivo (LTA)")).toBeInTheDocument();
    expect(screen.getByText("Ingesta inicial")).toBeInTheDocument();
  });

  it("un AIP sin versiones[] no inventa la sección", async () => {
    await abrir(AIP);
    expect(screen.queryByText(/Versiones del paquete/)).not.toBeInTheDocument();
  });
});
