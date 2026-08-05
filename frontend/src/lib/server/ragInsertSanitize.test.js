// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ragInsertSanitize.test.js — F6 frontend (E21 Inc.3), regla dura #1.
 *
 * El bloque que `$lib/utils/ragInsert.js::buildGeneratedHtml` produce debe
 * SOBREVIVIR el saneo server-side (`sanitizeHtml`) que se aplica SIEMPRE al
 * guardar el cuerpo de Salida/Interno o un borrador (D-05): si la marca
 * "generado por IA" o las citas dependieran de un atributo/clase, el saneador
 * (que solo conserva `href` en `<a>`) las borraría en el primer guardado y la
 * regla dura #1 (marcar SIEMPRE el texto generado, con sus citas) se rompería
 * en silencio en cuanto el documento se recargara.
 *
 * Vive en `src/lib/server/` (entorno "node", ver vite.config.js
 * environmentMatchGlobs) porque es el único sitio desde el que este repo
 * permite importar `$lib/server/sanitizeHtml.js` en un test.
 */
import { describe, it, expect } from "vitest";
import { buildGeneratedHtml } from "$lib/utils/ragInsert.js";
import { sanitizeHtml } from "./sanitizeHtml.js";

describe("buildGeneratedHtml sobrevive sanitizeHtml", () => {
  const citations = [
    {
      cited_text: "el plazo es de 15 días",
      source_type: "radicado",
      source_ref: "2024-ICETEX-E-000123",
      score: 0.912,
    },
  ];

  it("conserva la marca de generado por IA y las citas tras el saneo", () => {
    const html = buildGeneratedHtml("El plazo de respuesta es de 15 días hábiles.", citations);
    const sane = sanitizeHtml(html);
    expect(sane).toContain("Sugerencia generada por IA");
    expect(sane).toContain("2024-ICETEX-E-000123");
    expect(sane).toContain("El plazo de respuesta es de 15 días hábiles.");
  });

  it("es idempotente ante el saneo (nada que dependa de un atributo/clase que se pierda)", () => {
    const html = buildGeneratedHtml("texto generado", citations);
    const sane = sanitizeHtml(html);
    expect(sane).toBe(sanitizeHtml(sane));
  });

  it("un intento de inyección dentro de answer/citations no sobrevive ni el builder ni el saneo", () => {
    const html = buildGeneratedHtml("<img src=x onerror=alert(1)>", [
      {
        cited_text: "<script>alert(1)</script>",
        source_type: "radicado",
        source_ref: "ref",
        score: 0.5,
      },
    ]);
    const sane = sanitizeHtml(html);
    // El texto hostil sobrevive como TEXTO ESCAPADO (inerte) — lo que no debe
    // sobrevivir es una etiqueta real ejecutable.
    expect(sane).not.toContain("<img ");
    expect(sane).not.toContain("<script>");
    expect(sane).not.toContain("<script ");
    expect(sane).toContain("&lt;img");
    expect(sane).toContain("&lt;script&gt;");
  });
});
