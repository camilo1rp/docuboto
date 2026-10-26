// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import Stepper from "./Stepper.svelte";

const steps = ["Tipo documental", "Metadatos", "Anexos", "Confirmación"];

describe("Stepper — pasos guiados (radicación)", () => {
  it("marca el paso activo con aria-current=step", () => {
    render(Stepper, { props: { steps, current: 1 } });
    const active = screen.getByRole("button", { name: "Metadatos" });
    expect(active).toHaveAttribute("aria-current", "step");
  });

  it("los pasos completados se marcan como '(completado)' para lectores de pantalla", () => {
    render(Stepper, { props: { steps, current: 2 } });
    expect(
      screen.getByRole("button", { name: "Tipo documental (completado)" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Metadatos (completado)" })).toBeInTheDocument();
  });

  it("los pasos futuros están deshabilitados (no se puede saltar adelante)", () => {
    render(Stepper, { props: { steps, current: 0 } });
    expect(screen.getByRole("button", { name: "Metadatos" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Anexos" })).toBeDisabled();
  });

  it("clic en un paso completado (canGoBack=true, default) retrocede y emite stepChange", async () => {
    const { component } = render(Stepper, { props: { steps, current: 2 } });
    const onStepChange = vi.fn();
    component.$on("stepChange", onStepChange);

    await fireEvent.click(screen.getByRole("button", { name: "Tipo documental (completado)" }));

    expect(onStepChange).toHaveBeenCalledOnce();
    expect(onStepChange.mock.calls[0][0].detail).toEqual({ step: 0 });
  });

  it("canGoBack=false deshabilita los pasos anteriores (no se puede retroceder)", () => {
    render(Stepper, { props: { steps, current: 2, canGoBack: false } });
    expect(screen.getByRole("button", { name: "Tipo documental (completado)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Metadatos (completado)" })).toBeDisabled();
  });

  it("nav tiene aria-label descriptivo y la lista usa semántica de lista", () => {
    render(Stepper, { props: { steps, current: 0 } });
    expect(screen.getByRole("navigation", { name: "Pasos del proceso" })).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
  });
});
