// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import DataTable from "./DataTable.svelte";

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
