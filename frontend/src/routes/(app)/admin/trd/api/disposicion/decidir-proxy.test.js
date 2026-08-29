// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * decidir-proxy.test.js — El proxy de la decisión sobre un vencimiento.
 *
 * Lo importante no es lo que reenvía, es **lo que no**: la fecha del
 * vencimiento y el código de disposición no se aceptan del cliente en absoluto
 * —los resuelve el backend desde el snapshot congelado—, y `fase` y `decision`
 * pasan por listas CERRADAS. Si la fecha viajara por aquí, confirmar una
 * eliminación sería cuestión de mandar la adecuada.
 */
import { describe, it, expect, vi } from "vitest";

import { POST } from "./[expedienteId]/decidir/+server.js";

const EXP = "11111111-1111-1111-1111-111111111111";

function llamada({ body, token = "tok" } = {}) {
  const fetch = vi.fn(async () => ({
    ok: true,
    status: 201,
    json: async () => ({ decision: "confirmada", ejecutada: false }),
  }));
  const event = {
    params: { expedienteId: EXP },
    request: { json: async () => body },
    locals: { accessToken: token, user: { tenant_slug: "icetex", sub: "u-1" } },
    fetch,
  };
  return { event, fetch };
}

describe("el proxy no deja que el cliente elija sobre qué vencimiento decide", () => {
  it("descarta la fecha y el código que vengan en el cuerpo", async () => {
    const { event, fetch } = llamada({
      body: {
        fase: "central",
        decision: "confirmada",
        fecha_vencimiento: "1999-01-01",
        disposition_code: "CT",
      },
    });

    await POST(event);

    const enviado = JSON.parse(fetch.mock.calls[0][1].body);
    expect(enviado).toEqual({ fase: "central", decision: "confirmada" });
    expect(enviado.fecha_vencimiento).toBeUndefined();
    expect(enviado.disposition_code).toBeUndefined();
  });

  it("una fase fuera de la lista cerrada no llega al backend", async () => {
    const { event, fetch } = llamada({ body: { fase: "historico", decision: "confirmada" } });
    const res = await POST(event);
    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("una decisión inventada no llega al backend", async () => {
    const { event, fetch } = llamada({ body: { fase: "central", decision: "ejecutada" } });
    const res = await POST(event);
    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sin sesión no se propaga nada", async () => {
    const { event, fetch } = llamada({
      body: { fase: "central", decision: "confirmada" },
      token: "",
    });
    const res = await POST(event);
    expect(res.status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("un identificador que no es UUID se corta aquí", async () => {
    const { event, fetch } = llamada({ body: { fase: "central", decision: "confirmada" } });
    event.params.expedienteId = "../../otro";
    const res = await POST(event);
    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("aplazar exige lo que hace útil un aplazamiento", () => {
  it("motivo corto: se explica antes de llegar a un 422 genérico", async () => {
    const { event, fetch } = llamada({
      body: {
        fase: "central",
        decision: "aplazada",
        motivo: "no",
        revisar_de_nuevo_el: "2027-01-01",
      },
    });
    const res = await POST(event);
    expect(res.status).toBe(400);
    expect((await res.json()).detail).toMatch(/10 caracteres/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sin fecha de regreso no es aplazar", async () => {
    const { event, fetch } = llamada({
      body: {
        fase: "central",
        decision: "aplazada",
        motivo: "pendiente de concepto jurídico de la oficina asesora",
      },
    });
    const res = await POST(event);
    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("aplazamiento completo sí viaja", async () => {
    const { event, fetch } = llamada({
      body: {
        fase: "central",
        decision: "aplazada",
        motivo: "pendiente de concepto jurídico de la oficina asesora",
        revisar_de_nuevo_el: "2027-01-01",
      },
    });
    await POST(event);
    const enviado = JSON.parse(fetch.mock.calls[0][1].body);
    expect(enviado.revisar_de_nuevo_el).toBe("2027-01-01");
    expect(enviado.motivo).toMatch(/concepto jurídico/);
  });
});
