// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja — el detalle del radicado MUESTRA sus metadatos (B6-25).
 *
 * El detalle cargaba `metadata` y no la pintaba: lo capturado en el paso de
 * radicación (campos de la plantilla del tipo) no se podía leer desde ninguna
 * pantalla.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";
import { plantillaMetadatos } from "$lib/testing/plantillaMetadatos.js";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/bandeja") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { clearSession } = await import("$lib/stores/auth.js");

const ID = "11111111-1111-1111-1111-111111111111";
const DOC = {
  id: ID,
  tracking_number: "2026-ICETEX-S-000009",
  doc_type: "S",
  subject: "Circular",
  status: "distributed",
  registered_at: "2026-07-01T10:00:00Z",
  origin_dept: "Jurídica",
  dest_dept: "Externo",
  pages: 2,
  doc_class: "OFI",
};
const PLANTILLA = {
  activo: true,
  json_schema: { properties: { reserva: { type: "string", title: "Motivo de reserva" } } },
};
const original = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = original;
  clearSession();
  vi.restoreAllMocks();
});
const json = (b, status = 200) => new Response(JSON.stringify(b), { status });

async function abrir(detalle) {
  globalThis.fetch = vi.fn(async (url) => {
    const u = url.toString();
    if (u === `/bandeja/api/${ID}`) return json(detalle);
    if (u.startsWith("/bandeja/api/metadata-template"))
      return json([plantillaMetadatos(PLANTILLA)]);
    if (u === `/bandeja/api/${ID}/current`) return json({ error: "no_active_step" }, 404);
    if (u.startsWith("/anulaciones/api/")) return json({}, 404);
    if (u.endsWith("/actores") || u.endsWith("/respuestas")) return json([]);
    return json({ items: [], total: 0 });
  });
  render(BandejaPage, {
    props: {
      data: {
        documents: [DOC],
        total: 1,
        loadError: null,
        vista: "lista",
        dependencias: [],
        initialFilters: { docType: "", status: "", page: 1 },
      },
    },
  });
  await fireEvent.click(
    await screen.findByRole("button", { name: `Ver detalle de radicado ${DOC.tracking_number}` }),
  );
  await waitFor(() => expect(document.querySelector(".doc-detail")).toBeTruthy());
}

describe("/bandeja — metadatos del radicado", () => {
  it("con metadatos: la sección existe y usa el title de la plantilla", async () => {
    await abrir({ ...DOC, metadata: { reserva: "Ley 1712" } });
    await waitFor(() => expect(screen.getByText("Motivo de reserva")).toBeTruthy());
    expect(screen.getByRole("heading", { name: /Metadatos del tipo documental/ })).toBeTruthy();
    expect(screen.getByText("Ley 1712")).toBeTruthy();
  });

  it("sin metadatos: no hay sección", async () => {
    await abrir({ ...DOC, metadata: {} });
    await waitFor(() => expect(document.querySelector(".doc-detail")).toBeTruthy());
    expect(screen.queryByRole("heading", { name: /Metadatos del tipo documental/ })).toBeNull();
  });
});
