// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * login-page.test.js — Render aislado del marcado de /login (+page.svelte).
 *
 * En producción, `+page.server.js#load` SIEMPRE redirige (302) a Keycloak
 * antes de que este marcado llegue a pintarse (ver e2e/public-pages.spec.js
 * para el contrato real de redirect, que sí es E2E-testeable sin backend).
 * Este test cubre únicamente que el COMPONENTE en sí (el mensaje transitorio
 * "Redirigiendo…" que ve un usuario sin JS o con red lenta) renderiza texto
 * accesible — render de componente puro, sin red ni SvelteKit routing.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import LoginPage from "./+page.svelte";

describe("/login — marcado transitorio de redirección", () => {
  it("muestra el mensaje de redirección con role=status (live region)", () => {
    render(LoginPage, { data: {} });
    expect(screen.getByRole("status")).toHaveTextContent(/Redirigiendo/i);
  });

  it("ofrece un enlace de reintento manual a /login", () => {
    render(LoginPage, { data: {} });
    const link = screen.getByRole("link", { name: "haz clic aquí" });
    expect(link).toHaveAttribute("href", "/login");
  });
});

describe("/login — error reenviado por /login/callback (rompe el bucle de redirects)", () => {
  it("muestra el mensaje de error con role=alert en vez de reiniciar el flujo PKCE", () => {
    render(LoginPage, { data: { error: "No fue posible autenticarte." } });
    expect(screen.getByRole("alert")).toHaveTextContent("No fue posible autenticarte.");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("ofrece un enlace «Reintentar» a /login sin query string", () => {
    render(LoginPage, { data: { error: "No fue posible autenticarte." } });
    const link = screen.getByRole("link", { name: "Reintentar" });
    expect(link).toHaveAttribute("href", "/login");
  });
});
