// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Drawer.test.js — Foco accesible del panel lateral (frontend/CLAUDE.md:
 * focus trap + Escape + retorno de foco en Drawer/Modal). Mismo patrón de
 * pruebas que Modal.test.js, vía DrawerHarness.svelte (fixture).
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import DrawerHarness from "./__fixtures__/DrawerHarness.svelte";

describe("Drawer — accesibilidad del panel lateral (foco, Escape)", () => {
  it("role=dialog/aria-modal solo aparece tras abrir, no antes", () => {
    render(DrawerHarness, { props: { open: false } });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("al abrir, el foco entra al drawer (foco inicial en el botón de cerrar)", async () => {
    const { component } = render(DrawerHarness, { props: { open: false } });
    await component.$set({ open: true });
    await tick();

    const dialog = screen.getByRole("dialog", { name: "Detalle" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cerrar panel" }));
  });

  it("Tab cicla el foco dentro del drawer (trampa de foco)", async () => {
    render(DrawerHarness, { props: { open: true } });
    await tick();

    const closeBtn = screen.getByRole("button", { name: "Cerrar panel" });
    const lastAction = screen.getByTestId("last-action");

    lastAction.focus();
    await fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(closeBtn);

    await fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(lastAction);
  });

  it("Escape cierra el drawer y devuelve el foco al disparador externo", async () => {
    const { component } = render(DrawerHarness, { props: { open: false } });
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
  });
});
