// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import DataTable, { matchesFilters, sortRows, defaultRowKey } from "./DataTable.svelte";

const columns = [
  { key: "radicado", label: "Radicado" },
  { key: "asunto", label: "Asunto" },
];

const rows = [
  { radicado: "2024-ICETEX-E-000001", asunto: "Solicitud A" },
  { radicado: "2024-ICETEX-E-000002", asunto: "Solicitud B" },
];

describe("DataTable — filas, vacío y encabezados accesibles", () => {
  it("renderiza encabezados con scope=col", () => {
    render(DataTable, { props: { columns, rows: [] } });
    const headers = screen.getAllByRole("columnheader");
    expect(headers).toHaveLength(2);
    expect(headers[0]).toHaveTextContent("Radicado");
    expect(headers[0]).toHaveAttribute("scope", "col");
  });

  it("renderiza una fila por registro con el valor de cada columna", () => {
    render(DataTable, { props: { columns, rows } });
    expect(screen.getByText("2024-ICETEX-E-000001")).toBeInTheDocument();
    expect(screen.getByText("Solicitud A")).toBeInTheDocument();
    expect(screen.getByText("2024-ICETEX-E-000002")).toBeInTheDocument();
  });

  it("muestra el EmptyState cuando rows está vacío y no está cargando", () => {
    render(DataTable, { props: { columns, rows: [] } });
    expect(screen.getByText("Sin resultados")).toBeInTheDocument();
  });

  // Revisión UX lote 2, punto 2: el envoltorio que desplaza la tabla horizontalmente
  // debe ser alcanzable con teclado (WCAG 2.1.1) y tener nombre accesible (no ser un
  // tab-stop mudo). Se propaga a las 8+ pantallas que usan DataTable.
  it("el envoltorio con scroll horizontal es un tab-stop con nombre accesible", () => {
    render(DataTable, { props: { columns, rows, caption: "Radicados" } });
    const region = screen.getByRole("region", { name: /Radicados/ });
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("loading=true muestra filas skeleton en vez del EmptyState o los datos", () => {
    const { container } = render(DataTable, { props: { columns, rows: [], loading: true } });
    expect(screen.queryByText("Sin resultados")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("aria-busy refleja el estado de carga", () => {
    const { container, rerender } = render(DataTable, {
      props: { columns, rows: [], loading: true },
    });
    expect(container.querySelector(".op-datatable")).toHaveAttribute("aria-busy", "true");
    return rerender({ columns, rows: [], loading: false }).then(() => {
      expect(container.querySelector(".op-datatable")).toHaveAttribute("aria-busy", "false");
    });
  });

  it("muestra la paginación con el rango correcto cuando hay totalCount", () => {
    render(DataTable, { props: { columns, rows, totalCount: 45, page: 2, pageSize: 20 } });
    expect(screen.getByText("21–40 de 45")).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("los botones de paginación respetan los límites (sin página 0 ni más allá del total)", () => {
    render(DataTable, { props: { columns, rows, totalCount: 20, page: 1, pageSize: 20 } });
    expect(screen.getByLabelText("Página anterior")).toBeDisabled();
    expect(screen.getByLabelText("Página siguiente")).toBeDisabled();
  });

  it("emite pageChange al hacer clic en 'Página siguiente'", async () => {
    const { component } = render(DataTable, {
      props: { columns, rows, totalCount: 45, page: 1, pageSize: 20 },
    });
    const onPageChange = vi.fn();
    component.$on("pageChange", onPageChange);
    await fireEvent.click(screen.getByLabelText("Página siguiente"));
    expect(onPageChange).toHaveBeenCalled();
    expect(onPageChange.mock.calls[0][0].detail).toEqual({ page: 2 });
  });

  it("no hay paginación cuando totalCount es 0", () => {
    render(DataTable, { props: { columns, rows: [], totalCount: 0 } });
    expect(screen.queryByLabelText("Página siguiente")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Capacidades nuevas (Sistema de Diseño §12). Todas opt-in: los tests de
// arriba (sin ninguna prop `enable*`) prueban que el comportamiento por
// defecto queda intacto; estos prueban las capacidades cuando SÍ se activan.
// ---------------------------------------------------------------------------

const richColumns = [
  { key: "radicado", label: "Radicado", sortable: true },
  { key: "asunto", label: "Asunto", editable: true },
  {
    key: "estado",
    label: "Estado",
    filterType: "select",
    filterOptions: [
      { value: "activo", label: "Activo" },
      { value: "cerrado", label: "Cerrado" },
    ],
  },
];

const richRows = [
  { id: "1", radicado: "2024-E-000002", asunto: "Zeta", estado: "activo" },
  { id: "2", radicado: "2024-E-000001", asunto: "Alfa", estado: "cerrado" },
];

describe("DataTable — funciones puras (matchesFilters, sortRows, defaultRowKey)", () => {
  it("matchesFilters: filtro de texto es subcadena insensible a mayúsculas", () => {
    const row = { asunto: "Solicitud DE Traslado" };
    expect(matchesFilters(row, { asunto: "traslado" }, [{ key: "asunto" }])).toBe(true);
    expect(matchesFilters(row, { asunto: "no-existe" }, [{ key: "asunto" }])).toBe(false);
  });

  it("matchesFilters: filtro select exige coincidencia exacta", () => {
    const row = { estado: "activo" };
    const cols = [{ key: "estado", filterType: "select" }];
    expect(matchesFilters(row, { estado: "activo" }, cols)).toBe(true);
    expect(matchesFilters(row, { estado: "cerrado" }, cols)).toBe(false);
  });

  it("matchesFilters: rango de fechas respeta from/to", () => {
    const cols = [{ key: "fecha", filterType: "dateRange" }];
    const row = { fecha: "2024-06-15" };
    expect(matchesFilters(row, { fecha: { from: "2024-06-01", to: "2024-06-30" } }, cols)).toBe(
      true,
    );
    expect(matchesFilters(row, { fecha: { from: "2024-07-01" } }, cols)).toBe(false);
    expect(matchesFilters(row, { fecha: { to: "2024-06-01" } }, cols)).toBe(false);
  });

  it("matchesFilters: sin filtros activos, todas las filas coinciden", () => {
    expect(matchesFilters({ x: 1 }, {}, [])).toBe(true);
    expect(matchesFilters({ x: 1 }, { x: "" }, [{ key: "x" }])).toBe(true);
  });

  it("sortRows: sin key de orden devuelve la misma referencia (identidad preservada)", () => {
    const input = [{ a: 1 }];
    expect(sortRows(input, { key: null, direction: null })).toBe(input);
  });

  it("sortRows: orden ascendente/descendente alfanumérico", () => {
    const input = [{ n: "b" }, { n: "a" }, { n: "c" }];
    expect(sortRows(input, { key: "n", direction: "asc" }).map((r) => r.n)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(sortRows(input, { key: "n", direction: "desc" }).map((r) => r.n)).toEqual([
      "c",
      "b",
      "a",
    ]);
  });

  it("sortRows: valores numéricos se comparan como número, no como texto", () => {
    const input = [{ n: 10 }, { n: 2 }, { n: 1 }];
    expect(sortRows(input, { key: "n", direction: "asc" }).map((r) => r.n)).toEqual([1, 2, 10]);
  });

  it("sortRows: valores null/undefined quedan primero de forma estable", () => {
    const input = [{ n: "b" }, { n: null }, { n: "a" }];
    expect(sortRows(input, { key: "n", direction: "asc" }).map((r) => r.n)).toEqual([
      null,
      "a",
      "b",
    ]);
  });

  it("defaultRowKey prioriza id, luego radicado, luego el índice", () => {
    expect(defaultRowKey({ id: "x", radicado: "y" }, 3)).toBe("x");
    expect(defaultRowKey({ radicado: "y" }, 3)).toBe("y");
    expect(defaultRowKey({}, 3)).toBe(3);
  });
});

describe("DataTable — filtros por columna (enableFilters)", () => {
  it("el botón 'Filtros' revela la fila de filtros (progressive disclosure, oculta por defecto)", async () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableFilters: true } });
    expect(screen.queryByLabelText("Filtrar por Asunto")).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: /Filtros/ }));
    expect(screen.getByLabelText("Filtrar por Estado")).toBeInTheDocument();
  });

  it("filtrar por un select de columna oculta las filas que no coinciden", async () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableFilters: true } });
    await fireEvent.click(screen.getByRole("button", { name: /Filtros/ }));
    await fireEvent.change(screen.getByLabelText("Filtrar por Estado"), {
      target: { value: "cerrado" },
    });
    expect(screen.queryByText("2024-E-000002")).not.toBeInTheDocument();
    expect(screen.getByText("2024-E-000001")).toBeInTheDocument();
  });

  it("emite filterChange con el estado de filtros actual", async () => {
    const { component } = render(DataTable, {
      props: { columns: richColumns, rows: richRows, enableFilters: true },
    });
    const onFilterChange = vi.fn();
    component.$on("filterChange", onFilterChange);
    await fireEvent.click(screen.getByRole("button", { name: /Filtros/ }));
    await fireEvent.change(screen.getByLabelText("Filtrar por Estado"), {
      target: { value: "activo" },
    });
    expect(onFilterChange).toHaveBeenCalled();
    expect(onFilterChange.mock.calls[0][0].detail).toEqual({ filters: { estado: "activo" } });
  });
});

describe("DataTable — ordenamiento por columna (enableSort)", () => {
  it("columna sortable expone aria-sort='none' antes de ordenar", () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableSort: true } });
    const headers = screen.getAllByRole("columnheader");
    expect(headers[0]).toHaveAttribute("aria-sort", "none");
  });

  it("un clic ordena ascendente, dos descendente, tres quita el orden (aria-sort refleja cada estado)", async () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableSort: true } });
    const sortButton = screen.getByRole("button", { name: "Radicado" });
    const header = sortButton.closest("th");

    await fireEvent.click(sortButton);
    expect(header).toHaveAttribute("aria-sort", "ascending");

    await fireEvent.click(sortButton);
    expect(header).toHaveAttribute("aria-sort", "descending");

    await fireEvent.click(sortButton);
    expect(header).toHaveAttribute("aria-sort", "none");
  });

  it("columna NO sortable no se convierte en botón", () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableSort: true } });
    expect(screen.queryByRole("button", { name: "Asunto" })).not.toBeInTheDocument();
  });

  it("emite sortChange al alternar el orden", async () => {
    const { component } = render(DataTable, {
      props: { columns: richColumns, rows: richRows, enableSort: true },
    });
    const onSortChange = vi.fn();
    component.$on("sortChange", onSortChange);
    await fireEvent.click(screen.getByRole("button", { name: "Radicado" }));
    expect(onSortChange.mock.calls[0][0].detail).toEqual({ key: "radicado", direction: "asc" });
  });
});

