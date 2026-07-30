// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * landing-page.test.js — Render de la landing pública (/, +page.svelte).
 *
 * Cubre:
 *  - Un único <h1> en toda la página (jerarquía semántica).
 *  - El titular y el CTA principal renderizan.
 *  - El enlace "Iniciar sesión" del nav y del hero apuntan a /login.
 *  - La sección de cumplimiento normativo lista los referentes clave.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import LandingPage from "./+page.svelte";

describe("/ — landing pública", () => {
  it("renderiza exactamente un <h1> con el titular del hero", () => {
    render(LandingPage);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent(/gestión documental/i);
  });

  it("ofrece enlaces a /login para iniciar sesión (nav y hero)", () => {
    // Button.svelte renderiza <a role="button"> cuando recibe `href`, así que
    // estos CTA son accesibles con rol "button", no "link".
    render(LandingPage);
    const loginLinks = screen.getAllByRole("button", { name: "Iniciar sesión" });
    expect(loginLinks.length).toBeGreaterThanOrEqual(2);
    for (const link of loginLinks) {
      expect(link).toHaveAttribute("href", "/login");
    }
  });

  it("incluye la franja de cumplimiento normativo", () => {
    render(LandingPage);
    expect(screen.getByText("Ley 594/2000")).toBeInTheDocument();
    expect(screen.getByText("AGPL v3")).toBeInTheDocument();
  });

  it("incluye las seis tarjetas de características", () => {
    render(LandingPage);
    expect(screen.getByText("Radicación E/S/I")).toBeInTheDocument();
    expect(screen.getByText("IA / capa MCP")).toBeInTheDocument();
  });
});
