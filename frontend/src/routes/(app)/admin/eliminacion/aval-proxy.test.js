// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * aval-proxy.test.js — Los proxies del aval y de la anulación (RF-TRD-07, fase 3).
 *
 * Dos cosas que estos proxies no se pueden permitir:
 *
 *  - **Colapsar el 409.** El backend distingue «el plazo de publicidad sigue
 *    abierto hasta el X» de «el lote no está publicado», y esa diferencia es
 *    justo la que dice si hay que esperar o corregir. Un proxy que lo redujera
 *    a «no se pudo avalar» mandaría a buscar un permiso a quien lo que le
 *    faltan son cuatro días.
 *  - **Dejar pasar un aval a medias.** Las tres constancias se exigen aquí,
 *    en el backend y en un CHECK de la base: un aval sin acta, sin fecha o sin
 *    acto administrativo es una afirmación no verificada.
 */
import { describe, it, expect, vi } from "vitest";

import { POST as avalar } from "./api/[loteId]/avalar/+server.js";
import { POST as anular } from "./api/[loteId]/anular/+server.js";

const LOTE = "11111111-1111-4111-8111-111111111111";
const AVAL = {
  acta_comite: "Acta 07 del Comité",
  fecha_aval: "2026-08-20",
  acto_administrativo: "Resolución 512 de 2026",
};

function llamada(body, { token = "tok", respuesta } = {}) {
  const fetch = vi.fn(async () => ({
    ok: respuesta ? respuesta.ok : true,
    status: respuesta ? respuesta.status : 200,
    json: async () => (respuesta ? respuesta.body : { estado: "avalado" }),
  }));
  const event = {
    params: { loteId: LOTE },
    request: { json: async () => body },
    locals: { accessToken: token, user: { tenant_slug: "icetex", sub: "u-1" } },
    fetch,
  };
  return { event, fetch };
}

describe("proxy del aval", () => {
  it("reenvía las tres constancias, recortadas", async () => {
    const { event, fetch } = llamada({
      acta_comite: "  Acta 07 del Comité  ",
      fecha_aval: "2026-08-20",
      acto_administrativo: " Resolución 512 de 2026 ",
    });

    const res = await avalar(event);

    expect(res.status).toBe(200);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(AVAL);
  });

  it.each(["acta_comite", "fecha_aval", "acto_administrativo"])(
    "sin %s no llega al backend",
    async (campo) => {
      const cuerpo = { ...AVAL };
      delete cuerpo[campo];
      const { event, fetch } = llamada(cuerpo);

      const res = await avalar(event);

      expect(res.status).toBe(400);
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it("el 409 del backend llega ENTERO, con el motivo y la fecha", async () => {
    const detalle = {
      error: "publicidad_en_curso",
      detail: "El plazo para oponerse sigue abierto hasta el 2026-09-30: …",
      status: 409,
    };
    const { event } = llamada(AVAL, {
      respuesta: { ok: false, status: 409, body: { detail: detalle } },
    });

    const res = await avalar(event);

    expect(res.status).toBe(409);
    expect((await res.json()).detail).toEqual(detalle);
  });

  it("sin sesión no se llama al backend", async () => {
    const { event, fetch } = llamada(AVAL, { token: "" });
    const res = await avalar(event);
    expect(res.status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("un identificador de lote inválido no viaja", async () => {
    const { event, fetch } = llamada(AVAL);
    event.params.loteId = "../../otro";
    const res = await avalar(event);
    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("proxy de la anulación", () => {
  it("exige un motivo con contenido, igual que el CHECK de la base", async () => {
    const { event, fetch } = llamada({ motivo: "no" });
    const res = await anular(event);
    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reenvía el motivo recortado", async () => {
    const { event, fetch } = llamada({ motivo: "  Se retira por error material  " });
    await anular(event);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      motivo: "Se retira por error material",
    });
  });
});
