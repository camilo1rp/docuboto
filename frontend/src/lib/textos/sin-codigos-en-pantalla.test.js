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
 * sin-codigos-en-pantalla.test.js — Ningún código interno ni jerga en el texto
 * visible (ADR-057 §7 y §10, CR-001 UI-01).
 *
 * Mira solo el marcado: deja fuera `<script>`, `<style>`, comentarios, las
 * expresiones `{…}` y los atributos que no se ven (`marcado.js`). Los patrones
 * viven en `glosario.js`.
 *
 * **La lista de excepciones solo puede encogerse** (plan del CR-001, §1.2). Es
 * lo que incumplía el día que nació el guardián. Un archivo que ya cumple
 * tiene que salir de la lista: la prueba falla si sigue en ella.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { hallazgos } from "./marcado.js";
import { PATRONES_PROHIBIDOS } from "./glosario.js";

/** Todos los componentes del proyecto. */
function componentes() {
  return execSync("find src -name '*.svelte'", { encoding: "utf-8" }).trim().split("\n").sort();
}

/** Lo que incumplía al nacer el guardián. Se vacía por fases; nunca crece. */
const EXCEPCIONES = [
  "src/lib/components/features/admin/AsistenteEntidadCard.svelte",
  "src/lib/components/features/firmas/SignAssistant.svelte",
  "src/lib/components/workflow/TransaccionModal.svelte",
  "src/routes/(app)/admin/+page.svelte",
  "src/routes/(app)/admin/auditoria/+page.svelte",
  "src/routes/(app)/admin/colas/+page.svelte",
  "src/routes/(app)/admin/correo/+page.svelte",
  "src/routes/(app)/admin/correo/oauth/callback/+page.svelte",
  "src/routes/(app)/admin/eliminacion/+page.svelte",
  "src/routes/(app)/admin/grupos/+page.svelte",
  "src/routes/(app)/admin/interoperabilidad/+page.svelte",
  "src/routes/(app)/admin/metadatos/+page.svelte",
  "src/routes/(app)/admin/notificaciones/+page.svelte",
  "src/routes/(app)/admin/parametros/+page.svelte",
  "src/routes/(app)/admin/pinar/+page.svelte",
  "src/routes/(app)/admin/preservacion/+page.svelte",
  "src/routes/(app)/admin/tvd/+page.svelte",
  "src/routes/(app)/correo/+page.svelte",
  "src/routes/(app)/perfil/+page.svelte",
  "src/routes/(app)/reportes/+page.svelte",
];

const PATRONES = PATRONES_PROHIBIDOS.map((p) => p.patron);

describe("sin códigos internos ni jerga en pantalla", () => {
  const archivos = componentes();
  const porArchivo = new Map(
    archivos.map((f) => [f, hallazgos(readFileSync(f, "utf8"), PATRONES)]),
  );

  it("el censo ve el proyecto entero (anti-vacuidad)", () => {
    expect(archivos.length).toBeGreaterThan(100);
  });

  it("el extractor reconoce un código en el texto y lo ignora en el script", () => {
    const fuente =
      '<script>const p = "PERM_X";</script>\n<p>Requisito RF-RAD-04</p>\n<a href="/tenant">ok</a>';
    expect(hallazgos(fuente, PATRONES).map((h) => h.linea)).toEqual([2]);
  });

  it("ningún componente fuera de la lista muestra códigos o jerga", () => {
    const nuevos = archivos
      .filter((f) => !EXCEPCIONES.includes(f))
      .flatMap((f) =>
        porArchivo.get(f).map((h) => `${f}:${h.linea} «${h.coincide}» — ${h.texto.slice(0, 100)}`),
      );
    expect(
      nuevos,
      "Use las palabras del glosario (src/lib/textos/glosario.js): «entidad» y no «tenant», " +
        "sin códigos de permiso, de requisito ni de ADR.",
    ).toEqual([]);
  });

  it("la lista de excepciones solo tiene archivos que todavía incumplen", () => {
    const sobran = EXCEPCIONES.filter((f) => !(porArchivo.get(f)?.length > 0));
    expect(sobran, "Estos archivos ya cumplen: quítelos de EXCEPCIONES.").toEqual([]);
  });
});
