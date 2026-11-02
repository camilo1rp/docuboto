// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El detalle ofrece corregir el vencimiento solo con PERM_DUE_DATE nivel 3.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/bandeja") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { clearSession, initSession } = await import("$lib/stores/auth.js");

const ID = "11111111-1111-1111-1111-111111111111";
const DOC = {
  id: ID,
  tracking_number: "2026-DEMO-E-000117",
  doc_type: "E",
  subject: "Solicitud de certificación",
  status: "in_progress",
  registered_at: "2026-07-01T10:00:00Z",
  due_date: "2026-07-15",
  origin_dept: "Externo",
  dest_dept: "Ventanilla",
  pages: 1,
};

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

function mock(extra) {
  globalThis.fetch = vi.fn(async (url, opts) => {
    const u = url.toString();
    if (u === `/bandeja/api/${ID}`) return json(DOC);
    if (u.endsWith("/due-date")) return extra(u, opts);
    if (u.endsWith("/actores") || u.endsWith("/respuestas")) return json([]);
    if (u.endsWith("/current")) return json({ error: "no_active_step" }, 404);
    return json({ items: [], total: 0 }, 404);
  });
}

function sesion(levels) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operadora",
    preferred_username: "operadora",
    roles: [],
    tenant_slug: "icetex",
    permissions: levels ? ["PERM_DUE_DATE"] : ["PERM_RADI"],
    permission_levels: levels ?? {},
  };
}

const data = {
  documents: [DOC],
  total: 1,
  loadError: null,
  vista: "lista",
  dependencias: [],
  initialFilters: { docType: "", status: "", page: 1 },
};

async function abrir(levels) {
  initSession(sesion(levels));
  render(BandejaPage, { props: { data } });
  await waitFor(() => expect(screen.getByText(DOC.tracking_number)).toBeTruthy());
  await fireEvent.click(
    screen.getByRole("button", { name: `Ver detalle de radicado ${DOC.tracking_number}` }),
  );
  await waitFor(() => expect(document.querySelector(".doc-detail")).toBeTruthy());
}

describe("/bandeja — vencimiento", () => {
  it("sin el permiso no ofrece el formulario", async () => {
    mock(() => json({}));
    await abrir(null);
    expect(screen.queryByTestId("corregir-vencimiento")).toBeNull();
    expect(screen.getByText("2026-07-15")).toBeTruthy();
  });

  it("con nivel 3 envía la fecha y el motivo", async () => {
    mock(() => json({ due_date: "2026-07-10" }));
    await abrir({ PERM_DUE_DATE: 3 });
    expect(screen.getByTestId("corregir-vencimiento")).toBeTruthy();
    await fireEvent.input(screen.getByLabelText("Nueva fecha"), {
      target: { value: "2026-07-10" },
    });
    await fireEvent.input(screen.getByLabelText("Motivo"), {
      target: { value: "El término contado de más se acorta." },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Guardar vencimiento" }));
    await waitFor(() => {
      const llamada = globalThis.fetch.mock.calls.find((c) => String(c[0]).endsWith("/due-date"));
      expect(llamada).toBeTruthy();
      expect(JSON.parse(llamada[1].body)).toEqual({
        due_date: "2026-07-10",
        motivo: "El término contado de más se acorta.",
      });
    });
  });
});
