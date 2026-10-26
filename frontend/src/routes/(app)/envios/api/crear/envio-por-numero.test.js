// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Registrar un envío postal era imposible desde la interfaz.
 *
 * El formulario pedía el UUID del radicado y su ayuda decía «Disponible en el
 * detalle del radicado en la Bandeja». Ese detalle NO muestra el UUID ni deja
 * copiarlo: para enviar una carta había que sacar el identificador de la base
 * de datos. La instrucción, además, era falsa — que es lo que hace perder media
 * hora antes de sospechar del formulario.
 *
 * Ahora se admite el número de radicado —lo que la persona tiene delante— y el
 * servidor lo resuelve. El UUID se sigue aceptando: quien lo tenga (una
 * integración, un enlace) no se queda fuera.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./+server.js";

const UUID = "43632304-cdea-47c2-921b-1675da182480";
const LOCALS = { accessToken: "t", user: { tenant_slug: "demo", id: "u1" } };

function peticion(body) {
  return { json: async () => body };
}

describe("POST /envios/api/crear — identificar el radicado", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("resuelve el número de radicado y envía el UUID al gateway", async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ results: [{ id: UUID, tracking_number: "2026-DEMO-S-000013" }] }),
      })
      .mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({ id: "env-1" }) });

    const res = await POST({
      locals: LOCALS,
      request: peticion({ radicado_id: "2026-DEMO-S-000013", operador: "4-72" }),
    });

    expect(res.status).toBe(201);
    const urlCreacion = global.fetch.mock.calls[1][0];
    expect(urlCreacion).toContain(`/documents/${UUID}/envios`);
  });

  it("con un UUID no gasta una búsqueda: va directo", async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({}) });

    await POST({ locals: LOCALS, request: peticion({ radicado_id: UUID, operador: "4-72" }) });

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch.mock.calls[0][0]).toContain(`/documents/${UUID}/envios`);
  });

  it("una coincidencia aproximada NO vale: correo físico no se manda por parecido", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ results: [{ id: "otro", tracking_number: "2026-DEMO-S-000099" }] }),
    });

    const res = await POST({
      locals: LOCALS,
      request: peticion({ radicado_id: "2026-DEMO-S-000013", operador: "4-72" }),
    });

    expect(res.status).toBe(404);
    const cuerpo = await res.json();
    expect(cuerpo.error).toBe("radicado_no_encontrado");
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it("si la búsqueda falla, no inventa un destinatario", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });

    const res = await POST({
      locals: LOCALS,
      request: peticion({ radicado_id: "2026-DEMO-S-000013", operador: "4-72" }),
    });

    expect(res.status).toBe(404);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });
});
