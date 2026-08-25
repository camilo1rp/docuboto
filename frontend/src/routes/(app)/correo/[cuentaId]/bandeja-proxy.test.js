// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxies y `load` de la bandeja de correo (E19 Increment 2).
 *
 * Lo que se blinda: que la acción salga de una lista cerrada, que abrir la
 * pantalla no dispare una sincronización —traer del buzón es un acto que el
 * operador decide—, y que los 409 de la reserva lleguen al cliente tal cual en
 * vez de disfrazarse de avería.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { POST as accion } from "./api/[mensajeId]/+server.js";
import { GET as estado } from "./api/estado/+server.js";
import { load } from "./+page.server.js";

const locals = {
  accessToken: "tok",
  user: {
    tenant_slug: "demo",
    // Los DOS identificadores, distintos a propósito: el `sub` es de Keycloak y
    // el `user_id` es la identidad interna del tenant, que es la que el backend
    // escribe en las columnas de autoría. Con un solo valor, un test no podría
    // distinguir cuál usa la pantalla.
    sub: "11111111-1111-1111-1111-111111111111",
    user_id: "99999999-9999-9999-9999-999999999999",
  },
};
const CUENTA = "22222222-2222-2222-2222-222222222222";
const MENSAJE = "33333333-3333-3333-3333-333333333333";
const params = { cuentaId: CUENTA, mensajeId: MENSAJE };
const peticion = (c = {}) => ({ json: async () => c });
const conAccion = (a) => ({ searchParams: new URLSearchParams(`accion=${a}`) });

let original;
beforeEach(() => {
  original = globalThis.fetch;
});
afterEach(() => {
  globalThis.fetch = original;
});

describe("proxy de acciones sobre un correo", () => {
  it("solo admite las tres acciones de la lista cerrada", async () => {
    globalThis.fetch = vi.fn();
    for (const mala of ["borrar", "constructor", "toString", ""]) {
      const res = await accion({ locals, params, url: conAccion(mala), request: peticion() });
      expect(res.status, `aceptó «${mala}»`).toBe(400);
    }
    // `constructor` y `toString` son la razón de usar `Object.hasOwn`: un acceso
    // directo devolvería un miembro heredado de Object.prototype y pasaría el
    // guard.
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("compone la ruta de la acción y propaga la sesión", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(String(url)).toContain(`/email/cuentas/${CUENTA}/bandeja/${MENSAJE}/reservar`);
      expect(opts.headers["X-User-Id"]).toBe(locals.user.sub);
      return new Response(JSON.stringify({ id: MENSAJE }), { status: 200 });
    });
    const res = await accion({ locals, params, url: conAccion("reservar"), request: peticion() });
    expect(res.status).toBe(200);
  });

  it("un 409 de la reserva llega al cliente tal cual", async () => {
    // No es una avería: significa que la reserva está haciendo su trabajo, y la
    // pantalla necesita distinguirlo para avisar en vez de alarmar.
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: { error: "correo_ocupado", detail: "Otro lo trabaja" } }),
          { status: 409 },
        ),
    );
    const res = await accion({ locals, params, url: conAccion("reservar"), request: peticion() });
    expect(res.status).toBe(409);
    expect((await res.json()).detail.error).toBe("correo_ocupado");
  });

  it("sin sesión no se llama al gateway", async () => {
    globalThis.fetch = vi.fn();
    const res = await accion({
      locals: {},
      params,
      url: conAccion("radicar"),
      request: peticion(),
    });
    expect(res.status).toBe(401);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("un identificador que no es UUID se rechaza antes de construir la URL", async () => {
    globalThis.fetch = vi.fn();
    const res = await accion({
      locals,
      params: { cuentaId: CUENTA, mensajeId: "../../admin" },
      url: conAccion("radicar"),
      request: peticion(),
    });
    expect(res.status).toBe(400);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe("sondeo de estado", () => {
  it("consulta el resumen, no la lista entera", async () => {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (url) => {
      llamadas.push(String(url));
      return new Response(JSON.stringify({ pendientes: 2, ocupados: 1 }), { status: 200 });
    });
    const res = await estado({ locals, params: { cuentaId: CUENTA } });
    expect(res.status).toBe(200);
    expect(llamadas[0]).toContain("/bandeja/estado");
  });
});

describe("load de la bandeja", () => {
  it("carga la lista y la cuenta, y NUNCA sincroniza", async () => {
    const llamadas = [];
    const fake = vi.fn(async (url, opts) => {
      llamadas.push({ url: String(url), metodo: opts?.method ?? "GET" });
      if (String(url).includes("/bandeja")) {
        return new Response(JSON.stringify([{ id: "m1", asunto: "Petición" }]), { status: 200 });
      }
      return new Response(JSON.stringify([{ id: CUENTA, nombre: "Ventanilla" }]), { status: 200 });
    });

    const result = await load({
      cookies: { get: () => "tok" },
      parent: async () => ({ user: locals.user }),
      fetch: fake,
      params: { cuentaId: CUENTA },
    });

    // Traer del buzón es un acto que el operador dispara, no un efecto de abrir
    // una pantalla: descargar un buzón entero cada vez que alguien mira la
    // bandeja sería un coste que nadie pidió.
    expect(llamadas.every((l) => !l.url.includes("sincronizar"))).toBe(true);
    expect(llamadas.every((l) => l.metodo === "GET")).toBe(true);

    expect(result.correos).toHaveLength(1);
    expect(result.cuenta.nombre).toBe("Ventanilla");
    // `user_id`, NO `sub`: comparar la autoría del backend con el identificador
    // de Keycloak haría que el funcionario no reconociera sus propios correos y,
    // con el botón deshabilitado sobre lo ajeno, no pudiera continuar con el que
    // él mismo abrió. Ya rompió tres pantallas antes que esta.
    expect(result.yo).toBe(locals.user.user_id);
    expect(result.yo).not.toBe(locals.user.sub);
    expect(result.correosError).toBeNull();
  });

  it("un 403 conserva el status para que la pantalla no lo llame 'vacío'", async () => {
    const fake = vi.fn(async (url) =>
      String(url).includes("/bandeja")
        ? new Response(JSON.stringify({ detail: { detail: "Sin permiso" } }), { status: 403 })
        : new Response(JSON.stringify([]), { status: 200 }),
    );
    const result = await load({
      cookies: { get: () => "t" },
      parent: async () => ({ user: locals.user }),
      fetch: fake,
      params: { cuentaId: CUENTA },
    });
    expect(result.correos).toEqual([]);
    expect(result.correosError.status).toBe(403);
  });
});
