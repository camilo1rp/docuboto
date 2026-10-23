// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import AipVersiones from "./AipVersiones.svelte";

const V = (version, motivo, extra = {}) => ({
  version,
  aip_id: `aip-${version}`,
  sustituye_a_aip_id: version > 1 ? `aip-${version - 1}` : null,
  motivo,
  firma_ordinal: version,
  created_at: `2026-0${version}-01T10:00:00Z`,
  ...extra,
});

describe("AipVersiones", () => {
  it("lista las versiones de la más nueva a la más vieja, marca la vigente y traduce el motivo", () => {
    render(AipVersiones, { props: { versiones: [V(1, "ingesta"), V(2, "renovacion_lta")] } });
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Versión 2");
    expect(items[0]).toHaveTextContent("vigente");
    expect(items[0]).toHaveTextContent("Renovación del sello de archivo (LTA)");
    expect(items[0]).toHaveTextContent("Sustituye a la versión anterior");
    expect(items[1]).toHaveTextContent("Ingesta inicial");
    expect(items[1]).not.toHaveTextContent("vigente");
    expect(items[1]).not.toHaveTextContent("Sustituye");
  });

  it("sin versiones (ausente, null o vacío) no pinta nada", () => {
    for (const versiones of [undefined, null, []]) {
      const { container, unmount } = render(AipVersiones, { props: { versiones } });
      expect(container.querySelector("section")).toBeNull();
      unmount();
    }
  });

  it("un motivo desconocido se muestra tal cual, no se pierde", () => {
    render(AipVersiones, { props: { versiones: [V(1, "otro_motivo")] } });
    expect(screen.getByText("otro_motivo")).toBeInTheDocument();
  });
});
