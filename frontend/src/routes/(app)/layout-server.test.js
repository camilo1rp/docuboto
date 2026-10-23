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
  tokenVigente: vi.fn(() => true),
}));

const { load } = await import("./+layout.server.js");
const { tokenVigente, refreshAccessToken, setSessionCookies } =
  await import("$lib/services/auth.server.js");

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

const evento = (fetch, cookies) => ({
  cookies: cookies ?? {
    get: (n) => (n === "access_token" ? "tok" : undefined),
    set: vi.fn(),
    delete: vi.fn(),
  },
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

describe("(app)/+layout.server.js — vigencia de la sesión", () => {
  beforeEach(() => vi.clearAllMocks());

  /** Cookie presente: es el caso que la guarda anterior daba por bueno. */
  const conAmbasCookies = () => ({
    get: (n) => (n === "access_token" ? "tok-caducado" : "refresh-vivo"),
    set: vi.fn(),
    delete: vi.fn(),
  });

  it("con el access_token CADUCADO pero la cookie presente, renueva", async () => {
    // El defecto que esto fija: la guarda miraba `!accessToken`, así que una
    // cookie viva con un token muerto dentro saltaba el refresco entero y la
    // pantalla se dibujaba con un 401 dentro.
    tokenVigente.mockReturnValueOnce(false).mockReturnValue(true);
    refreshAccessToken.mockResolvedValueOnce({ access_token: "tok-nuevo" });

    await load(evento(fetchQue({}), conAmbasCookies()));

    expect(refreshAccessToken).toHaveBeenCalledWith("refresh-vivo");
    expect(setSessionCookies).toHaveBeenCalled();
  });

  it("si la renovación falla, manda a login en vez de servir la pantalla", async () => {
    tokenVigente.mockReturnValue(false);
    refreshAccessToken.mockResolvedValueOnce(null);

    await expect(load(evento(fetchQue({}), conAmbasCookies()))).rejects.toMatchObject({
      status: 302,
    });
  });

  it("un token VIGENTE no dispara ninguna renovación", async () => {
    // Anti-vacuidad: si el guardián renovara siempre, los dos casos de arriba
    // pasarían igual y no probarían nada.
    tokenVigente.mockReturnValue(true);

    await load(evento(fetchQue({})));

    expect(refreshAccessToken).not.toHaveBeenCalled();
  });
});

describe("(app)/+layout.server.js — entidad no operativa (ADR-035)", () => {
  beforeEach(() => vi.clearAllMocks());

  const cuerpo = (error, status) =>
    new Response(JSON.stringify({ detail: { error, status } }), {
      status,
      headers: { "content-type": "application/json" },
    });

  it.each([
    [403, "tenant_inactivo", "suspendida"],
    [503, "tenant_no_provisionado", "no_lista"],
    [503, "tenant_estado_no_disponible", "sin_comprobar"],
  ])(
    "%i %s se devuelve como estado %s, sin redirigir ni cerrar sesión",
    async (st, cod, esperado) => {
      const cookies = {
        get: (n) => (n === "access_token" ? "tok" : undefined),
        set: vi.fn(),
        delete: vi.fn(),
      };
      const fetch = fetchQue({ "/auth/me": () => cuerpo(cod, st) });
      const r = await load(evento(fetch, cookies));

      expect(r.entidadNoOperativa).toBe(esperado);
      expect(r.user.sub).toBe("u-1");
      expect(r.user.permissions).toEqual([]);
      expect(cookies.delete).not.toHaveBeenCalled();
      // No se siguen pidiendo contexto ni dependencias a una entidad que no se atiende.
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("también reconoce el sobre plano {error}", async () => {
    const fetch = fetchQue({
      "/auth/me": () => new Response(JSON.stringify({ error: "tenant_inactivo" }), { status: 403 }),
    });
    const r = await load(evento(fetch));
    expect(r.entidadNoOperativa).toBe("suspendida");
  });

  it("un 403 de otro motivo sigue siendo degradación de permisos", async () => {
    const r = await load(evento(fetchQue({ "/auth/me": () => cuerpo("permission_denied", 403) })));
    expect(r.entidadNoOperativa).toBeNull();
    expect(r.user.permisosDegradados).toBe(true);
  });

  it("entidad operativa: entidadNoOperativa es null", async () => {
    const r = await load(evento(fetchQue({ "/auth/me": () => json({ permissions: [] }) })));
    expect(r.entidadNoOperativa).toBeNull();
  });
});

describe("(app)/+layout.server.js — asistente por entidad (ADR-037)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("asistente_habilitado:true de /auth/me llega al usuario (control positivo)", async () => {
    const { user } = await load(
      evento(fetchQue({ "/auth/me": () => json({ permissions: [], asistente_habilitado: true }) })),
    );
    expect(user.asistente_habilitado).toBe(true);
  });

  it("false, ausente o no booleano: apagado", async () => {
    for (const me of [{ asistente_habilitado: false }, {}, { asistente_habilitado: "true" }]) {
      const { user } = await load(evento(fetchQue({ "/auth/me": () => json(me) })));
      expect(user.asistente_habilitado).toBe(false);
    }
  });

  it("con /auth/me caído: apagado (fail-closed)", async () => {
    const { user } = await load(
      evento(fetchQue({ "/auth/me": () => new Response("", { status: 503 }) })),
    );
    expect(user.asistente_habilitado).toBe(false);
  });
});
