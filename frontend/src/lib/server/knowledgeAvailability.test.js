// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * knowledgeAvailability.test.js — «Sugerir con antecedentes» solo con generación
 * (ADR-046 §3, fase D0).
 *
 * En modo `local` `/rag` responde 503: el botón no debe aparecer para luego
 * fallar. La carga pregunta a `GET /knowledge/availability` y solo un
 * `generation: true` estricto lo enciende; todo lo demás lo apaga.
 */
import { describe, it, expect, vi } from "vitest";

import { fetchGenerationAvailable } from "./knowledgeAvailability.server.js";

const HEADERS = { Authorization: "Bearer t", "X-Tenant-Slug": "demo" };
const CON_CONOCIMIENTO = {
  asistente_habilitado: true,
  permission_levels: { PERM_CONOCIMIENTO: 1 },
};

const responde = (status, cuerpo) =>
  vi.fn(async () => ({ ok: status < 400, status, json: async () => cuerpo }));

describe("fetchGenerationAvailable", () => {
  it("generation:true enciende, y pregunta a la ruta con el token del usuario (control positivo)", async () => {
    const f = responde(200, { generation: true, mode: "ppi" });

    expect(await fetchGenerationAvailable(f, HEADERS, CON_CONOCIMIENTO)).toBe(true);
    expect(f).toHaveBeenCalledWith("http://api-gateway:8080/api/v1/knowledge/availability", {
      headers: HEADERS,
    });
  });

  it("modo local sin generación: apagado", async () => {
    const f = responde(200, { generation: false, mode: "local" });
    expect(await fetchGenerationAvailable(f, HEADERS, CON_CONOCIMIENTO)).toBe(false);
  });

  it("403 de la bandera o del permiso: apagado", async () => {
    const f = responde(403, { detail: { error: "asistente_deshabilitado" } });
    expect(await fetchGenerationAvailable(f, HEADERS, CON_CONOCIMIENTO)).toBe(false);
  });

  it("5xx, cuerpo inesperado o red caída: apagado", async () => {
    for (const f of [
      responde(500, {}),
      responde(200, { generation: "true" }),
      responde(200, null),
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => Promise.reject(new Error("x")),
      })),
      vi.fn(async () => Promise.reject(new TypeError("fetch failed"))),
    ]) {
      expect(await fetchGenerationAvailable(f, HEADERS, CON_CONOCIMIENTO)).toBe(false);
    }
  });

  it("sesión sin bandera o sin PERM_CONOCIMIENTO: apagado y sin preguntar", async () => {
    for (const user of [
      { asistente_habilitado: false, permission_levels: { PERM_CONOCIMIENTO: 2 } },
      { asistente_habilitado: true, permission_levels: {} },
      null,
    ]) {
      const f = responde(200, { generation: true, mode: "ppi" });
      expect(await fetchGenerationAvailable(f, HEADERS, user)).toBe(false);
      expect(f).not.toHaveBeenCalled();
    }
  });
});
