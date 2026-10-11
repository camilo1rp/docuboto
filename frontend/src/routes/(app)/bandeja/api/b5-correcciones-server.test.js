// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Ronda de corrección B5 (ADR-032) en los proxies: mensajes 422 en español sin
 * nombres técnicos, `dest_dept` fuera del allowlist de respuesta, motivo del
 * renombre y carga masiva por código.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as assignPost } from "./assign/+server.js";
import { POST as transferPost } from "./transfer/+server.js";
import { POST as devolverPost } from "./[id]/devolver/+server.js";
import { POST as respuestaPost } from "./[id]/respuesta/+server.js";
import { PATCH as depPatch } from "../../admin/dependencias/api/[id]/+server.js";
import { POST as lotePost } from "../../admin/interoperabilidad/api/lotes/documentos/+server.js";

const LOCALS = { accessToken: "t", user: { tenant_slug: "icetex", sub: "u1" } };
const UUID = "11111111-1111-1111-1111-111111111111";
const req = (body) => ({ json: async () => body });

afterEach(() => vi.restoreAllMocks());

describe("422 sin nombres técnicos", () => {
  it.each([
    ["assign", assignPost, { radicado_id: UUID, tracking_number: "X" }],
    ["transfer", transferPost, { radicado_id: UUID, tracking_number: "X" }],
  ])("%s sin dependencia: mensaje en español", async (_n, handler, body) => {
    globalThis.fetch = vi.fn();
    const res = await handler({ locals: LOCALS, request: req(body) });
    const data = await res.json();
    expect(res.status).toBe(422);
    expect(data.detail).toBe("Seleccione la dependencia destino del catálogo.");
    expect(data.detail).not.toMatch(/_code|radicado_id|tracking_number/);
  });

  it("devolver sin dependencia", async () => {
    globalThis.fetch = vi.fn();
    const res = await devolverPost({ locals: LOCALS, params: { id: UUID }, request: req({}) });
    expect((await res.json()).detail).not.toMatch(/_code/);
  });

  it("assign con radicado inválido no expone nombres de campo", async () => {
    globalThis.fetch = vi.fn();
    const res = await assignPost({ locals: LOCALS, request: req({ radicado_id: "x" }) });
    expect((await res.json()).detail).not.toMatch(/radicado_id|tracking_number/);
  });
});

describe("respuesta: dest_dept ya no viaja", () => {
  it("descarta el nombre libre y conserva el código", async () => {
    let sent;
    globalThis.fetch = vi.fn(async (_u, init) => {
      sent = JSON.parse(init.body);
      return new Response("{}", { status: 201 });
    });
    await respuestaPost({
      locals: LOCALS,
      params: { id: UUID },
      request: req({ dest_dept: "Jurídica", dest_dept_code: 7 }),
    });
    expect(sent).toEqual({ dest_dept_code: 7 });
  });
});

describe("renombre con motivo", () => {
  const call = (body) => depPatch({ locals: LOCALS, params: { id: UUID }, request: req(body) });
  it("reenvía el motivo recortado", async () => {
    let sent;
    globalThis.fetch = vi.fn(async (_u, init) => {
      sent = JSON.parse(init.body);
      return new Response("{}", { status: 200 });
    });
    await call({ nombre: "Nueva", motivo: "  Acuerdo 12 de 2026 de reestructuración " });
    expect(sent.motivo).toBe("Acuerdo 12 de 2026 de reestructuración");
  });
  it("rechaza un motivo demasiado corto sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn();
    const res = await call({ nombre: "Nueva", motivo: "corto" });
    expect(res.status).toBe(400);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe("carga masiva por código", () => {
  it("envía dest_dept_code/origin_dept_code y nunca dest_dept", async () => {
    let sent;
    globalThis.fetch = vi.fn(async (_u, init) => {
      sent = JSON.parse(init.body);
      return new Response("{}", { status: 202 });
    });
    await lotePost({
      locals: LOCALS,
      request: req({
        documents: [
          { doc_type: "E", subject: "a", dest_dept: "Jurídica", dest_dept_code: "7" },
          { doc_type: "S", subject: "b", origin_dept_code: 3 },
        ],
      }),
    });
    expect(sent.documents[0]).toMatchObject({ dest_dept_code: 7 });
    expect(sent.documents[0]).not.toHaveProperty("dest_dept");
    expect(sent.documents[1]).toMatchObject({ origin_dept_code: 3 });
  });
});
