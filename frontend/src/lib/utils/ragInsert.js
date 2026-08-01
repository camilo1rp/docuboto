// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ragInsert.js — construye el fragmento HTML que se inserta en `RichEditor` cuando
 * el usuario acepta una sugerencia del RAG (E21 Inc.3, F6 frontend).
 *
 * Regla dura #1 (no negociable, ver prompt de la tarea): el texto generado se
 * inserta SIEMPRE marcado como generado y SIEMPRE con sus citas. `buildGeneratedHtml`
 * es el ÚNICO punto donde se construye ese marcado — nunca insertar `answer` a pelo.
 *
 * El marcado usa EXCLUSIVAMENTE etiquetas de la allowlist de
 * `$lib/server/sanitizeHtml.js` (p, strong, em, blockquote, ul, li) para que la marca
 * sobreviva el saneo server-side al guardar — un `<div class="...">` o cualquier
 * atributo/clase se perdería ahí (el saneador solo conserva `href` en `<a>`). Por
 * eso la marca es TEXTO dentro de esas etiquetas, no un atributo/clase.
 */

/** Escapa texto plano para insertarlo como contenido HTML seguro. */
export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Construye el bloque HTML (marcado + citado) que se inserta en el editor.
 *
 * Precondición que debe verificar el LLAMANTE (no esta función): `answer` no es
 * null/vacío Y `citations.length > 0`. Si el RAG no pudo fundamentar una respuesta
 * (`answer === null` o sin citas), NO se ofrece insertar — ver `RagSuggestButton.svelte`.
 *
 * @param {string} answer
 * @param {Array<{cited_text: string, source_type: string, source_ref: string, score: number}>} citations
 * @returns {string}
 */
export function buildGeneratedHtml(answer, citations) {
  const paragraphs = escapeHtml(answer)
    .split(/\n+/)
    .filter((p) => p.trim().length > 0)
    .map((p) => `<p>${p}</p>`)
    .join("");

  const citationItems = (citations ?? [])
    .map((c) => {
      const ref = escapeHtml(c.source_ref);
      const snippet = escapeHtml(c.cited_text);
      const score = Number.isFinite(c.score) ? c.score.toFixed(2) : "?";
      return `<li>${ref} (similitud ${score}): <em>${snippet}</em></li>`;
    })
    .join("");

  return (
    `<blockquote>` +
    `<p><strong>[Sugerencia generada por IA — verifique antes de firmar]</strong></p>` +
    paragraphs +
    (citationItems ? `<p><strong>Fuentes citadas:</strong></p><ul>${citationItems}</ul>` : "") +
    `</blockquote>`
  );
}

/**
 * Concatena el HTML generado al valor actual del editor (append, nunca reemplaza
 * lo que el usuario ya redactó).
 * @param {string} currentValue
 * @param {string} generatedHtml
 * @returns {string}
 */
export function appendGeneratedHtml(currentValue, generatedHtml) {
  const current = typeof currentValue === "string" ? currentValue : "";
  return current && current !== "<p></p>" ? `${current}${generatedHtml}` : generatedHtml;
}
