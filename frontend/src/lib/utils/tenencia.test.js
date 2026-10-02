// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import {
  esPoseedor,
  puedeFinalizar,
  puedeMarcarEnTramite,
  puedeDevolver,
  nombresDeDependenciasDelUsuario,
  puedeFinalizarTramite,
  poseiaUltimoPaso,
  puedeAsignarAFlujo,
  puedeRevertir,
  esMotivoCierre,
  MOTIVOS_CIERRE,
} from "./tenencia.js";
import { mensajeError, MENSAJES_ACTO_TRAMITE } from "./apiError.js";

const YO = "u-yo";
const OTRO = "u-otro";
const step = (o = {}) => ({ status: "pending", assigned_to: null, to_dept: "Jurídica", ...o });

describe("tenencia del paso (ADR-028)", () => {
  it("poseedor: asignado a mí", () => {
    expect(esPoseedor(step({ assigned_to: YO }), YO, [])).toBe(true);
  });
  it("NO poseedor: asignado a un colega aunque sea de mi dependencia", () => {
    expect(esPoseedor(step({ assigned_to: OTRO }), YO, ["Jurídica"])).toBe(false);
  });
  it("poseedor: sin asignar y de mi dependencia", () => {
    expect(esPoseedor(step(), YO, ["Jurídica"])).toBe(true);
  });
  it("NO poseedor: sin asignar y de otra dependencia", () => {
    expect(esPoseedor(step(), YO, ["Archivo"])).toBe(false);
  });
  it("fail-closed: sin usuario, sin paso o paso ya cerrado", () => {
    expect(esPoseedor(step({ assigned_to: YO }), null, [])).toBe(false);
    expect(esPoseedor(null, YO, [])).toBe(false);
    expect(esPoseedor(step({ assigned_to: YO, status: "completed" }), YO, [])).toBe(false);
  });
  it("marcar en trámite solo sobre pasos pendientes; finalizar y devolver también en trámite", () => {
    const s = step({ assigned_to: YO, status: "in_progress" });
    expect(puedeMarcarEnTramite(s, YO, [])).toBe(false);
    expect(puedeFinalizar(s, YO, [])).toBe(true);
    expect(puedeDevolver(s, YO, [])).toBe(true);
    expect(puedeMarcarEnTramite(step({ assigned_to: YO }), YO, [])).toBe(true);
  });
  it("las dependencias salen del catálogo por código, solo las activas", () => {
    const user = {
      dependencias: [
        { codigo: 1, nombre: "Jurídica" },
        { codigo: 2, nombre: "Archivo" },
      ],
      context: {
        available_depts: [
          { depe_id: 1, is_active: true },
          { depe_id: 2, is_active: false },
          { depe_id: 99 },
        ],
      },
    };
    expect(nombresDeDependenciasDelUsuario(user)).toEqual(["Jurídica"]);
    expect(nombresDeDependenciasDelUsuario(null)).toEqual([]);
  });
});

describe("mensajes de los actos de trámite (ADR-028)", () => {
  it.each([
    ["radicado_terminal", 409],
    ["no_active_step", 404],
    ["status_managed_by_workflow", 422],
    ["step_not_found", 404],
    ["radicado_not_found", 404],
  ])("%s se traduce, plano o en sobre doble", (codigo, status) => {
    const esperado = MENSAJES_ACTO_TRAMITE[codigo];
    expect(mensajeError({ error: codigo, detail: "x", status }, "por defecto")).toBe(esperado);
    expect(mensajeError({ detail: { error: codigo, detail: "x", status } }, "por defecto")).toBe(
      esperado,
    );
  });
  it("los mensajes llevan tildes", () => {
    expect(MENSAJES_ACTO_TRAMITE.radicado_terminal).toContain("está");
    expect(MENSAJES_ACTO_TRAMITE.no_active_step).toContain("está a su cargo");
    expect(MENSAJES_ACTO_TRAMITE.status_managed_by_workflow).toContain("trámite");
  });
});

