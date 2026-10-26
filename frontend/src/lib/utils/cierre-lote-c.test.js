// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Cierre del lote C: selección (S) con método de muestreo, motivo DDHH / memoria histórica y área
 * /admin/preservacion con USUA_PERM_ADMIN >= 3.
 *
 * Mutaciones que deben caer:
 *  - construirValoracion deja de exigir el método con disposición S;
 *  - construirSeleccion admite una acción fuera de conservar|descartar;
 *  - construirLevantar acepta 10 caracteres para DDHH;
 *  - accesoAdmin ignora el nivel mínimo (un ADMIN de nivel 1 entra a preservación);
 *  - filterVisibleNavItems ignora `minLevels`.
 */
import { describe, it, expect } from "vitest";
import { construirLevantar, construirFijar, motivoLabel } from "./legalHold.js";
import { construirSeleccion, construirValoracion, esSeleccion } from "./valoracion.js";
import { accesoAdmin, ADMIN_AREA_MIN_LEVEL, ADMIN_AREA_PERMISSIONS } from "./adminGate.js";
import { filterVisibleNavItems, NAV_ITEMS } from "./navItems.js";

const val = { acta_comite: "Acta 3", fecha_acta: "2026-03-01", fundamento: "x".repeat(20) };

describe("selección (S)", () => {
  it("reconoce la disposición S y su alias", () => {
    expect(esSeleccion({ disposition: "S" })).toBe(true);
    expect(esSeleccion({ disposition: "select" })).toBe(true);
    expect(esSeleccion({ disposition: "CT" })).toBe(false);
  });

  it("con S el método de muestreo es obligatorio; sin S es opcional", () => {
    expect("error" in construirValoracion(val, true)).toBe(true);
    expect("error" in construirValoracion({ ...val, metodo_muestreo: "corto" }, true)).toBe(true);
    const ok = construirValoracion(
      { ...val, metodo_muestreo: "Uno de cada diez, por código" },
      true,
    );
    expect("ok" in ok && ok.ok.metodo_muestreo).toBe("Uno de cada diez, por código");
    expect("ok" in construirValoracion(val, false)).toBe(true);
  });

  it("el acto de selección pide acción válida y fundamento", () => {
    expect("error" in construirSeleccion({ accion: "borrar", fundamento: "x".repeat(20) })).toBe(
      true,
    );
    expect("error" in construirSeleccion({ accion: "conservar", fundamento: "corto" })).toBe(true);
    expect(
      construirSeleccion({ accion: "descartar", fundamento: "No entra a la muestra" }),
    ).toEqual({
      ok: { accion: "descartar", fundamento: "No entra a la muestra" },
    });
  });
});

describe("DDHH / memoria histórica", () => {
  it("se puede fijar a mano y tiene etiqueta", () => {
    expect(motivoLabel("ddhh_memoria_historica")).toMatch(/DDHH/);
    expect(
      "ok" in
        construirFijar({
          motivo_tipo: "ddhh_memoria_historica",
          fundamento: "x".repeat(12),
          acta_ref: "A-1",
        }),
    ).toBe(true);
  });

  it("levantarla exige el acto de la autoridad explicado (>= 50)", () => {
    const f = { motivo_confirmado: "ddhh_memoria_historica", acta_ref: "A-2" };
    expect("error" in construirLevantar({ ...f, fundamento: "x".repeat(20) })).toBe(true);
    expect("ok" in construirLevantar({ ...f, fundamento: "x".repeat(50) })).toBe(true);
    expect(
      "ok" in
        construirLevantar({
          motivo_confirmado: "judicial",
          acta_ref: "A",
          fundamento: "x".repeat(10),
        }),
    ).toBe(true);
  });
});

describe("área /admin/preservacion", () => {
  it("la abre USUA_PERM_EXPEDIENTE con cualquier nivel", () => {
    expect(accesoAdmin("/admin/preservacion", ["USUA_PERM_EXPEDIENTE"], {})).toBe(true);
  });

  it("la abre USUA_PERM_ADMIN solo desde el nivel 3", () => {
    expect(ADMIN_AREA_PERMISSIONS.preservacion).toEqual([
      "USUA_PERM_EXPEDIENTE",
      "USUA_PERM_ADMIN",
    ]);
    expect(ADMIN_AREA_MIN_LEVEL.preservacion).toEqual({ USUA_PERM_ADMIN: 3 });
    expect(accesoAdmin("/admin/preservacion", ["USUA_PERM_ADMIN"], { USUA_PERM_ADMIN: 2 })).toBe(
      false,
    );
    expect(accesoAdmin("/admin/preservacion", ["USUA_PERM_ADMIN"], { USUA_PERM_ADMIN: 3 })).toBe(
      true,
    );
    expect(accesoAdmin("/admin/preservacion", ["USUA_PERM_ADMIN"], undefined)).toBe(false);
  });

  it("otras áreas no cambian", () => {
    expect(accesoAdmin("/admin/usuarios", ["USUA_PERM_ADMIN"], {})).toBe(true);
    expect(accesoAdmin("/admin/trd", ["USUA_PERM_ADMIN"], { USUA_PERM_ADMIN: 5 })).toBe(false);
  });

  it("la navegación espeja el mismo criterio (minLevels)", () => {
    const item = NAV_ITEMS.find((i) => i.href?.includes("preservacion"));
    expect(item?.minLevels).toEqual({ USUA_PERM_ADMIN: 3 });
    const visible = (permisos, niveles) =>
      filterVisibleNavItems([item], permisos, [], niveles).length === 1;
    expect(visible(["USUA_PERM_EXPEDIENTE"], {})).toBe(true);
    expect(visible(["USUA_PERM_ADMIN"], { USUA_PERM_ADMIN: 1 })).toBe(false);
    expect(visible(["USUA_PERM_ADMIN"], { USUA_PERM_ADMIN: 3 })).toBe(true);
  });
});
