// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id] — «Verificar integridad» del índice muestra las
 * advertencias de la firma (ADR-016 addendum 2026-10-04) y los 409/503 de las
 * cadenas de firma en español.
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
const INDICE = { version: 1, estado: "firmado", num_documentos: 0, items: [] };

function data() {
  return {
    expediente: EXPEDIENTE,
    facetas: {
      indice: { data: INDICE, error: null },
      eventos: { data: [], error: null },
      ubicacion: { data: [], error: null },
    },
    trdSeries: [],
  };
}

const resp = (body, status = 200) => ({ ok: status < 300, status, json: async () => body });

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

async function verificar() {
  initSession({ sub: "u", name: "Op", tenant_slug: "icetex", roles: [], permissions: [] });
  render(ExpedientePage, { props: { data: data() } });
  await fireEvent.click(screen.getByRole("tab", { name: /índice electrónico/i }));
  await fireEvent.click(await screen.findByRole("button", { name: /verificar integridad/i }));
}

describe("/expedientes/[id] — verificar el índice", () => {
  it("muestra las advertencias de la firma cuando el índice es válido", async () => {
    globalThis.fetch = vi.fn(async () =>
      resp({
        version: 1,
        valido: true,
        huella_indice_ok: true,
        documentos: [],
        firma: {
          presente: true,
          valida: true,
          advertencias: ["tsa_sin_esscertid_anterior_al_corte"],
        },
        fecha_verificacion: "2026-10-09T10:00:00Z",
      }),
    );
    await verificar();
    await waitFor(() => expect(screen.getByText(/ESSCertID/)).toBeInTheDocument());
    expect(screen.getByText(/Índice íntegro y válido/)).toBeInTheDocument();
  });

  it("sin advertencias no pinta el aviso", async () => {
    globalThis.fetch = vi.fn(async () =>
      resp({
        version: 1,
        valido: true,
        huella_indice_ok: true,
        documentos: [],
        firma: { presente: true, valida: true, advertencias: [] },
        fecha_verificacion: "2026-10-09T10:00:00Z",
      }),
    );
    await verificar();
    await waitFor(() => expect(screen.getByText(/Índice íntegro y válido/)).toBeInTheDocument());
    expect(screen.queryByTestId("advertencias-verificacion")).toBeNull();
  });

  it("un 503 de verificación de documento se explica en español", async () => {
    globalThis.fetch = vi.fn(async () =>
      resp({ detail: { error: "verificacion_documento_no_disponible", detail: "down" } }, 503),
    );
    await verificar();
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toMatch(
        /No se pudo comprobar que el documento/,
      ),
    );
  });
});
