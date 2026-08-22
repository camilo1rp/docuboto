// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy BFF del restablecimiento administrativo del segundo factor.
 *
 * Lo que se fija aquí es sobre todo lo que el proxy NO debe hacer: no inventar
 * el motivo (sería una firma en blanco en el asiento de auditoría), no aceptar
 * un id que no sea UUID, y no colapsar el 409 del backend —el que dice que
 * nadie se restablece a sí mismo— en un error genérico.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { DELETE as resetTotp } from "./+server.js";

const USER_ID = "44444444-4444-4444-4444-444444444444";
const LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};
const MOTIVO = "Perdió el teléfono; verificado en persona";

function peticion(body) {
  return { json: async () => body };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DELETE /admin/usuarios/api/[id]/totp", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await resetTotp({
      locals: {},
      params: { id: USER_ID },
      request: peticion({ motivo: MOTIVO }),
    });
    expect(res.status).toBe(401);
  });

  it("400 con un id que no es UUID, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con id inválido");
    });
    const res = await resetTotp({
      locals: LOCALS,
      params: { id: "../../otro" },
      request: peticion({ motivo: MOTIVO }),
    });
    expect(res.status).toBe(400);
  });

  it("rechaza un motivo ausente o de relleno SIN llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin motivo");
    });
    for (const body of [{}, { motivo: "   " }, { motivo: "perdio" }, { motivo: 42 }]) {
      const res = await resetTotp({
        locals: LOCALS,
        params: { id: USER_ID },
        request: peticion(body),
      });
      expect(res.status).toBe(400);
    }
  });

  it("manda el motivo escrito, recortado, y nunca uno inventado", async () => {
    const capturado = { url: "", init: null };
    globalThis.fetch = vi.fn(async (url, init) => {
      capturado.url = String(url);
      capturado.init = init;
      return new Response(JSON.stringify({ estado: "revocado" }), { status: 200 });
    });

    const res = await resetTotp({
      locals: LOCALS,
      params: { id: USER_ID },
      request: peticion({ motivo: `  ${MOTIVO}  ` }),
    });

    expect(res.status).toBe(200);
    expect(capturado.url).toContain(`/auth/users/${USER_ID}/totp/revoke`);
    expect(capturado.init.method).toBe("POST");
    expect(JSON.parse(capturado.init.body)).toEqual({ motivo: MOTIVO });
    // La identidad sale de la sesión del servidor, jamás del cuerpo.
    expect(capturado.init.headers["X-Tenant-Slug"]).toBe("icetex");
    expect(capturado.init.headers["X-User-Id"]).toBe(LOCALS.user.sub);
  });

  it("no reenvía campos ajenos que vengan en el cuerpo", async () => {
    let enviado = null;
    globalThis.fetch = vi.fn(async (_url, init) => {
      enviado = JSON.parse(init.body);
      return new Response("{}", { status: 200 });
    });
    await resetTotp({
      locals: LOCALS,
      params: { id: USER_ID },
      request: peticion({ motivo: MOTIVO, user_id: "otro", is_root: true, actor: "suplantado" }),
    });
    expect(Object.keys(enviado)).toEqual(["motivo"]);
  });

  it("propaga el 409 de «no puede restablecerse a sí mismo» tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            detail: {
              error: "restablecimiento_propio_no_permitido",
              detail: "Use «Mi perfil».",
              status: 409,
            },
          }),
          { status: 409 },
        ),
    );
    const res = await resetTotp({
      locals: LOCALS,
      params: { id: USER_ID },
      request: peticion({ motivo: MOTIVO }),
    });
    expect(res.status).toBe(409);
    const cuerpo = await res.json();
    expect(cuerpo.detail.error).toBe("restablecimiento_propio_no_permitido");
  });

  it("503 si el gateway no responde", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await resetTotp({
      locals: LOCALS,
      params: { id: USER_ID },
      request: peticion({ motivo: MOTIVO }),
    });
    expect(res.status).toBe(503);
  });
});
