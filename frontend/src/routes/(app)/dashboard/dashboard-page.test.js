// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * dashboard-page.test.js — Render de /dashboard (+page.svelte, T-07, §10).
 *
 * `$app/stores` se mockea (patrón estándar del proyecto, ver SidebarNav.test.js
 * / expedientes-page.test.js). Cubre:
 *  - Un solo <h1>, los cuatro módulos siempre presentes con su heading propio.
 *  - Estados vacíos explícitos por módulo (pendientes/alertas/recientes/favoritos).
 *  - Jerarquía de alertas: vencido vs próximo llevan icono + texto, no solo color.
 *  - Favoritos reutiliza pickFavoriteNavItems/navItems — respeta permisos.
 *  - La sección de métricas agregadas está colapsada por defecto (progressive
 *    disclosure) y se expande al activar el botón toggle.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { writable } from "svelte/store";

const navigatingStore = writable(null);

vi.mock("$app/environment", () => ({ browser: true }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/dashboard") });
  return { page, navigating: navigatingStore, updated: writable(false) };
});

const { default: DashboardPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");
const { resetFavorites, toggleFavorite, initFavorites } = await import("$lib/stores/favorites.js");

function sessionWith({ roles = [], permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operadora",
    preferred_username: "operadora",
    roles,
    tenant_slug: "icetex",
    permissions,
  };
}

const SUMMARY = {
  total: 10,
  by_doc_type: { E: 6, S: 3, I: 1 },
  by_status: { registered: 2, distributed: 3, in_progress: 4, closed: 1 },
  by_month: [{ month: "2026-01", count: 10 }],
  by_dependencia: { Tesorería: 4 },
};

/** @param {Partial<import('./$types').PageData>} overrides */
function baseData(overrides = {}) {
  return {
    pendientes: { items: [], total: 0, error: null },
    alertas: { items: [], error: null },
    recientes: { items: [], error: null },
    summary: SUMMARY,
    summaryError: null,
    ...overrides,
  };
}

function makeDoc(overrides = {}) {
  return {
    id: "doc-1",
    tracking_number: "2026-ICETEX-E-000001",
    doc_type: "E",
    subject: "Contrato de arrendamiento",
    status: "distributed",
    registered_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    due_date: null,
    ...overrides,
  };
}

describe("/dashboard — estructura y jerarquía de encabezados", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
    navigatingStore.set(null);
  });

  it("expone exactamente un <h1> y los cuatro módulos con su propio <h2>", () => {
    initSession(sessionWith());
    render(DashboardPage, { data: baseData() });

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      screen.getByRole("heading", { level: 2, name: /Radicados pendientes/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /Actividad reciente/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /Alertas/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /Favoritos/ })).toBeInTheDocument();
  });
});

describe("/dashboard — módulo 01 Radicados pendientes", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
  });

  it("estado vacío: comunica que el usuario va al día", () => {
    initSession(sessionWith());
    render(DashboardPage, { data: baseData() });

    expect(screen.getByText("Vas al día")).toBeInTheDocument();
  });

  it("con pendientes, muestra el número de radicado y el total como badge", () => {
    initSession(sessionWith());
    render(DashboardPage, {
      data: baseData({
        pendientes: {
          items: [makeDoc({ id: "d1", tracking_number: "2026-ICETEX-E-000042" })],
          total: 7,
          error: null,
        },
      }),
    });

    expect(screen.getByText("2026-ICETEX-E-000042")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("error de carga: EmptyState con el mensaje del backend", () => {
    initSession(sessionWith());
    render(DashboardPage, {
      data: baseData({ pendientes: { items: [], total: 0, error: "Sin permisos" } }),
    });

    expect(screen.getByText("Sin permisos")).toBeInTheDocument();
  });
});

describe("/dashboard — módulo 03 Alertas: jerarquía por icono + texto, no solo color", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
  });

  it("estado vacío: sin vencimientos próximos", () => {
    initSession(sessionWith());
    render(DashboardPage, { data: baseData() });

    expect(screen.getByText("Sin vencimientos próximos")).toBeInTheDocument();
  });

  it("un radicado vencido muestra texto explícito 'Vencido hace' (no solo un color)", () => {
    initSession(sessionWith());
    render(DashboardPage, {
      data: baseData({
        alertas: {
          items: [makeDoc({ id: "a1", diasRestantes: -3 })],
          error: null,
        },
      }),
    });

    expect(screen.getByText(/Vencido hace 3 días/)).toBeInTheDocument();
  });

  it("un radicado próximo a vencer muestra 'Vence en N días'", () => {
    initSession(sessionWith());
    render(DashboardPage, {
      data: baseData({
        alertas: { items: [makeDoc({ id: "a2", diasRestantes: 2 })], error: null },
      }),
    });

    expect(screen.getByText(/Vence en 2 días/)).toBeInTheDocument();
  });
});

describe("/dashboard — módulo 02 Actividad reciente", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
  });

  it("estado vacío informativo", () => {
    initSession(sessionWith());
    render(DashboardPage, { data: baseData() });

    expect(screen.getByText("Sin actividad reciente")).toBeInTheDocument();
  });

  it("con actividad, muestra el radicado más reciente", () => {
    initSession(sessionWith());
    render(DashboardPage, {
      data: baseData({
        recientes: { items: [makeDoc({ id: "r1", status: "closed" })], error: null },
      }),
    });

    expect(screen.getByText("2026-ICETEX-E-000001")).toBeInTheDocument();
  });
});

