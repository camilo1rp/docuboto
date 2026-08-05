// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * preservacion-page.test.js — Render de /admin/preservacion (+page.svelte),
 * pestaña "Plan de preservación".
 *
 * Cubre A6 (revisión UX): `set_plan` (PUT /preservacion/plan,
 * storage-service preservacion.py línea 99) exige
 * `require_permission("USUA_PERM_EXPEDIENTE", min_crud=3)` — antes el
 * formulario "Actualizar plan" solo miraba presencia del permiso (el mismo
 * que gatea la vista de solo lectura, `get_plan`/`list_plan_versions`,
 * min_crud=1 default).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/preservacion") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: PreservacionPage } = await import("./+page.svelte");
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

function baseData(overrides = {}) {
  return {
    plan: { data: null, error: { status: 404 } },
    versions: { data: [], error: null },
    indicesPendientes: { data: [], error: null },
    wormPendientes: { data: [], error: null },
    ...overrides,
  };
}

describe("/admin/preservacion — 'Actualizar plan' exige min_crud=3 (A6, revisión UX)", () => {
  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  it("solo presencia (nivel lectura): la vista se ve, pero NO el formulario de actualizar plan", () => {
    initSession(
      sessionWith({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 1 },
      }),
    );
    render(PreservacionPage, { props: { data: baseData() } });
    expect(screen.queryByText("Sin permiso")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Guardar nueva versión/ }),
    ).not.toBeInTheDocument();
  });

  it("con min_crud=3: el formulario 'Actualizar plan' sí se ve", () => {
    initSession(
      sessionWith({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
      }),
    );
    render(PreservacionPage, { props: { data: baseData() } });
    expect(screen.getByRole("button", { name: /Guardar nueva versión/ })).toBeInTheDocument();
  });

  it("sin el permiso: la vista completa queda bloqueada (canRead también depende de él)", () => {
    initSession(sessionWith({ permissions: [] }));
    render(PreservacionPage, { props: { data: baseData() } });
    expect(screen.getByText("Sin permiso")).toBeInTheDocument();
  });
});
