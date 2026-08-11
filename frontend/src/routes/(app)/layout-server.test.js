// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * layout-server.test.js — Guarda de sesión de las rutas privadas.
 *
 * Se centra en la degradación silenciosa: cuando `GET /auth/me` no responde, la
 * sesión sigue viva pero sin permisos granulares, así que el menú se vacía y
 * las acciones desaparecen de todas las pantallas. Degradar es correcto —el
 * RBAC real está en el backend y nada se franquea—, pero desde el otro lado de
 * la pantalla es indistinguible de una revocación de permisos.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://api-gateway:8080/api/v1",
}));

const claims = {
  sub: "u-1",
  name: "Operador",
  preferred_username: "operador",
  tenant_slug: "icetex",
};

vi.mock("$lib/services/auth.server.js", () => ({
  COOKIE_ACCESS_TOKEN: "access_token",
  COOKIE_REFRESH_TOKEN: "refresh_token",
  refreshAccessToken: vi.fn(),
  setSessionCookies: vi.fn(),
  clearSessionCookies: vi.fn(),
  decodeJwt: () => ({ sub: "u-1" }),
  extractSessionUser: () => ({ ...claims, roles: [], permissions: [] }),
}));

const { load } = await import("./+layout.server.js");

/** Respuestas por URL; lo no listado responde 200 con `[]`. */
function fetchQue(mapa) {
  return vi.fn(async (url) => {
    for (const [fragmento, respuesta] of Object.entries(mapa)) {
      if (url.includes(fragmento)) return respuesta();
    }
    return new Response("[]", { headers: { "content-type": "application/json" } });
  });
}

const json = (cuerpo) =>
  new Response(JSON.stringify(cuerpo), { headers: { "content-type": "application/json" } });

const evento = (fetch) => ({
  cookies: { get: (n) => (n === "access_token" ? "tok" : undefined) },
  url: new URL("http://app.local/bandeja"),
  fetch,
});

describe("(app)/+layout.server.js — permisos", () => {
  beforeEach(() => vi.clearAllMocks());

  it("con /auth/me OK, no marca degradación", async () => {
    const { user } = await load(
      evento(
        fetchQue({
          "/auth/me": () =>
            json({ permissions: ["PERM_RADI"], permission_levels: { PERM_RADI: 3 } }),
        }),
      ),
    );

    expect(user.permissions).toEqual(["PERM_RADI"]);
    expect(user.permisosDegradados).toBe(false);
  });

  it("con /auth/me caído (503), degrada Y LO DECLARA", async () => {
    const { user } = await load(
      evento(fetchQue({ "/auth/me": () => new Response("", { status: 503 }) })),
    );

    expect(user.permissions).toEqual([]);
    expect(user.permisosDegradados).toBe(true);
  });

  it("con error de red hacia el gateway, degrada Y LO DECLARA", async () => {
    const { user } = await load(
      evento(
        fetchQue({
          "/auth/me": () => {
            throw new TypeError("fetch failed");
          },
        }),
      ),
    );

    expect(user.permissions).toEqual([]);
    expect(user.permisosDegradados).toBe(true);
  });

  it("la sesión NO se cierra por un /auth/me caído", async () => {
    // Cerrar sesión ante un fallo del gateway convertiría una degradación en
    // una expulsión: el usuario perdería lo que estuviera escribiendo.
    const resultado = await load(
      evento(fetchQue({ "/auth/me": () => new Response("", { status: 503 }) })),
    );

    expect(resultado.user.sub).toBe("u-1");
  });
});
