// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-contenido-server.test.js — Los proxies del contenido del plan (Fase 8).
 *
 * Se prueba la FÁBRICA a través de sus usos reales, no por separado: lo que
 * importa no es que la función genérica funcione, sino que cada ruta declare la
 * allowlist correcta — es la barrera anti *mass-assignment*, y once copias de
 * una barrera se desalinean en silencio.
 *
 * Los dos casos que no se pueden fallar:
 *
 *  * **`avance_pct` NO viaja al crear ni al editar un proyecto.** El avance sale
 *    del seguimiento; admitirlo aquí dejaría declarar un proyecto terminado sin
 *    un solo registro que lo respalde.
 *  * **La prioridad NO viaja en la matriz.** La calcula el backend; si viajara,
 *    el orden de lo que la entidad atiende primero lo decidiría quien manda el
 *    formulario.
 */
import { describe, it, expect, vi } from "vitest";

import { POST as crearProyecto } from "./planes/[id]/proyectos/+server.js";
import { PATCH as editarProyecto } from "./proyectos/[proyectoId]/+server.js";
import { PUT as priorizar } from "./planes/[id]/priorizacion/+server.js";
import { POST as seguimiento } from "./proyectos/[proyectoId]/seguimiento/+server.js";
import { POST as crearAspecto } from "./planes/[id]/aspectos/+server.js";

const PLAN = "11111111-1111-4111-8111-111111111111";
const PROY = "22222222-2222-4222-8222-222222222222";
const ASPECTO = "33333333-3333-4333-8333-333333333333";

function llamada(body, { params = {}, token = "tok", status = 200, respuesta = {} } = {}) {
  const fetch = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => respuesta,
  }));
  const event = {
    params,
    request: { json: async () => body },
    locals: { accessToken: token, user: { tenant_slug: "icetex", sub: "u-1" } },
    fetch,
  };
  return { event, fetch };
}

const enviado = (fetch) => JSON.parse(fetch.mock.calls[0][1].body);

describe("proyectos: el avance no se declara, se registra", () => {
  it("al crear, `avance_pct` y `estado` se descartan", async () => {
    const { event, fetch } = llamada(
      { nombre: "Adecuación", avance_pct: 100, estado: "terminado" },
      { params: { id: PLAN } },
    );

    await crearProyecto(event);

    const cuerpo = enviado(fetch);
    expect(cuerpo.nombre).toBe("Adecuación");
    expect(cuerpo.avance_pct).toBeUndefined();
    expect(cuerpo.estado).toBeUndefined();
  });

  it("al editar, `estado` SÍ viaja (hay estados que no derivan del avance) pero `avance_pct` no", async () => {
    const { event, fetch } = llamada(
      { estado: "cancelado", avance_pct: 90 },
      { params: { proyectoId: PROY } },
    );

    await editarProyecto(event);

    const cuerpo = enviado(fetch);
    expect(cuerpo.estado).toBe("cancelado");
    expect(cuerpo.avance_pct).toBeUndefined();
  });

  it("un estado fuera de la lista cerrada no llega al backend", async () => {
    const { event, fetch } = llamada({ estado: "inventado" }, { params: { proyectoId: PROY } });

    const res = await editarProyecto(event);

    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("omitir un campo NO es enviarlo en null: un PATCH de un campo no borra el resto", async () => {
    const { event, fetch } = llamada({ nombre: "Otro nombre" }, { params: { proyectoId: PROY } });

    await editarProyecto(event);

    const cuerpo = enviado(fetch);
    expect(cuerpo).toEqual({ nombre: "Otro nombre" });
    expect("meta" in cuerpo).toBe(false);
  });
});

describe("priorización: la prioridad la calcula el backend", () => {
  it("solo viajan el aspecto y las celdas", async () => {
    const { event, fetch } = llamada(
      {
        aspecto_id: ASPECTO,
        celdas: [{ eje_code: 1, impacto: 8 }],
        prioridad: 999,
      },
      { params: { id: PLAN } },
    );

    await priorizar(event);

    const cuerpo = enviado(fetch);
    expect(cuerpo.prioridad).toBeUndefined();
    expect(cuerpo.celdas).toEqual([{ eje_code: 1, impacto: 8 }]);
  });

  it("un impacto fuera de 1..10 no llega al backend", async () => {
    const { event, fetch } = llamada(
      { aspecto_id: ASPECTO, celdas: [{ eje_code: 1, impacto: 99 }] },
      { params: { id: PLAN } },
    );

    const res = await priorizar(event);

    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("un eje fuera de 1..5 tampoco", async () => {
    const { event, fetch } = llamada(
      { aspecto_id: ASPECTO, celdas: [{ eje_code: 9, impacto: 5 }] },
      { params: { id: PLAN } },
    );

    expect((await priorizar(event)).status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("las guardas comunes de la fábrica", () => {
  it("un identificador que no es UUID no se reenvía NUNCA, ni para que el backend lo rechace", async () => {
    const { event, fetch } = llamada({ descripcion: "x" }, { params: { id: "../../otro" } });

    const res = await crearAspecto(event);

    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sin sesión no se llama al backend", async () => {
    const { event, fetch } = llamada({ descripcion: "x" }, { params: { id: PLAN }, token: "" });

    expect((await crearAspecto(event)).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("el 409 del ciclo de vida llega ENTERO: dice que el plan ya está congelado", async () => {
    const detalle = {
      error: "plan_no_editable",
      detail: "El plan en estado 'aprobado' no admite cambios estructurales (reformule…).",
      status: 409,
    };
    const { event } = llamada(
      { descripcion: "x" },
      { params: { id: PLAN }, status: 409, respuesta: { detail: detalle } },
    );

    const res = await crearAspecto(event);

    expect(res.status).toBe(409);
    expect((await res.json()).detail).toEqual(detalle);
  });

  it("el seguimiento exige fecha y un avance de 0 a 100", async () => {
    const { event: sinFecha, fetch: f1 } = llamada(
      { avance_pct: 50 },
      { params: { proyectoId: PROY } },
    );
    expect((await seguimiento(sinFecha)).status).toBe(400);
    expect(f1).not.toHaveBeenCalled();

    const { event: fuera, fetch: f2 } = llamada(
      { fecha: "2026-08-29", avance_pct: 120 },
      { params: { proyectoId: PROY } },
    );
    expect((await seguimiento(fuera)).status).toBe(400);
    expect(f2).not.toHaveBeenCalled();
  });
});
