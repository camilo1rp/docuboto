// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ invalidateAll: vi.fn() }));

const { default: EntidadNoOperativa } = await import("./EntidadNoOperativa.svelte");

describe("EntidadNoOperativa", () => {
  it("suspendida: alerta, sin reintentar y con salida", () => {
    render(EntidadNoOperativa, { estado: "suspendida" });
    expect(screen.getByRole("heading", { name: "La entidad está suspendida" })).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cerrar sesión" })).toBeTruthy();
  });

  it("no_lista: ofrece reintentar", () => {
    render(EntidadNoOperativa, { estado: "no_lista" });
    expect(screen.getByRole("heading", { name: "La entidad aún no está lista" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
  });

  it("sin_comprobar: pide intentar de nuevo", () => {
    render(EntidadNoOperativa, { estado: "sin_comprobar" });
    expect(screen.getByText(/No se pudo comprobar el estado de la entidad/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
  });

  it("el cierre de sesión es un POST a /logout", () => {
    const { container } = render(EntidadNoOperativa, { estado: "suspendida" });
    const f = container.querySelector("form");
    expect(f?.getAttribute("method")?.toLowerCase()).toBe("post");
    expect(f?.getAttribute("action")).toBe("/logout");
  });

  it("al reintentar sin cambio avisa con la hora y devuelve el foco al título", async () => {
    render(EntidadNoOperativa, { estado: "sin_comprobar" });
    await fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(screen.getByText(/Sigue sin estar disponible \(/)).toBeTruthy());
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });
});
