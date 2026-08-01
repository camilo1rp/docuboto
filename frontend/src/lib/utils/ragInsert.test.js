// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ragInsert.test.js — F6 frontend (E21 Inc.3).
 *
 * La prueba de que el bloque generado SOBREVIVE el saneo server-side
 * (`$lib/server/sanitizeHtml.js`) vive aparte, en
 * `$lib/server/ragInsertSanitize.test.js` — este módulo es código de CLIENTE
 * (D-05: `$lib/server/*` no puede importarse desde aquí) y ese test necesita el
 * entorno "node" con el que corren los módulos server-only.
 */
import { describe, it, expect } from "vitest";
import { escapeHtml, buildGeneratedHtml, appendGeneratedHtml } from "./ragInsert.js";

describe("escapeHtml", () => {
  it("escapa <, >, &, comillas", () => {
    expect(escapeHtml(`<script>alert("x")</script> & 'y'`)).toBe(
      "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &#39;y&#39;",
    );
  });
});

describe("buildGeneratedHtml", () => {
  const citations = [
    { cited_text: "el plazo es de 15 días", source_type: "radicado", source_ref: "2024-ICETEX-E-000123", score: 0.912 },
  ];

  it("incluye la marca de generado por IA y las citas", () => {
    const html = buildGeneratedHtml("El plazo de respuesta es de 15 días hábiles.", citations);
    expect(html).toContain("Sugerencia generada por IA");
    expect(html).toContain("2024-ICETEX-E-000123");
    expect(html).toContain("el plazo es de 15 días");
    expect(html).toContain("El plazo de respuesta es de 15 días hábiles.");
  });

  it("escapa contenido hostil dentro de answer/citations (nunca inyecta marcado)", () => {
    const html = buildGeneratedHtml('<img src=x onerror=alert(1)>', [
      { cited_text: "<b>x</b>", source_type: "radicado", source_ref: "<i>ref</i>", score: 0.5 },
    ]);
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>x</b>");
    expect(html).not.toContain("<i>ref</i>");
    expect(html).toContain("&lt;img");
  });

  it("usa únicamente etiquetas de la allowlist del saneador (sin <div>/clases)", () => {
    const html = buildGeneratedHtml("texto", citations);
    const tags = [...html.matchAll(/<\/?([a-zA-Z][a-zA-Z0-9]*)\b/g)].map((m) => m[1].toLowerCase());
    const allowed = new Set(["p", "strong", "em", "blockquote", "ul", "li"]);
    for (const tag of tags) expect(allowed.has(tag)).toBe(true);
  });
});

describe("appendGeneratedHtml", () => {
  it("antepone/concatena sin perder lo ya redactado por el usuario", () => {
    expect(appendGeneratedHtml("<p>hola</p>", "<blockquote>gen</blockquote>")).toBe(
      "<p>hola</p><blockquote>gen</blockquote>",
    );
  });

  it("un editor vacío (<p></p> o cadena vacía) no dobla contenido vacío por delante", () => {
    expect(appendGeneratedHtml("", "<blockquote>gen</blockquote>")).toBe("<blockquote>gen</blockquote>");
    expect(appendGeneratedHtml("<p></p>", "<blockquote>gen</blockquote>")).toBe(
      "<blockquote>gen</blockquote>",
    );
  });
});
