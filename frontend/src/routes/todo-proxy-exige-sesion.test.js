// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * todo-proxy-exige-sesion.test.js — Guardián DERIVADO DEL ÁRBOL (AREA-12-01).
 *
 * `bffProxyGuards.test.js` prueba a mano una muestra de proxies; nada impedía
 * que el siguiente `+server.js` naciera sin el guard de sesión. Este recorre
 * TODOS los `(app)/**\/+server.js`, invoca cada verbo exportado con
 * `locals = {}` (sin sesión) y exige 401 ANTES de tocar red, body ni cookies:
 * `fetch` global y `request.json()/formData()/...` lanzan si se llegan a usar.
 *
 * Un proxy que se salte el guard, o que lea el token de otra fuente (body,
 * query, cookie directa) en vez de `locals.accessToken`, rompe D-05 y cae aquí.
 * Una ruta que de verdad deba ser pública se declara en `PUBLICOS` con motivo.
 */
import { describe, it, expect, vi, afterEach } from "vitest";

// El glob de Vite trata `(app)` como grupo de alternancia: se recorre todo el
// árbol y se filtra por prefijo.
const todos = import.meta.glob("./**/+server.js", { eager: true });
const modulos = Object.fromEntries(
  Object.entries(todos).filter(([ruta]) => ruta.startsWith("./(app)/")),
);

/** Verbos HTTP que SvelteKit enruta. */
const VERBOS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

/**
 * Handlers (app) que NO exigen sesión, con el motivo. Vacío hoy: las rutas
 * públicas viven en `(public)`.
 * @type {Record<string, string>}
 */
const PUBLICOS = {};

const ya = () => {
  throw new Error("el handler tocó el body/red antes del guard de sesión");
};

function evento(ruta) {
  const params = {};
  for (const m of ruta.matchAll(/\[(?:\.\.\.)?([^\]]+)\]/g)) params[m[1]] = "x";
  const request = new Proxy(
    { method: "POST", headers: new Headers() },
    {
      get: (t, k) => (k in t ? t[k] : ya),
    },
  );
  return {
    locals: {},
    params,
    request,
    url: new URL("http://localhost/x?q=1"),
    cookies: { get: ya, getAll: ya, set: ya, delete: ya },
    fetch: ya,
    getClientAddress: () => "127.0.0.1",
  };
}

const casos = [];
for (const [ruta, mod] of Object.entries(modulos)) {
  for (const verbo of VERBOS) {
    if (typeof mod[verbo] === "function") casos.push([ruta, verbo, mod[verbo]]);
  }
}

afterEach(() => vi.unstubAllGlobals());

describe("todo +server.js de (app) exige sesión (D-05)", () => {
  it("el censo no está vacío (anti-vacuidad)", () => {
    expect(Object.keys(modulos).length).toBeGreaterThan(200);
    expect(casos.length).toBeGreaterThan(200);
  });

  it.each(casos.map(([r, v, h]) => [`${v} ${r}`, r, v, h]))(
    "%s → 401 sin locals.accessToken",
    async (_nombre, ruta, _verbo, handler) => {
      if (PUBLICOS[ruta]) return;
      vi.stubGlobal("fetch", ya);
      const res = await handler(evento(ruta));
      expect(res.status).toBe(401);
    },
  );
});
