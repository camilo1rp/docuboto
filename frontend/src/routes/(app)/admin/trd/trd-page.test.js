// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * trd-page.test.js — Render de /admin/trd (+page.svelte).
 *
 * Cubre A6 (revisión UX): "Editar" serie (`update_trd_serie`) y el circuito
 * de convalidación exigen `require_permission("USUA_PERM_TRD", min_crud=3)`
 * (trd.py líneas 183-330); "Editar" tipo documental (`update_tipo`, default
 * min_crud=1) sigue en presencia, pero "Eliminar" tipo (`delete_tipo`,
 * tipos_documentales.py línea 132, min_crud=3) también sube de nivel.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/trd") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: TrdPage } = await import("./+page.svelte");
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

const SERIE_1 = {
  id: "s-1",
  code: "10",
  version: 1,
  name: "Serie de prueba",
  parent_id: null,
  disposition: "CT",
  retention_years: 2,
  total_retention: 10,
  estado: "borrador",
};

const TIPO_1 = {
  id: "t-1",
  code: "10.1",
  nombre: "Oficio",
  trd_serie_id: "s-1",
  obligatorio: true,
  activo: true,
};

function baseData(overrides = {}) {
  return {
    series: [SERIE_1],
    seriesError: null,
    tipos: [TIPO_1],
    tiposError: null,
    ...overrides,
  };
}

describe("/admin/trd — Series TRD/CCD: gate por nivel CRUD (A6, revisión UX)", () => {
  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  it("solo presencia (nivel lectura): 'Nueva serie' visible, 'Editar' serie y 'Aprobar' NO", () => {
    initSession(
      sessionWith({ permissions: ["USUA_PERM_TRD"], permission_levels: { USUA_PERM_TRD: 1 } }),
    );
    render(TrdPage, { props: { data: baseData() } });
    expect(screen.getByRole("button", { name: "Nueva serie" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Editar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
  });

  it("con min_crud=3: 'Editar' serie y 'Aprobar' sí se ven", () => {
    initSession(
      sessionWith({ permissions: ["USUA_PERM_TRD"], permission_levels: { USUA_PERM_TRD: 3 } }),
    );
    render(TrdPage, { props: { data: baseData() } });
    expect(screen.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeInTheDocument();
  });
});

describe("/admin/trd — Tipos documentales: 'Editar' en presencia, 'Eliminar' exige min_crud=3", () => {
  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  async function abrirPestanaTipos() {
    await fireEvent.click(screen.getByRole("tab", { name: "Tipos documentales" }));
  }

  it("solo presencia (nivel lectura): 'Editar' tipo visible, 'Eliminar' NO", async () => {
    initSession(
      sessionWith({ permissions: ["USUA_PERM_TRD"], permission_levels: { USUA_PERM_TRD: 1 } }),
    );
    render(TrdPage, { props: { data: baseData() } });
    await abrirPestanaTipos();
    expect(screen.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Eliminar" })).not.toBeInTheDocument();
  });

  it("con min_crud=3: 'Editar' y 'Eliminar' tipo ambos visibles", async () => {
    initSession(
      sessionWith({ permissions: ["USUA_PERM_TRD"], permission_levels: { USUA_PERM_TRD: 3 } }),
    );
    render(TrdPage, { props: { data: baseData() } });
    await abrirPestanaTipos();
    expect(screen.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Eliminar" })).toBeInTheDocument();
  });
});
