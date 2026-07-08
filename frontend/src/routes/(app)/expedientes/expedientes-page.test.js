// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expedientes-page.test.js — Render de /expedientes (+page.svelte, T-12).
 *
 * `$app/navigation`, `$app/forms` y `$app/stores` se mockean porque el
 * componente se monta aislado (sin el router real de SvelteKit) — mismo
 * patrón que SidebarNav.test.js. Los permisos se inyectan vía
 * initSession/clearSession (store real de $lib/stores/auth.js), igual que
 * el resto de la suite de RBAC del front.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { writable } from "svelte/store";

const gotoMock = vi.fn();
const invalidateAllMock = vi.fn();

vi.mock("$app/navigation", () => ({ goto: gotoMock, invalidateAll: invalidateAllMock }));
vi.mock("$app/forms", () => ({
  // Acción no-op: no probamos el envío real del <form>, solo el gating/render.
  enhance: () => () => ({ destroy() {} }),
}));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/expedientes") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: ExpedientesPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

/** @param {Partial<import('./$types').PageData>} overrides */
function baseData(overrides = {}) {
  return {
    expedientes: [],
    total: 0,
    loadError: null,
    initialFilters: { q: "", status: "", dateFrom: "", dateTo: "", page: 1 },
    ...overrides,
  };
}

const HIT_1 = {
  id: "exp-1",
  code: "2026-ICETEX-EXP-000001",
  name: "Contrato de arrendamiento sede norte",
  status: "open",
  opened_at: "2026-01-15T10:00:00Z",
  rank: 0.9,
};
const HIT_2 = {
  id: "exp-2",
  code: "2026-ICETEX-EXP-000002",
  name: "Proceso disciplinario 2026-04",
  status: "closed",
  opened_at: "2026-02-01T08:30:00Z",
  rank: 0.7,
};

describe("/expedientes — gate del botón Crear expediente por permiso (USUA_PERM_EXPEDIENTE)", () => {
  beforeEach(() => {
    clearSession();
    gotoMock.mockClear();
  });
  afterEach(() => clearSession());

  it("sin USUA_PERM_EXPEDIENTE: no se ve el botón Crear expediente", () => {
    initSession(sessionWith({ permissions: [] }));
    render(ExpedientesPage, { props: { data: baseData({ expedientes: [HIT_1] }), form: null } });

    expect(screen.queryByRole("button", { name: "Crear expediente" })).not.toBeInTheDocument();
  });

  it("con USUA_PERM_EXPEDIENTE: se ve el botón Crear expediente", () => {
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE"] }));
    render(ExpedientesPage, { props: { data: baseData({ expedientes: [HIT_1] }), form: null } });

    expect(screen.getAllByRole("button", { name: "Crear expediente" }).length).toBeGreaterThan(0);
  });
});

describe("/expedientes — estados de la vista", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE"] }));
    gotoMock.mockClear();
    invalidateAllMock.mockClear();
  });
  afterEach(() => clearSession());

  it("estado vacío sin filtros activos: 'Sin expedientes' + CTA de creación (canManage)", () => {
    render(ExpedientesPage, { props: { data: baseData(), form: null } });

    expect(screen.getByText("Sin expedientes")).toBeInTheDocument();
    expect(screen.getByText(/Aún no se han creado expedientes/i)).toBeInTheDocument();
  });

  it("estado vacío con filtros activos: 'Sin resultados' + Limpiar filtros (sin CTA de creación)", () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({
          initialFilters: { q: "no-existe", status: "", dateFrom: "", dateTo: "", page: 1 },
        }),
        form: null,
      },
    });

    expect(screen.getByText("Sin resultados")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Limpiar filtros" })).toBeInTheDocument();
  });

  it("Limpiar filtros navega a /expedientes sin query params (vía goto)", async () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({
          initialFilters: { q: "no-existe", status: "", dateFrom: "", dateTo: "", page: 1 },
        }),
        form: null,
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));

    expect(gotoMock).toHaveBeenCalledWith("/expedientes", { keepFocus: true });
  });

  it("estado de error: EmptyState de error con botón Reintentar", () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({ loadError: "No se pudo conectar al servidor de expedientes." }),
        form: null,
      },
    });

    expect(screen.getByText("Error al cargar los expedientes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });

  it("Reintentar llama invalidateAll (M-1): goto() con la MISMA URL sería un no-op en SvelteKit 2, invalidateAll fuerza el re-fetch del load", async () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({
          loadError: "No se pudo conectar al servidor de expedientes.",
          initialFilters: { q: "contrato", status: "open", dateFrom: "", dateTo: "", page: 2 },
        }),
        form: null,
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));

    expect(invalidateAllMock).toHaveBeenCalledOnce();
    expect(gotoMock).not.toHaveBeenCalled();
  });

  it("con resultados: renderiza filas con código, nombre, estado y enlace Abrir", () => {
    render(ExpedientesPage, {
      props: { data: baseData({ expedientes: [HIT_1, HIT_2], total: 2 }), form: null },
    });

    expect(screen.getByText(HIT_1.code)).toBeInTheDocument();
    expect(screen.getByText(HIT_1.name)).toBeInTheDocument();
    expect(screen.getByText(HIT_2.name)).toBeInTheDocument();

    const abrirLinks = screen.getAllByRole("link", { name: /^Abrir expediente/ });
    expect(abrirLinks).toHaveLength(2);
    expect(abrirLinks[0]).toHaveAttribute("href", `/expedientes/${HIT_1.id}`);
    expect(abrirLinks[1]).toHaveAttribute("href", `/expedientes/${HIT_2.id}`);
  });

  it("NO renderiza acciones de cerrar/transferir en el listado (solo Abrir)", () => {
    render(ExpedientesPage, {
      props: { data: baseData({ expedientes: [HIT_1], total: 1 }), form: null },
    });

    // Sin filtro de `role`: la fila de acciones es un <a> (role="link"), no
    // un <button> — filtrar por role dejaría pasar una regresión que
    // añadiera "Cerrar"/"Transferir" como enlace en vez de botón (M-5).
    expect(screen.queryByText(/cerrar/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/transferir/i)).not.toBeInTheDocument();
  });

  it("NO muestra el nivel de seguridad en el listado", () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({ expedientes: [{ ...HIT_1, nivel_seguridad: 2 }], total: 1 }),
        form: null,
      },
    });

    // Aserta sobre las cabeceras reales de la tabla: buscar la ETIQUETA
    // "nivel de seguridad" nunca aparecería aunque se renderizara el valor
    // crudo (`2`) en una columna — esa era la aserción vacua original (M-5).
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers.some((h) => /nivel|seguridad/i.test(h))).toBe(false);
  });
});

