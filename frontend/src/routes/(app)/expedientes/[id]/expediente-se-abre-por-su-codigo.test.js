// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El expediente se abre por el identificador que el sistema ENSEÑA.
 *
 * La pantalla imprime `EXP-2026-0002` en las migas, en el título y en la
 * carátula, y exigía el UUID en la URL. Copiar de la pantalla y pegar en la
 * barra —o compartir el enlace— daba una tarjeta que decía «500» arriba y
 * «HTTP 422» abajo: dos códigos distintos, ninguno cierto, ninguno accionable.
 * El 422 lo devolvía archive-service al recibir un código donde espera un UUID,
 * y el `load` lo colapsaba a 500 por no ser ni 404 ni 403.
 *
 * Lo delator es que sus PROPIOS proxies hijos ya aceptaban las dos formas con
 * el mismo helper, cuyo docstring dice que el código «es la identidad con la
 * que se trabaja». La corrección estaba escrita al lado y esta pantalla no la
 * usaba.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { load } from "./+page.server.js";

const UUID = "11111111-1111-1111-1111-111111111111";
const CODIGO = "EXP-2026-0002";

const EXPEDIENTE = { id: UUID, code: CODIGO, name: "Expediente de prueba", status: "open", radicados: [] };

function sesion(referencia) {
  return {
    cookies: { get: () => "tok-abc" },
    parent: async () => ({
      user: { tenant_slug: "icetex", sub: "22222222-2222-2222-2222-222222222222" },
    }),
    params: { id: referencia },
  };
}

/** Registra las rutas pedidas para poder afirmar CON QUÉ se llamó al gateway. */
function mockGateway(responder) {
  const vistas = [];
  globalThis.fetch = vi.fn(async (url) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    vistas.push(path);
    const { status, body } = responder(path);
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  });
  return vistas;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("load de /expedientes/[id] — se entra por el código, no solo por el UUID", () => {
  it("resuelve el CÓDIGO a su UUID y carga el expediente", async () => {
    const vistas = mockGateway((path) => {
      if (path.startsWith("/expedientes/search")) {
        return { status: 200, body: { hits: [{ id: UUID, code: CODIGO }] } };
      }
      if (path === `/expedientes/${UUID}`) return { status: 200, body: EXPEDIENTE };
      return { status: 200, body: {} };
    });

    const data = await load(sesion(CODIGO));

    expect(data.expediente.id).toBe(UUID);
    // Y las facetas se piden por el UUID resuelto, no por el código: si alguna
    // se pidiera con el código, archive-service devolvería 422 otra vez.
    expect(vistas.some((p) => p === `/expedientes/${UUID}/unidades`)).toBe(true);
    expect(vistas.some((p) => p.includes(CODIGO) && !p.startsWith("/expedientes/search"))).toBe(
      false,
    );
  });

  it("sigue aceptando el UUID directo, sin gastar una búsqueda", async () => {
    const vistas = mockGateway((path) => {
      if (path === `/expedientes/${UUID}`) return { status: 200, body: EXPEDIENTE };
      return { status: 200, body: {} };
    });

    const data = await load(sesion(UUID));

    expect(data.expediente.id).toBe(UUID);
    expect(vistas.some((p) => p.startsWith("/expedientes/search"))).toBe(false);
  });

  it("una referencia que no existe da 404 con el texto que la persona escribió", async () => {
    mockGateway((path) => {
      if (path.startsWith("/expedientes/search")) return { status: 200, body: { hits: [] } };
      return { status: 200, body: {} };
    });

    // No debe ser el 500/«HTTP 422» de antes, y el mensaje tiene que nombrar la
    // referencia tecleada —el código—, no el identificador interno.
    await expect(load(sesion("EXP-9999-9999"))).rejects.toMatchObject({
      status: 404,
      body: { message: expect.stringContaining("EXP-9999-9999") },
    });
  });

  it("la coincidencia es EXACTA: un parecido no abre un expediente", async () => {
    // La búsqueda es de texto completo y devuelve parecidos por definición.
    // Abrir un expediente es un acto sobre uno concreto.
    mockGateway((path) => {
      if (path.startsWith("/expedientes/search")) {
        return {
          status: 200,
          body: { hits: [{ id: UUID, code: "EXP-2026-00021" }, { id: "otro", code: "EXP-2026-00022" }] },
        };
      }
      return { status: 200, body: {} };
    });

    await expect(load(sesion(CODIGO))).rejects.toMatchObject({ status: 404 });
  });
});