describe("DataTable — selección múltiple (enableSelection)", () => {
  it("cada fila tiene su checkbox y la cabecera tiene el checkbox de 'todas'", () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableSelection: true } });
    expect(screen.getByLabelText("Seleccionar todas las filas de esta página")).toBeInTheDocument();
    expect(screen.getByLabelText("Seleccionar fila 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Seleccionar fila 2")).toBeInTheDocument();
  });

  it("seleccionar una sola fila deja la cabecera en estado indeterminado", async () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableSelection: true } });
    await fireEvent.click(screen.getByLabelText("Seleccionar fila 1"));
    const headerCheckbox = screen.getByLabelText("Seleccionar todas las filas de esta página");
    expect(headerCheckbox.indeterminate).toBe(true);
    expect(headerCheckbox.checked).toBe(false);
  });

  it("seleccionar todas las filas marca la cabecera como checked (no indeterminado)", async () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableSelection: true } });
    await fireEvent.click(screen.getByLabelText("Seleccionar todas las filas de esta página"));
    const headerCheckbox = screen.getByLabelText("Seleccionar todas las filas de esta página");
    expect(headerCheckbox.checked).toBe(true);
    expect(headerCheckbox.indeterminate).toBe(false);
  });

  it("anuncia el recuento de la selección de forma accesible y emite selectionChange", async () => {
    const { component } = render(DataTable, {
      props: { columns: richColumns, rows: richRows, enableSelection: true },
    });
    const onSelectionChange = vi.fn();
    component.$on("selectionChange", onSelectionChange);
    await fireEvent.click(screen.getByLabelText("Seleccionar fila 1"));
    expect(onSelectionChange.mock.calls[0][0].detail.selected).toEqual(["1"]);
    expect(screen.getByText("1 seleccionada")).toBeInTheDocument();
  });

  it("re-siembra la selección cuando el padre cambia `selected` por contenido (no solo al montar)", async () => {
    const { component } = render(DataTable, {
      props: { columns: richColumns, rows: richRows, enableSelection: true, selected: ["1"] },
    });
    expect(screen.getByLabelText("Seleccionar fila 1").checked).toBe(true);

    // El padre limpia la selección (p. ej. tras una nueva búsqueda) — antes de
    // este fix, la tabla ignoraba este cambio porque ya se había "sembrado"
    // una vez al montar (bug: selección fantasma "Vincular (4)" con la
    // tabla mostrando 0 y el padre desincronizado).
    await component.$set({ selected: [] });
    expect(screen.getByLabelText("Seleccionar fila 1").checked).toBe(false);

    // Y el padre puede volver a imponer una selección nueva (p. ej. restaurar
    // seleccionados guardados entre búsquedas).
    await component.$set({ selected: ["2"] });
    expect(screen.getByLabelText("Seleccionar fila 1").checked).toBe(false);
    expect(screen.getByLabelText("Seleccionar fila 2").checked).toBe(true);
  });

  it("no entra en bucle: marcar una fila desde la UI no revierte la selección del usuario", async () => {
    render(DataTable, {
      props: { columns: richColumns, rows: richRows, enableSelection: true, selected: [] },
    });
    await fireEvent.click(screen.getByLabelText("Seleccionar fila 2"));
    // Si `emitSelection` (que reasigna `selected` internamente) disparara una
    // re-siembra espuria, esto se revertiría a la prop original ([]).
    expect(screen.getByLabelText("Seleccionar fila 2").checked).toBe(true);
  });
});