describe("/dashboard — módulo 04 Favoritos (reutiliza favorites.js + navItems.js)", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
  });

  it("sin favoritos fijados, muestra el estado vacío con instrucciones", () => {
    initSession(sessionWith({ permissions: ["PERM_RADI"] }));
    render(DashboardPage, { data: baseData() });

    expect(screen.getByText("Sin accesos fijados")).toBeInTheDocument();
  });

  it("un favorito fijado y autorizado aparece como acceso directo", () => {
    initFavorites("icetex", "u-1");
    toggleFavorite("/bandeja");
    initSession(sessionWith({ permissions: ["PERM_RADI"] }));

    render(DashboardPage, { data: baseData() });

    expect(screen.getByRole("link", { name: "Bandeja" })).toHaveAttribute("href", "/bandeja");
  });

  it("REGLA DE SEGURIDAD: un favorito huérfano (permiso perdido) no se muestra", () => {
    initFavorites("icetex", "u-1");
    toggleFavorite("/firmas");
    initSession(sessionWith({ permissions: ["PERM_RADI"] })); // sin PERM_FIRMA

    render(DashboardPage, { data: baseData() });

    expect(screen.queryByRole("link", { name: "Firmas" })).not.toBeInTheDocument();
    expect(screen.getByText("Sin accesos fijados")).toBeInTheDocument();
  });
});

describe("/dashboard — sección secundaria de métricas (progressive disclosure)", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
  });

  it("está colapsada por defecto: el total agregado no es visible sin interacción", () => {
    initSession(sessionWith({ permissions: ["SGD_PERM_ESTADISTICA"] }));
    render(DashboardPage, { data: baseData() });

    const toggle = screen.getByRole("button", { name: /Métricas del tenant/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Total radicados")).not.toBeInTheDocument();
  });

  it("al expandir, muestra el total agregado y el desglose por estado", async () => {
    initSession(sessionWith({ permissions: ["SGD_PERM_ESTADISTICA"] }));
    render(DashboardPage, { data: baseData() });

    await fireEvent.click(screen.getByRole("button", { name: /Métricas del tenant/ }));

    expect(screen.getByText("Total radicados")).toBeInTheDocument();
    expect(screen.getByText("Radicados por estado")).toBeInTheDocument();
  });

  it("con error de reportes, muestra el EmptyState de error dentro del panel expandido", async () => {
    initSession(sessionWith({ permissions: ["SGD_PERM_ESTADISTICA"] }));
    render(DashboardPage, {
      data: baseData({ summary: null, summaryError: "Error 500" }),
    });

    await fireEvent.click(screen.getByRole("button", { name: /Métricas del tenant/ }));

    expect(screen.getByText("Error 500")).toBeInTheDocument();
  });

  // Espejo del RBAC (ADR-013/020): mismo permiso que gatea la entrada de menú
  // "Reportes" — la UI no debe ofrecer un panel que el gateway rechazaría con
  // 403 en GET /reports/radicados.
  it("sin SGD_PERM_ESTADISTICA, la sección de métricas ni siquiera se renderiza", () => {
    initSession(sessionWith()); // sin permisos
    render(DashboardPage, { data: baseData() });

    expect(screen.queryByRole("button", { name: /Métricas del tenant/ })).not.toBeInTheDocument();
  });
});

describe("/dashboard — el panel de métricas que su botón dice controlar existe", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
    navigatingStore.set(null);
  });

  it("plegado: el panel sigue en el árbol, oculto — no desaparece", () => {
    // El botón declara `aria-controls="metrics-panel"`. Cuando el panel solo
    // existía desplegado, ese identificador no resolvía: el lector anunciaba un
    // control que gobierna algo que no puede encontrar, y quien navega con
    // teclado no tenía forma de saltar a lo que acababa de abrir.
    // La sección entera es espejo del RBAC: sin este permiso el gateway
    // rechazaría el informe con 403 y la UI no la ofrece.
    initSession(sessionWith({ permissions: ["SGD_PERM_ESTADISTICA"] }));
    render(DashboardPage, { data: baseData() });

    const boton = screen.getByRole("button", { name: /Métricas del tenant/ });
    expect(boton).toHaveAttribute("aria-controls", "metrics-panel");
    expect(boton).toHaveAttribute("aria-expanded", "false");

    const panel = document.getElementById("metrics-panel");
    expect(panel).not.toBeNull();
    // Y oculto de verdad: `hidden` lo saca del árbol de accesibilidad, así que
    // estar presente no lo hace anunciable mientras está plegado.
    expect(panel).toHaveAttribute("hidden");
  });
});

describe("/dashboard — los indicadores clave no mienten sobre su semántica", () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
    resetFavorites();
    navigatingStore.set(null);
  });

  it("la fila de KPIs es un grupo con nombre, no una lista sin elementos", async () => {
    // Llevaba `role="list"` y sus hijos son `Card`, que renderiza un `<div>` sin
    // rol: un lector anuncia una lista y después no encuentra sus elementos, así
    // que no se puede navegar por ella. Peor que no declarar nada.
    initSession(sessionWith({ permissions: ["SGD_PERM_ESTADISTICA"] }));
    render(DashboardPage, { data: baseData({ summary: SUMMARY }) });
    // El panel arranca plegado —y desde ahora sigue en el árbol, oculto—, así
    // que los KPIs solo se pintan al desplegarlo.
    await fireEvent.click(screen.getByRole("button", { name: /Métricas del tenant/ }));

    expect(screen.queryByRole("list", { name: /Indicadores clave/ })).toBeNull();
    expect(screen.getByRole("group", { name: /Indicadores clave/ })).toBeInTheDocument();
  });
});
