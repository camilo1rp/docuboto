// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * overlayStacking.test.js — Modal apilado sobre un Drawer abierto (frontend/CLAUDE.md:
 * "no apilar Modal sobre Drawer abierto").
 *
 * Escenario real: `(app)/bandeja/+page.svelte` mantiene el Drawer de detalle
 * de un radicado abierto mientras el operador lanza un Modal de confirmación
 * desde dentro (p. ej. "Solicitar anulación") — ambos componentes están
 * siempre montados y su visibilidad depende solo de la prop `open`. Modal y
 * Drawer registran cada uno su propio listener `keydown` en `document` para
 * Tab/Escape; sin coordinación, un Escape pensado para cerrar el Modal (el
 * de encima) también cerraría el Drawer de fondo. `overlayStack.js` evita
 * esto: solo el tope de la pila reacciona.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import StackedOverlaysHarness from "./__fixtures__/StackedOverlaysHarness.svelte";
import { _resetOverlayStackForTests } from "$lib/utils/overlayStack.js";

describe("Modal apilado sobre Drawer abierto — solo el tope reacciona a Escape/Tab", () => {
  beforeEach(() => {
    _resetOverlayStackForTests();
  });

  it("con solo el Drawer abierto, Escape SÍ lo cierra (es el tope)", async () => {
    render(StackedOverlaysHarness, { props: { drawerOpen: true, modalOpen: false } });
    await tick();
    expect(screen.getByRole("dialog", { name: "Detalle" })).toBeInTheDocument();

    await fireEvent.keyDown(document, { key: "Escape" });
    await tick();
    await tick();

    expect(screen.queryByRole("dialog", { name: "Detalle" })).not.toBeInTheDocument();
  });

  it("Drawer abierto + Modal lanzado desde dentro: Escape cierra SOLO el Modal, el Drawer sigue abierto", async () => {
    const { component } = render(StackedOverlaysHarness, {
      props: { drawerOpen: true, modalOpen: false },
    });
    await tick();

    // El Modal se lanza desde dentro del Drawer sin cerrarlo (patrón real de bandeja).
    await component.$set({ modalOpen: true });
    await tick();

    expect(screen.getByRole("dialog", { name: "Detalle" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Confirmar" })).toBeInTheDocument();

    await fireEvent.keyDown(document, { key: "Escape" });
    await tick();
    await tick();

    // El Modal (tope de la pila) se cierra...
    expect(screen.queryByRole("dialog", { name: "Confirmar" })).not.toBeInTheDocument();
    // ...pero el Drawer de fondo NO debe cerrarse por el mismo Escape.
    expect(screen.getByRole("dialog", { name: "Detalle" })).toBeInTheDocument();
  });

  it("tras cerrar el Modal apilado, el Drawer vuelve a ser el tope y Escape sí lo cierra", async () => {
    const { component } = render(StackedOverlaysHarness, {
      props: { drawerOpen: true, modalOpen: true },
    });
    await tick();

    await fireEvent.keyDown(document, { key: "Escape" });
    await tick();
    await tick();
    expect(screen.queryByRole("dialog", { name: "Confirmar" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Detalle" })).toBeInTheDocument();

    // Segundo Escape: ahora el Drawer es el tope de la pila y debe cerrarse.
    await fireEvent.keyDown(document, { key: "Escape" });
    await tick();
    await tick();
    expect(screen.queryByRole("dialog", { name: "Detalle" })).not.toBeInTheDocument();

    expect(component).toBeTruthy();
  });
});
