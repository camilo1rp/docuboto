// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * hooks.server.test.js — `handleError` no publica topología interna.
 *
 * El comentario del código decía «no filtrar stack traces al cliente» y acto
 * seguido devolvía `error.message` tal cual. El stack no viajaba; el mensaje
 * sí — y el mensaje de un fallo de red de Node es
 * `connect ECONNREFUSED 172.18.0.7:8080`: nombres de servicio, direcciones de
 * la red Docker y puertos, servidos a cualquiera capaz de provocar un error.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://api-gateway:8080/api/v1",
  KEYCLOAK_INTERNAL_BASE: "http://keycloak:8080",
}));

vi.mock("$lib/services/auth.server.js", () => ({
  COOKIE_ACCESS_TOKEN: "access_token",
  COOKIE_REFRESH_TOKEN: "refresh_token",
  refreshAccessToken: vi.fn(),
  setSessionCookies: vi.fn(),
  decodeJwt: vi.fn(),
  extractSessionUser: vi.fn(),
}));

const { handleError } = await import("./hooks.server.js");

const evento = { url: new URL("http://app.local/bandeja") };

describe("handleError", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("no devuelve al cliente el mensaje crudo del error", () => {
    const error = new Error("connect ECONNREFUSED 172.18.0.7:8080");

    const salida = handleError({ error, event: evento });

    expect(salida.message).not.toContain("ECONNREFUSED");
    expect(salida.message).not.toContain("172.18.0.7");
    expect(salida.message).not.toContain("8080");
  });

  it("devuelve una referencia y la escribe en el log del servidor", () => {
    // La referencia es lo que permite atender el incidente sin que el mensaje
    // de error sea el canal: el usuario la dicta, soporte la busca en el log.
    const salida = handleError({ error: new Error("lo que sea"), event: evento });

    expect(salida.referencia).toMatch(/^[0-9a-f]{8}$/);
    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining(salida.referencia),
      expect.any(Error),
    );
  });

  it("el detalle real sí llega al log", () => {
    const error = new Error("connect ECONNREFUSED 172.18.0.7:8080");

    handleError({ error, event: evento });

    expect(console.error).toHaveBeenCalledWith(expect.any(String), error);
  });

  it("una referencia distinta por error: no se confunden dos incidentes", () => {
    const a = handleError({ error: new Error("a"), event: evento });
    const b = handleError({ error: new Error("b"), event: evento });

    expect(a.referencia).not.toBe(b.referencia);
  });
});
