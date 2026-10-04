// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * el-velo-sale-del-token.test.js — El velo de los diálogos no se escribe a mano.
 *
 * Cuatro componentes pintaban su velo con el MISMO color y cuatro opacidades
 * distintas: `rgba(33, 36, 33, ·)` con 0.25, 0.35, 0.4 y 0.5. Dos de ellos —los
 * dos cajones— diferían en 0.05, que no es una decisión de diseño: es deriva.
 * Y cambiar el velo del sistema obligaba a encontrar cuatro literales repartidos
 * por tres componentes y una ruta, sin nada que dijera dónde estaban.
 *
 * Ahora el color vive una vez (`$op-scrim-rgb`) y hay DOS niveles con
 * significado: `--op-scrim` para lo que bloquea —Modal y cajones, que atrapan el
 * foco— y `--op-scrim-soft` para el asistente, que se abre sobre la pantalla sin
 * pretender que el resto desaparece.
 *
 * El censo recorre también las hojas `.scss` (salvo `_tokens.scss`): `layout.scss`
 * repetía el velo a mano y escapaba porque solo se leían los `.svelte`.
 *
 * Este guardián no prohíbe `rgba()` en general —un degradado de marca o una
 * sombra son otra cosa—: prohíbe ESE color, que es el del velo.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(AQUI, "..");
const TOKENS = join(SRC, "styles", "_tokens.scss");

/** El color del velo, escrito a mano. Tolera los espacios que ponga cada quien. */
const VELO_CRUDO = /rgba\(\s*33\s*,\s*36\s*,\s*33\s*,/;

function fuentes(dir) {
  /** @type {string[]} */
  const salida = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const ruta = join(dir, e.name);
    if (e.isDirectory()) salida.push(...fuentes(ruta));
    // `_tokens.scss` es donde el color se declara: es el único sitio permitido.
    else if (e.isFile() && ruta.endsWith(".scss") && ruta !== TOKENS) salida.push(ruta);
    else if (e.isFile() && ruta.endsWith(".svelte")) salida.push(ruta);
  }
  return salida;
}

describe("el velo de los diálogos sale del token", () => {
  const archivos = fuentes(SRC);

  it("el censo lee algo: un guardián vacío aprobaría por vacuidad", () => {
    expect(archivos.length).toBeGreaterThan(50);
  });

  it("los dos niveles de velo existen y comparten color", () => {
    const tokens = readFileSync(TOKENS, "utf8");
    expect(tokens).toMatch(/\$op-scrim-rgb:\s*33,\s*36,\s*33;/);
    expect(tokens).toMatch(/--op-scrim:\s*rgba\(#\{\$op-scrim-rgb\}/);
    expect(tokens).toMatch(/--op-scrim-soft:\s*rgba\(#\{\$op-scrim-rgb\}/);
  });

  it("el censo incluye las hojas .scss (layout.scss escapaba al guardián)", () => {
    expect(archivos.some((f) => f.endsWith("layout.scss"))).toBe(true);
  });

  it("landing y login no escriben a mano el blanco translúcido sobre la marca", () => {
    const tokens = readFileSync(TOKENS, "utf8");
    for (const t of ["overlay", "overlay-border", "text-muted"]) {
      expect(tokens).toContain(`--op-on-brand-${t}:`);
    }
    for (const f of ["routes/(public)/+page.svelte", "routes/(public)/login/+page.svelte"]) {
      expect(readFileSync(join(SRC, f), "utf8"), f).not.toMatch(/rgba\(\s*255\s*,\s*255\s*,\s*255/);
    }
  });

  it("ningún componente escribe el color del velo a mano", () => {
    const culpables = archivos
      .filter((f) => VELO_CRUDO.test(readFileSync(f, "utf8")))
      .map((f) => f.slice(SRC.length + 1));

    expect(culpables).toEqual([]);
  });

  it("quien bloquea usa el velo fuerte, y el asistente el suave", () => {
    // El nivel no es estético: dice si lo de detrás se puede usar. Un Modal o un
    // cajón atrapan el foco; el dock del asistente acompaña. Confundirlos hace
    // que la pantalla mienta sobre lo que está disponible.
    const lee = (f) => readFileSync(join(SRC, f), "utf8");
    for (const f of [
      "lib/components/ui/Modal.svelte",
      "lib/components/ui/Drawer.svelte",
      "lib/components/layout/AppLayout.svelte",
    ]) {
      expect(lee(f), f).toMatch(/background:\s*var\(--op-scrim\)/);
    }
    expect(lee("lib/components/layout/AssistantDock.svelte")).toMatch(
      /background:\s*var\(--op-scrim-soft\)/,
    );
  });
});
