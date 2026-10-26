// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * Guardián C-4 (ADR-040): `retention_years` y `total_retention` ya no se escriben
 * (el backend responde 422 extra_forbidden) y ninguna pantalla debe caer a
 * `retention_years` como respaldo de las fases. Lo único legítimo es LEER
 * `total_retention` (derivado, solo lectura).
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const SRC = resolve(process.cwd(), "src");

function archivos(dir) {
  const salida = [];
  for (const entrada of readdirSync(dir)) {
    const ruta = join(dir, entrada);
    if (statSync(ruta).isDirectory()) salida.push(...archivos(ruta));
    else if (/\.(svelte|js)$/.test(entrada) && !/\.test\.js$/.test(entrada)) salida.push(ruta);
  }
  return salida;
}

/** Líneas de código (sin comentarios de línea ni de bloque) que usan el campo legado como respaldo o lo escriben. */
export function usosLegados(texto) {
  const sinComentarios = texto
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  return sinComentarios
    .split("\n")
    .filter(
      (l) =>
        /\?\?\s*[\w.?]*retention_years/.test(l) ||
        /payload\.(retention_years|total_retention)/.test(l) ||
        /^\s*(retention_years|total_retention)\s*[:,]/.test(l),
    );
}

describe("C-4: sin campos de retención legados", () => {
  it("control positivo: el detector SÍ ve el respaldo y la escritura legados", () => {
    expect(usosLegados("a = x.archivo_gestion_years ?? x.retention_years;")).toHaveLength(1);
    expect(usosLegados("payload.total_retention = 5;")).toHaveLength(1);
    expect(usosLegados("  retention_years: 5,")).toHaveLength(1);
    expect(usosLegados("// payload.total_retention = 5;")).toHaveLength(0);
    expect(usosLegados("<td>{item.total_retention}</td>")).toHaveLength(0);
  });

  it("ningún archivo fuente de src/ los usa", () => {
    const hallazgos = [];
    for (const f of archivos(SRC)) {
      const malos = usosLegados(readFileSync(f, "utf8"));
      if (malos.length) hallazgos.push(`${f}: ${malos.join(" | ")}`);
    }
    expect(hallazgos).toEqual([]);
  });
});
