// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * vitest-setup.js — Setup global de Vitest.
 *
 * Extiende `expect` con los matchers de @testing-library/jest-dom
 * (toBeInTheDocument, toHaveAttribute, toBeDisabled, etc.) para los tests
 * de componentes que corren en entorno jsdom. No afecta a los tests que
 * corren en entorno 'node' (p. ej. src/lib/server/**) — jest-dom no se usa
 * ahí y sus matchers simplemente no se invocan.
 *
 * Se limpia el DOM entre tests (evita fugas de estado entre `render()`
 * sucesivos de @testing-library/svelte).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/svelte";
import { afterEach, vi } from "vitest";

// SvelteKit resuelve $env/* como módulos virtuales que dependen del server de
// Vite (dev/build) para poblarse; fuera de ese contexto (Vitest) quedan
// indefinidos y revientan al importarse (incluso transitivamente, vía
// $lib/utils/config.js). Se mockean con objetos vacíos: los módulos que los
// consumen (config.js/config.server.js) ya tienen fallback (`?? "..."`) para
// cuando la variable no está definida, así que el comportamiento por defecto
// se preserva en los tests.
vi.mock("$env/dynamic/public", () => ({ env: {} }));
vi.mock("$env/dynamic/private", () => ({ env: {} }));
vi.mock("$env/static/public", () => ({}));
vi.mock("$env/static/private", () => ({}));

afterEach(() => {
  cleanup();
});
