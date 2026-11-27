// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * logout-server.test.js — /logout cierra sesión SOLO por POST.
 *
 * El cierre de sesión vivía en el `load` de la ruta, es decir, en un GET, y el
 * topbar lo enlazaba con un `<a>` bajo `data-sveltekit-preload-data="hover"`:
 * pasar el ratón por encima ejecutaba el `load` y cerraba la sesión. Con el
 * efecto en un GET lo dispara además cualquier cosa que "visite" la URL, que es
 * la forma clásica del CSRF de cierre de sesión.
 *
 * El primer test es el que importa y es una aserción de AUSENCIA: el módulo no
 * debe exportar `load`. Si alguien lo reintroduce, aquí se ve.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

const revokeSession = vi.fn(async () => {});
const clearSessionCookies = vi.fn();

vi.mock("$lib/services/auth.server.js", () => ({
  COOKIE_REFRESH_TOKEN: "refresh_token",
  revokeSession: (...args) => revokeSession(...args),
  clearSessionCookies: (...args) => clearSessionCookies(...args),
}));

const modulo = await import("./+page.server.js");

/** Cookies mínimas: solo lo que usa la action. */
function cookiesCon(refresh) {
  return { get: vi.fn(() => refresh) };
}

describe("/logout — solo POST", () => {
  beforeEach(() => {
    revokeSession.mockClear();
    clearSessionCookies.mockClear();
  });

  it("NO exporta `load`: un GET no puede cerrar la sesión", () => {
    // Un GET con efectos lo dispara el prefetch al pasar el ratón (`hover`),
    // un escáner de enlaces o un `<img src>` remoto.
    expect(modulo.load).toBeUndefined();
  });

  it("la action POST revoca en la app, borra cookies y redirige", async () => {
    const cookies = cookiesCon("rt-1");

    await expect(
      modulo.actions.default({ cookies, url: new URL("http://app.local/logout") }),
    ).rejects.toMatchObject({ status: 303 });

    expect(revokeSession).toHaveBeenCalledWith("rt-1");
    expect(clearSessionCookies).toHaveBeenCalledWith(cookies);
  });

  it("la redirección vuelve a /login, en este mismo origen (ADR-048)", async () => {
    let destino = "";
    await modulo.actions
      .default({ cookies: cookiesCon("rt-1"), url: new URL("http://app.local/logout") })
      .catch((e) => {
        destino = e.location;
      });
    expect(destino).toBe("/login");
  });

  it("sin refresh_token no revoca nada, pero limpia las cookies igual", async () => {
    // La sesión local debe morir aunque la revocación remota no aplique: dejar
    // cookies vivas por no tener a quién revocar sería lo peor de los dos.
    await modulo.actions
      .default({ cookies: cookiesCon(undefined), url: new URL("http://app.local/logout") })
      .catch(() => {});

    expect(revokeSession).not.toHaveBeenCalled();
    expect(clearSessionCookies).toHaveBeenCalled();
  });
});
