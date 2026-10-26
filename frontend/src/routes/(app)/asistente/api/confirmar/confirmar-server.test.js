// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /asistente/api/confirmar — el proxy que ejecuta un acto ya aprobado.
 *
 * La invariante que sostiene todo el diseño de escritura del asistente: **el
 * cuerpo lleva solo el token (y la frase)**. Si este proxy reenviara argumentos,
 * la tarjeta describiría un acto y se ejecutaría otro — y la confirmación de la
 * persona pasaría a valer para algo que nunca leyó.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { POST } from "./+server.js";

const LOCALS = {
  accessToken: "tok-servidor",
  user: { tenant_slug: "demo", sub: "u-1", preferred_username: "ana" },
};

function req(body) {
  return { json: async () => body };
}

let fetchMock;

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ kind: "result", ejecutado: true }),
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("confirmar — sesión y forma del cuerpo", () => {
  it("sin sesión no llama al gateway", async () => {
    const res = await POST({ locals: {}, request: req({ token: "op1.a.b" }) });
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sin token no llama al gateway", async () => {
    const res = await POST({ locals: LOCALS, request: req({}) });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "propuesta_invalida" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("un cuerpo no-JSON no llama al gateway", async () => {
    const res = await POST({
      locals: LOCALS,
      request: {
        json: async () => {
          throw new Error("no es json");
        },
      },
    });
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("confirmar — SOLO el token viaja (la garantía del diseño)", () => {
  it("los argumentos que meta el cliente NO se reenvían", async () => {
    await POST({
      locals: LOCALS,
      request: req({
        token: "op1.a.b",
        // Un cliente hostil intentando que se ejecute algo distinto de lo que
        // la persona leyó en la tarjeta.
        subject: "LO QUE NADIE APROBÓ",
        doc_type: "S",
        args: { document_id: "otro" },
      }),
    });

    const enviado = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(enviado).toEqual({ token: "op1.a.b" });
  });

  it("la frase sí viaja, porque el backend la exige para lo irreversible", async () => {
    await POST({ locals: LOCALS, request: req({ token: "op1.a.b", frase: "CONFIRMO" }) });

    const enviado = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(enviado).toEqual({ token: "op1.a.b", frase: "CONFIRMO" });
  });

  it("el token de sesión sale de locals, nunca del cuerpo (D-05)", async () => {
    await POST({
      locals: LOCALS,
      request: req({ token: "op1.a.b", accessToken: "token-inventado-por-el-cliente" }),
    });

    const headers = fetchMock.mock.calls[0][1].headers;
    expect(headers.Authorization).toBe("Bearer tok-servidor");
    expect(headers["X-Tenant-Slug"]).toBe("demo");
    expect(headers["X-User-Id"]).toBe("u-1");
  });
});

describe("confirmar — los errores no se disfrazan", () => {
  it("un 400 del backend (falta la frase) se propaga con su motivo", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({
        detail: { error: "confirmacion_requerida", detail: "Escriba «CONFIRMO»." },
      }),
    });

    const res = await POST({ locals: LOCALS, request: req({ token: "op1.a.b" }) });

    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.detail.error).toBe("confirmacion_requerida");
  });

  it("un 403 (le retiraron el permiso entre proponer y confirmar) NO se convierte en 503", async () => {
    // Que el asistente esté disponible y que esta persona pueda hacer esto son
    // dos cosas distintas. Colapsarlas dejaría a alguien esperando a que
    // "vuelva el servicio" por un permiso que ya no tiene.
    fetchMock.mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: { error: "permiso_insuficiente" } }),
    });

    const res = await POST({ locals: LOCALS, request: req({ token: "op1.a.b" }) });
    expect(res.status).toBe(403);
  });

  it("el gateway caído sí da 503 y nunca finge que se ejecutó", async () => {
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));

    const res = await POST({ locals: LOCALS, request: req({ token: "op1.a.b" }) });

    expect(res.status).toBe(503);
    expect(await res.json()).toMatchObject({ error: "assistant_unavailable" });
  });
});
