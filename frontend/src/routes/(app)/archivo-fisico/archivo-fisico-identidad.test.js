// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El archivo físico era inoperable desde el navegador.
 *
 * Ocho campos de una sola pantalla pedían un UUID escrito a mano: la ubicación
 * superior, la ubicación de una unidad nueva, el filtro de unidades, el
 * expediente a vincular, el expediente a localizar, la unidad a prestar y los
 * dos filtros del FUID. Ninguna vista del sistema muestra esos identificadores
 * ni deja copiarlos, así que crear la jerarquía de un depósito, prestar una
 * caja o consultar el inventario exigía abrir la base de datos.
 *
 * Aquí se prueba la mitad que vive en el servidor: que un expediente se puede
 * referir por su CÓDIGO, y que la resolución exige coincidencia exacta.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { resolverExpediente } from "$lib/server/expedienteRef.js";
import { GET as LOCALIZAR } from "./api/expedientes/[id]/unidades/+server.js";
import { POST as VINCULAR } from "./api/unidades/[id]/expedientes/+server.js";

const UUID = "7ce8f33d-0dd9-4ecc-9785-423f731032a8";
const LOCALS = { accessToken: "t", user: { tenant_slug: "demo", id: "u1" } };

describe("resolverExpediente", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("resuelve el código del expediente a su UUID", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ hits: [{ id: UUID, code: "EXP-2026-0020" }] }),
    });

    expect(await resolverExpediente("EXP-2026-0020", "t", "demo")).toBe(UUID);
  });

  it("con un UUID no gasta una búsqueda: va directo", async () => {
    expect(await resolverExpediente(UUID, "t", "demo")).toBe(UUID);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("una coincidencia aproximada NO vale", async () => {
    // La búsqueda es de texto completo: devuelve parecidos por definición.
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ hits: [{ id: "otro", code: "EXP-2026-0021" }] }),
    });

    expect(await resolverExpediente("EXP-2026-0020", "t", "demo")).toBeNull();
  });

  it("dos expedientes con el mismo código no se desempatan solos", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        hits: [
          { id: "a", code: "EXP-2026-0020" },
          { id: "b", code: "EXP-2026-0020" },
        ],
      }),
    });

    expect(await resolverExpediente("EXP-2026-0020", "t", "demo")).toBeNull();
  });

  it("si la búsqueda falla, no inventa un expediente", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });
    expect(await resolverExpediente("EXP-2026-0020", "t", "demo")).toBeNull();
  });
});

describe("localizar y vincular admiten el número de expediente", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("localizar resuelve el código antes de preguntar dónde está", async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ hits: [{ id: UUID, code: "EXP-2026-0020" }] }),
      })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => [] });

    const res = await LOCALIZAR({ locals: LOCALS, params: { id: "EXP-2026-0020" } });

    expect(res.status).toBe(200);
    expect(global.fetch.mock.calls[1][0]).toContain(`/expedientes/${UUID}/unidades`);
  });

  it("vincular envía al gateway el UUID, no el código", async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ hits: [{ id: UUID, code: "EXP-2026-0020" }] }),
      })
      .mockResolvedValueOnce({ ok: true, status: 204, json: async () => ({}) });

    const res = await VINCULAR({
      locals: LOCALS,
      params: { id: "unidad-1" },
      request: { json: async () => ({ expediente_id: "EXP-2026-0020", folio_inicio: 1 }) },
    });

    expect(res.status).toBe(200);
    const enviado = JSON.parse(global.fetch.mock.calls[1][1].body);
    expect(enviado.expediente_id).toBe(UUID);
  });

  it("un expediente que no existe no vincula nada", async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, json: async () => ({ hits: [] }) });

    const res = await VINCULAR({
      locals: LOCALS,
      params: { id: "unidad-1" },
      request: { json: async () => ({ expediente_id: "EXP-INVENTADO" }) },
    });

    expect(res.status).toBe(404);
    expect((await res.json()).error).toBe("expediente_no_encontrado");
    // Y sobre todo: no llegó a llamar al gateway.
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });
});
