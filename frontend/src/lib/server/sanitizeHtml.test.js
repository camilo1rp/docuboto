// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { sanitizeHtml, attachSafeBody, attachSafeCuerpo } from "./sanitizeHtml.js";

describe("sanitizeHtml — formato permitido (StarterKit + Link)", () => {
  it("conserva el formato básico", () => {
    const html = "<p>Hola <strong>mundo</strong> y <em>texto</em></p>";
    expect(sanitizeHtml(html)).toBe(html);
  });

  it("conserva listas, encabezados, blockquote y code", () => {
    const html =
      "<h2>Título</h2><ul><li>uno</li><li>dos</li></ul><blockquote>cita</blockquote><pre><code>x</code></pre>";
    expect(sanitizeHtml(html)).toBe(html);
  });

  it("conserva enlaces válidos pero fija target/rel seguros", () => {
    const out = sanitizeHtml('<p><a href="https://orpyca.com">sitio</a></p>');
    expect(out).toBe(
      '<p><a href="https://orpyca.com" target="_blank" rel="noopener noreferrer nofollow">sitio</a></p>',
    );
  });

  it("permite mailto y enlaces relativos", () => {
    expect(sanitizeHtml('<a href="mailto:a@b.co">m</a>')).toContain('href="mailto:a@b.co"');
    expect(sanitizeHtml('<a href="/ruta/interna">r</a>')).toContain('href="/ruta/interna"');
  });
});

describe("sanitizeHtml — neutraliza vectores XSS", () => {
  const vectores = [
    ["<script>alert(1)</script>", "script"],
    ["<p>hola</p><script>alert(1)</script>", "script"],
    ['<img src=x onerror="alert(1)">', "onerror"],
    ['<p onclick="alert(1)">x</p>', "onclick"],
    ['<iframe src="https://evil"></iframe>', "iframe"],
    ['<svg onload="alert(1)"></svg>', "onload"],
    ['<a href="javascript:alert(1)">x</a>', "javascript:"],
    ['<a href="JAVASCRIPT:alert(1)">x</a>', "javascript:"],
    ['<a href="java\tscript:alert(1)">x</a>', "javascript"],
    ['<a href="vbscript:msgbox(1)">x</a>', "vbscript"],
    ['<a href="data:text/html,<script>alert(1)</script>">x</a>', "data:"],
    ['<p style="background:url(javascript:alert(1))">x</p>', "style"],
    ["<!--[if IE]><script>alert(1)</script><![endif]-->", "script"],
    ["<object data='evil'></object>", "object"],
    ["<embed src='evil'>", "embed"],
  ];

  it.each(vectores)("neutraliza %s", (input, needle) => {
    const out = sanitizeHtml(input).toLowerCase();
    // El payload no debe sobrevivir como marcado/atributo ejecutable.
    expect(out).not.toContain(`<script`);
    expect(out).not.toContain("onerror");
    expect(out).not.toContain("onclick");
    expect(out).not.toContain("onload");
    expect(out).not.toContain("<iframe");
    expect(out).not.toContain("<object");
    expect(out).not.toContain("<embed");
    expect(out).not.toContain("javascript:");
    expect(out).not.toContain("vbscript:");
    expect(out).not.toContain("style=");
    expect(needle).toBeTruthy(); // la etiqueta del caso es informativa
  });

  it("descarta una etiqueta no permitida pero conserva su texto escapado", () => {
    const out = sanitizeHtml("<div>contenido</div>");
    expect(out).toBe("contenido");
  });

  it("escapa `<` sueltos que no forman etiqueta válida", () => {
    const out = sanitizeHtml("<p>2 < 3 y 5 > 4</p>");
    expect(out).toContain("&lt;");
    expect(out).toContain("&gt;");
    expect(out).not.toContain("< 3");
  });

  it("elimina el href inseguro pero deja el ancla y su texto", () => {
    const out = sanitizeHtml('<a href="javascript:alert(1)">click</a>');
    expect(out).toBe("<a>click</a>");
  });

  it("rechaza URLs protocol-relative (//host)", () => {
    expect(sanitizeHtml('<a href="//evil.com">x</a>')).toBe("<a>x</a>");
  });

  it("neutraliza javascript: ofuscado con entidad &colon;", () => {
    const out = sanitizeHtml('<a href="javascript&colon;alert(1)">x</a>').toLowerCase();
    expect(out).not.toContain("javascript:");
  });

  it("descarta atributos de una etiqueta permitida (class/style/id)", () => {
    expect(sanitizeHtml('<p class="x" id="y" style="color:red">t</p>')).toBe("<p>t</p>");
  });

  it("fuerza target/rel seguros ignorando los del input", () => {
    const out = sanitizeHtml('<a href="https://a.co" target="_self" rel="opener">x</a>');
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener noreferrer nofollow"');
    expect(out).not.toContain('target="_self"');
  });

  it("neutraliza <script> sin cierre y anidado", () => {
    expect(sanitizeHtml("<scr<script>ipt>alert(1)").toLowerCase()).not.toContain("<script");
    expect(sanitizeHtml("<p>x</p><script>alert(1)").toLowerCase()).not.toContain("<script");
  });

  it("neutraliza un <iframe> anidado dentro de una etiqueta permitida", () => {
    const out = sanitizeHtml('<p>ok<iframe src="https://evil"></iframe></p>').toLowerCase();
    expect(out).not.toContain("<iframe");
    expect(out).toContain("ok");
  });

  it("descarta <img> (no está en la allowlist) y neutraliza su onerror", () => {
    const out = sanitizeHtml('<p><img src=x onerror="alert(1)">texto</p>').toLowerCase();
    expect(out).not.toContain("<img");
    expect(out).not.toContain("onerror");
    expect(out).toContain("texto");
  });

  it("descarta <form>/<input> (no forman parte del editor) preservando el texto", () => {
    const out = sanitizeHtml(
      '<form><input type="text" onfocus="alert(1)"></form>texto',
    ).toLowerCase();
    expect(out).not.toContain("<form");
    expect(out).not.toContain("<input");
    expect(out).not.toContain("onfocus");
    expect(out).toContain("texto");
  });

  it("neutraliza handlers on* variados (onmouseover, onload, onfocus) en etiquetas permitidas", () => {
    const out = sanitizeHtml('<p onmouseover="alert(1)" onfocus="alert(2)">x</p>').toLowerCase();
    expect(out).toBe("<p>x</p>");
  });

  it("elimina <meta http-equiv=refresh> (bloque no peligroso per se, pero fuera de allowlist)", () => {
    const out = sanitizeHtml('<meta http-equiv="refresh" content="0;url=https://evil">texto');
    expect(out.toLowerCase()).not.toContain("<meta");
    expect(out).toContain("texto");
  });
});

