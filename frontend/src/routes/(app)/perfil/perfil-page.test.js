// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * perfil-page.test.js — revisión UX del cierre del desfase API↔UI, lote 2,
 * punto 4: los errores de validación de campo en /perfil salían SOLO como
 * toast, nunca asociados al campo vía `aria-invalid`/`aria-describedby`
 * pese a que `FormField` ya soporta `error`. Este test cubre el caso más
 * directo (código TOTP con formato inválido) y los atributos de teclado
 * numérico/autocompletado de los campos de código OTP.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import PerfilPage from "./+page.svelte";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function baseData(overrides = {}) {
  return {
    user: { permissions: [], roles: [], context: null },
    totpStatus: { estado: "pendiente_activacion" },
    totpError: null,
    signingKeyStatus: { estado: "no_configurado" },
    signingKeyError: null,
    ...overrides,
  };
}

describe("/perfil — errores de validación asociados al campo", () => {
  it("código TOTP con formato inválido: marca aria-invalid y asocia el mensaje por aria-describedby (no solo toast)", async () => {
    render(PerfilPage, { props: { data: baseData() } });

    const input = screen.getByLabelText(/código de 6 dígitos de su app autenticadora/i);
    await fireEvent.input(input, { target: { value: "12" } });
    await fireEvent.click(screen.getByRole("button", { name: /^activar$/i }));

    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("El código debe tener 6 dígitos.")).toBeInTheDocument();
    expect(input.getAttribute("aria-describedby")).toContain("totp-activate-code-error");
  });

  it("campos de código OTP declaran inputmode=numeric y autocomplete=one-time-code", () => {
    render(PerfilPage, { props: { data: baseData() } });
    const input = screen.getByLabelText(/código de 6 dígitos de su app autenticadora/i);
    expect(input).toHaveAttribute("inputmode", "numeric");
    expect(input).toHaveAttribute("autocomplete", "one-time-code");
  });

  it("código de revocación TOTP inválido: error asociado al campo `totp-revoke-code`", async () => {
    globalThis.fetch = vi.fn();
    render(PerfilPage, { props: { data: baseData({ totpStatus: { estado: "activo" } }) } });

    // El formulario de revocación vive dentro de un <details> plegado.
    const revokeSummary = screen.getByText(/revocar el segundo factor/i);
    await fireEvent.click(revokeSummary);

    const input = screen.getByLabelText(/código actual de 6 dígitos/i, { selector: "#totp-revoke-code" });
    await fireEvent.click(screen.getByRole("button", { name: /^revocar$/i }));

    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByText("Ingrese el código actual de 6 dígitos para revocar."),
    ).toBeInTheDocument();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
