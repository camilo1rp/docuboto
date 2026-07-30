<!--
  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  /busqueda — Búsqueda avanzada de radicados (T-11, RF-UI-03).

  Resuelve el enlace muerto a /busqueda que existe en bandeja/+page.svelte.

  Filtros soportados por /api/v1/search:
    - q (texto libre, ≥2 chars, requerido)
    - doc_type: E | S | I
    - status: registered | distributed | in_progress | closed
    - date_from, date_to (ISO date)
    - dest_dept_code (entero)
    - page, size

  PATRÓN elegido (coherente con bandeja):
    - SSR inicial vía +page.server.js (deep-link/bookmarkable).
    - Nueva búsqueda: goto() actualiza la URL y re-ejecuta el server load sin
      recarga completa del navegador (SvelteKit navigation).
    - Paginación: usa /busqueda/api (proxy SSR, como bandeja usa /bandeja/api)
      para navegar entre páginas sin perder el estado del formulario.
-->
<script>
  import { onMount } from "svelte";
  import { browser } from "$app/environment";
  import { goto } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notifyError } from "$lib/stores/notifications.js";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import DataTable from "$lib/components/ui/DataTable.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Estado del formulario (inicializado desde la URL vía server load)
  // ---------------------------------------------------------------------------
  let q = data.initialFilters?.q ?? "";
  let filterDocType = data.initialFilters?.docType ?? "";
  let filterStatus = data.initialFilters?.status ?? "";
  let filterDateFrom = data.initialFilters?.dateFrom ?? "";
  let filterDateTo = data.initialFilters?.dateTo ?? "";
  let filterDestDept = data.initialFilters?.destDeptCode ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  const pageSize = 20;

  let showAdvanced = !!(
    filterDocType ||
    filterStatus ||
    filterDateFrom ||
    filterDateTo ||
    filterDestDept
  );

  // ---------------------------------------------------------------------------
  // Estado de resultados
  // ---------------------------------------------------------------------------

  /** @type {any[]} */
  let results = data.results ?? [];
  let total = data.total ?? 0;
  let loading = false;
  let qError = "";
  let dateRangeError = "";

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Búsqueda avanzada" }]);

    if (!browser) return;
    const mql = window.matchMedia("(max-width: 768px)");
    const syncViewport = () => {
      isNarrowViewport = mql.matches;
    };
    syncViewport();
    mql.addEventListener("change", syncViewport);
    return () => mql.removeEventListener("change", syncViewport);
  });

  // ---------------------------------------------------------------------------
  // Helpers de presentación
  // ---------------------------------------------------------------------------
  const DOC_TYPE_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };
  const STATUS_LABELS = {
    registered: "Radicado",
    distributed: "Distribuido",
    in_progress: "En trámite",
    closed: "Cerrado",
  };
  const STATUS_CHIP_MAP = {
    registered: "info",
    distributed: "pending",
    in_progress: "active",
    closed: "closed",
  };

  // Columnas de DataTable — el renderizado real de cada celda lo controla el
  // slot="row" de abajo (radicado en monoespaciada, badge de tipo, StatusChip,
  // enlace de acción), así que `label` es lo único que consume el <thead>
  // automático de DataTable.
  const TABLE_COLUMNS = [
    { key: "tracking_number", label: "Radicado", width: "160px" },
    { key: "doc_type", label: "Tipo", width: "90px" },
    { key: "subject", label: "Asunto" },
    { key: "dest_dept", label: "Dependencia destino", width: "180px" },
    { key: "registered_at", label: "Fecha", width: "110px" },
    { key: "status", label: "Estado", width: "140px" },
    { key: "actions", label: "Acciones", width: "150px" },
  ];

  // La columna "Dependencia destino" se oculta en viewports estrechos — se
  // calcula en la FUENTE (no solo por CSS) para que el <th> generado por
  // DataTable y el <td> de la fila desaparezcan juntos y no se rompa la
  // asociación encabezado↔dato para lectores de pantalla (mismo breakpoint
  // que el resto del layout responsive de esta pantalla, 768px).
  let isNarrowViewport = false;
  $: tableColumns = isNarrowViewport
    ? TABLE_COLUMNS.filter((col) => col.key !== "dest_dept")
    : TABLE_COLUMNS;

  /**
   * Formatea una fecha ISO 8601 como fecha legible en español.
   * @param {string} iso
   */
  function formatDate(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }

  // ---------------------------------------------------------------------------
  // Búsqueda — usa goto() para actualizar la URL (bookmarkable) y re-ejecutar SSR
  // ---------------------------------------------------------------------------

  /**
   * Construye los query params a partir del estado actual del formulario.
   */
  function buildSearchParams(page = 1) {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (filterDocType) params.set("doc_type", filterDocType);
    if (filterStatus) params.set("status", filterStatus);
    if (filterDateFrom) params.set("date_from", filterDateFrom);
    if (filterDateTo) params.set("date_to", filterDateTo);
    // dest_dept_code: solo entero positivo para evitar 422 en el gateway
    const deptCode = parseInt(filterDestDept, 10);
    if (Number.isFinite(deptCode) && deptCode > 0) params.set("dest_dept_code", String(deptCode));
    if (page > 1) params.set("page", String(page));
    return params;
  }

  /**
   * Valida y lanza una nueva búsqueda actualizando la URL (deep-link).
   * goto() ejecuta SvelteKit client-side navigation → re-run del server load.
   */
  async function search() {
    qError = "";
    dateRangeError = "";
    if (!q.trim() || q.trim().length < 2) {
      qError = "Introduce al menos 2 caracteres para buscar.";
      return;
    }
    if (filterDateFrom && filterDateTo && filterDateFrom > filterDateTo) {
      dateRangeError = "La fecha inicio no puede ser posterior a la fecha fin.";
      return;
    }
    currentPage = 1;
    await goto(`/busqueda?${buildSearchParams(1).toString()}`, { keepFocus: true });
  }

  function clearFilters() {
    filterDocType = "";
    filterStatus = "";
    filterDateFrom = "";
    filterDateTo = "";
    filterDestDept = "";
    dateRangeError = "";
    currentPage = 1;
    if (q.trim().length >= 2) {
      goto(`/busqueda?${buildSearchParams(1).toString()}`, { keepFocus: true });
    }
  }

  // ---------------------------------------------------------------------------
  // Paginación — usa el endpoint proxy local /busqueda/api (sin recarga de UI)
  // para navegar entre páginas sin perder el estado del formulario (como bandeja).
  // ---------------------------------------------------------------------------

  $: totalPages = Math.ceil(total / pageSize);

  async function goToPage(page) {
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    await loadPage(page);
  }

  /**
   * Carga una página de resultados vía el endpoint proxy SSR.
   * El token nunca alcanza el JS del cliente (D-05).
   * @param {number} page
   */
  async function loadPage(page) {
    loading = true;
    try {
      const params = buildSearchParams(page);
      const res = await fetch(`/busqueda/api?${params.toString()}`);
      const result = await res.json();
      if (!res.ok) throw result;
      results = result.results;
      total = result.total;
    } catch (err) {
      notifyError(err?.detail ?? "Error al cargar los resultados.");
    } finally {
      loading = false;
    }
  }

  $: hasActiveFilters = !!(
    filterDocType ||
    filterStatus ||
    filterDateFrom ||
    filterDateTo ||
    filterDestDept
  );
  $: hasQuery = q.trim().length >= 2;
  $: searched = !!data.initialFilters?.q;