describe("attachSafeBody — observations_html es campo de salida", () => {
  it("sanea el cuerpo S/I y lo expone como observations_html", () => {
    const doc = { doc_type: "S", observations: "<p>ok</p><script>x</script>" };
    attachSafeBody(doc);
    expect(doc.observations_html).toBe("<p>ok</p>");
  });

  it("ignora un observations_html inyectado cuando observations es null (S/I)", () => {
    const doc = {
      doc_type: "I",
      observations: null,
      observations_html: "<img src=x onerror=alert(1)>",
    };
    attachSafeBody(doc);
    // se reescribe desde el saneo (observations null → ""), nunca se acepta el inyectado
    expect(doc.observations_html).toBe("");
  });

  it("sobrescribe un observations_html inyectado aunque observations sea válido (S/I)", () => {
    const doc = {
      doc_type: "S",
      observations: "<p>real</p>",
      observations_html: "<script>evil</script>",
    };
    attachSafeBody(doc);
    expect(doc.observations_html).toBe("<p>real</p>");
  });

  it("elimina cualquier observations_html en documentos de Entrada", () => {
    const doc = {
      doc_type: "E",
      observations: "nota",
      observations_html: "<img src=x onerror=alert(1)>",
    };
    attachSafeBody(doc);
    expect("observations_html" in doc).toBe(false);
  });
});

describe("attachSafeCuerpo — cuerpo_html es campo de salida (T-12c)", () => {
  it("sanea el cuerpo y lo expone como cuerpo_html", () => {
    const item = { cuerpo: "<p>ok</p><script>x</script>" };
    attachSafeCuerpo(item);
    expect(item.cuerpo_html).toBe("<p>ok</p>");
  });

  it("ignora un cuerpo_html inyectado cuando cuerpo es null", () => {
    const item = { cuerpo: null, cuerpo_html: "<img src=x onerror=alert(1)>" };
    attachSafeCuerpo(item);
    expect(item.cuerpo_html).toBe("");
  });

  it("sobrescribe un cuerpo_html inyectado aunque cuerpo sea válido", () => {
    const item = { cuerpo: "<p>real</p>", cuerpo_html: "<script>evil</script>" };
    attachSafeCuerpo(item);
    expect(item.cuerpo_html).toBe("<p>real</p>");
  });

  it("devuelve null sin modificar para items null", () => {
    expect(attachSafeCuerpo(null)).toBeNull();
  });

  it("sanea cuerpo HTML sin alterar el texto legítimo", () => {
    const item = { cuerpo: "<p>Comunicado <strong>oficial</strong></p>" };
    attachSafeCuerpo(item);
    expect(item.cuerpo_html).toBe("<p>Comunicado <strong>oficial</strong></p>");
  });
});

describe("sanitizeHtml — casos borde", () => {
  it("devuelve '' para no-strings", () => {
    expect(sanitizeHtml(null)).toBe("");
    expect(sanitizeHtml(undefined)).toBe("");
    expect(sanitizeHtml(42)).toBe("");
  });

  it("colapsa el párrafo vacío del editor a cadena vacía", () => {
    expect(sanitizeHtml("<p></p>")).toBe("");
  });
});

describe("una dirección sin esquema no puede navegar fuera", () => {
  it("bloquea las dos formas de escribir lo mismo", () => {
    // Los navegadores normalizan la barra invertida antes de resolver, así que
    // `\\host` lleva al mismo sitio que `//host`. Bloquear solo una dejaba la
    // puerta abierta con cambiar un carácter.
    for (const href of ["//evil.example/x", "\\\\evil.example/x", "/\\evil.example/x"]) {
      const salida = sanitizeHtml(`<a href="${href}">ir</a>`);
      expect(salida).not.toContain("evil.example");
    }
  });

  it("las relativas y absolutas legítimas siguen pasando", () => {
    expect(sanitizeHtml('<a href="/bandeja">ir</a>')).toContain("/bandeja");
    expect(sanitizeHtml('<a href="https://gov.co/x">ir</a>')).toContain("gov.co");
  });
});
