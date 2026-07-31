// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * firmas-fase3-server.test.js — Proxies BFF nuevos/ampliados de /firmas
 * (Fase 3 cierre de desfase API↔UI, E06 Inc.2/RF-FIR-13, D-05).
 *
 * Cubre: guard 401 por handler, allowlist anti mass-assignment, ids de ruta
 * escapados/validados, y que el segundo factor (challenge/otp/totp_code) se
 * propaga tal cual al gateway sin que el proxy reinterprete sus errores.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as challengePost } from "./[id]/challenge/+server.js";
import { POST as signPost } from "./[id]/sign/+server.js";
import { GET as certificadoGet } from "./certificado/[firmaId]/+server.js";
import { POST as verifyPost } from "./verify/+server.js";
import { POST as requestsPost, GET as requestsGet } from "./requests/+server.js";
import { POST as signXmlPost } from "./sign-xml/+server.js";
import { POST as signPersonalPost } from "./sign-personal/+server.js";
import { GET as documentoGet, POST as documentoPost } from "./documento/[documentId]/+server.js";
import { GET as documentoVerifyGet } from "./documento/[documentId]/verify/+server.js";

const SOLICITUD_ID = "11111111-1111-1111-1111-111111111111";
const FIRMA_ID = "22222222-2222-2222-2222-222222222222";
const DOCUMENT_ID = "33333333-3333-3333-3333-333333333333";
const USER_SUB = "44444444-4444-4444-4444-444444444444";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: USER_SUB, preferred_username: "jperez" },
};

const url = (path) => new URL(`http://localhost${path}`);

afterEach(() => {
  vi.restoreAllMocks();
});

function noFetch() {
  return vi.fn(() => {
    throw new Error("fetch no debió invocarse sin sesión/validación previa");
  });
}

