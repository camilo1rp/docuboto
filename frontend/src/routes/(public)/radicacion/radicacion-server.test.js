// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Ventanilla en línea: SIN sesión, y el multipart llega íntegro al backend. */
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));

const { POST, GET } = await import("./api/+server.js");

function eventoPost(respuesta, cuerpo = new FormData()) {
  return {
    request: { formData: async () => cuerpo },
    fetch: vi.fn().mockResolvedValue(respuesta),
  };
}

describe("/radicacion/api", () => {
  it("radica SIN sesión y devuelve el acuse", async () => {
    const acuse = {
      tracking_number: "2026-DEMO-E-000020",
      verification_code: "abc",
      dependencia_destino: "Ventanilla Única de Radicación",
    };
    const ev = eventoPost({ ok: true, status: 201, json: async () => acuse });

    const res = await POST(ev);

    expect(res.status).toBe(201);
    expect(await res.json()).toEqual(acuse);
    expect(String(ev.fetch.mock.calls[0][0])).toContain("/public/demo/ventanilla");
  });

  it("reenvía el multipart TAL CUAL, sin reconstruirlo", async () => {
    // Los límites de anexos los impone el backend. Si el proxy rehiciera el
    // cuerpo, habría dos formatos que pueden divergir — y el que se puede
    // saltar sería el único que ve el ciudadano.
    const cuerpo = new FormData();
    cuerpo.append("datos", JSON.stringify({ asunto: "x" }));
    const ev = eventoPost({ ok: true, status: 201, json: async () => ({}) }, cuerpo);

    await POST(ev);

    expect(ev.fetch.mock.calls[0][1].body).toBe(cuerpo);
  });

  it("reenvía el campo `altcha` de `datos` íntegro, sin tocarlo", async () => {
    // El reto viaja DENTRO del JSON de `datos`: si el proxy reconstruyera el cuerpo
    // lo perdería, y el backend rechazaría toda radicación con 422.
    const cuerpo = new FormData();
    const datos = JSON.stringify({ asunto: "x", altcha: "eyJhbGdvcml0aG0iOiJTSEEtMjU2In0=" });
    cuerpo.append("datos", datos);
    const ev = eventoPost({ ok: true, status: 201, json: async () => ({}) }, cuerpo);

    await POST(ev);

    expect(ev.fetch.mock.calls[0][1].body.get("datos")).toBe(datos);
  });

  it("devuelve el código `antirrobots_usado` del backend con su estado", async () => {
    const ev = eventoPost({
      ok: false,
      status: 409,
      json: async () => ({ detail: { error: "antirrobots_usado", detail: "x", status: 409 } }),
    });
    const res = await POST(ev);
    expect(res.status).toBe(409);
    expect((await res.json()).error).toBe("antirrobots_usado");
  });

  it("reenvía la Idempotency-Key del navegador y no inventa una si no viene", async () => {
    // AREA-02-07: la clave la genera el cliente; el proxy solo la deja pasar. Sin ella
    // no se añade cabecera alguna (el comportamiento previo se conserva).
    const conClave = {
      request: {
        formData: async () => new FormData(),
        headers: new Headers({ "Idempotency-Key": "k".repeat(24) }),
      },
      fetch: vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({}) }),
    };
    await POST(conClave);
    expect(conClave.fetch.mock.calls[0][1].headers).toEqual({ "Idempotency-Key": "k".repeat(24) });

    const sinClave = eventoPost({ ok: true, status: 201, json: async () => ({}) });
    await POST(sinClave);
    expect(sinClave.fetch.mock.calls[0][1].headers).toBeUndefined();
  });

  it("propaga el rechazo del backend en vez de disimularlo", async () => {
    const ev = eventoPost({
      ok: false,
      status: 422,
      json: async () => ({
        detail: { error: "anexo_rechazado", detail: "Solo se admiten archivos PDF, JPG…" },
      }),
    });

    const res = await POST(ev);
    const cuerpo = await res.json();

    expect(res.status).toBe(422);
    expect(cuerpo.error).toBe("anexo_rechazado");
    expect(cuerpo.detail).toMatch(/PDF/);
  });

  it("si la entidad no publica tipos, la lista viene vacía y no revienta", async () => {
    const ev = { fetch: vi.fn().mockRejectedValue(new Error("caído")) };
    const res = await GET(ev);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([]);
  });
});
