// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * Un comentario `<!-- … -->` no puede contener la secuencia que lo cierra.
 *
 * Dos componentes documentaban su máquina de estados con un diagrama en texto
 * —`borrador --aprobar--> aprobada`—. Ese primer `-->` **cierra el comentario
 * HTML**, y todo lo que venía detrás (notas internas, lista de props, nombres
 * de eventos) pasaba a ser contenido de la página. En `/admin/trd` se veía
 * literalmente en la columna «Acciones», junto al botón Editar.
 *
 * Lo encontré navegando, y ese es el punto: **ninguna herramienta lo vio**.
 * Prettier, ESLint, `svelte-check` y los 1504 tests unitarios pasaban en verde.
 * Peor: prettier reflotó el texto ya filtrado como si fuera prosa, dejándolo
 * con una pinta plausible. Es la misma familia que los atributos con comillas
 * sin escapar que truncaron texto en diez pantallas sin romper la compilación:
 * defectos a nivel de marcado que el compilador acepta porque, para él, son
 * marcado válido.
 *
 * El control es deliberadamente tonto —contar aperturas y cierres— porque un
 * `-->` de más NO se puede detectar leyendo el comentario ya parseado: para
 * entonces el comentario terminó donde no debía y el resto es texto legítimo.
 * Contar es lo único que sigue funcionando después del daño.
 *
 * Si esto se pone rojo: sustituye las flechas del diagrama por «→».
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

/** Recorre `src/` sin depender de un paquete de globbing que no está instalado. */
function svelteFiles(dir) {
  const salida = [];
  for (const entrada of readdirSync(dir, { withFileTypes: true })) {
    const completo = path.join(dir, entrada.name);
    if (entrada.isDirectory()) salida.push(...svelteFiles(completo));
    else if (entrada.name.endsWith(".svelte")) salida.push(completo);
  }
  return salida;
}

describe("comentarios HTML de los .svelte", () => {
  it("ningún archivo cierra más comentarios de los que abre", () => {
    const archivos = svelteFiles(path.join(RAIZ, "src"));

    // Control del propio control: si el glob deja de encontrar archivos, este
    // test pasaría en verde sin haber mirado nada.
    expect(archivos.length).toBeGreaterThan(50);

    const rotos = archivos
      .map((f) => {
        const src = readFileSync(f, "utf-8");
        const abre = (src.match(/<!--/g) ?? []).length;
        const cierra = (src.match(/-->/g) ?? []).length;
        return { f: path.relative(RAIZ, f), abre, cierra };
      })
      .filter(({ abre, cierra }) => abre !== cierra);

    expect(rotos.map(({ f, abre, cierra }) => `${f} (abre ${abre}, cierra ${cierra})`)).toEqual([]);
  });
});