describe("/expedientes — formulario de creación muestra el error del action", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE"] }));
  });
  afterEach(() => clearSession());

  it("form.error truthy reabre/mantiene el modal con el detail visible", () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({ expedientes: [HIT_1], total: 1 }),
        form: { error: "validation_error", detail: "El nombre del expediente es obligatorio." },
      },
    });

    expect(screen.getByRole("alert")).toHaveTextContent("El nombre del expediente es obligatorio.");
  });

  it("un error validation_error se asocia al campo Nombre (aria-invalid, M-7)", () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({ expedientes: [HIT_1], total: 1 }),
        form: { error: "validation_error", detail: "El nombre del expediente es obligatorio." },
      },
    });

    const nameInput = screen.getByLabelText("Nombre", { exact: false });
    expect(nameInput).toHaveAttribute("aria-invalid", "true");
    expect(nameInput.getAttribute("aria-describedby")).toContain("create-name-error");
  });

  it("repuebla el formulario con los `values` que devuelve fail() tras un error (M-7)", () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({ expedientes: [HIT_1], total: 1 }),
        form: {
          error: "validation_error",
          detail: "El nombre no puede superar los 500 caracteres.",
          values: {
            name: "x".repeat(501),
            description: "Descripción de prueba",
            trdSerieId: "11111111-1111-1111-1111-111111111111",
          },
        },
      },
    });

    expect(screen.getByLabelText("Nombre", { exact: false })).toHaveValue("x".repeat(501));
    expect(screen.getByLabelText("Descripción (opcional)")).toHaveValue("Descripción de prueba");
    expect(screen.getByLabelText("Serie TRD (opcional)")).toHaveValue(
      "11111111-1111-1111-1111-111111111111",
    );
  });

  it("un error no atribuible a un campo (upstream_error) NO se pisa en el campo Nombre, se muestra en el aviso general", () => {
    render(ExpedientesPage, {
      props: {
        data: baseData({ expedientes: [HIT_1], total: 1 }),
        form: { error: "upstream_error", detail: "Error 500 al crear el expediente." },
      },
    });

    expect(screen.getByLabelText("Nombre", { exact: false })).toHaveAttribute(
      "aria-invalid",
      "false",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Error 500 al crear el expediente.");
  });
});

describe("/expedientes — modal de creación: `name` de los campos coincide con actions.create", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE"] }));
  });
  afterEach(() => clearSession());

  it("Nombre/Descripción/Serie TRD tienen el `name` que lee el FormData del action (evita romper el submit al tocar FormField)", async () => {
    render(ExpedientesPage, {
      props: { data: baseData({ expedientes: [HIT_1], total: 1 }), form: null },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Crear expediente" }));

    expect(screen.getByLabelText("Nombre", { exact: false })).toHaveAttribute("name", "name");
    expect(screen.getByLabelText("Descripción (opcional)")).toHaveAttribute("name", "description");
    expect(screen.getByLabelText("Serie TRD (opcional)")).toHaveAttribute("name", "trd_serie_id");
  });
});
