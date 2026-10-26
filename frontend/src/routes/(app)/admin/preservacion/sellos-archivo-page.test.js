// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * sellos-archivo-page.test.js — faceta «Sellos de archivo» (C-6, ADR-042):
 * visible solo con USUA_PERM_ADMIN >= 3; «Reservado» sin ocultar la fila;
 * 403 = «Sin permiso» (no «sin sellos»); textos de ultimo_aviso en español.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/admin/preservacion") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: PreservacionPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const sesion = (adminNivel) =>
  initSession({
    sub: "u-1",
    email: "a@orpyca.com",
    name: "A",
    preferred_username: "a",
    roles: [],
    tenant_slug: "icetex",
    permissions: ["USUA_PERM_EXPEDIENTE", "USUA_PERM_ADMIN"],
    permission_levels: { USUA_PERM_EXPEDIENTE: 1, USUA_PERM_ADMIN: adminNivel },
  });

const FILA = {
  firma_id: "f-1",
  objeto_tipo: "aip",
  objeto_id: "EXP-001",
  objeto_reservado: false,
  ordinal: 2,
  estado: "vigente",
  situaciones: ["por_vencer"],
  gen_time: "2026-01-01T00:00:00Z",
  vence_at: "2026-11-01T00:00:00Z",
  dias_restantes: 11,
  tsa_cert_fingerprint: "ab".repeat(32),
  tsa_revocacion_fuente: "crl_ocsp",
  revocacion_tsa_previa: true,
  ultimo_aviso: "rechazada:algoritmo_no_vigente",
};
const RESERVADA = {
  ...FILA,
  firma_id: "f-2",
  objeto_tipo: null,
  objeto_id: null,
  objeto_reservado: true,
  ultimo_aviso: "tsa_revocada",
  situaciones: ["vencido"],
  estado: "caducado",
  dias_restantes: -4,
};

const sellos = (extra = {}) => ({
  resumen: {
    data: {
      por_vencer: 1,
      vencido: 1,
      sin_revocacion: 0,
      pendiente_worm: 0,
      rechazado: 0,
      margen_dias: 30,
    },
    error: null,
  },
  lista: { data: { items: [FILA, RESERVADA], total: 2 }, error: null },
  ...extra,
});
const data = (s) => ({
  plan: { data: null, error: { status: 404 } },
  versions: { data: [], error: null },
  pendientesFirma: { data: [], error: null },
  pendientesRenovacion: { data: [], error: null },
  sellos: s,
});

beforeEach(() => clearSession());
afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

describe("/admin/preservacion — pestaña Sellos de archivo", () => {
  it("nivel 3 de administración: la pestaña existe (control positivo)", () => {
    sesion(3);
    render(PreservacionPage, { props: { data: data(sellos()) } });
    expect(screen.getByRole("tab", { name: /Sellos de archivo/ })).toBeInTheDocument();
  });

  it("nivel 1 de administración: NO existe, aunque el loader trajera datos", () => {
    sesion(1);
    render(PreservacionPage, { props: { data: data(sellos()) } });
    expect(screen.queryByRole("tab", { name: /Sellos de archivo/ })).not.toBeInTheDocument();
  });

  it("nivel 3 pero el loader no trajo la faceta (null): tampoco se ofrece", () => {
    sesion(3);
    render(PreservacionPage, { props: { data: data(null) } });
    expect(screen.queryByRole("tab", { name: /Sellos de archivo/ })).not.toBeInTheDocument();
  });

  it("muestra el resumen, el objeto reservado SIN ocultar la fila y los avisos en español", async () => {
    sesion(3);
    render(PreservacionPage, { props: { data: data(sellos()) } });
    await fireEvent.click(screen.getByRole("tab", { name: /Sellos de archivo/ }));
    expect(screen.getByText("Reservado")).toBeInTheDocument();
    expect(screen.getByText(/Paquete AIP · EXP-001/)).toBeInTheDocument();
    expect(screen.queryByText("EXP-001", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByText("Renovación rechazada: algoritmo_no_vigente")).toBeInTheDocument();
    expect(screen.getByText(/la TSA fue revocada/)).toBeInTheDocument();
    expect(screen.getByText(/Venció hace 4 días/)).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3); // cabecera + 2 filas
  });

  it("403 del listado: «Sin permiso», no «Sin sellos que atender»", async () => {
    sesion(3);
    render(PreservacionPage, {
      props: {
        data: data(
          sellos({
            lista: { data: null, error: { status: 403, detail: "Sin permiso" } },
            resumen: { data: null, error: { status: 403, detail: "x" } },
          }),
        ),
      },
    });
    await fireEvent.click(screen.getByRole("tab", { name: /Sellos de archivo/ }));
    expect(screen.getAllByText("Sin permiso").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Sin sellos que atender/)).not.toBeInTheDocument();
  });

  it("vacío de verdad: se dice que no hay sellos que atender", async () => {
    sesion(3);
    render(PreservacionPage, {
      props: { data: data(sellos({ lista: { data: { items: [], total: 0 }, error: null } })) },
    });
    await fireEvent.click(screen.getByRole("tab", { name: /Sellos de archivo/ }));
    expect(screen.getByText("Sin sellos que atender")).toBeInTheDocument();
  });

  it("filtrar por una situación pide al BFF con ese parámetro y vuelve a la página 1", async () => {
    sesion(3);
    globalThis.fetch = vi.fn(async (url) =>
      String(url).includes("/resumen")
        ? new Response(
            JSON.stringify({
              por_vencer: 1,
              vencido: 1,
              sin_revocacion: 0,
              pendiente_worm: 0,
              rechazado: 0,
              margen_dias: 30,
            }),
            { status: 200 },
          )
        : new Response(JSON.stringify([FILA]), { status: 200, headers: { "X-Total-Count": "1" } }),
    );
    render(PreservacionPage, { props: { data: data(sellos()) } });
    await fireEvent.click(screen.getByRole("tab", { name: /Sellos de archivo/ }));
    await fireEvent.click(screen.getByRole("button", { name: /Vencido/ }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    const url = String(globalThis.fetch.mock.calls[0][0]);
    expect(url).toContain("/admin/preservacion/api/sellos-archivo?");
    expect(url).toContain("situacion=vencido");
    expect(url).toContain("page=1");
  });
});
