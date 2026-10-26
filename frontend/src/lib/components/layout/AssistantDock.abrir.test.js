// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ABRIR el dock, que es lo único que hace un usuario.
 *
 * Los diez tests que ya existían montan `AssistantDock` con `open: true` desde
 * el primer render. Ninguno lo abría: nadie cubría la transición
 * cerrado → abierto, que es exactamente lo que hace el botón de la barra
 * superior. Un componente probado solo en el estado en que nadie lo encuentra
 * no está probado.
 *
 * El hueco salió a la luz durante la revisión por navegador, cuando pulsar ese
 * botón dejó la pestaña sin responder. Estos tests NO reproducen aquel cuelgue
 * —en jsdom la transición es limpia—, así que la causa de lo visto en Chrome
 * sigue sin confirmarse y no se declara aquí. Lo que sí cierran es la brecha de
 * cobertura, incluida la apertura repetida.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import AssistantDock from "./AssistantDock.svelte";

globalThis.fetch = vi.fn(async () => ({
  ok: true,
  status: 200,
  json: async () => ({ acciones: [], consultas: [], nivel_maximo: 1 }),
}));

describe("AssistantDock — abrirlo desde el botón", () => {
  it("pasar de cerrado a abierto no cuelga el componente", async () => {
    const { component } = render(AssistantDock, {
      props: { open: false, onClose: vi.fn() },
    });

    // Esto es lo que hace el botón de la barra superior.
    await component.$set({ open: true });

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeTruthy();
    });
  });

  it("y cerrarlo después tampoco", async () => {
    const { component } = render(AssistantDock, {
      props: { open: false, onClose: vi.fn() },
    });

    await component.$set({ open: true });
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());

    await component.$set({ open: false });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("abrir y cerrar varias veces seguidas sigue respondiendo", async () => {
    // Gemelo: una corrección que solo convergiera la primera vez —por ejemplo
    // marcando un flag que nunca se rearma— dejaría el dock inservible a
    // partir de la segunda apertura.
    const { component } = render(AssistantDock, {
      props: { open: false, onClose: vi.fn() },
    });

    for (let i = 0; i < 3; i += 1) {
      await component.$set({ open: true });
      await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
      await component.$set({ open: false });
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    }
  });
});
