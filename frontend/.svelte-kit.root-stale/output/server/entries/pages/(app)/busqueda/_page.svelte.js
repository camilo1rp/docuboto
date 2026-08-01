import { c as create_ssr_component, e as escape, b as add_attribute, v as validate_component } from "../../../../chunks/ssr.js";
import "../../../../chunks/client.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
import { B as Button } from "../../../../chunks/Button.js";
import { F as FormField } from "../../../../chunks/FormField.js";
import { D as DataTable } from "../../../../chunks/DataTable.js";
const css = {
  code: ".busqueda.svelte-1bkjfyd{padding:var(--op-space-6);max-width:1200px;margin:0 auto;display:flex;flex-direction:column;gap:var(--op-space-5)}.busqueda__header.svelte-1bkjfyd{display:flex;align-items:baseline;gap:var(--op-space-4);flex-wrap:wrap;padding-bottom:var(--op-space-4);border-bottom:1px solid var(--op-border)}.busqueda__title.svelte-1bkjfyd{font-size:var(--op-font-xl);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}.busqueda__total.svelte-1bkjfyd{font-size:var(--op-font-sm);color:var(--op-text-secondary);flex-shrink:0}.busqueda__search-panel.svelte-1bkjfyd{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);padding:var(--op-space-5)}.search-form.svelte-1bkjfyd{display:flex;flex-direction:column;gap:var(--op-space-4)}.search-form__main.svelte-1bkjfyd{display:flex;flex-direction:column;gap:var(--op-space-2)}.search-form__label.svelte-1bkjfyd{font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);color:var(--op-text)}.search-form__input-row.svelte-1bkjfyd{display:flex;gap:var(--op-space-3);align-items:stretch}.search-form__input.svelte-1bkjfyd{flex:1;height:40px;padding:0 var(--op-space-3);border:1px solid var(--op-border);border-radius:var(--op-radius);font-size:var(--op-font-sm);color:var(--op-text);background:var(--op-bg);transition:border-color var(--op-transition)}.search-form__input.svelte-1bkjfyd::placeholder{color:var(--op-text-secondary)}.search-form__input.svelte-1bkjfyd:focus{outline:none;border-color:var(--op-primary);box-shadow:0 0 0 3px var(--op-focus-ring)}.search-form__input--error.svelte-1bkjfyd{border-color:var(--op-error)}.search-form__field-error.svelte-1bkjfyd{font-size:var(--op-font-xs);color:var(--op-error);margin:0}.search-form__date-range-error.svelte-1bkjfyd{display:flex;align-items:center;gap:var(--op-space-2);font-size:var(--op-font-xs);color:var(--op-error);margin:var(--op-space-2) 0 0}.search-form__advanced-toggle.svelte-1bkjfyd{display:flex;align-items:center}.advanced-toggle-btn.svelte-1bkjfyd{display:inline-flex;align-items:center;gap:var(--op-space-2);font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);color:var(--op-primary-dark);background:none;border:none;cursor:pointer;padding:var(--op-space-1) 0;text-decoration:none;transition:color var(--op-transition)}.advanced-toggle-btn.svelte-1bkjfyd:hover{color:var(--op-primary)}.advanced-toggle-btn.svelte-1bkjfyd:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px;border-radius:var(--op-radius-sm)}.advanced-toggle-btn__badge.svelte-1bkjfyd{font-size:8px;color:var(--op-accent)}.search-form__filters.svelte-1bkjfyd{border:1px solid var(--op-border);border-radius:var(--op-radius);padding:var(--op-space-4);margin:0}.search-form__filters-legend.svelte-1bkjfyd{font-size:var(--op-font-sm);font-weight:var(--op-weight-bold);color:var(--op-text-secondary);padding:0 var(--op-space-2)}.search-form__filters-grid.svelte-1bkjfyd{display:grid;grid-template-columns:repeat(auto-fill, minmax(200px, 1fr));gap:var(--op-space-3)}.search-form__filters-actions.svelte-1bkjfyd{margin-top:var(--op-space-3);display:flex;justify-content:flex-end}.busqueda__results.svelte-1bkjfyd{min-height:200px}.busqueda__table-wrapper.svelte-1bkjfyd{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);overflow:hidden}.search-table__td--subject.svelte-1bkjfyd{max-width:280px}.search-table__td--dep.svelte-1bkjfyd{max-width:160px}.search-table__td--date.svelte-1bkjfyd{white-space:nowrap;color:var(--op-text-secondary)}.search-table__td--actions.svelte-1bkjfyd{text-align:right;white-space:nowrap}.tracking-number.svelte-1bkjfyd{font-family:monospace;font-size:var(--op-font-xs);font-weight:var(--op-weight-medium);color:var(--op-primary-dark);background:var(--op-primary-light);padding:2px var(--op-space-2);border-radius:var(--op-radius-sm);white-space:nowrap}.doc-type-badge.svelte-1bkjfyd{display:inline-block;font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);padding:2px var(--op-space-2);border-radius:var(--op-radius-sm);white-space:nowrap}.doc-type-badge--e.svelte-1bkjfyd{background:var(--op-info-bg);color:var(--op-info-fg)}.doc-type-badge--s.svelte-1bkjfyd{background:var(--op-warning-bg);color:var(--op-warning-fg)}.doc-type-badge--i.svelte-1bkjfyd{background:var(--op-bg);color:var(--op-text-secondary);border:1px solid var(--op-border)}.subject-text.svelte-1bkjfyd{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.action-link.svelte-1bkjfyd{display:inline-flex;align-items:center;gap:var(--op-space-1);font-size:var(--op-font-xs);font-weight:var(--op-weight-medium);color:var(--op-primary-dark);text-decoration:none;padding:3px var(--op-space-2);border-radius:var(--op-radius-sm);border:1px solid var(--op-primary-light);background:var(--op-primary-light);transition:background var(--op-transition), color var(--op-transition)}.action-link.svelte-1bkjfyd:hover{background:var(--op-primary);color:var(--op-surface);border-color:var(--op-primary)}.action-link.svelte-1bkjfyd:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}@media(max-width: 768px){.busqueda.svelte-1bkjfyd{padding:var(--op-space-4)}.search-form__input-row.svelte-1bkjfyd{flex-direction:column}}",
  map: '{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /busqueda — Búsqueda avanzada de radicados (T-11, RF-UI-03).\\n\\n  Resuelve el enlace muerto a /busqueda que existe en bandeja/+page.svelte.\\n\\n  Filtros soportados por /api/v1/search:\\n    - q (texto libre, ≥2 chars, requerido)\\n    - doc_type: E | S | I\\n    - status: registered | distributed | in_progress | closed\\n    - date_from, date_to (ISO date)\\n    - dest_dept_code (entero)\\n    - page, size\\n\\n  PATRÓN elegido (coherente con bandeja):\\n    - SSR inicial vía +page.server.js (deep-link/bookmarkable).\\n    - Nueva búsqueda: goto() actualiza la URL y re-ejecuta el server load sin\\n      recarga completa del navegador (SvelteKit navigation).\\n    - Paginación: usa /busqueda/api (proxy SSR, como bandeja usa /bandeja/api)\\n      para navegar entre páginas sin perder el estado del formulario.\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { browser } from \\"$app/environment\\";\\n  import { goto } from \\"$app/navigation\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { notifyError } from \\"$lib/stores/notifications.js\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import DataTable from \\"$lib/components/ui/DataTable.svelte\\";\\n\\n  /** @type {import(\'./$types\').PageData} */\\n  export let data;\\n\\n  // ---------------------------------------------------------------------------\\n  // Estado del formulario (inicializado desde la URL vía server load)\\n  // ---------------------------------------------------------------------------\\n  let q = data.initialFilters?.q ?? \\"\\";\\n  let filterDocType = data.initialFilters?.docType ?? \\"\\";\\n  let filterStatus = data.initialFilters?.status ?? \\"\\";\\n  let filterDateFrom = data.initialFilters?.dateFrom ?? \\"\\";\\n  let filterDateTo = data.initialFilters?.dateTo ?? \\"\\";\\n  let filterDestDept = data.initialFilters?.destDeptCode ?? \\"\\";\\n  let currentPage = data.initialFilters?.page ?? 1;\\n  const pageSize = 20;\\n\\n  let showAdvanced = !!(\\n    filterDocType ||\\n    filterStatus ||\\n    filterDateFrom ||\\n    filterDateTo ||\\n    filterDestDept\\n  );\\n\\n  // ---------------------------------------------------------------------------\\n  // Estado de resultados\\n  // ---------------------------------------------------------------------------\\n\\n  /** @type {any[]} */\\n  let results = data.results ?? [];\\n  let total = data.total ?? 0;\\n  let loading = false;\\n  let qError = \\"\\";\\n  let dateRangeError = \\"\\";\\n\\n  // ---------------------------------------------------------------------------\\n  // Breadcrumbs\\n  // ---------------------------------------------------------------------------\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Búsqueda avanzada\\" }]);\\n\\n    if (!browser) return;\\n    const mql = window.matchMedia(\\"(max-width: 768px)\\");\\n    const syncViewport = () => {\\n      isNarrowViewport = mql.matches;\\n    };\\n    syncViewport();\\n    mql.addEventListener(\\"change\\", syncViewport);\\n    return () => mql.removeEventListener(\\"change\\", syncViewport);\\n  });\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de presentación\\n  // ---------------------------------------------------------------------------\\n  const DOC_TYPE_LABELS = { E: \\"Entrada\\", S: \\"Salida\\", I: \\"Interno\\" };\\n  const STATUS_LABELS = {\\n    registered: \\"Radicado\\",\\n    distributed: \\"Distribuido\\",\\n    in_progress: \\"En trámite\\",\\n    closed: \\"Cerrado\\",\\n  };\\n  const STATUS_CHIP_MAP = {\\n    registered: \\"info\\",\\n    distributed: \\"pending\\",\\n    in_progress: \\"active\\",\\n    closed: \\"closed\\",\\n  };\\n\\n  // Columnas de DataTable — el renderizado real de cada celda lo controla el\\n  // slot=\\"row\\" de abajo (radicado en monoespaciada, badge de tipo, StatusChip,\\n  // enlace de acción), así que `label` es lo único que consume el <thead>\\n  // automático de DataTable.\\n  const TABLE_COLUMNS = [\\n    { key: \\"tracking_number\\", label: \\"Radicado\\", width: \\"160px\\" },\\n    { key: \\"doc_type\\", label: \\"Tipo\\", width: \\"90px\\" },\\n    { key: \\"subject\\", label: \\"Asunto\\" },\\n    { key: \\"dest_dept\\", label: \\"Dependencia destino\\", width: \\"180px\\" },\\n    { key: \\"registered_at\\", label: \\"Fecha\\", width: \\"110px\\" },\\n    { key: \\"status\\", label: \\"Estado\\", width: \\"140px\\" },\\n    { key: \\"actions\\", label: \\"Acciones\\", width: \\"150px\\" },\\n  ];\\n\\n  // La columna \\"Dependencia destino\\" se oculta en viewports estrechos — se\\n  // calcula en la FUENTE (no solo por CSS) para que el <th> generado por\\n  // DataTable y el <td> de la fila desaparezcan juntos y no se rompa la\\n  // asociación encabezado↔dato para lectores de pantalla (mismo breakpoint\\n  // que el resto del layout responsive de esta pantalla, 768px).\\n  let isNarrowViewport = false;\\n  $: tableColumns = isNarrowViewport\\n    ? TABLE_COLUMNS.filter((col) => col.key !== \\"dest_dept\\")\\n    : TABLE_COLUMNS;\\n\\n  /**\\n   * Formatea una fecha ISO 8601 como fecha legible en español.\\n   * @param {string} iso\\n   */\\n  function formatDate(iso) {\\n    if (!iso) return \\"—\\";\\n    return new Date(iso).toLocaleDateString(\\"es-CO\\", {\\n      year: \\"numeric\\",\\n      month: \\"2-digit\\",\\n      day: \\"2-digit\\",\\n    });\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Búsqueda — usa goto() para actualizar la URL (bookmarkable) y re-ejecutar SSR\\n  // ---------------------------------------------------------------------------\\n\\n  /**\\n   * Construye los query params a partir del estado actual del formulario.\\n   */\\n  function buildSearchParams(page = 1) {\\n    const params = new URLSearchParams();\\n    if (q.trim()) params.set(\\"q\\", q.trim());\\n    if (filterDocType) params.set(\\"doc_type\\", filterDocType);\\n    if (filterStatus) params.set(\\"status\\", filterStatus);\\n    if (filterDateFrom) params.set(\\"date_from\\", filterDateFrom);\\n    if (filterDateTo) params.set(\\"date_to\\", filterDateTo);\\n    // dest_dept_code: solo entero positivo para evitar 422 en el gateway\\n    const deptCode = parseInt(filterDestDept, 10);\\n    if (Number.isFinite(deptCode) && deptCode > 0) params.set(\\"dest_dept_code\\", String(deptCode));\\n    if (page > 1) params.set(\\"page\\", String(page));\\n    return params;\\n  }\\n\\n  /**\\n   * Valida y lanza una nueva búsqueda actualizando la URL (deep-link).\\n   * goto() ejecuta SvelteKit client-side navigation → re-run del server load.\\n   */\\n  async function search() {\\n    qError = \\"\\";\\n    dateRangeError = \\"\\";\\n    if (!q.trim() || q.trim().length < 2) {\\n      qError = \\"Introduce al menos 2 caracteres para buscar.\\";\\n      return;\\n    }\\n    if (filterDateFrom && filterDateTo && filterDateFrom > filterDateTo) {\\n      dateRangeError = \\"La fecha inicio no puede ser posterior a la fecha fin.\\";\\n      return;\\n    }\\n    currentPage = 1;\\n    await goto(`/busqueda?${buildSearchParams(1).toString()}`, { keepFocus: true });\\n  }\\n\\n  function clearFilters() {\\n    filterDocType = \\"\\";\\n    filterStatus = \\"\\";\\n    filterDateFrom = \\"\\";\\n    filterDateTo = \\"\\";\\n    filterDestDept = \\"\\";\\n    dateRangeError = \\"\\";\\n    currentPage = 1;\\n    if (q.trim().length >= 2) {\\n      goto(`/busqueda?${buildSearchParams(1).toString()}`, { keepFocus: true });\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Paginación — usa el endpoint proxy local /busqueda/api (sin recarga de UI)\\n  // para navegar entre páginas sin perder el estado del formulario (como bandeja).\\n  // ---------------------------------------------------------------------------\\n\\n  $: totalPages = Math.ceil(total / pageSize);\\n\\n  async function goToPage(page) {\\n    if (page < 1 || page > totalPages) return;\\n    currentPage = page;\\n    await loadPage(page);\\n  }\\n\\n  /**\\n   * Carga una página de resultados vía el endpoint proxy SSR.\\n   * El token nunca alcanza el JS del cliente (D-05).\\n   * @param {number} page\\n   */\\n  async function loadPage(page) {\\n    loading = true;\\n    try {\\n      const params = buildSearchParams(page);\\n      const res = await fetch(`/busqueda/api?${params.toString()}`);\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      results = result.results;\\n      total = result.total;\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"Error al cargar los resultados.\\");\\n    } finally {\\n      loading = false;\\n    }\\n  }\\n\\n  $: hasActiveFilters = !!(\\n    filterDocType ||\\n    filterStatus ||\\n    filterDateFrom ||\\n    filterDateTo ||\\n    filterDestDept\\n  );\\n  $: hasQuery = q.trim().length >= 2;\\n  $: searched = !!data.initialFilters?.q;\\n<\/script>\\n\\n<svelte:head>\\n  <title>Búsqueda avanzada — OrpycaMCP</title>\\n</svelte:head>\\n\\n<div class=\\"busqueda\\">\\n  <!-- -------------------------------------------------------------------------\\n    Encabezado\\n  ------------------------------------------------------------------------- -->\\n  <header class=\\"busqueda__header\\">\\n    <h1 class=\\"busqueda__title\\">Búsqueda avanzada</h1>\\n    {#if searched && !loading}\\n      <span class=\\"busqueda__total\\" aria-live=\\"polite\\">\\n        {total.toLocaleString(\\"es-CO\\")} resultado{total !== 1 ? \\"s\\" : \\"\\"}\\n      </span>\\n    {/if}\\n  </header>\\n\\n  <!-- -------------------------------------------------------------------------\\n    Formulario de búsqueda\\n  ------------------------------------------------------------------------- -->\\n  <section class=\\"busqueda__search-panel\\" aria-label=\\"Formulario de búsqueda\\">\\n    <form\\n      class=\\"search-form\\"\\n      on:submit|preventDefault={search}\\n      role=\\"search\\"\\n      aria-label=\\"Buscar radicados\\"\\n    >\\n      <!-- Caja de búsqueda principal -->\\n      <div class=\\"search-form__main\\">\\n        <label for=\\"search-q\\" class=\\"search-form__label\\">Texto de búsqueda</label>\\n        <div class=\\"search-form__input-row\\">\\n          <input\\n            id=\\"search-q\\"\\n            type=\\"search\\"\\n            class=\\"search-form__input\\"\\n            class:search-form__input--error={!!qError}\\n            bind:value={q}\\n            placeholder=\\"Asunto, número de radicado, remitente…\\"\\n            autocomplete=\\"off\\"\\n            minlength=\\"2\\"\\n            aria-describedby={qError ? \\"search-q-error\\" : undefined}\\n            aria-required=\\"true\\"\\n          />\\n          <Button type=\\"submit\\" variant=\\"primary\\" disabled={loading} aria-label=\\"Buscar\\">\\n            <i class=\\"fas fa-search\\" aria-hidden=\\"true\\"></i>\\n            <span>Buscar</span>\\n          </Button>\\n        </div>\\n        {#if qError}\\n          <p id=\\"search-q-error\\" class=\\"search-form__field-error\\" role=\\"alert\\">{qError}</p>\\n        {/if}\\n      </div>\\n\\n      <!-- Toggle filtros avanzados -->\\n      <div class=\\"search-form__advanced-toggle\\">\\n        <button\\n          type=\\"button\\"\\n          class=\\"advanced-toggle-btn\\"\\n          aria-expanded={showAdvanced}\\n          aria-controls=\\"advanced-filters\\"\\n          on:click={() => (showAdvanced = !showAdvanced)}\\n        >\\n          <i class=\\"fas fa-sliders-h\\" aria-hidden=\\"true\\"></i>\\n          {showAdvanced ? \\"Ocultar filtros\\" : \\"Filtros avanzados\\"}\\n          {#if hasActiveFilters}\\n            <span class=\\"advanced-toggle-btn__badge\\" aria-label=\\"Hay filtros activos\\">\\n              <i class=\\"fas fa-circle\\" aria-hidden=\\"true\\"></i>\\n            </span>\\n          {/if}\\n        </button>\\n      </div>\\n\\n      <!-- Filtros avanzados (colapsables) -->\\n      {#if showAdvanced}\\n        <fieldset id=\\"advanced-filters\\" class=\\"search-form__filters\\">\\n          <legend class=\\"search-form__filters-legend\\">Filtros avanzados</legend>\\n          <div class=\\"search-form__filters-grid\\">\\n            <FormField\\n              label=\\"Tipo de radicado\\"\\n              type=\\"select\\"\\n              id=\\"filter-doc-type\\"\\n              bind:value={filterDocType}\\n              options={[\\n                { value: \\"\\", label: \\"Todos los tipos\\" },\\n                { value: \\"E\\", label: \\"Entrada\\" },\\n                { value: \\"S\\", label: \\"Salida\\" },\\n                { value: \\"I\\", label: \\"Interno\\" },\\n              ]}\\n            />\\n            <FormField\\n              label=\\"Estado\\"\\n              type=\\"select\\"\\n              id=\\"filter-status\\"\\n              bind:value={filterStatus}\\n              options={[\\n                { value: \\"\\", label: \\"Todos los estados\\" },\\n                { value: \\"registered\\", label: \\"Radicado\\" },\\n                { value: \\"distributed\\", label: \\"Distribuido\\" },\\n                { value: \\"in_progress\\", label: \\"En trámite\\" },\\n                { value: \\"closed\\", label: \\"Cerrado\\" },\\n              ]}\\n            />\\n            <FormField\\n              label=\\"Fecha desde\\"\\n              type=\\"date\\"\\n              id=\\"filter-date-from\\"\\n              bind:value={filterDateFrom}\\n            />\\n            <FormField\\n              label=\\"Fecha hasta\\"\\n              type=\\"date\\"\\n              id=\\"filter-date-to\\"\\n              bind:value={filterDateTo}\\n            />\\n            <FormField\\n              label=\\"Código dependencia destino\\"\\n              type=\\"number\\"\\n              id=\\"filter-dest-dept\\"\\n              bind:value={filterDestDept}\\n              placeholder=\\"Ej. 12\\"\\n              hint=\\"Código numérico entero de la dependencia.\\"\\n            />\\n          </div>\\n\\n          <!-- Error de rango de fechas (accesible con role=alert) -->\\n          {#if dateRangeError}\\n            <p class=\\"search-form__date-range-error\\" role=\\"alert\\" id=\\"date-range-error\\">\\n              <i class=\\"fas fa-exclamation-circle\\" aria-hidden=\\"true\\"></i>\\n              {dateRangeError}\\n            </p>\\n          {/if}\\n\\n          {#if hasActiveFilters}\\n            <div class=\\"search-form__filters-actions\\">\\n              <Button type=\\"button\\" variant=\\"ghost\\" size=\\"sm\\" on:click={clearFilters}>\\n                <i class=\\"fas fa-times\\" aria-hidden=\\"true\\"></i>\\n                Limpiar filtros\\n              </Button>\\n            </div>\\n          {/if}\\n        </fieldset>\\n      {/if}\\n    </form>\\n  </section>\\n\\n  <!-- -------------------------------------------------------------------------\\n    Resultados\\n    El aria-live está en un nodo pequeño y estático para no reanunciar la tabla\\n    completa en cada paginación — solo anuncia el conteo de resultados (Fix #6).\\n  ------------------------------------------------------------------------- -->\\n  <p class=\\"sr-only\\" aria-live=\\"polite\\" aria-atomic=\\"true\\">\\n    {#if loading}Buscando radicados…{:else if searched}{total.toLocaleString(\\"es-CO\\")} resultado{total !==\\n      1\\n        ? \\"s\\"\\n        : \\"\\"} encontrado{total !== 1 ? \\"s\\" : \\"\\"}{/if}\\n  </p>\\n  <section class=\\"busqueda__results\\" aria-label=\\"Resultados de búsqueda\\" aria-busy={loading}>\\n    {#if data.loadError}\\n      <EmptyState\\n        icon=\\"fas fa-exclamation-circle\\"\\n        title=\\"Error en la búsqueda\\"\\n        description={data.loadError}\\n      >\\n        <Button variant=\\"primary\\" on:click={search} disabled={!hasQuery}>Reintentar</Button>\\n      </EmptyState>\\n    {:else if !searched}\\n      <EmptyState\\n        icon=\\"fas fa-search\\"\\n        title=\\"Realiza una búsqueda\\"\\n        description=\\"Introduce al menos 2 caracteres en el campo de búsqueda y pulsa Buscar.\\"\\n      />\\n    {:else if !loading && results.length === 0}\\n      <EmptyState\\n        icon=\\"fas fa-file-search\\"\\n        title=\\"Sin resultados\\"\\n        description=\\"No se encontraron radicados que coincidan con los criterios de búsqueda.\\"\\n      >\\n        {#if hasActiveFilters}\\n          <Button variant=\\"ghost\\" on:click={clearFilters}>Limpiar filtros</Button>\\n        {/if}\\n      </EmptyState>\\n    {:else}\\n      <!-- Tabla de resultados -->\\n      <div class=\\"busqueda__table-wrapper\\">\\n        <DataTable\\n          columns={tableColumns}\\n          rows={results}\\n          totalCount={total}\\n          page={currentPage}\\n          {pageSize}\\n          {loading}\\n          caption=\\"Resultados de búsqueda de radicados{data.initialFilters?.q\\n            ? ` para \\"${data.initialFilters.q}\\"`\\n            : \'\'}\\"\\n          on:pageChange={(e) => goToPage(e.detail.page)}\\n        >\\n          <svelte:fragment slot=\\"row\\" let:row>\\n            <td class=\\"op-datatable__td\\">\\n              <span class=\\"tracking-number\\">{row.tracking_number}</span>\\n            </td>\\n            <td class=\\"op-datatable__td\\">\\n              <span class=\\"doc-type-badge doc-type-badge--{row.doc_type?.toLowerCase()}\\">\\n                {DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type ?? \\"—\\"}\\n              </span>\\n            </td>\\n            <td class=\\"op-datatable__td search-table__td--subject\\">\\n              <span class=\\"subject-text\\" title={row.subject}>{row.subject ?? \\"—\\"}</span>\\n            </td>\\n            {#if !isNarrowViewport}\\n              <td class=\\"op-datatable__td search-table__td--dep\\">\\n                {row.dest_dept ?? \\"—\\"}\\n              </td>\\n            {/if}\\n            <td class=\\"op-datatable__td search-table__td--date\\">\\n              <time datetime={row.registered_at}>{formatDate(row.registered_at)}</time>\\n            </td>\\n            <td class=\\"op-datatable__td\\">\\n              <StatusChip\\n                status={STATUS_CHIP_MAP[row.status] ?? \\"neutral\\"}\\n                label={STATUS_LABELS[row.status] ?? row.status ?? \\"—\\"}\\n                dot={true}\\n              />\\n            </td>\\n            <td class=\\"op-datatable__td search-table__td--actions\\">\\n              <a\\n                href=\\"/bandeja\\"\\n                class=\\"action-link\\"\\n                aria-label=\\"Ver radicado {row.tracking_number} en la bandeja\\"\\n                title=\\"Ver en bandeja\\"\\n              >\\n                <i class=\\"fas fa-external-link-alt\\" aria-hidden=\\"true\\"></i>\\n                Ver en bandeja\\n              </a>\\n            </td>\\n          </svelte:fragment>\\n        </DataTable>\\n      </div>\\n    {/if}\\n  </section>\\n</div>\\n\\n<style lang=\\"scss\\">.busqueda {\\n  padding: var(--op-space-6);\\n  max-width: 1200px;\\n  margin: 0 auto;\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-5);\\n}\\n\\n.busqueda__header {\\n  display: flex;\\n  align-items: baseline;\\n  gap: var(--op-space-4);\\n  flex-wrap: wrap;\\n  padding-bottom: var(--op-space-4);\\n  border-bottom: 1px solid var(--op-border);\\n}\\n\\n.busqueda__title {\\n  font-size: var(--op-font-xl);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n\\n.busqueda__total {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n  flex-shrink: 0;\\n}\\n\\n.busqueda__search-panel {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  padding: var(--op-space-5);\\n}\\n\\n.search-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}\\n\\n.search-form__main {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-2);\\n}\\n\\n.search-form__label {\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text);\\n}\\n\\n.search-form__input-row {\\n  display: flex;\\n  gap: var(--op-space-3);\\n  align-items: stretch;\\n}\\n\\n.search-form__input {\\n  flex: 1;\\n  height: 40px;\\n  padding: 0 var(--op-space-3);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  background: var(--op-bg);\\n  transition: border-color var(--op-transition);\\n}\\n.search-form__input::placeholder {\\n  color: var(--op-text-secondary);\\n}\\n.search-form__input:focus {\\n  outline: none;\\n  border-color: var(--op-primary);\\n  box-shadow: 0 0 0 3px var(--op-focus-ring);\\n}\\n.search-form__input--error {\\n  border-color: var(--op-error);\\n}\\n\\n.search-form__field-error {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-error);\\n  margin: 0;\\n}\\n\\n.search-form__date-range-error {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  font-size: var(--op-font-xs);\\n  color: var(--op-error);\\n  margin: var(--op-space-2) 0 0;\\n}\\n\\n.search-form__advanced-toggle {\\n  display: flex;\\n  align-items: center;\\n}\\n\\n.advanced-toggle-btn {\\n  display: inline-flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n  background: none;\\n  border: none;\\n  cursor: pointer;\\n  padding: var(--op-space-1) 0;\\n  text-decoration: none;\\n  transition: color var(--op-transition);\\n}\\n.advanced-toggle-btn:hover {\\n  color: var(--op-primary);\\n}\\n.advanced-toggle-btn:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n  border-radius: var(--op-radius-sm);\\n}\\n.advanced-toggle-btn__badge {\\n  font-size: 8px;\\n  color: var(--op-accent);\\n}\\n\\n.search-form__filters {\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n  padding: var(--op-space-4);\\n  margin: 0;\\n}\\n\\n.search-form__filters-legend {\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text-secondary);\\n  padding: 0 var(--op-space-2);\\n}\\n\\n.search-form__filters-grid {\\n  display: grid;\\n  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));\\n  gap: var(--op-space-3);\\n}\\n\\n.search-form__filters-actions {\\n  margin-top: var(--op-space-3);\\n  display: flex;\\n  justify-content: flex-end;\\n}\\n\\n.busqueda__results {\\n  min-height: 200px;\\n}\\n\\n.busqueda__table-wrapper {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  overflow: hidden;\\n}\\n\\n.search-table__td--subject {\\n  max-width: 280px;\\n}\\n.search-table__td--dep {\\n  max-width: 160px;\\n}\\n.search-table__td--date {\\n  white-space: nowrap;\\n  color: var(--op-text-secondary);\\n}\\n.search-table__td--actions {\\n  text-align: right;\\n  white-space: nowrap;\\n}\\n\\n.tracking-number {\\n  font-family: monospace;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n  background: var(--op-primary-light);\\n  padding: 2px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  white-space: nowrap;\\n}\\n\\n.doc-type-badge {\\n  display: inline-block;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  padding: 2px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  white-space: nowrap;\\n}\\n.doc-type-badge--e {\\n  background: var(--op-info-bg);\\n  color: var(--op-info-fg);\\n}\\n.doc-type-badge--s {\\n  background: var(--op-warning-bg);\\n  color: var(--op-warning-fg);\\n}\\n.doc-type-badge--i {\\n  background: var(--op-bg);\\n  color: var(--op-text-secondary);\\n  border: 1px solid var(--op-border);\\n}\\n\\n.subject-text {\\n  display: block;\\n  overflow: hidden;\\n  white-space: nowrap;\\n  text-overflow: ellipsis;\\n}\\n\\n.action-link {\\n  display: inline-flex;\\n  align-items: center;\\n  gap: var(--op-space-1);\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n  text-decoration: none;\\n  padding: 3px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  border: 1px solid var(--op-primary-light);\\n  background: var(--op-primary-light);\\n  transition: background var(--op-transition), color var(--op-transition);\\n}\\n.action-link:hover {\\n  background: var(--op-primary);\\n  color: var(--op-surface);\\n  border-color: var(--op-primary);\\n}\\n.action-link:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n\\n@media (max-width: 768px) {\\n  .busqueda {\\n    padding: var(--op-space-4);\\n  }\\n  .search-form__input-row {\\n    flex-direction: column;\\n  }\\n}</style>\\n"],"names":[],"mappings":"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"}'
};
const pageSize = 20;
function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("es-CO", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  });
}
const Page = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let tableColumns;
  let hasActiveFilters;
  let hasQuery;
  let searched;
  let { data } = $$props;
  let q = data.initialFilters?.q ?? "";
  let filterDocType = data.initialFilters?.docType ?? "";
  let filterStatus = data.initialFilters?.status ?? "";
  let filterDateFrom = data.initialFilters?.dateFrom ?? "";
  let filterDateTo = data.initialFilters?.dateTo ?? "";
  let filterDestDept = data.initialFilters?.destDeptCode ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  let showAdvanced = !!(filterDocType || filterStatus || filterDateFrom || filterDateTo || filterDestDept);
  let results = data.results ?? [];
  let total = data.total ?? 0;
  let loading = false;
  const DOC_TYPE_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };
  const STATUS_LABELS = {
    registered: "Radicado",
    distributed: "Distribuido",
    in_progress: "En trámite",
    closed: "Cerrado"
  };
  const STATUS_CHIP_MAP = {
    registered: "info",
    distributed: "pending",
    in_progress: "active",
    closed: "closed"
  };
  const TABLE_COLUMNS = [
    {
      key: "tracking_number",
      label: "Radicado",
      width: "160px"
    },
    {
      key: "doc_type",
      label: "Tipo",
      width: "90px"
    },
    { key: "subject", label: "Asunto" },
    {
      key: "dest_dept",
      label: "Dependencia destino",
      width: "180px"
    },
    {
      key: "registered_at",
      label: "Fecha",
      width: "110px"
    },
    {
      key: "status",
      label: "Estado",
      width: "140px"
    },
    {
      key: "actions",
      label: "Acciones",
      width: "150px"
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
    tableColumns = TABLE_COLUMNS;
    hasActiveFilters = !!(filterDocType || filterStatus || filterDateFrom || filterDateTo || filterDestDept);
    hasQuery = q.trim().length >= 2;
    searched = !!data.initialFilters?.q;
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-160na06_START -->${$$result.title = `<title>Búsqueda avanzada — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-160na06_END -->`, ""} <div class="busqueda svelte-1bkjfyd"> <header class="busqueda__header svelte-1bkjfyd"><h1 class="busqueda__title svelte-1bkjfyd" data-svelte-h="svelte-1vjma3a">Búsqueda avanzada</h1> ${searched && !loading ? `<span class="busqueda__total svelte-1bkjfyd" aria-live="polite">${escape(total.toLocaleString("es-CO"))} resultado${escape(total !== 1 ? "s" : "")}</span>` : ``}</header>  <section class="busqueda__search-panel svelte-1bkjfyd" aria-label="Formulario de búsqueda"><form class="search-form svelte-1bkjfyd" role="search" aria-label="Buscar radicados"> <div class="search-form__main svelte-1bkjfyd"><label for="search-q" class="search-form__label svelte-1bkjfyd" data-svelte-h="svelte-18qga06">Texto de búsqueda</label> <div class="search-form__input-row svelte-1bkjfyd"><input id="search-q" type="search" class="${[
      "search-form__input svelte-1bkjfyd",
      ""
    ].join(" ").trim()}" placeholder="Asunto, número de radicado, remitente…" autocomplete="off" minlength="2"${add_attribute("aria-describedby", void 0, 0)} aria-required="true"${add_attribute("value", q, 0)}> ${validate_component(Button, "Button").$$render(
      $$result,
      {
        type: "submit",
        variant: "primary",
        disabled: loading,
        "aria-label": "Buscar"
      },
      {},
      {
        default: () => {
          return `<i class="fas fa-search" aria-hidden="true"></i> <span data-svelte-h="svelte-1d6zpv6">Buscar</span>`;
        }
      }
    )}</div> ${``}</div>  <div class="search-form__advanced-toggle svelte-1bkjfyd"><button type="button" class="advanced-toggle-btn svelte-1bkjfyd"${add_attribute("aria-expanded", showAdvanced, 0)} aria-controls="advanced-filters"><i class="fas fa-sliders-h" aria-hidden="true"></i> ${escape(showAdvanced ? "Ocultar filtros" : "Filtros avanzados")} ${hasActiveFilters ? `<span class="advanced-toggle-btn__badge svelte-1bkjfyd" aria-label="Hay filtros activos" data-svelte-h="svelte-ou9iqg"><i class="fas fa-circle" aria-hidden="true"></i></span>` : ``}</button></div>  ${showAdvanced ? `<fieldset id="advanced-filters" class="search-form__filters svelte-1bkjfyd"><legend class="search-form__filters-legend svelte-1bkjfyd" data-svelte-h="svelte-s7ikrz">Filtros avanzados</legend> <div class="search-form__filters-grid svelte-1bkjfyd">${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Tipo de radicado",
        type: "select",
        id: "filter-doc-type",
        options: [
          { value: "", label: "Todos los tipos" },
          { value: "E", label: "Entrada" },
          { value: "S", label: "Salida" },
          { value: "I", label: "Interno" }
        ],
        value: filterDocType
      },
      {
        value: ($$value) => {
          filterDocType = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Estado",
        type: "select",
        id: "filter-status",
        options: [
          { value: "", label: "Todos los estados" },
          { value: "registered", label: "Radicado" },
          {
            value: "distributed",
            label: "Distribuido"
          },
          {
            value: "in_progress",
            label: "En trámite"
          },
          { value: "closed", label: "Cerrado" }
        ],
        value: filterStatus
      },
      {
        value: ($$value) => {
          filterStatus = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Fecha desde",
        type: "date",
        id: "filter-date-from",
        value: filterDateFrom
      },
      {
        value: ($$value) => {
          filterDateFrom = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Fecha hasta",
        type: "date",
        id: "filter-date-to",
        value: filterDateTo
      },
      {
        value: ($$value) => {
          filterDateTo = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Código dependencia destino",
        type: "number",
        id: "filter-dest-dept",
        placeholder: "Ej. 12",
        hint: "Código numérico entero de la dependencia.",
        value: filterDestDept
      },
      {
        value: ($$value) => {
          filterDestDept = $$value;
          $$settled = false;
        }
      },
      {}
    )}</div>  ${``} ${hasActiveFilters ? `<div class="search-form__filters-actions svelte-1bkjfyd">${validate_component(Button, "Button").$$render(
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
                Limpiar filtros`;
        }
      }
    )}</div>` : ``}</fieldset>` : ``}</form></section>  <p class="sr-only" aria-live="polite" aria-atomic="true">${`${searched ? `${escape(total.toLocaleString("es-CO"))} resultado${escape(total !== 1 ? "s" : "")} encontrado${escape(total !== 1 ? "s" : "")}` : ``}`}</p> <section class="busqueda__results svelte-1bkjfyd" aria-label="Resultados de búsqueda"${add_attribute("aria-busy", loading, 0)}>${data.loadError ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-exclamation-circle",
        title: "Error en la búsqueda",
        description: data.loadError
      },
      {},
      {
        default: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "primary", disabled: !hasQuery }, {}, {
            default: () => {
              return `Reintentar`;
            }
          })}`;
        }
      }
    )}` : `${!searched ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-search",
        title: "Realiza una búsqueda",
        description: "Introduce al menos 2 caracteres en el campo de búsqueda y pulsa Buscar."
      },
      {},
      {}
    )}` : `${results.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-file-search",
        title: "Sin resultados",
        description: "No se encontraron radicados que coincidan con los criterios de búsqueda."
      },
      {},
      {
        default: () => {
          return `${hasActiveFilters ? `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Limpiar filtros`;
            }
          })}` : ``}`;
        }
      }
    )}` : ` <div class="busqueda__table-wrapper svelte-1bkjfyd">${validate_component(DataTable, "DataTable").$$render(
      $$result,
      {
        columns: tableColumns,
        rows: results,
        totalCount: total,
        page: currentPage,
        pageSize,
        loading,
        caption: "Resultados de búsqueda de radicados" + (data.initialFilters?.q ? ` para "${data.initialFilters.q}"` : "")
      },
      {},
      {
        row: ({ row }) => {
          return `<td class="op-datatable__td"><span class="tracking-number svelte-1bkjfyd">${escape(row.tracking_number)}</span></td> <td class="op-datatable__td"><span class="${"doc-type-badge doc-type-badge--" + escape(row.doc_type?.toLowerCase(), true) + " svelte-1bkjfyd"}">${escape(DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type ?? "—")}</span></td> <td class="op-datatable__td search-table__td--subject svelte-1bkjfyd"><span class="subject-text svelte-1bkjfyd"${add_attribute("title", row.subject, 0)}>${escape(row.subject ?? "—")}</span></td> ${`<td class="op-datatable__td search-table__td--dep svelte-1bkjfyd">${escape(row.dest_dept ?? "—")}</td>`} <td class="op-datatable__td search-table__td--date svelte-1bkjfyd"><time${add_attribute("datetime", row.registered_at, 0)}>${escape(formatDate(row.registered_at))}</time></td> <td class="op-datatable__td">${validate_component(StatusChip, "StatusChip").$$render(
            $$result,
            {
              status: STATUS_CHIP_MAP[row.status] ?? "neutral",
              label: STATUS_LABELS[row.status] ?? row.status ?? "—",
              dot: true
            },
            {},
            {}
          )}</td> <td class="op-datatable__td search-table__td--actions svelte-1bkjfyd"><a href="/bandeja" class="action-link svelte-1bkjfyd" aria-label="${"Ver radicado " + escape(row.tracking_number, true) + " en la bandeja"}" title="Ver en bandeja"><i class="fas fa-external-link-alt" aria-hidden="true"></i>
                Ver en bandeja</a></td>`;
        }
      }
    )}</div>`}`}`}</section> </div>`;
  } while (!$$settled);
  return $$rendered;
});
export {
  Page as default
};
