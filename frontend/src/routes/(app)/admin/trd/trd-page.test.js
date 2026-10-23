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
  archivo_gestion_years: 2,
  archivo_central_years: 8,
  total_retention: 10, // derivado de solo lectura
  dependencias_productoras: [
    { dependencia_codigo: 12, dependencia_nombre: "Secretaría General", ruta_convalidada: null },
  ],
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

describe("/admin/trd — Revisión pendiente (ADR-025 D4/D6)", () => {
  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  const REV_RATIFICABLE = {
    id: "e-1",
    code: "EXP-2024-001",
    status: "closed",
    trd_serie_id: "s-1",
    trd_serie_code: "10",
    trd_revision_motivo: "cerrado_bajo_serie_migrada",
    closed_at: "2024-05-10T00:00:00Z",
    tipo_instrumento: "TRD",
  };
  const REV_SIN_VIA = {
    id: "e-2",
    code: "EXP-2023-042",
    status: "transferred",
    trd_serie_id: null,
    trd_serie_code: null,
    trd_revision_motivo: "serie_no_resoluble",
    closed_at: null,
    tipo_instrumento: null,
  };

  async function abrirPestana() {
    await fireEvent.click(screen.getByRole("tab", { name: "Revisión pendiente" }));
  }

  function sesion() {
    initSession(
      sessionWith({ permissions: ["USUA_PERM_TRD"], permission_levels: { USUA_PERM_TRD: 3 } }),
    );
  }

  it("lista los expedientes marcados, con su motivo", async () => {
    sesion();
    render(TrdPage, {
      props: { data: baseData({ revision: [REV_RATIFICABLE], revisionError: null }) },
    });
    await abrirPestana();
    expect(screen.getByText("EXP-2024-001")).toBeInTheDocument();
    expect(screen.getAllByText("Cerrado bajo serie migrada").length).toBeGreaterThan(0);
  });

  it("distingue el motivo que el sistema resuelve del que no tiene vía automática", async () => {
    sesion();
    render(TrdPage, {
      props: { data: baseData({ revision: [REV_RATIFICABLE, REV_SIN_VIA], revisionError: null }) },
    });
    await abrirPestana();
    // El ratificable dice cómo se limpia; el otro declara que NO hay vía.
    expect(screen.getByText(/Se limpia solo al convalidar/i)).toBeInTheDocument();
    expect(screen.getAllByText(/No hay vía automática/i).length).toBeGreaterThan(0);
  });

  it("un fallo al cargar la cola no se muestra como 'nada pendiente'", async () => {
    sesion();
    render(TrdPage, {
      props: {
        data: baseData({ revision: [], revisionError: "No se pudo conectar al servidor." }),
      },
    });
    await abrirPestana();
    expect(screen.getByRole("alert")).toHaveTextContent("No se pudo conectar al servidor.");
    expect(screen.queryByText("Ningún expediente pendiente de revisión")).not.toBeInTheDocument();
  });

  it("cola vacía de verdad: se dice que no hay nada que revisar", async () => {
    sesion();
    render(TrdPage, { props: { data: baseData({ revision: [], revisionError: null }) } });
    await abrirPestana();
    expect(screen.getByText("Ningún expediente pendiente de revisión")).toBeInTheDocument();
  });
});
