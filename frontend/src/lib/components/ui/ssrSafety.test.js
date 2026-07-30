// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

// @vitest-environment node

/**
 * ssrSafety.test.js — Regresión real de D-16 (SSR: `document` no existe).
 *
 * El bug histórico D-16 fue un 500 SSR causado por un `onDestroy`/bloque
 * reactivo que tocaba `document` sin guardar — invisible para cualquier test
 * que monte el componente con `@testing-library/svelte` en jsdom, porque en
 * jsdom `document` SIEMPRE existe (jsdom no reproduce la ausencia real de
 * `document` del Node del servidor). Por eso este archivo corre en entorno
 * 'node' (`@vitest-environment node`, sin jsdom) y compila Modal.svelte y
 * Drawer.svelte con el compilador de Svelte en modo `generate: "ssr"` — el
 * mismo modo que usa `adapter-node` para renderizar en el servidor — y
 * ejecuta `Component.render(props)` directamente, tal como lo haría
 * SvelteKit en una request real.
 *
 * Se prueba deliberadamente con `open: true` desde el primer render (no solo
 * `open: false`): ese es exactamente el caso que revienta si algún bloque
 * reactivo o lifecycle hook accede a `document` sin comprobar
 * `typeof document !== "undefined"` — un Modal/Drawer que abre según estado
 * inicial (ej. una query param) tiraría 500 en toda la ruta que lo monte.
 */
import { describe, it, expect } from "vitest";
import { compile } from "svelte/compiler";
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import path from "node:path";

/**
 * Compila un componente `.svelte` en modo SSR real y devuelve su módulo
 * ejecutable. Escribe el JS compilado a un archivo temporal DENTRO del
 * proyecto (no en el tmpdir del SO): Vitest enruta los `import()` dinámicos a
 * través del resolvedor de Vite, que rechaza servir archivos fuera de la raíz
 * del proyecto — de ahí que el archivo temporal viva bajo `process.cwd()`.
 *
 * El archivo temporal se escribe como HERMANO del componente (misma carpeta
 * ui/, no un subdirectorio aparte): `compile()` no reescribe los imports
 * relativos del `.svelte` original (p. ej. `import EmptyState from
 * "./EmptyState.svelte"` en DataTable.svelte) — si el archivo compilado
 * viviera en otra carpeta, esos imports relativos apuntarían al lugar
 * equivocado y el `import()` dinámico fallaría con "Does the file exist?".
 *
 * @param {string} componentFileName — nombre de archivo dentro de este directorio (ui/)
 * @returns {Promise<{ render: (props?: object) => { html: string } }>}
 */
async function compileForSsr(componentFileName) {
  const uiDir = path.resolve(process.cwd(), "src/lib/components/ui");
  const absoluteSveltePath = path.join(uiDir, componentFileName);
  const source = readFileSync(absoluteSveltePath, "utf-8");
  const { js } = compile(source, {
    generate: "ssr",
    filename: componentFileName,
    css: "injected",
  });

  const tmpFile = path.join(
    uiDir,
    `.${path.basename(componentFileName, ".svelte")}.ssr-tmp-${process.hrtime.bigint()}.mjs`,
  );
  writeFileSync(tmpFile, js.code);

  try {
    const mod = await import(/* @vite-ignore */ tmpFile);
    return mod.default;
  } finally {
    rmSync(tmpFile, { force: true });
  }
}

describe("SSR real (generate: ssr) — Modal.svelte no revienta con document (D-16)", () => {
  it("open:false (estado por defecto) — no lanza y renderiza vacío", async () => {
    const Modal = await compileForSsr("Modal.svelte");
    const { html } = Modal.render({ open: false, title: "Confirmar" });
    expect(html).not.toContain("op-modal");
  });

  it("open:true desde el primer render — NO revienta con 'document is not defined'", async () => {
    const Modal = await compileForSsr("Modal.svelte");
    // Antes del fix, este render lanzaba ReferenceError: document is not
    // defined (el bloque `$: if (open) { previousFocus = document.activeElement }`
    // corría durante el render SSR, igual que el onDestroy del bug D-16 original).
    const { html } = Modal.render({ open: true, title: "Confirmar" });
    expect(html).toContain('role="dialog"');
    expect(html).toContain("Confirmar");
  });
});

describe("SSR real (generate: ssr) — Drawer.svelte no revienta con document (D-16)", () => {
  it("open:false (estado por defecto) — no lanza y renderiza vacío", async () => {
    const Drawer = await compileForSsr("Drawer.svelte");
    const { html } = Drawer.render({ open: false, title: "Detalle" });
    expect(html).not.toContain("op-drawer__backdrop");
  });

  it("open:true desde el primer render — NO revienta con 'document is not defined'", async () => {
    const Drawer = await compileForSsr("Drawer.svelte");
    const { html } = Drawer.render({ open: true, title: "Detalle" });
    expect(html).toContain('role="dialog"');
    expect(html).toContain("Detalle");
  });
});

describe("SSR real (generate: ssr) — DataTable.svelte no revienta con localStorage/window", () => {
  const columns = [
    { key: "radicado", label: "Radicado", sortable: true },
    { key: "estado", label: "Estado", filterType: "select" },
  ];
  const rows = [{ id: "1", radicado: "2024-E-1", estado: "activo" }];

  it("con enableColumnToggle + enableSavedViews + tableId — no lanza 'window is not defined'", async () => {
    const DataTable = await compileForSsr("DataTable.svelte");
    // Antes de guardar cada acceso a `localStorage` tras `typeof window !==
    // "undefined"`, este render lanzaría ReferenceError en Node real (donde,
    // a diferencia de jsdom, `window` no existe) — la misma clase de bug que
    // D-16 con `document`.
    const { html } = DataTable.render({
      columns,
      rows,
      enableColumnToggle: true,
      enableSavedViews: true,
      enableFilters: true,
      enableSort: true,
      enableSelection: true,
      tableId: "kit-demo",
    });
    expect(html).toContain("op-datatable");
    expect(html).toContain("Radicado");
  });

  it("sin tableId (persistencia deshabilitada) también renderiza sin lanzar", async () => {
    const DataTable = await compileForSsr("DataTable.svelte");
    const { html } = DataTable.render({ columns, rows, enableFilters: true, enableSort: true });
    expect(html).toContain("op-datatable");
  });
});
