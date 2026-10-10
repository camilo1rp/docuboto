// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Espejo del gate min_crud=3: dependencias, catálogos y PINAR ofrecen escritura solo con
 * USUA_PERM_ADMIN a nivel >= 3; con nivel 2 quedan de solo lectura.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/admin") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: Dependencias } = await import("./dependencias/+page.svelte");
const { default: Catalogos } = await import("./catalogos/+page.svelte");
const { default: Pinar } = await import("./pinar/+page.svelte");
const { default: PinarDetalle } = await import("./pinar/[id]/+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");
const { puedeEscribirAdmin, mensajeEscrituraAdmin, MENSAJE_NIVEL_ADMIN } =
  await import("$lib/utils/nivelAdmin.js");

const sesion = (nivel) => ({
  sub: "u-1",
  email: "a@b.co",
  name: "A",
  preferred_username: "a",
  roles: [],
  tenant_slug: "icetex",
  permissions: ["USUA_PERM_ADMIN"],
  permission_levels: { USUA_PERM_ADMIN: nivel },
});

const NODO = {
  id: "d1",
  codigo: "100",
  sigla: "GG",
  nombre: "Gerencia",
  activo: true,
  children: [],
};
const PLAN = {
  id: "p-1",
  version: 1,
  estado: "borrador",
  vigencia_desde: "2026-01-01",
  vigencia_hasta: "2029-12-31",
  created_at: "2026-01-01T10:00:00Z",
};

beforeEach(() => clearSession());
afterEach(() => {
  clearSession();
  vi.unstubAllGlobals();
});

describe("nivelAdmin", () => {
  it("puedeEscribirAdmin es fail-closed", () => {
    expect(puedeEscribirAdmin(undefined)).toBe(false);
    expect(puedeEscribirAdmin({ USUA_PERM_ADMIN: 2 })).toBe(false);
    expect(puedeEscribirAdmin({ USUA_PERM_ADMIN: 3 })).toBe(true);
  });
  it("explica el 403 en español", () => {
    expect(mensajeEscrituraAdmin({ error: "forbidden", status: 403 }, "x")).toBe(
      MENSAJE_NIVEL_ADMIN,
    );
    expect(mensajeEscrituraAdmin({ detail: "boom" }, "fallback")).toBeTruthy();
  });
});

describe.each([
  [2, false],
  [3, true],
])("nivel %i", (nivel, ve) => {
  it("dependencias", () => {
    initSession(sesion(nivel));
    render(Dependencias, { props: { data: { tree: [NODO], flat: [NODO] } } });
    expect(!!screen.queryByRole("button", { name: "Nueva dependencia" })).toBe(ve);
    expect(!!screen.queryByRole("button", { name: /Editar Gerencia/ })).toBe(ve);
  });

  it("catálogos", async () => {
    initSession(sesion(nivel));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => [{ id: "i1", codigo: "A", nombre: "Item", activo: true }],
      })),
    );
    render(Catalogos, { props: { data: { catalogos: ["medio_recepcion"] } } });
    await fireEvent.click(screen.getAllByRole("button", { name: /Medio|medio/ })[0]);
    await waitFor(() => expect(screen.getByText("Item")).toBeInTheDocument());
    expect(!!screen.queryByRole("button", { name: "Nuevo item" })).toBe(ve);
    expect(!!screen.queryByRole("button", { name: "Editar" })).toBe(ve);
  });

  it("PINAR listado", () => {
    initSession(sesion(nivel));
    render(Pinar, { props: { data: { planes: [PLAN], loadError: null, estadoFiltro: "" } } });
    expect(!!screen.queryByRole("button", { name: "Nuevo plan" })).toBe(ve);
  });

  it("PINAR detalle", () => {
    initSession(sesion(nivel));
    render(PinarDetalle, {
      props: {
        data: {
          plan: PLAN,
          tablero: { data: { plan_id: "p-1", por_objetivo: [], por_eje: [] }, error: null },
        },
      },
    });
    expect(!!screen.queryByRole("button", { name: /Aprobar/ })).toBe(ve);
  });
});
