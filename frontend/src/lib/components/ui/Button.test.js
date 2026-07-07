// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Button.test.js — usa ButtonHarness.svelte (fixture) para poder pasar el
 * slot por defecto: @testing-library/svelte no ofrece una opción `slots`
 * para componentes Svelte 4/legacy en esta versión, así que el harness
 * inyecta el label como children y reenvía el resto de props/eventos.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import ButtonHarness from "./__fixtures__/ButtonHarness.svelte";

describe("Button — variantes y estados", () => {
  it("renderiza como <button> por defecto, con label y variante primary", () => {
    render(ButtonHarness, { props: { label: "Radicar", variant: "primary" } });
    const btn = screen.getByRole("button", { name: "Radicar" });
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveClass("op-btn--primary");
    expect(btn).not.toBeDisabled();
  });

  it("aplica la clase de variante y tamaño solicitados", () => {
    render(ButtonHarness, { props: { label: "Anular", variant: "danger", size: "lg" } });
    const btn = screen.getByRole("button", { name: "Anular" });
    expect(btn).toHaveClass("op-btn--danger");
    expect(btn).toHaveClass("op-btn--lg");
  });

  it("disabled: deshabilita el control y marca aria-disabled", () => {
    render(ButtonHarness, { props: { label: "Guardar", disabled: true } });
    const btn = screen.getByRole("button", { name: "Guardar" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-disabled", "true");
  });

  it("loading: aria-busy=true, deshabilita el control y muestra el spinner (no el icono)", () => {
    render(ButtonHarness, {
      props: { label: "Guardar", loading: true, icon: "fas fa-save" },
    });
    const btn = screen.getByRole("button", { name: "Guardar" });
    expect(btn).toHaveAttribute("aria-busy", "true");
    expect(btn).toBeDisabled();
    expect(btn.querySelector(".op-btn__spinner")).toBeInTheDocument();
    expect(btn.querySelector(".fa-save")).not.toBeInTheDocument();
  });

  it("emite el evento click cuando está habilitado", async () => {
    const handleClick = vi.fn();
    const { component } = render(ButtonHarness, { props: { label: "Radicar" } });
    component.$on("click", handleClick);
    await fireEvent.click(screen.getByRole("button", { name: "Radicar" }));
    expect(handleClick).toHaveBeenCalledOnce();
  });

  it("href: renderiza como enlace con role=button y el href indicado", () => {
    render(ButtonHarness, { props: { label: "Ir a radicar", href: "/radicar" } });
    const link = screen.getByRole("button", { name: "Ir a radicar" });
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/radicar");
  });

  it("href + disabled: cae a <button> disabled (no se puede navegar)", () => {
    render(ButtonHarness, {
      props: { label: "Ir a radicar", href: "/radicar", disabled: true },
    });
    const btn = screen.getByRole("button", { name: "Ir a radicar" });
    expect(btn.tagName).toBe("BUTTON");
    expect(btn).toBeDisabled();
  });
});
