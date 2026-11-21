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
 * usted-siempre.test.js — La interfaz trata de usted (ADR-057 §7 y §10,
 * CR-001 UI-03).
 *
 * Busca en el texto visible las formas de tú de `MARCADORES_TUTEO`
 * (`glosario.js`), con el mismo extractor que `sin-codigos-en-pantalla`.
 *
 * **La lista de excepciones solo puede encogerse** (plan del CR-001, §1.2).
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { hallazgos, palabrasEnteras } from "./marcado.js";
import { MARCADORES_TUTEO } from "./glosario.js";

/** Todos los componentes del proyecto. */
function componentes() {
  return execSync("find src -name '*.svelte'", { encoding: "utf-8" }).trim().split("\n").sort();
}

/** Lo que incumplía al nacer el guardián. Se vacía por fases; nunca crece. */
const EXCEPCIONES = [
  "src/lib/components/features/firmas/SignAssistant.svelte",
  "src/lib/components/features/terceros/ActorPicker.svelte",
  "src/lib/components/features/terceros/CascadaDivipola.svelte",
  "src/lib/components/layout/AppLayout.svelte",
  "src/lib/components/workflow/FinalizarTramiteModal.svelte",
  "src/routes/(app)/admin/+page.svelte",
  "src/routes/(app)/admin/auditoria/+page.svelte",
  "src/routes/(app)/admin/catalogos/+page.svelte",
  "src/routes/(app)/admin/colas/+page.svelte",
  "src/routes/(app)/admin/correo/+page.svelte",
  "src/routes/(app)/admin/dependencias/+page.svelte",
  "src/routes/(app)/admin/eliminacion/+page.svelte",
  "src/routes/(app)/admin/flujos/reasignacion/+page.svelte",
  "src/routes/(app)/admin/flujos/reglas/+page.svelte",
  "src/routes/(app)/admin/grupos/+page.svelte",
  "src/routes/(app)/admin/notificaciones/+page.svelte",
  "src/routes/(app)/admin/pinar/+page.svelte",
  "src/routes/(app)/admin/seguridad/claves/+page.svelte",
  "src/routes/(app)/admin/trd/+page.svelte",
  "src/routes/(app)/anulaciones/+page.svelte",
  "src/routes/(app)/archivo-fisico/+page.svelte",
  "src/routes/(app)/busqueda/+page.svelte",
  "src/routes/(app)/correo/[cuentaId]/+page.svelte",
  "src/routes/(app)/transferencias/+page.svelte",
  "src/routes/(public)/+page.svelte",
  "src/routes/(public)/login/+page.svelte",
  "src/routes/(public)/login/callback/+page.svelte",
  "src/routes/(public)/logout/+page.svelte",
  "src/routes/(public)/radicacion/+page.svelte",
];

const PATRONES = [palabrasEnteras(MARCADORES_TUTEO)];

describe("usted siempre", () => {
  const archivos = componentes();
  const porArchivo = new Map(
    archivos.map((f) => [f, hallazgos(readFileSync(f, "utf8"), PATRONES)]),
  );

  it("el censo ve el proyecto entero (anti-vacuidad)", () => {
    expect(archivos.length).toBeGreaterThan(100);
  });

  it("el marcador distingue tú de usted y respeta las tildes", () => {
    expect(hallazgos("<p>Sube tu archivo</p>", PATRONES)).toHaveLength(1);
    expect(hallazgos("<p>Suba su archivo</p>", PATRONES)).toHaveLength(0);
    expect(hallazgos("<p>Está al día</p>", PATRONES)).toHaveLength(0);
    expect(hallazgos("<p>Estás al día</p>", PATRONES)).toHaveLength(1);
    expect(hallazgos("<p>Tutela</p>", PATRONES)).toHaveLength(0);
  });

  it("ningún componente fuera de la lista tutea", () => {
    const nuevos = archivos
      .filter((f) => !EXCEPCIONES.includes(f))
      .flatMap((f) =>
        porArchivo.get(f).map((h) => `${f}:${h.linea} «${h.coincide}» — ${h.texto.slice(0, 100)}`),
      );
    expect(nuevos, "Escriba en usted: «suba», «su», «puede», no «sube», «tu», «puedes».").toEqual(
      [],
    );
  });

  it("la lista de excepciones solo tiene archivos que todavía incumplen", () => {
    const sobran = EXCEPCIONES.filter((f) => !(porArchivo.get(f)?.length > 0));
    expect(sobran, "Estos archivos ya cumplen: quítelos de EXCEPCIONES.").toEqual([]);
  });
});