// ---------------------------------------------------------------------------
// /firmas/api/[id]/challenge
// ---------------------------------------------------------------------------
describe("firmas/api/[id]/challenge POST", () => {
  it("401 unauthenticated sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await challengePost({ locals: {}, params: { id: SOLICITUD_ID } });
    expect(res.status).toBe(401);
  });

  it("reenvía el ChallengeResponse del gateway tal cual (canal ya enmascarado)", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init.headers;
      return new Response(
        JSON.stringify({
          challenge_id: "c1",
          factor_tipo: "otp_email",
          canal: "j***@dominio.gov.co",
          expira_at: "2026-07-30T10:05:00Z",
        }),
        { status: 201 },
      );
    });
    const res = await challengePost({ locals: SESSION_LOCALS, params: { id: SOLICITUD_ID } });
    expect(res.status).toBe(201);
    expect(capturedUrl).toContain(`/signature/requests/${SOLICITUD_ID}/challenge`);
    expect(capturedHeaders["X-User-Id"]).toBe(USER_SUB);
    const body = await res.json();
    expect(body.canal).toBe("j***@dominio.gov.co");
  });

  it("429 challenge_cooldown se reenvía sin reinterpretar", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "challenge_cooldown", status: 429 }), {
          status: 429,
        }),
    );
    const res = await challengePost({ locals: SESSION_LOCALS, params: { id: SOLICITUD_ID } });
    expect(res.status).toBe(429);
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await challengePost({ locals: SESSION_LOCALS, params: { id: SOLICITUD_ID } });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/[id]/sign — ampliado con 2FA
// ---------------------------------------------------------------------------
describe("firmas/api/[id]/sign POST — segundo factor (E06 Inc.2)", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await signPost({
      locals: {},
      params: { id: SOLICITUD_ID },
      request: { text: async () => "{}" },
    });
    expect(res.status).toBe(401);
  });

  it("cuerpo vacío: firma sin segundo factor (camino rápido PUBLICA)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ solicitud_id: SOLICITUD_ID, estado: "firmado" }), {
        status: 200,
      });
    });
    const res = await signPost({
      locals: SESSION_LOCALS,
      params: { id: SOLICITUD_ID },
      request: { text: async () => "" },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({});
  });

  it("allowlist: solo challenge_id/otp/totp_code — nunca el body íntegro", async () => {
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ solicitud_id: SOLICITUD_ID, estado: "firmado" }), {
        status: 200,
      });
    });
    const res = await signPost({
      locals: SESSION_LOCALS,
      params: { id: SOLICITUD_ID },
      request: {
        text: async () =>
          JSON.stringify({
            challenge_id: "c1",
            otp: "123456",
            // Campos ajenos que un cliente malicioso podría colar:
            firmante_id: "otro-usuario",
            payload_xml: "<x/>",
          }),
      },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ challenge_id: "c1", otp: "123456" });
    expect(capturedHeaders["X-User-Id"]).toBe(USER_SUB);
  });

  it("409 2fa_requerido se reenvía tal cual (la UI decide pedir el reto)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "2fa_requerido", status: 409 }), { status: 409 }),
    );
    const res = await signPost({
      locals: SESSION_LOCALS,
      params: { id: SOLICITUD_ID },
      request: { text: async () => "{}" },
    });
    expect(res.status).toBe(409);
    expect((await res.json()).error).toBe("2fa_requerido");
  });

  it("401 otp_incorrecto con intentos_restantes se reenvía íntegro", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ error: "otp_incorrecto", intentos_restantes: 2, status: 401 }),
          { status: 401 },
        ),
    );
    const res = await signPost({
      locals: SESSION_LOCALS,
      params: { id: SOLICITUD_ID },
      request: { text: async () => JSON.stringify({ challenge_id: "c1", otp: "000000" }) },
    });
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.error).toBe("otp_incorrecto");
    expect(body.intentos_restantes).toBe(2);
  });

  it("challenge_id vacío: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await signPost({
      locals: SESSION_LOCALS,
      params: { id: SOLICITUD_ID },
      request: { text: async () => JSON.stringify({ challenge_id: "  " }) },
    });
    expect(res.status).toBe(422);
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/certificado/[firmaId]
// ---------------------------------------------------------------------------
describe("firmas/api/certificado/[firmaId] GET", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await certificadoGet({ locals: {}, params: { firmaId: FIRMA_ID } });
    expect(res.status).toBe(401);
  });

  it("firmaId no-UUID: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await certificadoGet({
      locals: SESSION_LOCALS,
      params: { firmaId: "no-es-un-uuid" },
    });
    expect(res.status).toBe(400);
  });

  it("reenvía FirmaResponse con acreditado=false tal cual (nunca lo reescribe)", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(
        JSON.stringify({ firma_id: FIRMA_ID, provider: "nativa", acreditado: false }),
        { status: 200 },
      );
    });
    const res = await certificadoGet({ locals: SESSION_LOCALS, params: { firmaId: FIRMA_ID } });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/signature/firmas/${FIRMA_ID}`);
    const body = await res.json();
    expect(body.acreditado).toBe(false);
  });

  it("404 se reenvía tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "firma_not_found", status: 404 }), { status: 404 }),
    );
    const res = await certificadoGet({ locals: SESSION_LOCALS, params: { firmaId: FIRMA_ID } });
    expect(res.status).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/verify
// ---------------------------------------------------------------------------
describe("firmas/api/verify POST", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await verifyPost({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("sin payload_xml: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await verifyPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ firma_id: FIRMA_ID }) },
    });
    expect(res.status).toBe(422);
  });

  it("firma_id no-UUID: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await verifyPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ firma_id: "x", payload_xml: "<a/>" }) },
    });
    expect(res.status).toBe(422);
  });

  it("allowlist: solo firma_id/payload_xml se reenvían", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ firma_id: FIRMA_ID, valida: true }), { status: 200 });
    });
    const res = await verifyPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          firma_id: FIRMA_ID,
          payload_xml: "<firmado/>",
          otro_campo: "ignorar",
        }),
      },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ firma_id: FIRMA_ID, payload_xml: "<firmado/>" });
  });

  it("valida=false con motivo se reenvía completo (nunca se colapsa a un booleano)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ firma_id: FIRMA_ID, valida: false, motivo: "hmac_no_coincide" }),
          { status: 200 },
        ),
    );
    const res = await verifyPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ firma_id: FIRMA_ID, payload_xml: "<a/>" }) },
    });
    const body = await res.json();
    expect(body.valida).toBe(false);
    expect(body.motivo).toBe("hmac_no_coincide");
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/requests
// ---------------------------------------------------------------------------
describe("firmas/api/requests POST — crear cadena", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await requestsPost({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("sin firmantes: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await requestsPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ objeto_tipo: "radicado", objeto_id: "r1", firmantes: [] }) },
    });
    expect(res.status).toBe(422);
  });

  it("firmantes con un no-UUID: 422 validation_error", async () => {
    globalThis.fetch = noFetch();
    const res = await requestsPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          objeto_tipo: "radicado",
          objeto_id: "r1",
          firmantes: ["no-es-uuid"],
        }),
      },
    });
    expect(res.status).toBe(422);
  });

  it("allowlist: campo a campo, nunca el body íntegro", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ solicitud_id: SOLICITUD_ID }), { status: 201 });
    });
    const res = await requestsPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          objeto_tipo: "radicado",
          objeto_id: "r1",
          firmantes: [USER_SUB],
          titulo: "Mi doc",
          // Campo ajeno que un cliente malicioso podría intentar colar:
          firmante_creador: "otro-usuario",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      objeto_tipo: "radicado",
      objeto_id: "r1",
      firmantes: [USER_SUB],
      titulo: "Mi doc",
    });
  });
});

describe("firmas/api/requests GET — por objeto", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await requestsGet({ locals: {}, url: url("/firmas/api/requests") });
    expect(res.status).toBe(401);
  });

  it("sin objeto_tipo/objeto_id: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await requestsGet({ locals: SESSION_LOCALS, url: url("/firmas/api/requests") });
    expect(res.status).toBe(400);
  });

  it("reenvía la query al gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ solicitud_id: SOLICITUD_ID }), { status: 200 });
    });
    const res = await requestsGet({
      locals: SESSION_LOCALS,
      url: url("/firmas/api/requests?objeto_tipo=radicado&objeto_id=r1"),
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("objeto_tipo=radicado");
    expect(capturedUrl).toContain("objeto_id=r1");
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/sign-xml
// ---------------------------------------------------------------------------
describe("firmas/api/sign-xml POST", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await signXmlPost({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("sin payload_xml: 422 validation_error", async () => {
    globalThis.fetch = noFetch();
    const res = await signXmlPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ objeto_tipo: "indice" }) },
    });
    expect(res.status).toBe(422);
  });

  it("allowlist + X-User-Id de trazabilidad", async () => {
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ firma_id: FIRMA_ID }), { status: 201 });
    });
    const res = await signXmlPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          payload_xml: "<indice/>",
          objeto_tipo: "indice",
          firmante_id: "otro-usuario",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({ payload_xml: "<indice/>", objeto_tipo: "indice" });
    expect(capturedHeaders["X-User-Id"]).toBe(USER_SUB);
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/sign-personal
// ---------------------------------------------------------------------------
describe("firmas/api/sign-personal POST", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await signPersonalPost({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("totp_code inválido (no 6 dígitos): 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await signPersonalPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          payload_xml: "<a/>",
          objeto_tipo: "acta_transferencia",
          objeto_id: "t1",
          totp_code: "123",
        }),
      },
    });
    expect(res.status).toBe(422);
  });

  it("allowlist con totp_code válido", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ firma_id: FIRMA_ID }), { status: 201 });
    });
    const res = await signPersonalPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          payload_xml: "<a/>",
          objeto_tipo: "acta_transferencia",
          objeto_id: "t1",
          totp_code: "123456",
          firmante_id: "otro",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      payload_xml: "<a/>",
      objeto_tipo: "acta_transferencia",
      objeto_id: "t1",
      totp_code: "123456",
    });
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/documento/[documentId] — firma a nivel documento
// ---------------------------------------------------------------------------
describe("firmas/api/documento/[documentId] GET/POST", () => {
  it("GET 401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await documentoGet({ locals: {}, params: { documentId: DOCUMENT_ID } });
    expect(res.status).toBe(401);
  });

  it("GET documentId no-UUID: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await documentoGet({
      locals: SESSION_LOCALS,
      params: { documentId: "no-es-uuid" },
    });
    expect(res.status).toBe(400);
  });

  it("GET 404 no-read-up se reenvía tal cual (NUNCA se reinterpreta como lista vacía)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "document_not_found", status: 404 }), {
          status: 404,
        }),
    );
    const res = await documentoGet({
      locals: SESSION_LOCALS,
      params: { documentId: DOCUMENT_ID },
    });
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.error).toBe("document_not_found");
  });

  it("GET envuelve la lista con total (X-Total-Count)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([{ id: "s1" }]), {
          status: 200,
          headers: { "X-Total-Count": "1" },
        }),
    );
    const res = await documentoGet({
      locals: SESSION_LOCALS,
      params: { documentId: DOCUMENT_ID },
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ signatures: [{ id: "s1" }], total: 1 });
  });

  it("POST content_hash inválido: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await documentoPost({
      locals: SESSION_LOCALS,
      params: { documentId: DOCUMENT_ID },
      request: { json: async () => ({ content_hash: "no-es-un-hash" }) },
    });
    expect(res.status).toBe(422);
  });

  it("POST allowlist + X-Username de trazabilidad", async () => {
    let capturedBody;
    let capturedHeaders;
    const hash = "a".repeat(64);
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "s1" }), { status: 201 });
    });
    const res = await documentoPost({
      locals: SESSION_LOCALS,
      params: { documentId: DOCUMENT_ID },
      request: {
        json: async () => ({
          content_hash: hash,
          reason: "conforme",
          signer_id: "otro-usuario",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({ content_hash: hash, reason: "conforme" });
    expect(capturedHeaders["X-Username"]).toBe("jperez");
  });
});

// ---------------------------------------------------------------------------
// /firmas/api/documento/[documentId]/verify
// ---------------------------------------------------------------------------
describe("firmas/api/documento/[documentId]/verify GET", () => {
  it("401 unauthenticated, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await documentoVerifyGet({
      locals: {},
      params: { documentId: DOCUMENT_ID },
      url: url(`/x?content_hash=${"a".repeat(64)}`),
    });
    expect(res.status).toBe(401);
  });

  it("content_hash inválido: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetch();
    const res = await documentoVerifyGet({
      locals: SESSION_LOCALS,
      params: { documentId: DOCUMENT_ID },
      url: url("/x?content_hash=no-es-un-hash"),
    });
    expect(res.status).toBe(400);
  });

  it("reenvía valid=false con signatures[] (nunca colapsa a booleano)", async () => {
    const hash = "b".repeat(64);
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ content_hash: hash, valid: false, signatures: [] }), {
          status: 200,
        }),
    );
    const res = await documentoVerifyGet({
      locals: SESSION_LOCALS,
      params: { documentId: DOCUMENT_ID },
      url: url(`/x?content_hash=${hash}`),
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.valid).toBe(false);
    expect(body.signatures).toEqual([]);
  });
});
