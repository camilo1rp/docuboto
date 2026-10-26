// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * confirmBeforeWrite.test.js — "Toda acción de escritura exige confirmación
 * explícita" (frontend/CLAUDE.md, patrón usado en radicar/+page.svelte,
 * transferencias/+page.svelte, bandeja/+page.svelte: `showConfirmModal`
 * local + Modal, el proxy solo se invoca desde el botón DENTRO del diálogo).
 *
 * Usa WriteActionHarness.svelte (fixture) para probar el patrón de forma
 * aislada y determinista, sin depender de la red/stores de una página real:
 * las páginas reales aplican exactamente esta forma pero con más estado
 * (loading, stores de sesión, fetch al proxy BFF) que no aporta nada a la
 * invariante que se quiere blindar aquí.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import WriteActionHarness from "./__fixtures__/WriteActionHarness.svelte";

describe("acción de escritura — exige confirmación explícita antes de invocar el proxy", () => {
  it("el diálogo de confirmación no existe hasta que se dispara la acción", () => {
    const onConfirmWrite = vi.fn();
    render(WriteActionHarness, { props: { onConfirmWrite } });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onConfirmWrite).not.toHaveBeenCalled();
  });

  it("disparar la acción SOLO abre el diálogo — no invoca el proxy todavía", async () => {
    const onConfirmWrite = vi.fn();
    render(WriteActionHarness, { props: { onConfirmWrite } });

    await fireEvent.click(screen.getByTestId("action-anular"));
    await tick();

    const dialog = screen.getByRole("dialog", { name: "Confirmar anulación" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(onConfirmWrite).not.toHaveBeenCalled();
  });

  it("confirmar DENTRO del diálogo invoca el proxy exactamente una vez, y cierra el diálogo", async () => {
    const onConfirmWrite = vi.fn();
    render(WriteActionHarness, { props: { onConfirmWrite } });

    await fireEvent.click(screen.getByTestId("action-anular"));
    await tick();

    const dialog = screen.getByRole("dialog", { name: "Confirmar anulación" });
    const confirmBtn = screen.getByTestId("confirm-write");
    // La acción de escritura vive dentro del propio diálogo de confirmación.
    expect(dialog).toContainElement(confirmBtn);

    await fireEvent.click(confirmBtn);
    await tick();

    expect(onConfirmWrite).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancelar cierra el diálogo SIN invocar el proxy", async () => {
    const onConfirmWrite = vi.fn();
    render(WriteActionHarness, { props: { onConfirmWrite } });

    await fireEvent.click(screen.getByTestId("action-anular"));
    await tick();
    await fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    await tick();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onConfirmWrite).not.toHaveBeenCalled();
  });
});
