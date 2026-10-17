// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { clasificarEstadoEntidad, TEXTOS_ENTIDAD } from "./estadoEntidad.js";

describe("clasificarEstadoEntidad", () => {
  it("clasifica los tres rechazos del ADR-035", () => {
    expect(clasificarEstadoEntidad(403, { error: "tenant_inactivo" })).toBe("suspendida");
    expect(clasificarEstadoEntidad(503, { error: "tenant_no_provisionado" })).toBe("no_lista");
    expect(clasificarEstadoEntidad(503, { detail: { error: "tenant_estado_no_disponible" } })).toBe(
      "sin_comprobar",
    );
  });

  it("no confunde otros errores ni códigos con estado equivocado", () => {
    expect(clasificarEstadoEntidad(403, { error: "forbidden" })).toBeNull();
    expect(clasificarEstadoEntidad(503, { error: "tenant_inactivo" })).toBeNull();
    expect(clasificarEstadoEntidad(403, null)).toBeNull();
    expect(clasificarEstadoEntidad(500, undefined)).toBeNull();
  });

  it("los textos llevan tildes y solo la suspensión no es reintentable", () => {
    expect(TEXTOS_ENTIDAD.suspendida.titulo).toBe("La entidad está suspendida");
    expect(TEXTOS_ENTIDAD.no_lista.titulo).toBe("La entidad aún no está lista");
    expect(TEXTOS_ENTIDAD.sin_comprobar.titulo).toContain("No se pudo comprobar");
    expect(TEXTOS_ENTIDAD.suspendida.reintentable).toBe(false);
    expect(TEXTOS_ENTIDAD.no_lista.reintentable).toBe(true);
  });
});
