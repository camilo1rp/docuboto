// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * tvd-page.test.js — Render de /admin/tvd (+page.svelte).
 *
 * Cubre A6 (revisión UX): "Editar" (`update_tvd_agrupacion`) y el circuito de
 * convalidación de InstrumentLifecycleActions exigen
 * `require_permission("USUA_PERM_TRD", min_crud=3)` en el backend
 * (tvd.py líneas 116/134-253) — la sola presencia del permiso (nivel lectura)
 * ya NO basta para ver esos botones, solo para "Nueva agrupación TVD"
 * (create_tvd_agrupacion, min_crud=1 default).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/tvd") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: TvdPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [], permission_levels = {} } = {}) {
  return {
    sub: "u-1",
    email: "admin@orpyca.com",
    name: "Administradora",
    preferred_username: "administradora",
    roles: [],
    tenant_slug: "icetex",
    permissions,
    permission_levels,
  };
}

const AGRUPACION_1 = {
  id: "tvd-1",
  code: "TVD-001",
  version: 1,
  name: "Fondo acumulado — dependencia suprimida",
  parent_id: null,
  fondo_nombre: "Dependencia X",
  fecha_extrema_inicial: "1990-01-01",
  fecha_extrema_final: "2000-01-01",
  disposition: "CT",
  estado: "borrador",
};

function baseData(overrides = {}) {
  return { agrupaciones: [AGRUPACION_1], agrupacionesError: null, ...overrides };
}

describe("/admin/tvd — gate por nivel CRUD (A6, revisión UX)", () => {
  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  it("solo presencia (nivel lectura): 'Nueva agrupación TVD' visible, 'Editar' y 'Aprobar' NO", () => {
    initSession(
      sessionWith({
        permissions: ["USUA_PERM_TRD"],
        permission_levels: { USUA_PERM_TRD: 1 },
      }),
    );
    render(TvdPage, { props: { data: baseData() } });
    expect(screen.getByRole("button", { name: "Nueva agrupación TVD" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Editar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
  });

  it("con min_crud=3: 'Editar' y el circuito de convalidación (Aprobar) sí se ven", () => {
    initSession(
      sessionWith({
        permissions: ["USUA_PERM_TRD"],
        permission_levels: { USUA_PERM_TRD: 3 },
      }),
    );
    render(TvdPage, { props: { data: baseData() } });
    expect(screen.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeInTheDocument();
  });

  it("sin el permiso en absoluto: ninguna acción de escritura visible", () => {
    initSession(sessionWith({ permissions: [] }));
    render(TvdPage, { props: { data: baseData() } });
    expect(screen.queryByRole("button", { name: "Nueva agrupación TVD" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Editar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
  });
});