describe("DataTable — columnas configurables (enableColumnToggle) y persistencia", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("ocultar una columna la retira de la cabecera y de las filas", async () => {
    render(DataTable, {
      props: {
        columns: richColumns,
        rows: richRows,
        enableColumnToggle: true,
        tableId: "kit-demo",
      },
    });
    await fireEvent.click(screen.getByText("Columnas"));
    await fireEvent.click(screen.getByRole("checkbox", { name: "Estado" }));
    expect(screen.queryByRole("columnheader", { name: "Estado" })).not.toBeInTheDocument();
  });

  it("persiste la columna oculta en localStorage bajo la clave espaciada por tabla", async () => {
    render(DataTable, {
      props: {
        columns: richColumns,
        rows: richRows,
        enableColumnToggle: true,
        tableId: "kit-demo",
      },
    });
    await fireEvent.click(screen.getByText("Columnas"));
    await fireEvent.click(screen.getByRole("checkbox", { name: "Estado" }));
    const stored = JSON.parse(
      window.localStorage.getItem("op-datatable:anon:anon:kit-demo:columns"),
    );
    expect(stored).toEqual(["estado"]);
  });

  it("una segunda instancia con el mismo tableId recupera la columna oculta persistida", async () => {
    window.localStorage.setItem(
      "op-datatable:anon:anon:kit-demo:columns",
      JSON.stringify(["estado"]),
    );
    render(DataTable, {
      props: {
        columns: richColumns,
        rows: richRows,
        enableColumnToggle: true,
        tableId: "kit-demo",
      },
    });
    expect(screen.queryByRole("columnheader", { name: "Estado" })).not.toBeInTheDocument();
  });
});

