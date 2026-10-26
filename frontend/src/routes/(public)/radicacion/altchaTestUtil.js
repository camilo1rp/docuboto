// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Utilidades de prueba para la ventanilla con ALTCHA. El widget real es un
 * elemento personalizado que jsdom no ejecuta: aquí se sustituye por el
 * contrato mínimo que la página usa (`statechange`, `verify()`, `reset()`).
 */
import { vi } from "vitest";
import { render, screen } from "@testing-library/svelte";

/** Payload tal como lo emite altcha 2.3.0: base64(JSON{...}). */
export const PAYLOAD = btoa(
  JSON.stringify({
    algorithm: "SHA-256",
    challenge: "c".repeat(64),
    number: 42,
    salt: "s".repeat(32) + "?expires=1&t=demo&v=1",
    signature: "f".repeat(64),
    took: 5,
  }),
);

/**
 * Pinta la página, espera a que el widget quede cargado y devuelve el elemento
 * con `verify`/`reset` espiables.
 * @param {any} Pagina
 * @param {any} data
 */
export async function pintar(Pagina, data) {
  render(Pagina, { data });
  await screen.findByText(/Se hará al enviar el formulario/i);
  const widget = /** @type {any} */ (document.querySelector("altcha-widget"));
  widget.verify = vi.fn();
  widget.reset = vi.fn();
  return widget;
}

/** @param {any} widget @param {string} [estado] @param {string} [payload] */
export function emitir(widget, estado = "verified", payload = PAYLOAD) {
  widget.dispatchEvent(new CustomEvent("statechange", { detail: { state: estado, payload } }));
}
