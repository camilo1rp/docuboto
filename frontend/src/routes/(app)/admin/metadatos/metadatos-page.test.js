// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * metadatos-page.test.js — Render de /admin/metadatos (+page.svelte),
 * pestaña "Elementos de metadato".
 *
 * Cubre A6 (revisión UX): "Editar" elemento (`update_element`, default
 * min_crud=1) sigue en presencia, pero "Eliminar" (`delete_element`,
 * metadata_elements.py línea 154) exige
 * `require_permission("USUA_PERM_TRD", min_crud=3)`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/metadatos") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: MetadatosPage } = await import("./+page.svelte");
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

const ELEMENTO_1 = {
  id: "el-1",
  clave: "asunto",
  etiqueta: "Asunto",
  tipo_dato: "text",
  searchable: true,
  activo: true,
};

function baseData(overrides = {}) {
  return {
    expedienteTemplates: [],
    expedienteTemplatesError: null,
    documentTemplates: [],
    documentTemplatesError: null,
    elements: [ELEMENTO_1],
    elementsError: null,
    series: [],
    ...overrides,
  };
}

async function abrirPestanaElementos() {
  await fireEvent.click(screen.getByRole("tab", { name: "Elementos de metadato" }));
}

describe("/admin/metadatos — Elementos: 'Editar' en presencia, 'Eliminar' exige min_crud=3", () => {
  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  it("solo presencia (nivel lectura): 'Editar' visible, 'Eliminar' NO", async () => {
    initSession(
      sessionWith({ permissions: ["USUA_PERM_TRD"], permission_levels: { USUA_PERM_TRD: 1 } }),
    );
    render(MetadatosPage, { props: { data: baseData() } });
    await abrirPestanaElementos();
    expect(screen.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Eliminar" })).not.toBeInTheDocument();
  });

  it("con min_crud=3: 'Editar' y 'Eliminar' ambos visibles", async () => {
    initSession(
      sessionWith({ permissions: ["USUA_PERM_TRD"], permission_levels: { USUA_PERM_TRD: 3 } }),
    );
    render(MetadatosPage, { props: { data: baseData() } });
    await abrirPestanaElementos();
    expect(screen.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Eliminar" })).toBeInTheDocument();
  });

  it("sin el permiso: ni 'Editar' ni 'Eliminar' se ven", async () => {
    initSession(sessionWith({ permissions: [] }));
    render(MetadatosPage, { props: { data: baseData() } });
    await abrirPestanaElementos();
    expect(screen.queryByRole("button", { name: "Editar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Eliminar" })).not.toBeInTheDocument();
  });
});