describe("Finalizar: espejo de PERM_TRAMITAR nivel 3 + tenencia (ADR-028 R3/R12)", () => {
  const propio = step({ assigned_to: YO, status: "in_progress" });
  const base = { userId: YO, dependencias: ["Jurídica"], radicadoStatus: "in_progress" };

  it.each([
    [3, true],
    [4, true],
    [2, false],
    [1, false],
    [0, false],
    [undefined, false],
  ])("paso propio con nivel %s: %s", (nivel, esperado) => {
    expect(puedeFinalizarTramite({ ...base, nivel, step: propio })).toBe(esperado);
  });

  it("paso de un colega: no, ni con nivel 3", () => {
    expect(puedeFinalizarTramite({ ...base, nivel: 3, step: step({ assigned_to: OTRO }) })).toBe(
      false,
    );
  });

  describe("sin paso activo: poseedor del último paso completado", () => {
    const h = (...o) =>
      o.map((x, i) => ({ step_number: i + 1, status: "completed", to_dept: "Jurídica", ...x }));
    it("lo poseía (asignado a mí) o era de mi dependencia sin dueño", () => {
      expect(
        poseiaUltimoPaso(h({ assigned_to: OTRO }, { assigned_to: YO }), "in_progress", YO, []),
      ).toBe(true);
      expect(poseiaUltimoPaso(h({ assigned_to: null }), "distributed", YO, ["Jurídica"])).toBe(
        true,
      );
    });
    it("cuenta el de mayor step_number, no el último del arreglo", () => {
      const pasos = [
        { step_number: 2, status: "completed", assigned_to: OTRO, to_dept: "X" },
        { step_number: 1, status: "completed", assigned_to: YO, to_dept: "X" },
      ];
      expect(poseiaUltimoPaso(pasos, "in_progress", YO, [])).toBe(false);
    });
    it("no si el último no está completed, el radicado es terminal, o falta dato", () => {
      expect(
        poseiaUltimoPaso(h({ assigned_to: YO, status: "cancelled" }), "in_progress", YO, []),
      ).toBe(false);
      expect(poseiaUltimoPaso(h({ assigned_to: YO }), "closed", YO, [])).toBe(false);
      expect(poseiaUltimoPaso(h({ assigned_to: YO }), "anulado", YO, [])).toBe(false);
      expect(poseiaUltimoPaso(h({ assigned_to: YO }), null, YO, [])).toBe(false);
      expect(poseiaUltimoPaso([], "in_progress", YO, [])).toBe(false);
      expect(poseiaUltimoPaso(null, "in_progress", YO, [])).toBe(false);
      expect(poseiaUltimoPaso(h({ assigned_to: YO }), "in_progress", null, [])).toBe(false);
    });
    it("puedeFinalizarTramite sin paso: exige además nivel 3", () => {
      const historial = h({ assigned_to: YO });
      expect(puedeFinalizarTramite({ ...base, nivel: 3, step: null, historial })).toBe(true);
      expect(puedeFinalizarTramite({ ...base, nivel: 2, step: null, historial })).toBe(false);
    });
  });
});

describe("motivos de cierre", () => {
  it("cuatro motivos con etiquetas en español", () => {
    expect(MOTIVOS_CIERRE.map((m) => m.value)).toEqual([
      "respondido",
      "nrr",
      "traslado_competencia",
      "otro",
    ]);
    expect(MOTIVOS_CIERRE.map((m) => m.label)).toEqual([
      "Respondido",
      "No requiere respuesta (NRR)",
      "Traslado por competencia",
      "Otro",
    ]);
  });
  it("esMotivoCierre solo admite los cuatro valores exactos", () => {
    expect(esMotivoCierre("nrr")).toBe(true);
    expect(esMotivoCierre("NRR")).toBe(false);
    expect(esMotivoCierre("")).toBe(false);
    expect(esMotivoCierre(undefined)).toBe(false);
  });
});

describe("Asignar a flujo sin paso activo (ADR-028 R16)", () => {
  const completado = (o = {}) => ({
    step_number: 1,
    status: "completed",
    to_dept: "Jurídica",
    ...o,
  });
  const base = { step: null, radicadoStatus: "distributed", userId: YO, dependencias: [] };

  it("sin pasos: solo quien radicó (registered_by === userId)", () => {
    expect(puedeAsignarAFlujo({ ...base, historial: [], registeredBy: YO })).toBe(true);
    expect(puedeAsignarAFlujo({ ...base, historial: [], registeredBy: OTRO })).toBe(false);
  });
  it("registered_by nulo (importado/ingesta): nadie lo reparte a mano", () => {
    expect(puedeAsignarAFlujo({ ...base, historial: [], registeredBy: null })).toBe(false);
    expect(puedeAsignarAFlujo({ ...base, historial: [], registeredBy: undefined })).toBe(false);
  });
  it("con historial: quien radicó ya no basta; hace falta poseer el último paso completado", () => {
    const h = [completado({ assigned_to: OTRO })];
    expect(puedeAsignarAFlujo({ ...base, historial: h, registeredBy: YO })).toBe(false);
    const mio = [completado({ assigned_to: YO })];
    expect(puedeAsignarAFlujo({ ...base, historial: mio, registeredBy: OTRO })).toBe(true);
  });
  it("con paso activo, radicado cerrado o historial no cargado (null): no se ofrece", () => {
    expect(puedeAsignarAFlujo({ ...base, step: step(), historial: [], registeredBy: YO })).toBe(
      false,
    );
    expect(
      puedeAsignarAFlujo({ ...base, radicadoStatus: "closed", historial: [], registeredBy: YO }),
    ).toBe(false);
    expect(puedeAsignarAFlujo({ ...base, historial: null, registeredBy: YO })).toBe(false);
  });
});

describe("Devolver / Revertir con la tenencia de _holder_condition (R19)", () => {
  it("paso sin dueño de mi dependencia: puedo devolver y revertir si está pending", () => {
    const s = step({ assigned_to: null, to_dept: "Jurídica" });
    expect(puedeDevolver(s, YO, ["Jurídica"])).toBe(true);
    expect(puedeRevertir(s, YO, ["Jurídica"])).toBe(true);
    expect(puedeRevertir(s, YO, ["Otra"])).toBe(false);
  });
  it("revertir exige pending; devolver admite in_progress", () => {
    const s = step({ status: "in_progress", assigned_to: YO });
    expect(puedeDevolver(s, YO, [])).toBe(true);
    expect(puedeRevertir(s, YO, [])).toBe(false);
  });
  it("no_flow tiene mensaje en español y no queda rastro de not_current_holder", () => {
    expect(MENSAJES_ACTO_TRAMITE.no_flow).toMatch(/flujo/);
    expect(MENSAJES_ACTO_TRAMITE.no_flow).toMatch(/está por encima/);
    expect(MENSAJES_ACTO_TRAMITE).not.toHaveProperty("not_current_holder");
  });
});
