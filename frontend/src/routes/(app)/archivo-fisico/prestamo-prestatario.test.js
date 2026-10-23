// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * C-5 / C11: el prestatario del préstamo es una unión discriminada
 * (interno por `auth_users.id` | externo con documento) y el cliente NUNCA
 * decide quién entrega (`prestado_por`).
 *
 * Mutaciones que deben ponerlo en rojo:
 *  - reenviar `prestado_por` o `prestado_a` del cuerpo al backend;
 *  - aceptar un externo sin tipo/número de documento;
 *  - aceptar un `user_id` que no es UUID (p. ej. un username);
 *  - copiar campos desconocidos del cuerpo (mass-assignment).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { construirPayloadPrestamo, prestatarioEtiqueta } from "$lib/utils/prestamo.js";
import { POST } from "./api/unidades/[id]/prestamos/+server.js";

const UID = "7ce8f33d-0dd9-4ecc-9785-423f731032a8";
const LOCALS = { accessToken: "t", user: { tenant_slug: "demo", sub: "kc-sub" } };
const FECHA = "2099-01-31";

describe("construirPayloadPrestamo", () => {
  it("interno: solo tipo y user_id (control positivo)", () => {
    const r = construirPayloadPrestamo({ tipo: "interno", user_id: UID, extra: "x" }, FECHA);
    expect(r).toEqual({
      ok: { prestatario: { tipo: "interno", user_id: UID }, fecha_devolucion_esperada: FECHA },
    });
  });

  it("interno con algo que no es UUID se rechaza", () => {
    expect("error" in construirPayloadPrestamo({ tipo: "interno", user_id: "jperez" }, FECHA)).toBe(
      true,
    );
  });

  it("externo exige nombre y documento; los opcionales vacíos no viajan", () => {
    const r = construirPayloadPrestamo(
      {
        tipo: "externo",
        nombre: "Ana Ruiz",
        documento_tipo: "CC",
        documento_numero: " 123 ",
        entidad: "",
        correo: "a@b.co",
      },
      FECHA,
      "  caja 4 ",
    );
    expect(r).toEqual({
      ok: {
        prestatario: {
          tipo: "externo",
          nombre: "Ana Ruiz",
          documento_tipo: "CC",
          documento_numero: "123",
          correo: "a@b.co",
        },
        fecha_devolucion_esperada: FECHA,
        observaciones: "caja 4",
      },
    });
  });

  it.each([
    [{ tipo: "externo", nombre: "Ana", documento_tipo: "CC", documento_numero: "1" }],
    [{ tipo: "externo", nombre: "Ana Ruiz", documento_tipo: "XX", documento_numero: "1" }],
    [{ tipo: "externo", nombre: "Ana Ruiz", documento_tipo: "CC", documento_numero: " " }],
    [{ tipo: "otro" }],
    [null],
  ])("rechaza formas inválidas %#", (p) => {
    expect("error" in construirPayloadPrestamo(p, FECHA)).toBe(true);
  });
});

describe("prestatarioEtiqueta", () => {
  it("nombre + tipo", () => {
    expect(
      prestatarioEtiqueta({ prestatario_nombre: "Ana Ruiz", prestatario_tipo: "externo" }),
    ).toBe("Ana Ruiz (externo)");
  });
});

describe("POST /archivo-fisico/api/unidades/[id]/prestamos", () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 201,
      json: async () => ({ id: "p1" }),
    });
  });

  const llamar = (body) =>
    POST({
      locals: LOCALS,
      params: { id: "u1" },
      request: { json: async () => body },
    });

  it("reenvía la unión y nunca prestado_por / prestado_a", async () => {
    await llamar({
      prestatario: { tipo: "interno", user_id: UID },
      fecha_devolucion_esperada: FECHA,
      prestado_por: "intruso",
      prestado_a: "intruso",
    });
    const enviado = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(enviado).toEqual({
      prestatario: { tipo: "interno", user_id: UID },
      fecha_devolucion_esperada: FECHA,
    });
    expect(enviado).not.toHaveProperty("prestado_por");
    expect(enviado).not.toHaveProperty("prestado_a");
  });

  it("externo sin documento: 422 sin tocar el backend", async () => {
    const res = await llamar({
      prestatario: { tipo: "externo", nombre: "Ana Ruiz" },
      fecha_devolucion_esperada: FECHA,
    });
    expect(res.status).toBe(422);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("sin sesión: 401", async () => {
    const res = await POST({
      locals: {},
      params: { id: "u1" },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(401);
  });
});
