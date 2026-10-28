// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /busqueda — pestañas de conocimiento (`semantica`) y Antecedentes solo con
 * la capa de conocimiento activa en la entidad
 * Y PERM_CONOCIMIENTO >= 1 (C-1, ADR-037). Sin ellas, `?tab=semantica` cae a `exacta`
 * SIN llamar a knowledge.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { load } from "./+page.server.js";

const conLlamadas = () => {
  const f = vi.fn(async () => ({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => [],
  }));
  globalThis.fetch = f;
  return f;
};

const cargar = (user, search) =>
  load({
    cookies: { get: () => "tok" },
    parent: async () => ({ user: { tenant_slug: "icetex", sub: "u-1", ...user } }),
    url: new URL(`http://localhost/busqueda${search}`),
  });

const llamadasAKnowledge = (f) =>
  f.mock.calls.filter(([u]) => String(u).includes("/knowledge/")).length;

afterEach(() => vi.restoreAllMocks());

describe("load de /busqueda — gate del conocimiento", () => {
  it("con bandera y PERM_CONOCIMIENTO: semántica se sirve y llama a knowledge (control positivo)", async () => {
    const f = conLlamadas();
    const data = await cargar(
      { asistente_habilitado: true, permission_levels: { PERM_CONOCIMIENTO: 1 } },
      "?tab=semantica&sq=contrato",
    );
    expect(data.tab).toBe("semantica");
    expect(data.tabsPermitidas).toEqual(["exacta", "semantica", "antecedentes"]);
    expect(llamadasAKnowledge(f)).toBe(1);
  });

  it("con bandera y permiso: antecedentes también (control positivo)", async () => {
    const f = conLlamadas();
    const data = await cargar(
      { asistente_habilitado: true, permission_levels: { PERM_CONOCIMIENTO: 1 } },
      "?tab=antecedentes&aq=contrato",
    );
    expect(data.tab).toBe("antecedentes");
    expect(llamadasAKnowledge(f)).toBe(1);
  });

  it.each([
    [
      "entidad sin asistente",
      { asistente_habilitado: false, permission_levels: { PERM_CONOCIMIENTO: 3 } },
    ],
    ["sin PERM_CONOCIMIENTO", { asistente_habilitado: true, permission_levels: {} }],
    ["bandera ausente", { permission_levels: { PERM_CONOCIMIENTO: 1 } }],
  ])("%s: ?tab=semantica cae a exacta y NO llama a knowledge", async (_n, user) => {
    const f = conLlamadas();
    const data = await cargar(user, "?tab=semantica&sq=contrato");
    expect(data.tab).toBe("exacta");
    expect(data.tabsPermitidas).toEqual(["exacta"]);
    expect(llamadasAKnowledge(f)).toBe(0);
  });

  it("sin permiso, ?tab=antecedentes tampoco llama a knowledge", async () => {
    const f = conLlamadas();
    const data = await cargar(
      { asistente_habilitado: false },
      "?tab=antecedentes&aq=contrato&atr=2026-X-E-000001",
    );
    expect(data.tab).toBe("exacta");
    expect(llamadasAKnowledge(f)).toBe(0);
  });

  it("asistente_deshabilitado del backend se traduce a un mensaje claro", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 403,
      headers: new Headers(),
      json: async () => ({
        detail: { error: "asistente_deshabilitado", detail: "x", status: 403 },
      }),
    }));
    const data = await cargar(
      { asistente_habilitado: true, permission_levels: { PERM_CONOCIMIENTO: 1 } },
      "?tab=semantica&sq=contrato",
    );
    expect(data.semanticaError).toMatch(/entidad no tiene activada la capa de conocimiento/);
  });
});