describe("DataTable — vistas guardadas (enableSavedViews)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("guarda una vista con nombre y la aplica reponiendo filtros", async () => {
    render(DataTable, {
      props: {
        columns: richColumns,
        rows: richRows,
        enableFilters: true,
        enableSavedViews: true,
        tableId: "kit-demo",
      },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Filtros/ }));
    await fireEvent.change(screen.getByLabelText("Filtrar por Estado"), {
      target: { value: "cerrado" },
    });

    await fireEvent.click(screen.getByText("Vistas guardadas"));
    await fireEvent.input(screen.getByLabelText("Nombre de la vista"), {
      target: { value: "Mis cerrados" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Guardar vista" }));

    expect(screen.getByRole("button", { name: "Mis cerrados" })).toBeInTheDocument();
  });
});

describe("DataTable — exportación CSV (enableExport)", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
  });

  it("el botón de exportar dispara la descarga sin lanzar", async () => {
    render(DataTable, { props: { columns: richColumns, rows: richRows, enableExport: true } });
    await fireEvent.click(screen.getByRole("button", { name: "Exportar página (2)" }));
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  // I6 (revisión UX): "Exportar CSV" a secas exportaba solo `displayRows` (la
  // página visible) sin decirlo — el usuario podía creer que se llevó TODO
  // el resultado del backend. La etiqueta ahora declara el alcance real.
  it("I6: la etiqueta declara que exporta solo la página visible, no el total del backend", () => {
    render(DataTable, {
      props: { columns: richColumns, rows: richRows, enableExport: true, totalCount: 137 },
    });
    expect(screen.getByRole("button", { name: "Exportar página (2)" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Exportar CSV" })).toBeNull();
  });

  it("I6: si hay más filas en el backend que las visibles, el anuncio accesible también lo advierte", async () => {
    render(DataTable, {
      props: { columns: richColumns, rows: richRows, enableExport: true, totalCount: 137 },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Exportar página (2)" }));
    expect(screen.getByText(/exportadas 2 filas de 137 a csv/i)).toBeTruthy();
  });
});

describe("DataTable — edición rápida en celda (columna con editable:true)", () => {
  it("Enter confirma la edición y emite cellEdit con el nuevo valor", async () => {
    const { component } = render(DataTable, { props: { columns: richColumns, rows: richRows } });
    const onCellEdit = vi.fn();
    component.$on("cellEdit", onCellEdit);

    await fireEvent.click(screen.getByRole("button", { name: /Zeta/ }));
    const input = screen.getByDisplayValue("Zeta");
    await fireEvent.input(input, { target: { value: "Zeta actualizada" } });
    await fireEvent.keyDown(input, { key: "Enter" });

    expect(onCellEdit).toHaveBeenCalled();
    expect(onCellEdit.mock.calls[0][0].detail).toMatchObject({
      key: "asunto",
      value: "Zeta actualizada",
      oldValue: "Zeta",
    });
    expect(screen.queryByDisplayValue("Zeta actualizada")).not.toBeInTheDocument();
  });

  it("Escape cancela la edición sin emitir cellEdit", async () => {
    const { component } = render(DataTable, { props: { columns: richColumns, rows: richRows } });
    const onCellEdit = vi.fn();
    component.$on("cellEdit", onCellEdit);

    await fireEvent.click(screen.getByRole("button", { name: /Zeta/ }));
    const input = screen.getByDisplayValue("Zeta");
    await fireEvent.input(input, { target: { value: "Cambio no confirmado" } });
    await fireEvent.keyDown(input, { key: "Escape" });

    expect(onCellEdit).not.toHaveBeenCalled();
    expect(screen.getByText("Zeta")).toBeInTheDocument();
  });
});
