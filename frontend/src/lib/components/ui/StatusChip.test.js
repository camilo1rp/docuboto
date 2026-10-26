// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import StatusChip from "./StatusChip.svelte";

describe("StatusChip — mapeo de estados", () => {
  it.each([
    ["active", "success"],
    ["open", "success"],
    ["success", "success"],
    ["pending", "warning"],
    ["warning", "warning"],
    ["closed", "error"],
    ["error", "error"],
    ["info", "info"],
    ["neutral", "neutral"],
  ])("status=%s → variante visual %s", (status, variant) => {
    render(StatusChip, { props: { status } });
    const chip = screen.getByText(`${status.charAt(0).toUpperCase()}${status.slice(1)}`);
    expect(chip).toHaveClass(`op-status-chip--${variant}`);
  });

  it("un status desconocido cae a la variante neutral (sin romper el render)", () => {
    render(StatusChip, { props: { status: "algo_no_mapeado" } });
    const chip = screen.getByText("Algo_no_mapeado");
    expect(chip).toHaveClass("op-status-chip--neutral");
  });

  it("usa el label explícito si se provee, en vez de capitalizar el status", () => {
    render(StatusChip, { props: { status: "closed", label: "Expediente cerrado" } });
    const chip = screen.getByText("Expediente cerrado");
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveClass("op-status-chip--error");
  });

  it("dot=true agrega el indicador visual de punto", () => {
    render(StatusChip, { props: { status: "active", dot: true } });
    const chip = screen.getByText("Active");
    expect(chip.querySelector(".op-status-chip__dot")).toBeInTheDocument();
  });

  it("dot=false (default) no agrega el punto", () => {
    render(StatusChip, { props: { status: "active" } });
    const chip = screen.getByText("Active");
    expect(chip.querySelector(".op-status-chip__dot")).not.toBeInTheDocument();
  });

  // Menor (revisión UX): `aria-label` sobre un <span> con rol implícito
  // `generic` está prohibido por ARIA 1.2 y los lectores de pantalla lo
  // ignoran — regresión: el nombre accesible debe venir del CONTENIDO
  // visible, sin ningún `aria-label` adicional en el chip.
  it("no expone aria-label (rol implícito generic; el nombre accesible viene del contenido visible)", () => {
    render(StatusChip, { props: { status: "active", label: "Radicado activo" } });
    const chip = screen.getByText("Radicado activo");
    expect(chip).not.toHaveAttribute("aria-label");
  });
});
