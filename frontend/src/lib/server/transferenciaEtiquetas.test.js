// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi } from "vitest";
import { anotarCodigoExpediente, MAX_EXPEDIENTES_RESUELTOS } from "./transferenciaEtiquetas.js";

const A = "7ce8f33d-0dd9-4ecc-9785-423f731032a8";
const B = "2e460997-28c2-4b1f-b101-45ff89bd43d2";

const CATALOGO = {
  [A]: { code: "EXP-2026-0020", name: "Certificación laboral" },
  [B]: { code: "EXP-2026-0022", name: "Vinculación por lote" },
};

const obtener = vi.fn(async (id) => CATALOGO[id] ?? null);

describe("anotarCodigoExpediente", () => {
  it("anota el código y el nombre sobre la forma envuelta", async () => {
    const r = await anotarCodigoExpediente({ items: [{ expediente_id: A }], total: 1 }, obtener);
    expect(r.items[0].expediente_codigo).toBe("EXP-2026-0020");
    expect(r.items[0].expediente_nombre).toBe("Certificación laboral");
    expect(r.total).toBe(1); // conserva el resto del sobre
  });

  it("acepta también la lista pelada y devuelve lista", async () => {
    const r = await anotarCodigoExpediente([{ expediente_id: A }], obtener);
    expect(Array.isArray(r)).toBe(true);
    expect(r[0].expediente_codigo).toBe("EXP-2026-0020");
  });

  it("pide UNA vez por expediente aunque se repita en varias filas", async () => {
    const espia = vi.fn(async (id) => CATALOGO[id] ?? null);
    await anotarCodigoExpediente(
      { items: [{ expediente_id: A }, { expediente_id: A }, { expediente_id: B }] },
      espia,
    );
    expect(espia).toHaveBeenCalledTimes(2);
  });

  it("deja SIN anotar el expediente que no se puede leer, sin romper el resto", async () => {
    // No-read-up: la etiqueta no puede revelar lo que el control de acceso
    // oculta, y una fila inaccesible no puede tumbar la lista entera.
    const r = await anotarCodigoExpediente(
      { items: [{ expediente_id: A }, { expediente_id: "ajeno" }] },
      obtener,
    );
    expect(r.items[0].expediente_codigo).toBe("EXP-2026-0020");
    expect(r.items[1].expediente_codigo).toBeUndefined();
  });

  it("una excepción al resolver no tumba el listado", async () => {
    const r = await anotarCodigoExpediente({ items: [{ expediente_id: A }] }, async () => {
      throw new Error("gateway caído");
    });
    expect(r.items[0].expediente_codigo).toBeUndefined();
    expect(r.items).toHaveLength(1);
  });

  it("respeta el tope de expedientes resueltos", async () => {
    const espia = vi.fn(async () => ({ code: "X", name: "Y" }));
    const items = Array.from({ length: MAX_EXPEDIENTES_RESUELTOS + 25 }, (_, i) => ({
      expediente_id: `id-${i}`,
    }));
    await anotarCodigoExpediente({ items }, espia);
    expect(espia).toHaveBeenCalledTimes(MAX_EXPEDIENTES_RESUELTOS);
  });

  it("devuelve el listado intacto si viene vacío o con una forma inesperada", async () => {
    const espia = vi.fn();
    expect(await anotarCodigoExpediente({ items: [] }, espia)).toEqual({ items: [] });
    expect(await anotarCodigoExpediente(null, espia)).toBeNull();
    expect(await anotarCodigoExpediente({ error: "x" }, espia)).toEqual({ error: "x" });
    expect(espia).not.toHaveBeenCalled();
  });
});
