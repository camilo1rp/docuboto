import { c as create_ssr_component, e as escape, b as add_attribute, v as validate_component } from "../../../../chunks/ssr.js";
import "../../../../chunks/client.js";
/* empty css                                                      */
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
import { B as Button } from "../../../../chunks/Button.js";
import { F as FormField } from "../../../../chunks/FormField.js";
import { D as DataTable } from "../../../../chunks/DataTable.js";
const css = {
  code: ".reportes.svelte-5nt2bb{padding:var(--op-space-6);max-width:1200px;margin:0 auto;display:flex;flex-direction:column;gap:var(--op-space-5)}.reportes__header.svelte-5nt2bb{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--op-space-4);flex-wrap:wrap;padding-bottom:var(--op-space-4);border-bottom:1px solid var(--op-border)}.reportes__header-left.svelte-5nt2bb{display:flex;flex-direction:column;gap:var(--op-space-1)}.reportes__title.svelte-5nt2bb{font-size:var(--op-font-xl);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}.reportes__total.svelte-5nt2bb{font-size:var(--op-font-sm);color:var(--op-text-secondary)}.csv-download-btn.svelte-5nt2bb{display:inline-flex;align-items:center;gap:var(--op-space-2);padding:var(--op-space-2) var(--op-space-4);background:var(--op-primary-dark);color:var(--op-surface);border:none;border-radius:var(--op-radius);font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);cursor:pointer;transition:background var(--op-transition);flex-shrink:0}.csv-download-btn.svelte-5nt2bb:hover:not(:disabled){background:var(--op-brand-700)}.csv-download-btn.svelte-5nt2bb:disabled{opacity:0.6;cursor:not-allowed}.csv-download-btn.svelte-5nt2bb:focus-visible{outline:2px solid var(--op-primary);outline-offset:3px;border-radius:var(--op-radius)}.reportes__filters.svelte-5nt2bb{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);padding:var(--op-space-4) var(--op-space-5)}.filters-form.svelte-5nt2bb{display:flex;flex-direction:column;gap:var(--op-space-3)}.filters-form__row.svelte-5nt2bb{display:flex;align-items:flex-end;gap:var(--op-space-4);flex-wrap:wrap}.filters-form__fields.svelte-5nt2bb{display:flex;gap:var(--op-space-3);flex-wrap:wrap;flex:1}.filters-form__actions.svelte-5nt2bb{display:flex;gap:var(--op-space-2);align-items:flex-end;margin-bottom:2px}.filters-form__date-range-error.svelte-5nt2bb{display:flex;align-items:center;gap:var(--op-space-2);width:100%;font-size:var(--op-font-xs);color:var(--op-error);margin:var(--op-space-1) 0 0}.reportes__loading.svelte-5nt2bb{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);padding:var(--op-space-6)}.reportes__panels.svelte-5nt2bb{display:grid;grid-template-columns:repeat(2, 1fr);gap:var(--op-space-5)}.report-panel.svelte-5nt2bb{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);padding:var(--op-space-4) var(--op-space-5);display:flex;flex-direction:column;gap:var(--op-space-3)}.report-panel.svelte-5nt2bb .op-datatable{border:1px solid var(--op-border);border-radius:var(--op-radius);overflow:hidden}.report-panel--wide.svelte-5nt2bb{grid-column:1/-1}.report-panel__title.svelte-5nt2bb{font-size:var(--op-font-md);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0;padding-bottom:var(--op-space-3);border-bottom:1px solid var(--op-border)}.report-panel__total.svelte-5nt2bb{font-size:var(--op-font-sm);color:var(--op-text-secondary);text-align:right;margin:0;padding-top:var(--op-space-2);border-top:1px solid var(--op-border)}.report-cell--num.svelte-5nt2bb{text-align:right;font-variant-numeric:tabular-nums}.doc-type-badge.svelte-5nt2bb{display:inline-block;font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);padding:2px var(--op-space-2);border-radius:var(--op-radius-sm);white-space:nowrap}.doc-type-badge--e.svelte-5nt2bb{background:var(--op-info-bg);color:var(--op-info-fg)}.doc-type-badge--s.svelte-5nt2bb{background:var(--op-warning-bg);color:var(--op-warning-fg)}.doc-type-badge--i.svelte-5nt2bb{background:var(--op-bg);color:var(--op-text-secondary);border:1px solid var(--op-border)}.pct-bar.svelte-5nt2bb{display:inline-flex;width:60px;height:6px;background:var(--op-bg);border-radius:3px;overflow:hidden;vertical-align:middle;margin-right:var(--op-space-2);border:1px solid var(--op-border)}.pct-bar__fill.svelte-5nt2bb{display:block;height:100%;background:var(--op-primary);border-radius:3px;transition:width 0.3s ease}.pct-text.svelte-5nt2bb{font-variant-numeric:tabular-nums;font-size:var(--op-font-xs);color:var(--op-text-secondary)}.bar-visual.svelte-5nt2bb{display:flex;height:8px;width:100%;max-width:160px;background:var(--op-bg);border-radius:4px;overflow:hidden;border:1px solid var(--op-border)}.bar-visual__fill.svelte-5nt2bb{display:block;height:100%;background:var(--op-accent);border-radius:4px;transition:width 0.3s ease}@media(max-width: 900px){.reportes__panels.svelte-5nt2bb{grid-template-columns:1fr}.report-panel--wide.svelte-5nt2bb{grid-column:1}}@media(max-width: 600px){.reportes.svelte-5nt2bb{padding:var(--op-space-4)}.reportes__header.svelte-5nt2bb{flex-direction:column}.filters-form.svelte-5nt2bb{flex-direction:column;align-items:stretch}.filters-form__actions.svelte-5nt2bb{justify-content:flex-start}}",
  map: '{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /reportes — Reportes y estadísticas de radicados (T-11, RF-UI-03).\\n\\n  Muestra conteos de radicados agrupados por:\\n    - Tipo (E/S/I)\\n    - Estado (registered / distributed / in_progress / closed)\\n    - Mes (por fecha de radicación)\\n    - Dependencia destino\\n\\n  El botón \\"Descargar CSV\\" del encabezado apunta a /reportes/api/csv (proxy SSR)\\n  que lee el token de la cookie httpOnly — el token nunca alcanza el cliente\\n  (D-05). Exporta el reporte COMPLETO desde el backend, con los mismos filtros\\n  de fecha activos.\\n\\n  Cada uno de los cuatro paneles usa además el DataTable compartido (Sistema de\\n  Diseño §12): exportación CSV LOCAL de las filas visibles del panel (con\\n  filtro/orden ya aplicados) y ordenamiento. Ambas exportaciones son\\n  complementarias: la del encabezado trae todo el período desde el gateway; la\\n  de cada panel exporta justo lo que el usuario está mirando en ese momento.\\n  No se usan librerías de charting: las barras de proporción son CSS con\\n  tokens --op-*, dibujadas dentro de una celda controlada vía slot=\\"row\\" (el\\n  renderizado automático de DataTable no sabe dibujar una barra).\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { goto } from \\"$app/navigation\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { notifyError } from \\"$lib/stores/notifications.js\\";\\n  import Loader from \\"$lib/components/ui/Loader.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import DataTable from \\"$lib/components/ui/DataTable.svelte\\";\\n\\n  /** @type {import(\'./$types\').PageData} */\\n  export let data;\\n\\n  // ---------------------------------------------------------------------------\\n  // Filtros de rango de fechas\\n  // ---------------------------------------------------------------------------\\n  let dateFrom = data.initialFilters?.dateFrom ?? \\"\\";\\n  let dateTo = data.initialFilters?.dateTo ?? \\"\\";\\n  let loading = false;\\n  let csvLoading = false;\\n  let dateRangeError = \\"\\";\\n\\n  // ---------------------------------------------------------------------------\\n  // Breadcrumbs\\n  // ---------------------------------------------------------------------------\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Reportes\\" }]);\\n  });\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de presentación\\n  // ---------------------------------------------------------------------------\\n  const DOC_TYPE_LABELS = { E: \\"Entrada\\", S: \\"Salida\\", I: \\"Interno\\" };\\n  const STATUS_LABELS = {\\n    registered: \\"Radicado\\",\\n    distributed: \\"Distribuido\\",\\n    in_progress: \\"En trámite\\",\\n    closed: \\"Cerrado\\",\\n  };\\n\\n  /**\\n   * Formatea un mes ISO (YYYY-MM) como texto legible en español.\\n   * @param {string} month\\n   */\\n  function formatMonth(month) {\\n    if (!month) return \\"—\\";\\n    const [year, mon] = month.split(\\"-\\");\\n    const date = new Date(Number(year), Number(mon) - 1, 1);\\n    return date.toLocaleDateString(\\"es-CO\\", { year: \\"numeric\\", month: \\"long\\" });\\n  }\\n\\n  /**\\n   * Calcula el ancho de la barra de proporción (0–100%) respecto al máximo.\\n   * @param {number} value\\n   * @param {number} max\\n   */\\n  function barWidth(value, max) {\\n    if (!max || max === 0) return \\"0%\\";\\n    return `${Math.round((value / max) * 100)}%`;\\n  }\\n\\n  /** Total de radicados del período */\\n  $: grandTotal = (() => {\\n    const types = data.summary?.by_type ?? [];\\n    return types.reduce((acc, row) => acc + (row.count ?? 0), 0);\\n  })();\\n\\n  /** Máximo de los conteos de dependencia (para las barras) */\\n  $: maxDeptCount = (() => {\\n    const depts = data.summary?.by_dept ?? [];\\n    return Math.max(...depts.map((d) => d.count ?? 0), 0);\\n  })();\\n\\n  /** Máximo de los conteos por mes (para las barras) */\\n  $: maxMonthCount = (() => {\\n    const months = data.summary?.by_month ?? [];\\n    return Math.max(...months.map((m) => m.count ?? 0), 0);\\n  })();\\n\\n  // ---------------------------------------------------------------------------\\n  // Filas derivadas para cada DataTable — cada fila lleva ya los campos de\\n  // TEXTO que la exportación CSV va a leer (`buildCsv` lee `row[col.key]`\\n  // literal; si dejáramos solo `count`/`doc_type` crudos, exportar perdería la\\n  // etiqueta legible y el % ya calculado que el usuario ve en pantalla).\\n  // ---------------------------------------------------------------------------\\n\\n  $: typeRows = (data.summary?.by_type ?? []).map((row) => {\\n    const count = row.count ?? 0;\\n    const pct = grandTotal > 0 ? Math.round((count / grandTotal) * 100) : 0;\\n    return {\\n      doc_type: row.doc_type,\\n      type_label: DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type,\\n      count,\\n      pct,\\n      pct_label: `${pct}%`,\\n    };\\n  });\\n\\n  $: statusRows = (data.summary?.by_status ?? []).map((row) => {\\n    const count = row.count ?? 0;\\n    const pct = grandTotal > 0 ? Math.round((count / grandTotal) * 100) : 0;\\n    return {\\n      status: row.status,\\n      status_label: STATUS_LABELS[row.status] ?? row.status,\\n      count,\\n      pct,\\n      pct_label: `${pct}%`,\\n    };\\n  });\\n\\n  $: monthRows = (data.summary?.by_month ?? []).map((row) => {\\n    const count = row.count ?? 0;\\n    const share = maxMonthCount > 0 ? Math.round((count / maxMonthCount) * 100) : 0;\\n    return {\\n      month: row.month,\\n      month_label: formatMonth(row.month),\\n      count,\\n      share_label: `${share}%`,\\n    };\\n  });\\n\\n  $: deptRows = (data.summary?.by_dept ?? []).map((row) => {\\n    const count = row.count ?? 0;\\n    const share = maxDeptCount > 0 ? Math.round((count / maxDeptCount) * 100) : 0;\\n    return {\\n      dept: row.dept ?? row.dest_dept ?? null,\\n      dept_label: row.dept ?? row.dest_dept ?? \\"Sin dependencia\\",\\n      count,\\n      share_label: `${share}%`,\\n    };\\n  });\\n\\n  const typeColumns = [\\n    { key: \\"type_label\\", label: \\"Tipo\\", sortable: true },\\n    { key: \\"count\\", label: \\"Cantidad\\", sortable: true },\\n    { key: \\"pct_label\\", label: \\"%\\", sortable: true },\\n  ];\\n\\n  const statusColumns = [\\n    { key: \\"status_label\\", label: \\"Estado\\", sortable: true },\\n    { key: \\"count\\", label: \\"Cantidad\\", sortable: true },\\n    { key: \\"pct_label\\", label: \\"%\\", sortable: true },\\n  ];\\n\\n  const monthColumns = [\\n    { key: \\"month_label\\", label: \\"Mes\\", sortable: true, filterType: /** @type {const} */ (\\"text\\") },\\n    { key: \\"count\\", label: \\"Radicados\\", sortable: true },\\n    { key: \\"share_label\\", label: \\"Distribución\\", sortable: true },\\n  ];\\n\\n  const deptColumns = [\\n    {\\n      key: \\"dept_label\\",\\n      label: \\"Dependencia\\",\\n      sortable: true,\\n      filterType: /** @type {const} */ (\\"text\\"),\\n    },\\n    { key: \\"count\\", label: \\"Radicados\\", sortable: true },\\n    { key: \\"share_label\\", label: \\"Distribución\\", sortable: true },\\n  ];\\n\\n  // ---------------------------------------------------------------------------\\n  // Aplicar filtros — goto() actualiza la URL y re-ejecuta el server load (SSR)\\n  // ---------------------------------------------------------------------------\\n\\n  async function applyFilters() {\\n    dateRangeError = \\"\\";\\n    if (dateFrom && dateTo && dateFrom > dateTo) {\\n      dateRangeError = \\"La fecha inicio no puede ser posterior a la fecha fin.\\";\\n      return;\\n    }\\n    loading = true;\\n    const params = new URLSearchParams();\\n    if (dateFrom) params.set(\\"date_from\\", dateFrom);\\n    if (dateTo) params.set(\\"date_to\\", dateTo);\\n    const qs = params.toString();\\n    await goto(`/reportes${qs ? `?${qs}` : \\"\\"}`, { keepFocus: true });\\n    loading = false;\\n  }\\n\\n  function clearFilters() {\\n    dateFrom = \\"\\";\\n    dateTo = \\"\\";\\n    dateRangeError = \\"\\";\\n    goto(\\"/reportes\\");\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Descarga CSV vía fetch → blob con manejo de error (Fix #3).\\n  // Llama al proxy SSR /reportes/api/csv que lee el token de la cookie httpOnly.\\n  // Un <a download> simple guardaría el JSON de error como archivo .csv si el\\n  // upstream falla — la descarga vía fetch permite detectar el error y notificar.\\n  // ---------------------------------------------------------------------------\\n\\n  /**\\n   * Construye la URL del endpoint CSV con los filtros de fecha activos.\\n   * @returns {string}\\n   */\\n  function buildCsvUrl() {\\n    const p = new URLSearchParams();\\n    if (dateFrom && /^\\\\d{4}-\\\\d{2}-\\\\d{2}$/.test(dateFrom)) p.set(\\"date_from\\", dateFrom);\\n    if (dateTo && /^\\\\d{4}-\\\\d{2}-\\\\d{2}$/.test(dateTo)) p.set(\\"date_to\\", dateTo);\\n    const qs = p.toString();\\n    return `/reportes/api/csv${qs ? `?${qs}` : \\"\\"}`;\\n  }\\n\\n  async function downloadCsv() {\\n    if (csvLoading) return;\\n    csvLoading = true;\\n    try {\\n      const res = await fetch(buildCsvUrl());\\n      if (!res.ok) {\\n        const body = await res.json().catch(() => ({}));\\n        notifyError(body.detail ?? \\"Error al descargar el reporte CSV.\\");\\n        return;\\n      }\\n      const blob = await res.blob();\\n      const objectUrl = URL.createObjectURL(blob);\\n      const anchor = document.createElement(\\"a\\");\\n      anchor.href = objectUrl;\\n      anchor.download = \\"radicados.csv\\";\\n      document.body.appendChild(anchor);\\n      anchor.click();\\n      document.body.removeChild(anchor);\\n      URL.revokeObjectURL(objectUrl);\\n    } catch {\\n      notifyError(\\"No se pudo descargar el reporte CSV. Intente de nuevo.\\");\\n    } finally {\\n      csvLoading = false;\\n    }\\n  }\\n\\n  $: hasFilters = !!(dateFrom || dateTo);\\n<\/script>\\n\\n<svelte:head>\\n  <title>Reportes — OrpycaMCP</title>\\n</svelte:head>\\n\\n<div class=\\"reportes\\">\\n  <!-- -------------------------------------------------------------------------\\n    Encabezado\\n  ------------------------------------------------------------------------- -->\\n  <header class=\\"reportes__header\\">\\n    <div class=\\"reportes__header-left\\">\\n      <h1 class=\\"reportes__title\\">Reportes de radicados</h1>\\n      {#if grandTotal > 0}\\n        <span class=\\"reportes__total\\">\\n          {grandTotal.toLocaleString(\\"es-CO\\")} radicado{grandTotal !== 1 ? \\"s\\" : \\"\\"} en el período\\n        </span>\\n      {/if}\\n    </div>\\n    <!-- Descarga CSV vía fetch→blob; el proxy SSR lee el token de la cookie httpOnly.\\n         No usar <a download> porque guardaría el JSON de error como archivo .csv.\\n         Trae el reporte COMPLETO del período desde el backend (a diferencia de\\n         la exportación por-panel de cada DataTable, que solo exporta las filas\\n         visibles de ese panel). -->\\n    <button\\n      type=\\"button\\"\\n      class=\\"csv-download-btn\\"\\n      disabled={csvLoading}\\n      aria-label={csvLoading ? \\"Descargando reporte CSV…\\" : \\"Descargar reporte en formato CSV\\"}\\n      on:click={downloadCsv}\\n    >\\n      <i class=\\"fas fa-file-csv\\" aria-hidden=\\"true\\"></i>\\n      {csvLoading ? \\"Descargando…\\" : \\"Descargar CSV\\"}\\n    </button>\\n  </header>\\n\\n  <!-- -------------------------------------------------------------------------\\n    Filtros de rango de fechas\\n  ------------------------------------------------------------------------- -->\\n  <section class=\\"reportes__filters\\" aria-label=\\"Filtros de rango de fechas\\">\\n    <form\\n      class=\\"filters-form\\"\\n      on:submit|preventDefault={applyFilters}\\n      aria-label=\\"Filtrar reportes por período\\"\\n    >\\n      <div class=\\"filters-form__row\\">\\n        <div class=\\"filters-form__fields\\">\\n          <FormField label=\\"Fecha desde\\" type=\\"date\\" id=\\"report-date-from\\" bind:value={dateFrom} />\\n          <FormField label=\\"Fecha hasta\\" type=\\"date\\" id=\\"report-date-to\\" bind:value={dateTo} />\\n        </div>\\n        <div class=\\"filters-form__actions\\">\\n          <Button type=\\"submit\\" variant=\\"primary\\" size=\\"sm\\" disabled={loading}>\\n            <i class=\\"fas fa-filter\\" aria-hidden=\\"true\\"></i>\\n            Aplicar\\n          </Button>\\n          {#if hasFilters}\\n            <Button type=\\"button\\" variant=\\"ghost\\" size=\\"sm\\" on:click={clearFilters}>\\n              <i class=\\"fas fa-times\\" aria-hidden=\\"true\\"></i>\\n              Limpiar\\n            </Button>\\n          {/if}\\n        </div>\\n      </div>\\n      {#if dateRangeError}\\n        <p class=\\"filters-form__date-range-error\\" role=\\"alert\\">\\n          <i class=\\"fas fa-exclamation-circle\\" aria-hidden=\\"true\\"></i>\\n          {dateRangeError}\\n        </p>\\n      {/if}\\n    </form>\\n  </section>\\n\\n  <!-- -------------------------------------------------------------------------\\n    Contenido principal\\n  ------------------------------------------------------------------------- -->\\n  <!--\\n    El aria-live vive en un nodo pequeño y estático (Fix #6, patrón replicado de\\n    busqueda/+page.svelte) para no reanunciar los cuatro paneles/tablas completos\\n    en cada filtro/orden/skeleton; cada DataTable ya trae su propia región\\n    aria-live interna para sus propios cambios.\\n  -->\\n  <p class=\\"sr-only\\" aria-live=\\"polite\\" aria-atomic=\\"true\\">\\n    {#if loading}\\n      Cargando reporte…\\n    {:else if data.loadError}\\n      No se pudo cargar el reporte.\\n    {:else}\\n      Reporte actualizado: {grandTotal.toLocaleString(\\"es-CO\\")} radicado{grandTotal !== 1\\n        ? \\"s\\"\\n        : \\"\\"} en el período.\\n    {/if}\\n  </p>\\n\\n  <div class=\\"reportes__content\\" aria-busy={loading}>\\n    {#if loading}\\n      <div class=\\"reportes__loading\\">\\n        <Loader type=\\"skeleton\\" lines={6} label=\\"Cargando reporte…\\" />\\n      </div>\\n    {:else if data.loadError}\\n      <EmptyState\\n        icon=\\"fas fa-exclamation-circle\\"\\n        title=\\"Error al cargar el reporte\\"\\n        description={data.loadError}\\n      >\\n        <Button variant=\\"primary\\" on:click={applyFilters}>Reintentar</Button>\\n      </EmptyState>\\n    {:else if !data.summary}\\n      <EmptyState\\n        icon=\\"fas fa-chart-bar\\"\\n        title=\\"Sin datos\\"\\n        description=\\"No hay datos de radicados para mostrar en el período seleccionado.\\"\\n      />\\n    {:else}\\n      <div class=\\"reportes__panels\\">\\n        <!-- =================================================================\\n          Panel: Por tipo de radicado\\n        ================================================================= -->\\n        <section class=\\"report-panel\\" aria-labelledby=\\"panel-tipo-heading\\">\\n          <h2 id=\\"panel-tipo-heading\\" class=\\"report-panel__title\\">Por tipo</h2>\\n          <DataTable\\n            tableId=\\"reportes-tipo\\"\\n            columns={typeColumns}\\n            rows={typeRows}\\n            totalCount={typeRows.length}\\n            pageSize={Math.max(typeRows.length, 1)}\\n            enableSort\\n            enableExport\\n            exportFilename=\\"reportes-por-tipo.csv\\"\\n            caption=\\"Radicados por tipo\\"\\n          >\\n            <svelte:fragment slot=\\"row\\" let:row>\\n              <td class=\\"op-datatable__td\\">\\n                <span class=\\"doc-type-badge doc-type-badge--{(row.doc_type ?? \'\').toLowerCase()}\\">\\n                  {row.type_label}\\n                </span>\\n              </td>\\n              <td class=\\"op-datatable__td report-cell--num\\">{row.count.toLocaleString(\\"es-CO\\")}</td>\\n              <td class=\\"op-datatable__td\\">\\n                <span class=\\"pct-bar\\" aria-hidden=\\"true\\">\\n                  <span class=\\"pct-bar__fill\\" style=\\"width: {row.pct}%\\"></span>\\n                </span>\\n                <span class=\\"pct-text\\">{row.pct_label}</span>\\n              </td>\\n            </svelte:fragment>\\n          </DataTable>\\n          {#if typeRows.length > 0}\\n            <p class=\\"report-panel__total\\">\\n              Total: <strong>{grandTotal.toLocaleString(\\"es-CO\\")}</strong> (100%)\\n            </p>\\n          {/if}\\n        </section>\\n\\n        <!-- =================================================================\\n          Panel: Por estado\\n        ================================================================= -->\\n        <section class=\\"report-panel\\" aria-labelledby=\\"panel-estado-heading\\">\\n          <h2 id=\\"panel-estado-heading\\" class=\\"report-panel__title\\">Por estado</h2>\\n          <DataTable\\n            tableId=\\"reportes-estado\\"\\n            columns={statusColumns}\\n            rows={statusRows}\\n            totalCount={statusRows.length}\\n            pageSize={Math.max(statusRows.length, 1)}\\n            enableSort\\n            enableExport\\n            exportFilename=\\"reportes-por-estado.csv\\"\\n            caption=\\"Radicados por estado\\"\\n          >\\n            <svelte:fragment slot=\\"row\\" let:row>\\n              <td class=\\"op-datatable__td\\">{row.status_label}</td>\\n              <td class=\\"op-datatable__td report-cell--num\\">{row.count.toLocaleString(\\"es-CO\\")}</td>\\n              <td class=\\"op-datatable__td\\">\\n                <span class=\\"pct-bar\\" aria-hidden=\\"true\\">\\n                  <span class=\\"pct-bar__fill\\" style=\\"width: {row.pct}%\\"></span>\\n                </span>\\n                <span class=\\"pct-text\\">{row.pct_label}</span>\\n              </td>\\n            </svelte:fragment>\\n          </DataTable>\\n        </section>\\n\\n        <!-- =================================================================\\n          Panel: Por mes (actividad mensual)\\n        ================================================================= -->\\n        <section class=\\"report-panel report-panel--wide\\" aria-labelledby=\\"panel-mes-heading\\">\\n          <h2 id=\\"panel-mes-heading\\" class=\\"report-panel__title\\">Actividad mensual</h2>\\n          <DataTable\\n            tableId=\\"reportes-mes\\"\\n            columns={monthColumns}\\n            rows={monthRows}\\n            totalCount={monthRows.length}\\n            pageSize={Math.max(monthRows.length, 1)}\\n            enableSort\\n            enableFilters\\n            enableExport\\n            exportFilename=\\"reportes-actividad-mensual.csv\\"\\n            caption=\\"Radicados por mes\\"\\n          >\\n            <svelte:fragment slot=\\"row\\" let:row>\\n              <td class=\\"op-datatable__td\\">\\n                <time datetime={row.month}>{row.month_label}</time>\\n              </td>\\n              <td class=\\"op-datatable__td report-cell--num\\">{row.count.toLocaleString(\\"es-CO\\")}</td>\\n              <td class=\\"op-datatable__td\\">\\n                <span class=\\"bar-visual\\" aria-hidden=\\"true\\">\\n                  <span class=\\"bar-visual__fill\\" style=\\"width: {barWidth(row.count, maxMonthCount)}\\"\\n                  ></span>\\n                </span>\\n                <span class=\\"pct-text\\">{row.share_label}</span>\\n              </td>\\n            </svelte:fragment>\\n          </DataTable>\\n        </section>\\n\\n        <!-- =================================================================\\n          Panel: Por dependencia destino\\n        ================================================================= -->\\n        <section class=\\"report-panel report-panel--wide\\" aria-labelledby=\\"panel-dept-heading\\">\\n          <h2 id=\\"panel-dept-heading\\" class=\\"report-panel__title\\">Por dependencia destino</h2>\\n          <DataTable\\n            tableId=\\"reportes-dependencia\\"\\n            columns={deptColumns}\\n            rows={deptRows}\\n            totalCount={deptRows.length}\\n            pageSize={Math.max(deptRows.length, 1)}\\n            enableSort\\n            enableFilters\\n            enableExport\\n            virtualize\\n            exportFilename=\\"reportes-por-dependencia.csv\\"\\n            caption=\\"Radicados por dependencia destino\\"\\n          >\\n            <svelte:fragment slot=\\"row\\" let:row>\\n              <td class=\\"op-datatable__td\\">{row.dept_label}</td>\\n              <td class=\\"op-datatable__td report-cell--num\\">{row.count.toLocaleString(\\"es-CO\\")}</td>\\n              <td class=\\"op-datatable__td\\">\\n                <span class=\\"bar-visual\\" aria-hidden=\\"true\\">\\n                  <span class=\\"bar-visual__fill\\" style=\\"width: {barWidth(row.count, maxDeptCount)}\\"\\n                  ></span>\\n                </span>\\n                <span class=\\"pct-text\\">{row.share_label}</span>\\n              </td>\\n            </svelte:fragment>\\n          </DataTable>\\n        </section>\\n      </div>\\n    {/if}\\n  </div>\\n</div>\\n\\n<style lang=\\"scss\\">.reportes {\\n  padding: var(--op-space-6);\\n  max-width: 1200px;\\n  margin: 0 auto;\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-5);\\n}\\n\\n.reportes__header {\\n  display: flex;\\n  align-items: flex-start;\\n  justify-content: space-between;\\n  gap: var(--op-space-4);\\n  flex-wrap: wrap;\\n  padding-bottom: var(--op-space-4);\\n  border-bottom: 1px solid var(--op-border);\\n}\\n\\n.reportes__header-left {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-1);\\n}\\n\\n.reportes__title {\\n  font-size: var(--op-font-xl);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n\\n.reportes__total {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n}\\n\\n.csv-download-btn {\\n  display: inline-flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  padding: var(--op-space-2) var(--op-space-4);\\n  background: var(--op-primary-dark);\\n  color: var(--op-surface);\\n  border: none;\\n  border-radius: var(--op-radius);\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  cursor: pointer;\\n  transition: background var(--op-transition);\\n  flex-shrink: 0;\\n}\\n.csv-download-btn:hover:not(:disabled) {\\n  background: var(--op-brand-700);\\n}\\n.csv-download-btn:disabled {\\n  opacity: 0.6;\\n  cursor: not-allowed;\\n}\\n.csv-download-btn:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 3px;\\n  border-radius: var(--op-radius);\\n}\\n\\n.reportes__filters {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  padding: var(--op-space-4) var(--op-space-5);\\n}\\n\\n.filters-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n}\\n\\n.filters-form__row {\\n  display: flex;\\n  align-items: flex-end;\\n  gap: var(--op-space-4);\\n  flex-wrap: wrap;\\n}\\n\\n.filters-form__fields {\\n  display: flex;\\n  gap: var(--op-space-3);\\n  flex-wrap: wrap;\\n  flex: 1;\\n}\\n\\n.filters-form__actions {\\n  display: flex;\\n  gap: var(--op-space-2);\\n  align-items: flex-end;\\n  margin-bottom: 2px;\\n}\\n\\n.filters-form__date-range-error {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  width: 100%;\\n  font-size: var(--op-font-xs);\\n  color: var(--op-error);\\n  margin: var(--op-space-1) 0 0;\\n}\\n\\n.reportes__loading {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  padding: var(--op-space-6);\\n}\\n\\n.reportes__panels {\\n  display: grid;\\n  grid-template-columns: repeat(2, 1fr);\\n  gap: var(--op-space-5);\\n}\\n\\n.report-panel {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  padding: var(--op-space-4) var(--op-space-5);\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n}\\n.report-panel :global(.op-datatable) {\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n  overflow: hidden;\\n}\\n.report-panel--wide {\\n  grid-column: 1/-1;\\n}\\n\\n.report-panel__title {\\n  font-size: var(--op-font-md);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n  padding-bottom: var(--op-space-3);\\n  border-bottom: 1px solid var(--op-border);\\n}\\n\\n.report-panel__total {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n  text-align: right;\\n  margin: 0;\\n  padding-top: var(--op-space-2);\\n  border-top: 1px solid var(--op-border);\\n}\\n\\n.report-cell--num {\\n  text-align: right;\\n  font-variant-numeric: tabular-nums;\\n}\\n\\n.doc-type-badge {\\n  display: inline-block;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  padding: 2px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  white-space: nowrap;\\n}\\n.doc-type-badge--e {\\n  background: var(--op-info-bg);\\n  color: var(--op-info-fg);\\n}\\n.doc-type-badge--s {\\n  background: var(--op-warning-bg);\\n  color: var(--op-warning-fg);\\n}\\n.doc-type-badge--i {\\n  background: var(--op-bg);\\n  color: var(--op-text-secondary);\\n  border: 1px solid var(--op-border);\\n}\\n\\n.pct-bar {\\n  display: inline-flex;\\n  width: 60px;\\n  height: 6px;\\n  background: var(--op-bg);\\n  border-radius: 3px;\\n  overflow: hidden;\\n  vertical-align: middle;\\n  margin-right: var(--op-space-2);\\n  border: 1px solid var(--op-border);\\n}\\n.pct-bar__fill {\\n  display: block;\\n  height: 100%;\\n  background: var(--op-primary);\\n  border-radius: 3px;\\n  transition: width 0.3s ease;\\n}\\n\\n.pct-text {\\n  font-variant-numeric: tabular-nums;\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n}\\n\\n.bar-visual {\\n  display: flex;\\n  height: 8px;\\n  width: 100%;\\n  max-width: 160px;\\n  background: var(--op-bg);\\n  border-radius: 4px;\\n  overflow: hidden;\\n  border: 1px solid var(--op-border);\\n}\\n.bar-visual__fill {\\n  display: block;\\n  height: 100%;\\n  background: var(--op-accent);\\n  border-radius: 4px;\\n  transition: width 0.3s ease;\\n}\\n\\n@media (max-width: 900px) {\\n  .reportes__panels {\\n    grid-template-columns: 1fr;\\n  }\\n  .report-panel--wide {\\n    grid-column: 1;\\n  }\\n}\\n@media (max-width: 600px) {\\n  .reportes {\\n    padding: var(--op-space-4);\\n  }\\n  .reportes__header {\\n    flex-direction: column;\\n  }\\n  .filters-form {\\n    flex-direction: column;\\n    align-items: stretch;\\n  }\\n  .filters-form__actions {\\n    justify-content: flex-start;\\n  }\\n}</style>\\n"],"names":[],"mappings":"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"}'
};
function formatMonth(month) {
  if (!month) return "—";
  const [year, mon] = month.split("-");
  const date = new Date(Number(year), Number(mon) - 1, 1);
  return date.toLocaleDateString("es-CO", { year: "numeric", month: "long" });
}
function barWidth(value, max) {
  if (!max || max === 0) return "0%";
  return `${Math.round(value / max * 100)}%`;
}
const Page = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let grandTotal;
  let maxDeptCount;
  let maxMonthCount;
  let typeRows;
  let statusRows;
  let monthRows;
  let deptRows;
  let hasFilters;
  let { data } = $$props;
  let dateFrom = data.initialFilters?.dateFrom ?? "";
  let dateTo = data.initialFilters?.dateTo ?? "";
  let loading = false;
  const DOC_TYPE_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };
  const STATUS_LABELS = {
    registered: "Radicado",
    distributed: "Distribuido",
    in_progress: "En trámite",
    closed: "Cerrado"
  };
  const typeColumns = [
    {
      key: "type_label",
      label: "Tipo",
      sortable: true
    },
    {
      key: "count",
      label: "Cantidad",
      sortable: true
    },
    {
      key: "pct_label",
      label: "%",
      sortable: true
    }
  ];
  const statusColumns = [
    {
      key: "status_label",
      label: "Estado",
      sortable: true
    },
    {
      key: "count",
      label: "Cantidad",
      sortable: true
    },
    {
      key: "pct_label",
      label: "%",
      sortable: true
    }
  ];
  const monthColumns = [
    {
      key: "month_label",
      label: "Mes",
      sortable: true,
      filterType: (
        /** @type {const} */
        "text"
      )
    },
    {
      key: "count",
      label: "Radicados",
      sortable: true
    },
    {
      key: "share_label",
      label: "Distribución",
      sortable: true
    }
  ];
  const deptColumns = [
    {
      key: "dept_label",
      label: "Dependencia",
      sortable: true,
      filterType: (
        /** @type {const} */
        "text"
      )
    },
    {
      key: "count",
      label: "Radicados",
      sortable: true
    },
    {
      key: "share_label",
      label: "Distribución",
      sortable: true
    }
  ];
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    grandTotal = (() => {
      const types = data.summary?.by_type ?? [];
      return types.reduce((acc, row) => acc + (row.count ?? 0), 0);
    })();
    maxDeptCount = (() => {
      const depts = data.summary?.by_dept ?? [];
      return Math.max(...depts.map((d) => d.count ?? 0), 0);
    })();
    maxMonthCount = (() => {
      const months = data.summary?.by_month ?? [];
      return Math.max(...months.map((m) => m.count ?? 0), 0);
    })();
    typeRows = (data.summary?.by_type ?? []).map((row) => {
      const count = row.count ?? 0;
      const pct = grandTotal > 0 ? Math.round(count / grandTotal * 100) : 0;
      return {
        doc_type: row.doc_type,
        type_label: DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type,
        count,
        pct,
        pct_label: `${pct}%`
      };
    });
    statusRows = (data.summary?.by_status ?? []).map((row) => {
      const count = row.count ?? 0;
      const pct = grandTotal > 0 ? Math.round(count / grandTotal * 100) : 0;
      return {
        status: row.status,
        status_label: STATUS_LABELS[row.status] ?? row.status,
        count,
        pct,
        pct_label: `${pct}%`
      };
    });
    monthRows = (data.summary?.by_month ?? []).map((row) => {
      const count = row.count ?? 0;
      const share = maxMonthCount > 0 ? Math.round(count / maxMonthCount * 100) : 0;
      return {
        month: row.month,
        month_label: formatMonth(row.month),
        count,
        share_label: `${share}%`
      };
    });
    deptRows = (data.summary?.by_dept ?? []).map((row) => {
      const count = row.count ?? 0;
      const share = maxDeptCount > 0 ? Math.round(count / maxDeptCount * 100) : 0;
      return {
        dept: row.dept ?? row.dest_dept ?? null,
        dept_label: row.dept ?? row.dest_dept ?? "Sin dependencia",
        count,
        share_label: `${share}%`
      };
    });
    hasFilters = !!(dateFrom || dateTo);
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-bkenjp_START -->${$$result.title = `<title>Reportes — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-bkenjp_END -->`, ""} <div class="reportes svelte-5nt2bb"> <header class="reportes__header svelte-5nt2bb"><div class="reportes__header-left svelte-5nt2bb"><h1 class="reportes__title svelte-5nt2bb" data-svelte-h="svelte-1shj5fw">Reportes de radicados</h1> ${grandTotal > 0 ? `<span class="reportes__total svelte-5nt2bb">${escape(grandTotal.toLocaleString("es-CO"))} radicado${escape(grandTotal !== 1 ? "s" : "")} en el período</span>` : ``}</div>  <button type="button" class="csv-download-btn svelte-5nt2bb" ${""}${add_attribute(
      "aria-label",
      "Descargar reporte en formato CSV",
      0
    )}><i class="fas fa-file-csv" aria-hidden="true"></i> ${escape("Descargar CSV")}</button></header>  <section class="reportes__filters svelte-5nt2bb" aria-label="Filtros de rango de fechas"><form class="filters-form svelte-5nt2bb" aria-label="Filtrar reportes por período"><div class="filters-form__row svelte-5nt2bb"><div class="filters-form__fields svelte-5nt2bb">${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Fecha desde",
        type: "date",
        id: "report-date-from",
        value: dateFrom
      },
      {
        value: ($$value) => {
          dateFrom = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Fecha hasta",
        type: "date",
        id: "report-date-to",
        value: dateTo
      },
      {
        value: ($$value) => {
          dateTo = $$value;
          $$settled = false;
        }
      },
      {}
    )}</div> <div class="filters-form__actions svelte-5nt2bb">${validate_component(Button, "Button").$$render(
      $$result,
      {
        type: "submit",
        variant: "primary",
        size: "sm",
        disabled: loading
      },
      {},
      {
        default: () => {
          return `<i class="fas fa-filter" aria-hidden="true"></i>
            Aplicar`;
        }
      }
    )} ${hasFilters ? `${validate_component(Button, "Button").$$render(
      $$result,
      {
        type: "button",
        variant: "ghost",
        size: "sm"
      },
      {},
      {
        default: () => {
          return `<i class="fas fa-times" aria-hidden="true"></i>
              Limpiar`;
        }
      }
    )}` : ``}</div></div> ${``}</form></section>   <p class="sr-only" aria-live="polite" aria-atomic="true">${`${data.loadError ? `No se pudo cargar el reporte.` : `Reporte actualizado: ${escape(grandTotal.toLocaleString("es-CO"))} radicado${escape(grandTotal !== 1 ? "s" : "")} en el período.`}`}</p> <div class="reportes__content"${add_attribute("aria-busy", loading, 0)}>${`${data.loadError ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-exclamation-circle",
        title: "Error al cargar el reporte",
        description: data.loadError
      },
      {},
      {
        default: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "primary" }, {}, {
            default: () => {
              return `Reintentar`;
            }
          })}`;
        }
      }
    )}` : `${!data.summary ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-chart-bar",
        title: "Sin datos",
        description: "No hay datos de radicados para mostrar en el período seleccionado."
      },
      {},
      {}
    )}` : `<div class="reportes__panels svelte-5nt2bb"> <section class="report-panel svelte-5nt2bb" aria-labelledby="panel-tipo-heading"><h2 id="panel-tipo-heading" class="report-panel__title svelte-5nt2bb" data-svelte-h="svelte-195uw1r">Por tipo</h2> ${validate_component(DataTable, "DataTable").$$render(
      $$result,
      {
        tableId: "reportes-tipo",
        columns: typeColumns,
        rows: typeRows,
        totalCount: typeRows.length,
        pageSize: Math.max(typeRows.length, 1),
        enableSort: true,
        enableExport: true,
        exportFilename: "reportes-por-tipo.csv",
        caption: "Radicados por tipo"
      },
      {},
      {
        row: ({ row }) => {
          return `<td class="op-datatable__td"><span class="${"doc-type-badge doc-type-badge--" + escape((row.doc_type ?? "").toLowerCase(), true) + " svelte-5nt2bb"}">${escape(row.type_label)}</span></td> <td class="op-datatable__td report-cell--num svelte-5nt2bb">${escape(row.count.toLocaleString("es-CO"))}</td> <td class="op-datatable__td"><span class="pct-bar svelte-5nt2bb" aria-hidden="true"><span class="pct-bar__fill svelte-5nt2bb" style="${"width: " + escape(row.pct, true) + "%"}"></span></span> <span class="pct-text svelte-5nt2bb">${escape(row.pct_label)}</span></td>`;
        }
      }
    )} ${typeRows.length > 0 ? `<p class="report-panel__total svelte-5nt2bb">Total: <strong>${escape(grandTotal.toLocaleString("es-CO"))}</strong> (100%)</p>` : ``}</section>  <section class="report-panel svelte-5nt2bb" aria-labelledby="panel-estado-heading"><h2 id="panel-estado-heading" class="report-panel__title svelte-5nt2bb" data-svelte-h="svelte-1ialvw3">Por estado</h2> ${validate_component(DataTable, "DataTable").$$render(
      $$result,
      {
        tableId: "reportes-estado",
        columns: statusColumns,
        rows: statusRows,
        totalCount: statusRows.length,
        pageSize: Math.max(statusRows.length, 1),
        enableSort: true,
        enableExport: true,
        exportFilename: "reportes-por-estado.csv",
        caption: "Radicados por estado"
      },
      {},
      {
        row: ({ row }) => {
          return `<td class="op-datatable__td">${escape(row.status_label)}</td> <td class="op-datatable__td report-cell--num svelte-5nt2bb">${escape(row.count.toLocaleString("es-CO"))}</td> <td class="op-datatable__td"><span class="pct-bar svelte-5nt2bb" aria-hidden="true"><span class="pct-bar__fill svelte-5nt2bb" style="${"width: " + escape(row.pct, true) + "%"}"></span></span> <span class="pct-text svelte-5nt2bb">${escape(row.pct_label)}</span></td>`;
        }
      }
    )}</section>  <section class="report-panel report-panel--wide svelte-5nt2bb" aria-labelledby="panel-mes-heading"><h2 id="panel-mes-heading" class="report-panel__title svelte-5nt2bb" data-svelte-h="svelte-5r5eg1">Actividad mensual</h2> ${validate_component(DataTable, "DataTable").$$render(
      $$result,
      {
        tableId: "reportes-mes",
        columns: monthColumns,
        rows: monthRows,
        totalCount: monthRows.length,
        pageSize: Math.max(monthRows.length, 1),
        enableSort: true,
        enableFilters: true,
        enableExport: true,
        exportFilename: "reportes-actividad-mensual.csv",
        caption: "Radicados por mes"
      },
      {},
      {
        row: ({ row }) => {
          return `<td class="op-datatable__td"><time${add_attribute("datetime", row.month, 0)}>${escape(row.month_label)}</time></td> <td class="op-datatable__td report-cell--num svelte-5nt2bb">${escape(row.count.toLocaleString("es-CO"))}</td> <td class="op-datatable__td"><span class="bar-visual svelte-5nt2bb" aria-hidden="true"><span class="bar-visual__fill svelte-5nt2bb" style="${"width: " + escape(barWidth(row.count, maxMonthCount), true)}"></span></span> <span class="pct-text svelte-5nt2bb">${escape(row.share_label)}</span></td>`;
        }
      }
    )}</section>  <section class="report-panel report-panel--wide svelte-5nt2bb" aria-labelledby="panel-dept-heading"><h2 id="panel-dept-heading" class="report-panel__title svelte-5nt2bb" data-svelte-h="svelte-r2vu60">Por dependencia destino</h2> ${validate_component(DataTable, "DataTable").$$render(
      $$result,
      {
        tableId: "reportes-dependencia",
        columns: deptColumns,
        rows: deptRows,
        totalCount: deptRows.length,
        pageSize: Math.max(deptRows.length, 1),
        enableSort: true,
        enableFilters: true,
        enableExport: true,
        virtualize: true,
        exportFilename: "reportes-por-dependencia.csv",
        caption: "Radicados por dependencia destino"
      },
      {},
      {
        row: ({ row }) => {
          return `<td class="op-datatable__td">${escape(row.dept_label)}</td> <td class="op-datatable__td report-cell--num svelte-5nt2bb">${escape(row.count.toLocaleString("es-CO"))}</td> <td class="op-datatable__td"><span class="bar-visual svelte-5nt2bb" aria-hidden="true"><span class="bar-visual__fill svelte-5nt2bb" style="${"width: " + escape(barWidth(row.count, maxDeptCount), true)}"></span></span> <span class="pct-text svelte-5nt2bb">${escape(row.share_label)}</span></td>`;
        }
      }
    )}</section></div>`}`}`}</div> </div>`;
  } while (!$$settled);
  return $$rendered;
});
export {
  Page as default
};