</script>

<svelte:head>
  <title>Búsqueda avanzada — OrpycaMCP</title>
</svelte:head>

<div class="busqueda">
  <!-- -------------------------------------------------------------------------
    Encabezado
  ------------------------------------------------------------------------- -->
  <header class="busqueda__header">
    <h1 class="busqueda__title">Búsqueda avanzada</h1>
    {#if searched && !loading}
      <span class="busqueda__total" aria-live="polite">
        {total.toLocaleString("es-CO")} resultado{total !== 1 ? "s" : ""}
      </span>
    {/if}
  </header>

  <!-- -------------------------------------------------------------------------
    Formulario de búsqueda
  ------------------------------------------------------------------------- -->
  <section class="busqueda__search-panel" aria-label="Formulario de búsqueda">
    <form
      class="search-form"
      on:submit|preventDefault={search}
      role="search"
      aria-label="Buscar radicados"
    >
      <!-- Caja de búsqueda principal -->
      <div class="search-form__main">
        <label for="search-q" class="search-form__label">Texto de búsqueda</label>
        <div class="search-form__input-row">
          <input
            id="search-q"
            type="search"
            class="search-form__input"
            class:search-form__input--error={!!qError}
            bind:value={q}
            placeholder="Asunto, número de radicado, remitente…"
            autocomplete="off"
            minlength="2"
            aria-describedby={qError ? "search-q-error" : undefined}
            aria-required="true"
          />
          <Button type="submit" variant="primary" disabled={loading} aria-label="Buscar">
            <i class="fas fa-search" aria-hidden="true"></i>
            <span>Buscar</span>
          </Button>
        </div>
        {#if qError}
          <p id="search-q-error" class="search-form__field-error" role="alert">{qError}</p>
        {/if}
      </div>

      <!-- Toggle filtros avanzados -->
      <div class="search-form__advanced-toggle">
        <button
          type="button"
          class="advanced-toggle-btn"
          aria-expanded={showAdvanced}
          aria-controls="advanced-filters"
          on:click={() => (showAdvanced = !showAdvanced)}
        >
          <i class="fas fa-sliders-h" aria-hidden="true"></i>
          {showAdvanced ? "Ocultar filtros" : "Filtros avanzados"}
          {#if hasActiveFilters}
            <span class="advanced-toggle-btn__badge" aria-label="Hay filtros activos">
              <i class="fas fa-circle" aria-hidden="true"></i>
            </span>
          {/if}
        </button>
      </div>

      <!-- Filtros avanzados (colapsables) -->
      {#if showAdvanced}
        <fieldset id="advanced-filters" class="search-form__filters">
          <legend class="search-form__filters-legend">Filtros avanzados</legend>
          <div class="search-form__filters-grid">
            <FormField
              label="Tipo de radicado"
              type="select"
              id="filter-doc-type"
              bind:value={filterDocType}
              options={[
                { value: "", label: "Todos los tipos" },
                { value: "E", label: "Entrada" },
                { value: "S", label: "Salida" },
                { value: "I", label: "Interno" },
              ]}
            />
            <FormField
              label="Estado"
              type="select"
              id="filter-status"
              bind:value={filterStatus}
              options={[
                { value: "", label: "Todos los estados" },
                { value: "registered", label: "Radicado" },
                { value: "distributed", label: "Distribuido" },
                { value: "in_progress", label: "En trámite" },
                { value: "closed", label: "Cerrado" },
              ]}
            />
            <FormField
              label="Fecha desde"
              type="date"
              id="filter-date-from"
              bind:value={filterDateFrom}
            />
            <FormField
              label="Fecha hasta"
              type="date"
              id="filter-date-to"
              bind:value={filterDateTo}
            />
            <FormField
              label="Código dependencia destino"
              type="number"
              id="filter-dest-dept"
              bind:value={filterDestDept}
              placeholder="Ej. 12"
              hint="Código numérico entero de la dependencia."
            />
          </div>

          <!-- Error de rango de fechas (accesible con role=alert) -->
          {#if dateRangeError}
            <p class="search-form__date-range-error" role="alert" id="date-range-error">
              <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
              {dateRangeError}
            </p>
          {/if}

          {#if hasActiveFilters}
            <div class="search-form__filters-actions">
              <Button type="button" variant="ghost" size="sm" on:click={clearFilters}>
                <i class="fas fa-times" aria-hidden="true"></i>
                Limpiar filtros
              </Button>
            </div>
          {/if}
        </fieldset>
      {/if}
    </form>
  </section>

  <!-- -------------------------------------------------------------------------
    Resultados
    El aria-live está en un nodo pequeño y estático para no reanunciar la tabla
    completa en cada paginación — solo anuncia el conteo de resultados (Fix #6).
  ------------------------------------------------------------------------- -->
  <p class="sr-only" aria-live="polite" aria-atomic="true">
    {#if loading}Buscando radicados…{:else if searched}{total.toLocaleString("es-CO")} resultado{total !==
      1
        ? "s"
        : ""} encontrado{total !== 1 ? "s" : ""}{/if}
  </p>
  <section class="busqueda__results" aria-label="Resultados de búsqueda" aria-busy={loading}>
    {#if data.loadError}
      <EmptyState
        icon="fas fa-exclamation-circle"
        title="Error en la búsqueda"
        description={data.loadError}
      >
        <Button variant="primary" on:click={search} disabled={!hasQuery}>Reintentar</Button>
      </EmptyState>
    {:else if !searched}
      <EmptyState
        icon="fas fa-search"
        title="Realiza una búsqueda"
        description="Introduce al menos 2 caracteres en el campo de búsqueda y pulsa Buscar."
      />
    {:else if !loading && results.length === 0}
      <EmptyState
        icon="fas fa-file-search"
        title="Sin resultados"
        description="No se encontraron radicados que coincidan con los criterios de búsqueda."
      >
        {#if hasActiveFilters}
          <Button variant="ghost" on:click={clearFilters}>Limpiar filtros</Button>
        {/if}
      </EmptyState>
    {:else}
      <!-- Tabla de resultados -->
      <div class="busqueda__table-wrapper">
        <DataTable
          columns={tableColumns}
          rows={results}
          totalCount={total}
          page={currentPage}
          {pageSize}
          {loading}
          caption="Resultados de búsqueda de radicados{data.initialFilters?.q
            ? ` para "${data.initialFilters.q}"`
            : ''}"
          on:pageChange={(e) => goToPage(e.detail.page)}
        >
          <svelte:fragment slot="row" let:row>
            <td class="op-datatable__td">
              <span class="tracking-number">{row.tracking_number}</span>
            </td>
            <td class="op-datatable__td">
              <span class="doc-type-badge doc-type-badge--{row.doc_type?.toLowerCase()}">
                {DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type ?? "—"}
              </span>
            </td>
            <td class="op-datatable__td search-table__td--subject">
              <span class="subject-text" title={row.subject}>{row.subject ?? "—"}</span>
            </td>
            {#if !isNarrowViewport}
              <td class="op-datatable__td search-table__td--dep">
                {row.dest_dept ?? "—"}
              </td>
            {/if}
            <td class="op-datatable__td search-table__td--date">
              <time datetime={row.registered_at}>{formatDate(row.registered_at)}</time>
            </td>
            <td class="op-datatable__td">
              <StatusChip
                status={STATUS_CHIP_MAP[row.status] ?? "neutral"}
                label={STATUS_LABELS[row.status] ?? row.status ?? "—"}
                dot={true}
              />
            </td>
            <td class="op-datatable__td search-table__td--actions">
              <a
                href="/bandeja"
                class="action-link"
                aria-label="Ver radicado {row.tracking_number} en la bandeja"
                title="Ver en bandeja"
              >
                <i class="fas fa-external-link-alt" aria-hidden="true"></i>
                Ver en bandeja
              </a>
            </td>
          </svelte:fragment>
        </DataTable>
      </div>
    {/if}
  </section>
</div>

<style lang="scss">
  // ---------------------------------------------------------------------------
  // Layout principal
  // ---------------------------------------------------------------------------

  .busqueda {
    padding: var(--op-space-6);
    max-width: 1200px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);
  }

  .busqueda__header {
    display: flex;
    align-items: baseline;
    gap: var(--op-space-4);
    flex-wrap: wrap;
    padding-bottom: var(--op-space-4);
    border-bottom: 1px solid var(--op-border);
  }

  .busqueda__title {
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
  }

  .busqueda__total {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    flex-shrink: 0;
  }

  // ---------------------------------------------------------------------------
  // Panel de búsqueda
  // ---------------------------------------------------------------------------

  .busqueda__search-panel {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-5);
  }

  .search-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .search-form__main {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
  }

  .search-form__label {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text);
  }

  .search-form__input-row {
    display: flex;
    gap: var(--op-space-3);
    align-items: stretch;
  }

  .search-form__input {
    flex: 1;
    height: 40px;
    padding: 0 var(--op-space-3);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    background: var(--op-bg);
    transition: border-color var(--op-transition);

    &::placeholder {
      color: var(--op-text-secondary);
    }

    &:focus {
      outline: none;
      border-color: var(--op-primary);
      box-shadow: 0 0 0 3px var(--op-focus-ring);
    }

    &--error {
      border-color: var(--op-error);
    }
  }

  .search-form__field-error {
    font-size: var(--op-font-xs);
    color: var(--op-error);
    margin: 0;
  }

  .search-form__date-range-error {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-xs);
    color: var(--op-error);
    margin: var(--op-space-2) 0 0;
  }

  // Toggle filtros avanzados
  .search-form__advanced-toggle {
    display: flex;
    align-items: center;
  }

  .advanced-toggle-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    background: none;
    border: none;
    cursor: pointer;
    padding: var(--op-space-1) 0;
    text-decoration: none;
    transition: color var(--op-transition);

    &:hover {
      color: var(--op-primary);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
      border-radius: var(--op-radius-sm);
    }

    &__badge {
      font-size: 8px;
      color: var(--op-accent);
    }
  }

  // Fieldset de filtros avanzados
  .search-form__filters {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-4);
    margin: 0;
  }

  .search-form__filters-legend {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-text-secondary);
    padding: 0 var(--op-space-2);
  }

  .search-form__filters-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: var(--op-space-3);
  }

  .search-form__filters-actions {
    margin-top: var(--op-space-3);
    display: flex;
    justify-content: flex-end;
  }

  // ---------------------------------------------------------------------------
  // Resultados
  // ---------------------------------------------------------------------------

  .busqueda__results {
    min-height: 200px;
  }

  .busqueda__table-wrapper {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    overflow: hidden;
  }

  // ---------------------------------------------------------------------------
  // Tabla de resultados
  // ---------------------------------------------------------------------------

  // Celdas de datos que DataTable no formatea por sí misma (renderizadas vía
  // slot="row"). El resto del estilo de tabla/paginación es de DataTable.
  .search-table__td {
    &--subject {
      max-width: 280px;
    }
    &--dep {
      max-width: 160px;
    }
    &--date {
      white-space: nowrap;
      color: var(--op-text-secondary);
    }
    &--actions {
      text-align: right;
      white-space: nowrap;
    }
  }

  // Número de radicado
  .tracking-number {
    font-family: monospace;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    background: var(--op-primary-light);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    white-space: nowrap;
  }

  // Badge de tipo documental
  .doc-type-badge {
    display: inline-block;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-bold);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    white-space: nowrap;

    &--e {
      background: var(--op-info-bg);
      color: var(--op-info-fg);
    }
    &--s {
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);
    }
    &--i {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      border: 1px solid var(--op-border);
    }
  }

  // Asunto truncado
  .subject-text {
    display: block;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  // Enlace de acción
  .action-link {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-1);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    text-decoration: none;
    padding: 3px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    border: 1px solid var(--op-primary-light);
    background: var(--op-primary-light);
    transition:
      background var(--op-transition),
      color var(--op-transition);

    &:hover {
      background: var(--op-primary);
      color: var(--op-surface);
      border-color: var(--op-primary);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  // ---------------------------------------------------------------------------
  // Responsive
  // ---------------------------------------------------------------------------

  @media (max-width: 768px) {
    .busqueda {
      padding: var(--op-space-4);
    }

    .search-form__input-row {
      flex-direction: column;
    }
  }
</style>
