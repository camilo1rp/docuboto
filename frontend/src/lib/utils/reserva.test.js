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
  causalDe,
  causalesParaNivel,
  llevaPlazo,
  puedeDecidir,
  puedeSolicitar,
  requiereSegundaPersona,
  tieneNivelReclasificar,
  validarClasificacion,
} from "./reserva.js";
import { aprobacionConfirmada, fraseAprobacion } from "./desclasificacionConfirm.js";

const CAT = [
  {
    codigo: "art18_a_intimidad",
    articulo: "Ley 1712/2014 art. 18",
    literal: "a",
    descripcion: "Intimidad",
    nivel: 3,
    plazo_max_meses: null,
  },
  {
    codigo: "art19_b_seguridad_publica",
    articulo: "Ley 1712/2014 art. 19",
    literal: "b",
    descripcion: "Seguridad pública",
    nivel: 2,
    plazo_max_meses: 180,
  },
];

describe("gates por nivel CRUD de PERM_RECLASIFICAR", () => {
  it("solicitar/retirar exige >=3 y decidir >=4 (con control positivo)", () => {
    expect(puedeSolicitar({ PERM_RECLASIFICAR: 3 })).toBe(true);
    expect(puedeSolicitar({ PERM_RECLASIFICAR: 2 })).toBe(false);
    expect(puedeDecidir({ PERM_RECLASIFICAR: 4 })).toBe(true);
    expect(puedeDecidir({ PERM_RECLASIFICAR: 3 })).toBe(false);
  });
  it("falla cerrado sin niveles o sin el permiso", () => {
    expect(puedeDecidir(undefined)).toBe(false);
    expect(puedeDecidir({ OTRO: 5 })).toBe(false);
    expect(tieneNivelReclasificar({ PERM_RECLASIFICAR: 1 }, 1)).toBe(true);
    expect(tieneNivelReclasificar({}, 1)).toBe(false);
  });
});

describe("requiereSegundaPersona", () => {
  it("solo desde el umbral y solo hacia abajo", () => {
    expect(requiereSegundaPersona(3, 1, 3)).toBe(true);
    expect(requiereSegundaPersona(3, 2, 3)).toBe(true);
    expect(requiereSegundaPersona(2, 1, 3)).toBe(false);
    expect(requiereSegundaPersona(3, 3, 3)).toBe(false);
    expect(requiereSegundaPersona(1, 3, 3)).toBe(false);
  });
  it("usa el umbral del servidor", () => {
    expect(requiereSegundaPersona(2, 1, 2)).toBe(true);
  });
});

describe("catálogo de causales", () => {
  it("filtra por nivel de destino: 3 → art. 18, 2 → art. 19, 1 → ninguna", () => {
    expect(causalesParaNivel(CAT, 3).map((o) => o.value)).toEqual(["art18_a_intimidad"]);
    expect(causalesParaNivel(CAT, 2).map((o) => o.value)).toEqual(["art19_b_seguridad_publica"]);
    expect(causalesParaNivel(CAT, 1)).toEqual([]);
    expect(causalesParaNivel(null, 2)).toEqual([]);
    expect(causalesParaNivel(CAT, 2)[0].label).toContain("Art. 19 lit. b");
  });
  it("el plazo solo aplica al art. 19", () => {
    expect(llevaPlazo(causalDe(CAT, "art19_b_seguridad_publica"))).toBe(true);
    expect(llevaPlazo(causalDe(CAT, "art18_a_intimidad"))).toBe(false);
    expect(llevaPlazo(null)).toBe(false);
  });
});

describe("validarClasificacion", () => {
  const base = { catalogo: CAT, fundamento: "Ley 1581", plazo: "12", plazoMax: 180 };
  it("control positivo: art. 19 completo es válido", () => {
    expect(
      validarClasificacion({ ...base, nivel: 2, causal: "art19_b_seguridad_publica" }),
    ).toEqual({});
  });
  it("art. 18 es válido sin plazo", () => {
    expect(
      validarClasificacion({ ...base, nivel: 3, causal: "art18_a_intimidad", plazo: "" }),
    ).toEqual({});
  });
  it("nivel 1 no valida nada", () => {
    expect(validarClasificacion({ ...base, nivel: 1, causal: "", fundamento: "" })).toEqual({});
  });
  it("causal ausente, de la otra categoría o fuera del catálogo se rechaza", () => {
    expect(validarClasificacion({ ...base, nivel: 2, causal: "" }).causal).toBeTruthy();
    expect(
      validarClasificacion({ ...base, nivel: 2, causal: "art18_a_intimidad" }).causal,
    ).toBeTruthy();
    expect(validarClasificacion({ ...base, nivel: 2, causal: "texto libre" }).causal).toBeTruthy();
  });
  it("fundamento obligatorio desde nivel 2", () => {
    expect(
      validarClasificacion({ ...base, nivel: 3, causal: "art18_a_intimidad", fundamento: " " })
        .fundamento,
    ).toBeTruthy();
  });
  it.each([[""], ["0"], ["181"], ["1.5"], ["abc"]])("plazo %j inválido en art. 19", (plazo) => {
    expect(
      validarClasificacion({ ...base, nivel: 2, causal: "art19_b_seguridad_publica", plazo }).plazo,
    ).toBeTruthy();
  });
});

describe("confirmación escalonada de aprobar", () => {
  const fila = { tracking_number: "2026-DEMO-E-000001" };
  it("exige haber revisado Y teclear el número exacto", () => {
    expect(aprobacionConfirmada({ revisado: true, texto: "2026-DEMO-E-000001", fila })).toBe(true);
    expect(aprobacionConfirmada({ revisado: true, texto: " 2026-DEMO-E-000001 ", fila })).toBe(
      true,
    );
    expect(aprobacionConfirmada({ revisado: false, texto: "2026-DEMO-E-000001", fila })).toBe(
      false,
    );
    expect(aprobacionConfirmada({ revisado: true, texto: "2026-DEMO-E-000002", fila })).toBe(false);
    expect(aprobacionConfirmada({ revisado: true, texto: "", fila })).toBe(false);
  });
  it("sin número de radicado nunca se confirma", () => {
    expect(fraseAprobacion(null)).toBe("");
    expect(aprobacionConfirmada({ revisado: true, texto: "", fila: {} })).toBe(false);
  });
});
