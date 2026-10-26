// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expediente-preservacion-estado.test.js — AREA-12-04.
 *
 * La pestaña Preservación decía «Sin AIP empaquetado, o sin visibilidad» con la
 * preservación APAGADA en la instalación (`PRESERVATION_ENABLED=false`): un 404
 * neutro que hacía pasar por normal que nada se empaqueta jamás. Ahora consulta
 * el estado de INSTANCIA primero:
 *  - `{enabled:false}` → «desactivada», y NO se pide el AIP;
 *  - `{enabled:true}` + AIP 404 → sigue el texto neutro (no-read-up intacto);
 *  - estado caído → el AIP decide (un fallo del estado no inventa «desactivada»).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ invalidateAll: vi.fn() }));

const { default: ExpedientePage } = await import("./[id]/+page.svelte");
const { GET: estadoGet } = await import("./api/preservacion-estado/+server.js");
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

const resp = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

function montar(estado) {
  initSession({
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions: [],
  });
  const llamadas = [];
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    llamadas.push(u);
    if (u.includes("/preservacion-estado")) return estado();
    if (u.includes("/api/aip")) return resp({ error: "expediente_not_found" }, 404);
    if (u.includes("/api/preservacion-eventos")) return resp([]);
    return resp({});
  });
  render(ExpedientePage, { props: { data: data() } });
  return llamadas;
}

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

describe("pestaña Preservación — estado de instancia", () => {
  it("enabled=false: dice «desactivada» y NO pide el AIP", async () => {
    const llamadas = montar(() => resp({ enabled: false }));
    await fireEvent.click(screen.getByRole("tab", { name: /preservación/i }));

    await waitFor(() => {
      expect(screen.getByText(/preservación desactivada/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/PRESERVATION_ENABLED/)).toBeInTheDocument();
    expect(screen.queryByText(/sin aip empaquetado/i)).not.toBeInTheDocument();
    expect(llamadas.some((u) => u.includes("/api/aip"))).toBe(false);
  });

  it("enabled=true y AIP 404: sigue el texto neutro", async () => {
    const llamadas = montar(() => resp({ enabled: true }));
    await fireEvent.click(screen.getByRole("tab", { name: /preservación/i }));

    await waitFor(() => {
      expect(screen.getByText(/sin aip empaquetado/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/preservación desactivada/i)).not.toBeInTheDocument();
    expect(llamadas.some((u) => u.includes("/api/aip"))).toBe(true);
  });

  it("estado caído (503): el AIP decide, no se inventa «desactivada»", async () => {
    montar(() => resp({ error: "connection_error" }, 503));
    await fireEvent.click(screen.getByRole("tab", { name: /preservación/i }));

    await waitFor(() => {
      expect(screen.getByText(/sin aip empaquetado/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/preservación desactivada/i)).not.toBeInTheDocument();
  });
});

describe("GET /expedientes/api/preservacion-estado", () => {
  it("sin sesión → 401 sin tocar la red", async () => {
    const res = await estadoGet({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("con sesión reenvía al gateway con Bearer y tenant", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ enabled: false }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    const res = await estadoGet({ locals: { accessToken: "t", user: { tenant_slug: "demo" } } });
    expect(await res.json()).toEqual({ enabled: false });
    const [url, init] = f.mock.calls[0];
    expect(url).toMatch(/\/preservacion\/estado$/);
    expect(init.headers.Authorization).toBe("Bearer t");
    expect(init.headers["X-Tenant-Slug"]).toBe("demo");
    vi.unstubAllGlobals();
  });
});
