// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * landing-server.test.js — +page.server.js de la landing pública (/).
 *
 * Cubre:
 *  - Sin sesión (`locals.user` ausente): el `load` no redirige, la landing
 *    se sirve normalmente.
 *  - Con sesión activa (`locals.user` poblado por hooks.server.js): redirect
 *    302 a /dashboard — un usuario autenticado no debe ver la página de
 *    mercadeo.
 */
import { describe, it, expect } from "vitest";
import { load } from "./+page.server.js";

describe("/ +page.server.js#load", () => {
  it("sin sesión: no redirige y devuelve datos vacíos", async () => {
    const result = await load({ locals: {} });
    expect(result).toEqual({});
  });

  it("con sesión activa (locals.user): redirige 302 a /dashboard", async () => {
    await expect(
      load({ locals: { user: { tenant_slug: "icetex", user_id: "u1" } } }),
    ).rejects.toMatchObject({
      status: 302,
      location: "/dashboard",
    });
  });
});
