// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auth-cookies.test.js — Flags de las cookies de sesión (AREA-12-01, D-05).
 *
 * El access/refresh token vive SOLO en cookies httpOnly: si alguien cambia
 * `httpOnly`, `sameSite` o `secure` en `COOKIE_BASE`, el token pasa a ser
 * legible por JS (XSS), enviable cross-site (CSRF) o viajar por HTTP plano, y
 * ningún otro test lo notaría (hooks.server.test.js mockea setSessionCookies).
 * Se verifica con un `cookies.set` espiado y con `dev` en ambos valores.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

async function cargar(dev) {
  vi.resetModules();
  vi.doMock("$app/environment", () => ({ dev }));
  return import("./auth.server.js");
}

const espia = () => ({ set: vi.fn(), delete: vi.fn(), get: vi.fn() });

/** Todas las llamadas a set(nombre, valor, opciones) de una escritura. */
function opciones(c) {
  return c.set.mock.calls.map(([nombre, , opts]) => ({ nombre, ...opts }));
}

beforeEach(() => vi.resetModules());

describe.each([
  ["producción (dev=false)", false, true],
  ["desarrollo local (dev=true)", true, false],
])("cookies de sesión en %s", (_n, dev, secureEsperado) => {
  it("setSessionCookies: access y refresh httpOnly, sameSite lax, path /", async () => {
    const m = await cargar(dev);
    const c = espia();
    m.setSessionCookies(c, {
      access_token: "a",
      refresh_token: "r",
      expires_in: 300,
      refresh_expires_in: 1800,
    });
    const ops = opciones(c);
    expect(ops.map((o) => o.nombre).sort()).toEqual(
      [m.COOKIE_ACCESS_TOKEN, m.COOKIE_REFRESH_TOKEN].sort(),
    );
    for (const o of ops) {
      expect(o.httpOnly).toBe(true);
      expect(o.sameSite).toBe("lax");
      expect(o.path).toBe("/");
      expect(o.secure).toBe(secureEsperado);
      expect(o.maxAge).toBeGreaterThan(0);
    }
  });

  it("PKCE (verifier y state): mismos flags y vida de 5 minutos", async () => {
    const m = await cargar(dev);
    const c = espia();
    m.storePkceVerifierCookie(c, "v");
    m.storePkceStateCookie(c, "s");
    const ops = opciones(c);
    expect(ops).toHaveLength(2);
    for (const o of ops) {
      expect(o.httpOnly).toBe(true);
      expect(o.sameSite).toBe("lax");
      expect(o.path).toBe("/");
      expect(o.secure).toBe(secureEsperado);
      expect(o.maxAge).toBe(300);
    }
  });
});

describe("access_token: vida acotada", () => {
  it("nunca supera 10 minutos aunque Keycloak conceda más", async () => {
    const m = await cargar(false);
    const c = espia();
    m.setSessionCookies(c, { access_token: "a", expires_in: 86400 });
    expect(opciones(c)[0].maxAge).toBe(600);
  });
});
