// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * AdvertenciasVerificacion — los avisos de una firma válida con reservas se
 * ven; sin avisos no se pinta nada.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import AdvertenciasVerificacion from "./AdvertenciasVerificacion.svelte";

describe("AdvertenciasVerificacion", () => {
  it("traduce el código conocido y muestra uno desconocido con su código", () => {
    render(AdvertenciasVerificacion, {
      props: { advertencias: ["tsa_sin_esscertid_anterior_al_corte", "aviso_nuevo"] },
    });
    expect(screen.getByText(/ESSCertID/)).toBeTruthy();
    expect(screen.getByText(/aviso_nuevo/)).toBeTruthy();
    expect(screen.getByText(/válida, pero con reservas/)).toBeTruthy();
  });

  it.each([[[]], [undefined], [null]])("no pinta nada con %j", (advertencias) => {
    render(AdvertenciasVerificacion, { props: { advertencias } });
    expect(screen.queryByTestId("advertencias-verificacion")).toBeNull();
  });
});
