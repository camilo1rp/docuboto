// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Modal.test.js — Foco accesible del diálogo modal (frontend/CLAUDE.md: focus
 * trap + Escape + retorno de foco, "toda acción de escritura exige
 * confirmación explícita").
 *
 * Usa ModalHarness.svelte (fixture) para poder ejercer el patrón real: un
 * botón disparador fuera del modal + varios elementos enfocables dentro
 * (incluido un footer de confirmación), igual que en las páginas reales
 * (radicar, transferencias, bandeja).
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import ModalHarness from "./__fixtures__/ModalHarness.svelte";

describe("Modal — accesibilidad del diálogo (foco, Escape, confirmación)", () => {
  it("role=dialog/aria-modal solo aparece tras abrir, no antes", () => {
    render(ModalHarness, { props: { open: false } });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("al abrir, el foco entra al modal (foco inicial en el botón de cerrar)", async () => {
    const { component } = render(ModalHarness, { props: { open: false } });
    await component.$set({ open: true });
    await tick();

    const dialog = screen.getByRole("dialog", { name: "Confirmar acción" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cerrar" }));
  });

  it("Tab cicla el foco dentro del modal (trampa de foco: del último elemento vuelve al primero)", async () => {
    const { component } = render(ModalHarness, { props: { open: true } });
    await tick();

    const closeBtn = screen.getByRole("button", { name: "Cerrar" });
    const confirmBtn = screen.getByTestId("confirm");

    confirmBtn.focus();
    expect(document.activeElement).toBe(confirmBtn);

    await fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(closeBtn);

    // Shift+Tab desde el primero vuelve al último
    await fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirmBtn);

    // component solo se usa para forzar el montaje reactivo arriba
    expect(component).toBeTruthy();
  });

  it("Escape cierra el modal y devuelve el foco al disparador externo", async () => {
    const { component, rerender } = render(ModalHarness, { props: { open: false } });
    const trigger = screen.getByTestId("trigger");
    trigger.focus();

    await component.$set({ open: true });
    await tick();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await fireEvent.keyDown(document, { key: "Escape" });
    await tick();
    await tick();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger);
    // evita warning de variable no usada si rerender no se necesita más
    expect(rerender).toBeTruthy();
  });
});

describe("Modal — acción de escritura: confirmación explícita antes de proceder", () => {
  it("el botón de confirmación vive dentro del diálogo (role=dialog) — no hay atajo fuera de él", async () => {
    const { component } = render(ModalHarness, { props: { open: false } });
    // Antes de abrir, la acción de confirmación ni siquiera existe en el DOM.
    expect(screen.queryByTestId("confirm")).not.toBeInTheDocument();

    await component.$set({ open: true });
    await tick();

    const dialog = screen.getByRole("dialog", { name: "Confirmar acción" });
    const confirmBtn = screen.getByTestId("confirm");
    // La acción de escritura está anidada dentro del diálogo de confirmación.
    expect(dialog).toContainElement(confirmBtn);
  });
});
