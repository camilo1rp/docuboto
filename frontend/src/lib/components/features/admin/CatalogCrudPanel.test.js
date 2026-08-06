// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * CatalogCrudPanel.test.js — contrato del evento `create` (Fase 5).
 *
 * Por qué existe este archivo: al extraer el patrón compartido, el botón "Nuevo"
 * pasó a abrir el modal por `bind:createOpen` y las siete páginas de /admin
 * dejaron de limpiar su formulario, porque nadie volvía a llamar a su
 * `openCreate()`. El modal reabría con lo escrito —y con el error de
 * validación— de la vez anterior; en /admin/usuarios eso incluye la casilla
 * ROOT. El evento es el contrato que lo repara, así que se prueba aquí.
 *
 * Cubre:
 *  - Pulsar "Nuevo" emite `create` y abre el modal.
 *  - Sin `canCreate` no hay botón y no se emite nada.
 *
 * El EFECTO del evento (que el formulario reabra limpio) se prueba sobre la
 * página real en admin/usuarios/usuarios-page.test.js: aquí no hay formulario
 * que resetear, solo el contrato del evento.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import CatalogCrudPanel from "./CatalogCrudPanel.svelte";

const BASE_PROPS = {
  title: "Usuarios",
  ariaLabel: "Lista de usuarios",
  items: [],
  canCreate: true,
  createLabel: "Nuevo usuario",
  emptyTitle: "Sin usuarios",
};

describe("CatalogCrudPanel — evento create", () => {
  it("pulsar «Nuevo» emite create y abre el modal de creación", async () => {
    const { component } = render(CatalogCrudPanel, { props: BASE_PROPS });
    const onCreate = vi.fn();
    component.$on("create", onCreate);

    expect(screen.queryByRole("dialog")).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: /nuevo usuario/i }));

    expect(onCreate).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
  });

  it("sin canCreate no se ofrece el botón (el RBAC lo resuelve la página)", () => {
    const { component } = render(CatalogCrudPanel, {
      props: { ...BASE_PROPS, canCreate: false },
    });
    const onCreate = vi.fn();
    component.$on("create", onCreate);

    expect(screen.queryByRole("button", { name: /nuevo usuario/i })).toBeNull();
    expect(onCreate).not.toHaveBeenCalled();
  });
});
