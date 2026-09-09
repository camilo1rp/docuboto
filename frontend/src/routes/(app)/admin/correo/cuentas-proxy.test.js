// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * Proxies BFF de las cuentas de correo (E19).
 *
 * Lo que se prueba: que sin sesión no salga ni una petición al gateway, que el
 * identificador de cuenta se valide antes de interpolarlo, y que un `204` —el
 * que devuelve el canje OAuth— no reviente al intentar leerlo como JSON.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { POST as probar } from "./api/cuentas/[id]/probar/+server.js";
import { POST as canjear } from "./api/cuentas/[id]/oauth/canjear/+server.js";
import { GET as listarCuentas, POST as crearCuenta } from "./api/cuentas/+server.js";

const localsConSesion = {
  accessToken: "tok",
  user: { tenant_slug: "demo", sub: "11111111-1111-1111-1111-111111111111" },
};
const ID = "22222222-2222-2222-2222-222222222222";
const peticion = (cuerpo = {}) => ({ json: async () => cuerpo });

let fetchOriginal;
beforeEach(() => {
  fetchOriginal = globalThis.fetch;
});
afterEach(() => {
  globalThis.fetch = fetchOriginal;
});

describe("proxies de cuentas de correo", () => {
  it("sin sesión no se llama al gateway", async () => {
    globalThis.fetch = vi.fn();
    for (const [fn, args] of [
      [listarCuentas, [{ locals: {} }]],
      [crearCuenta, [{ locals: {}, request: peticion() }]],
      [probar, [{ locals: {}, params: { id: ID }, request: peticion() }]],
    ]) {
      const res = await fn(...args);
      expect(res.status).toBe(401);
    }
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("un identificador que no es UUID se rechaza antes de construir la URL", async () => {
    globalThis.fetch = vi.fn();
    const res = await probar({
      locals: localsConSesion,
      params: { id: "../../admin" },
      request: peticion(),
    });
    expect(res.status).toBe(400);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("propaga sesión, entidad y usuario, y no inventa cuerpo", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(String(url)).toContain(`/email/cuentas/${ID}/probar`);
      expect(opts.headers.Authorization).toBe("Bearer tok");
      expect(opts.headers["X-Tenant-Slug"]).toBe("demo");
      expect(opts.headers["X-User-Id"]).toBe(localsConSesion.user.sub);
      expect(JSON.parse(opts.body)).toEqual({ password: "secreta" });
      return new Response(JSON.stringify({ ok: true, carpetas: ["INBOX"] }), { status: 200 });
    });
    const res = await probar({
      locals: localsConSesion,
      params: { id: ID },
      request: peticion({ password: "secreta" }),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, carpetas: ["INBOX"] });
  });

  it("un 204 del canje se devuelve sin cuerpo, no se intenta leer como JSON", async () => {
    // `Response.json()` sobre un cuerpo vacío lanza; sin el corte explícito, el
    // caso de ÉXITO del flujo OAuth acabaría en el `catch` y se reportaría como
    // fallo de conexión.
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await canjear({
      locals: localsConSesion,
      params: { id: ID },
      request: peticion({ code: "c", state: "s" }),
    });
    expect(res.status).toBe(204);
  });

  it("la red caída degrada a 503 con mensaje propio, nunca a una excepción", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await listarCuentas({ locals: localsConSesion });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("el aviso de configuración no contradice a la pantalla", () => {
  /**
   * El defecto que esto blinda apareció al mirar la pantalla en el navegador:
   * arriba decía «La ingesta de correo no está configurada» y justo debajo
   * había un buzón configurado, probado y funcionando. Los dos bloques hablaban
   * de cosas distintas —el de arriba, del buzón único heredado del despliegue;
   * el de abajo, de las cuentas de la entidad— pero el de arriba estaba
   * redactado en nombre de toda la ingesta.
   *
   * La corrección de entonces fue condicionar el aviso a que no hubiera ninguna
   * cuenta. La de ahora es más fuerte y es la que se comprueba aquí: la
   * SEGUNDA FUENTE ya no existe. Retirada la ruta heredada, la pantalla tiene
   * un solo sitio del que sacar si la ingesta está configurada —las cuentas— y
   * dos afirmaciones que se contradigan ya no se pueden escribir.
   *
   * Se comprueba sobre el fuente porque lo que no puede volver es la fuente,
   * no una expresión concreta.
   */
  it("la pantalla no lee ningún estado heredado del despliegue", async () => {
    const { readFileSync } = await import("node:fs");
    const svelte = readFileSync("src/routes/(app)/admin/correo/+page.svelte", "utf-8");
    const server = readFileSync("src/routes/(app)/admin/correo/+page.server.js", "utf-8");

    // Anti-vacuidad: los ficheros son los que creo que son.
    expect(svelte).toContain("data.cuentas");
    expect(server).toContain("/email/cuentas");

    // Y no hay rastro de la ruta heredada en ninguno de los dos.
    for (const [nombre, fuente] of [
      ["+page.svelte", svelte],
      ["+page.server.js", server],
    ]) {
      const codigo = fuente.replace(/<!--[\s\S]*?-->|\/\*[\s\S]*?\*\/|(^|\s)\/\/.*$/gm, "");
      expect(codigo, `${nombre} sigue leyendo el estado heredado`).not.toMatch(
        /ingest\/email|data\.estado|imap_no_configurado/,
      );
    }
  });
});
