// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * assistant.service.test.js — Cliente del asistente (lado navegador, D-05).
 *
 * D-05: `sendMessage()` debe llamar SIEMPRE al proxy relativo `/asistente/api/message`
 * — nunca a una URL absoluta del gateway (el cliente no conoce `PUBLIC_API_URL` para
 * esto ni debería necesitarlo: el proxy SSR es el único que ve el token).
 * Cubre el mapeo status → `AssistantError.kind`.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { sendMessage, AssistantError } from "./assistant.service.js";

function stubFetch(impl) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("assistant.service — transporte (D-05: solo el proxy relativo)", () => {
  it("llama a POST /asistente/api/message (nunca una URL de gateway)", async () => {
    const fetchMock = stubFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ conversation_id: "c-1", message: { id: "m-1", texto: "ok" } }),
    }));

    await sendMessage({ mensaje: "hola" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/asistente/api/message");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ mensaje: "hola" });
  });

  it("solo incluye conversation_id en el body cuando está presente", async () => {
    const fetchMock = stubFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    }));
    await sendMessage({ mensaje: "hola", conversationId: "c-9" });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ mensaje: "hola", conversation_id: "c-9" });
  });

  it("éxito: devuelve el JSON del proxy tal cual", async () => {
    const payload = {
      conversation_id: "c-1",
      message: { id: "m-1", texto: "ok", html: null, ts: "t" },
    };
    stubFetch(async () => ({ ok: true, status: 200, json: async () => payload }));
    const result = await sendMessage({ mensaje: "hola" });
    expect(result).toEqual(payload);
  });
});

describe("assistant.service — mapeo de errores a AssistantError.kind", () => {
  it("fetch lanza (red caída) → kind='network'", async () => {
    stubFetch(async () => {
      throw new Error("network down");
    });
    await expect(sendMessage({ mensaje: "hola" })).rejects.toMatchObject({
      name: "AssistantError",
      kind: "network",
    });
  });

  it("503 / assistant_unavailable → kind='unavailable'", async () => {
    stubFetch(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ error: "assistant_unavailable", detail: "aún no disponible" }),
    }));
    let caught;
    try {
      await sendMessage({ mensaje: "hola" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(AssistantError);
    expect(caught.kind).toBe("unavailable");
    expect(caught.detail).toBe("aún no disponible");
  });

  it("401 → kind='auth'", async () => {
    stubFetch(async () => ({
      ok: false,
      status: 401,
      json: async () => ({ error: "unauthenticated" }),
    }));
    await expect(sendMessage({ mensaje: "hola" })).rejects.toMatchObject({ kind: "auth" });
  });

  it("400 → kind='invalid'", async () => {
    stubFetch(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "empty_message", detail: "vacío" }),
    }));
    await expect(sendMessage({ mensaje: "" })).rejects.toMatchObject({
      kind: "invalid",
      detail: "vacío",
    });
  });

  it("otro status inesperado (ej. 500) → kind='network'", async () => {
    stubFetch(async () => ({
      ok: false,
      status: 500,
      json: async () => ({ error: "internal" }),
    }));
    await expect(sendMessage({ mensaje: "hola" })).rejects.toMatchObject({ kind: "network" });
  });

  it("respuesta de error sin cuerpo JSON no revienta (fallback a mensaje genérico)", async () => {
    stubFetch(async () => ({
      ok: false,
      status: 503,
      json: async () => {
        throw new SyntaxError("no body");
      },
    }));
    await expect(sendMessage({ mensaje: "hola" })).rejects.toMatchObject({ kind: "unavailable" });
  });
});
