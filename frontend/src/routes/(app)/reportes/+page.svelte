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
  /reportes — Reportes y estadísticas de radicados (T-11, RF-UI-03).

  Muestra conteos de radicados agrupados por:
    - Tipo (E/S/I)
    - Estado (registered / distributed / in_progress / closed)
    - Mes (por fecha de radicación)
    - Dependencia destino

  El botón "Descargar CSV" apunta a /reportes/api/csv (proxy SSR) que lee el
  token de la cookie httpOnly — el token nunca alcanza el cliente (D-05).

  No se usan librerías de charting. Las visualizaciones son tablas Bulma/Sass
  con barras de proporción vía CSS (tokens --op-*) para contexto visual rápido.
-->
<script>
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notifyError } from "$lib/stores/notifications.js";
  import Loader from "$lib/components/ui/Loader.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Filtros de rango de fechas
  // ---------------------------------------------------------------------------
  let dateFrom = data.initialFilters?.dateFrom ?? "";
  let dateTo = data.initialFilters?.dateTo ?? "";
  let loading = false;
  let csvLoading = false;
  let dateRangeError = "";

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Reportes" }]);
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

  /**
   * Formatea un mes ISO (YYYY-MM) como texto legible en español.
   * @param {string} month
   */
  function formatMonth(month) {
    if (!month) return "—";
    const [year, mon] = month.split("-");
    const date = new Date(Number(year), Number(mon) - 1, 1);
    return date.toLocaleDateString("es-CO", { year: "numeric", month: "long" });
  }

  /**
   * Calcula el ancho de la barra de proporción (0–100%) respecto al máximo.
   * @param {number} value
   * @param {number} max
   */
  function barWidth(value, max) {
    if (!max || max === 0) return "0%";
    return `${Math.round((value / max) * 100)}%`;
  }

  /** Total de radicados del período */
  $: grandTotal = (() => {
    const types = data.summary?.by_type ?? [];
    return types.reduce((acc, row) => acc + (row.count ?? 0), 0);
  })();

  /** Máximo de los conteos de dependencia (para las barras) */
  $: maxDeptCount = (() => {
    const depts = data.summary?.by_dept ?? [];
    return Math.max(...depts.map((d) => d.count ?? 0), 0);
  })();

  /** Máximo de los conteos por mes (para las barras) */
  $: maxMonthCount = (() => {
    const months = data.summary?.by_month ?? [];
    return Math.max(...months.map((m) => m.count ?? 0), 0);
  })();

  // ---------------------------------------------------------------------------
  // Aplicar filtros — goto() actualiza la URL y re-ejecuta el server load (SSR)
  // ---------------------------------------------------------------------------

  async function applyFilters() {
    dateRangeError = "";
    if (dateFrom && dateTo && dateFrom > dateTo) {
      dateRangeError = "La fecha inicio no puede ser posterior a la fecha fin.";
      return;
    }
    loading = true;
    const params = new URLSearchParams();
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    const qs = params.toString();
    await goto(`/reportes${qs ? `?${qs}` : ""}`, { keepFocus: true });
    loading = false;
  }

  function clearFilters() {
    dateFrom = "";
    dateTo = "";
    dateRangeError = "";
    goto("/reportes");
  }

  // ---------------------------------------------------------------------------
  // Descarga CSV vía fetch → blob con manejo de error (Fix #3).
  // Llama al proxy SSR /reportes/api/csv que lee el token de la cookie httpOnly.
  // Un <a download> simple guardaría el JSON de error como archivo .csv si el
  // upstream falla — la descarga vía fetch permite detectar el error y notificar.
  // ---------------------------------------------------------------------------

  /**
   * Construye la URL del endpoint CSV con los filtros de fecha activos.
   * @returns {string}
   */
  function buildCsvUrl() {
    const p = new URLSearchParams();
    if (dateFrom && /^\d{4}-\d{2}-\d{2}$/.test(dateFrom)) p.set("date_from", dateFrom);
    if (dateTo && /^\d{4}-\d{2}-\d{2}$/.test(dateTo)) p.set("date_to", dateTo);
    const qs = p.toString();
    return `/reportes/api/csv${qs ? `?${qs}` : ""}`;
  }

  async function downloadCsv() {
    if (csvLoading) return;
    csvLoading = true;
    try {
      const res = await fetch(buildCsvUrl());
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        notifyError(body.detail ?? "Error al descargar el reporte CSV.");
        return;
      }
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = "radicados.csv";
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(objectUrl);
    } catch {
      notifyError("No se pudo descargar el reporte CSV. Intente de nuevo.");
    } finally {
      csvLoading = false;
    }
  }

  $: hasFilters = !!(dateFrom || dateTo);
</script>

<svelte:head>
  <title>Reportes — OrpycaMCP</title>
</svelte:head>

<div class="reportes">
  <!-- -------------------------------------------------------------------------
    Encabezado
  ------------------------------------------------------------------------- -->
  <header class="reportes__header">
    <div class="reportes__header-left">
      <h1 class="reportes__title">Reportes de radicados</h1>
      {#if grandTotal > 0}
        <span class="reportes__total">
          {grandTotal.toLocaleString("es-CO")} radicado{grandTotal !== 1 ? "s" : ""} en el período
        </span>
      {/if}
    </div>
    <!-- Descarga CSV vía fetch→blob; el proxy SSR lee el token de la cookie httpOnly.
         No usar <a download> porque guardaría el JSON de error como archivo .csv. -->
    <button
      type="button"
      class="csv-download-btn"
      disabled={csvLoading}
      aria-label={csvLoading ? "Descargando reporte CSV…" : "Descargar reporte en formato CSV"}
      on:click={downloadCsv}
    >
      <i class="fas fa-file-csv" aria-hidden="true"></i>
      {csvLoading ? "Descargando…" : "Descargar CSV"}
    </button>
  </header>

  <!-- -------------------------------------------------------------------------
    Filtros de rango de fechas
  ------------------------------------------------------------------------- -->
  <section class="reportes__filters" aria-label="Filtros de rango de fechas">
    <form
      class="filters-form"
      on:submit|preventDefault={applyFilters}
      aria-label="Filtrar reportes por período"
    >
      <div class="filters-form__row">
        <div class="filters-form__fields">
          <FormField label="Fecha desde" type="date" id="report-date-from" bind:value={dateFrom} />
          <FormField label="Fecha hasta" type="date" id="report-date-to" bind:value={dateTo} />
        </div>
        <div class="filters-form__actions">
          <Button type="submit" variant="primary" size="sm" disabled={loading}>
            <i class="fas fa-filter" aria-hidden="true"></i>
            Aplicar
          </Button>
          {#if hasFilters}
            <Button type="button" variant="ghost" size="sm" on:click={clearFilters}>
              <i class="fas fa-times" aria-hidden="true"></i>
              Limpiar
            </Button>
          {/if}
        </div>
      </div>
      {#if dateRangeError}
        <p class="filters-form__date-range-error" role="alert">
          <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
          {dateRangeError}
        </p>
      {/if}
    </form>
  </section>

  <!-- -------------------------------------------------------------------------
    Contenido principal
  ------------------------------------------------------------------------- -->
  <div class="reportes__content" aria-live="polite" aria-busy={loading}>
    {#if loading}
      <div class="reportes__loading">
        <Loader type="skeleton" lines={6} label="Cargando reporte…" />
      </div>
    {:else if data.loadError}
      <EmptyState
        icon="fas fa-exclamation-circle"
        title="Error al cargar el reporte"
        description={data.loadError}
      >
        <Button variant="primary" on:click={applyFilters}>Reintentar</Button>
      </EmptyState>
    {:else if !data.summary}
      <EmptyState
        icon="fas fa-chart-bar"
        title="Sin datos"
        description="No hay datos de radicados para mostrar en el período seleccionado."
      />
    {:else}
      <div class="reportes__panels">
        <!-- =================================================================
          Panel: Por tipo de radicado
        ================================================================= -->
        <section class="report-panel" aria-labelledby="panel-tipo-heading">
          <h2 id="panel-tipo-heading" class="report-panel__title">Por tipo</h2>
          {#if (data.summary.by_type ?? []).length === 0}
            <p class="report-panel__empty">Sin datos</p>
          {:else}
            <table class="report-table" aria-label="Radicados por tipo">
              <caption class="sr-only">Conteo de radicados agrupados por tipo</caption>
              <thead>
                <tr>
                  <th scope="col" class="report-table__th">Tipo</th>
                  <th scope="col" class="report-table__th report-table__th--num">Cantidad</th>
                  <th scope="col" class="report-table__th report-table__th--num">%</th>
                </tr>
              </thead>
              <tbody>
                {#each data.summary.by_type as row (row.doc_type)}
                  {@const pct = grandTotal > 0 ? Math.round((row.count / grandTotal) * 100) : 0}
                  <tr class="report-table__row">
                    <td class="report-table__td">
                      <span
                        class="doc-type-badge doc-type-badge--{(row.doc_type ?? '').toLowerCase()}"
                      >
                        {DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type}
                      </span>
                    </td>
                    <td class="report-table__td report-table__td--num">
                      {row.count?.toLocaleString("es-CO") ?? 0}
                    </td>
                    <td class="report-table__td report-table__td--num">
                      <span class="pct-bar" aria-hidden="true">
                        <span class="pct-bar__fill" style="width: {pct}%"></span>
                      </span>
                      <span class="pct-text">{pct}%</span>
                    </td>
                  </tr>
                {/each}
              </tbody>
              <tfoot>
                <tr class="report-table__total">
                  <td class="report-table__td">Total</td>
                  <td class="report-table__td report-table__td--num">
                    <strong>{grandTotal.toLocaleString("es-CO")}</strong>
                  </td>
                  <td class="report-table__td report-table__td--num">100%</td>
                </tr>
              </tfoot>
            </table>
          {/if}
        </section>

        <!-- =================================================================
          Panel: Por estado
        ================================================================= -->
        <section class="report-panel" aria-labelledby="panel-estado-heading">
          <h2 id="panel-estado-heading" class="report-panel__title">Por estado</h2>
          {#if (data.summary.by_status ?? []).length === 0}
            <p class="report-panel__empty">Sin datos</p>
          {:else}
            <table class="report-table" aria-label="Radicados por estado">
              <caption class="sr-only">Conteo de radicados agrupados por estado</caption>
              <thead>
                <tr>
                  <th scope="col" class="report-table__th">Estado</th>
                  <th scope="col" class="report-table__th report-table__th--num">Cantidad</th>
                  <th scope="col" class="report-table__th report-table__th--num">%</th>
                </tr>
              </thead>
              <tbody>
                {#each data.summary.by_status as row (row.status)}
                  {@const pct = grandTotal > 0 ? Math.round((row.count / grandTotal) * 100) : 0}
                  <tr class="report-table__row">
                    <td class="report-table__td">
                      {STATUS_LABELS[row.status] ?? row.status}
                    </td>
                    <td class="report-table__td report-table__td--num">
                      {row.count?.toLocaleString("es-CO") ?? 0}
                    </td>
                    <td class="report-table__td report-table__td--num">
                      <span class="pct-bar" aria-hidden="true">
                        <span class="pct-bar__fill" style="width: {pct}%"></span>
                      </span>
                      <span class="pct-text">{pct}%</span>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>

        <!-- =================================================================
          Panel: Por mes (actividad mensual)
        ================================================================= -->
        <section class="report-panel report-panel--wide" aria-labelledby="panel-mes-heading">
          <h2 id="panel-mes-heading" class="report-panel__title">Actividad mensual</h2>
          {#if (data.summary.by_month ?? []).length === 0}
            <p class="report-panel__empty">Sin datos</p>
          {:else}
            <table class="report-table" aria-label="Radicados por mes">
              <caption class="sr-only">Conteo de radicados agrupados por mes de radicación</caption>
              <thead>
                <tr>
                  <th scope="col" class="report-table__th">Mes</th>
                  <th scope="col" class="report-table__th report-table__th--num">Radicados</th>
                  <th scope="col" class="report-table__th report-table__th--bar">Distribución</th>
                </tr>
              </thead>
              <tbody>
                {#each data.summary.by_month as row (row.month)}
                  <tr class="report-table__row">
                    <td class="report-table__td">
                      <time datetime={row.month}>{formatMonth(row.month)}</time>
                    </td>
                    <td class="report-table__td report-table__td--num">
                      {row.count?.toLocaleString("es-CO") ?? 0}
                    </td>
                    <td class="report-table__td report-table__td--bar">
                      <span class="bar-visual" aria-hidden="true">
                        <span
                          class="bar-visual__fill"
                          style="width: {barWidth(row.count, maxMonthCount)}"
                        ></span>
                      </span>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>

        <!-- =================================================================
          Panel: Por dependencia destino
        ================================================================= -->
        <section class="report-panel report-panel--wide" aria-labelledby="panel-dept-heading">
          <h2 id="panel-dept-heading" class="report-panel__title">Por dependencia destino</h2>
          {#if (data.summary.by_dept ?? []).length === 0}
            <p class="report-panel__empty">Sin datos</p>
          {:else}
            <table class="report-table" aria-label="Radicados por dependencia destino">
              <caption class="sr-only"
                >Conteo de radicados agrupados por dependencia destino</caption
              >
              <thead>
                <tr>
                  <th scope="col" class="report-table__th">Dependencia</th>
                  <th scope="col" class="report-table__th report-table__th--num">Radicados</th>
                  <th scope="col" class="report-table__th report-table__th--bar">Distribución</th>
                </tr>
              </thead>
              <tbody>
                {#each data.summary.by_dept as row (row.dept ?? row.dest_dept)}
                  <tr class="report-table__row">
                    <td class="report-table__td">
                      {row.dept ?? row.dest_dept ?? "Sin dependencia"}
                    </td>
                    <td class="report-table__td report-table__td--num">
                      {row.count?.toLocaleString("es-CO") ?? 0}
                    </td>
                    <td class="report-table__td report-table__td--bar">
                      <span class="bar-visual" aria-hidden="true">
                        <span
                          class="bar-visual__fill"
                          style="width: {barWidth(row.count, maxDeptCount)}"
                        ></span>
                      </span>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>
      </div>
    {/if}
  </div>
</div>

<style lang="scss">
  // ---------------------------------------------------------------------------
  // Layout principal
  // ---------------------------------------------------------------------------

  .reportes {
    padding: var(--op-space-6);
    max-width: 1200px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);
  }

  .reportes__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--op-space-4);
    flex-wrap: wrap;
    padding-bottom: var(--op-space-4);
    border-bottom: 1px solid var(--op-border);
  }

  .reportes__header-left {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);
  }

  .reportes__title {
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
  }

  .reportes__total {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
  }

  // ---------------------------------------------------------------------------
  // Botón de descarga CSV
  // ---------------------------------------------------------------------------

  .csv-download-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-2) var(--op-space-4);
    background: var(--op-primary);
    color: var(--op-surface);
    border: none;
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    cursor: pointer;
    transition: background var(--op-transition);
    flex-shrink: 0;

    &:hover:not(:disabled) {
      background: var(--op-primary-dark);
    }

    &:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 3px;
      border-radius: var(--op-radius);
    }
  }

  // ---------------------------------------------------------------------------
  // Filtros
  // ---------------------------------------------------------------------------

  .reportes__filters {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4) var(--op-space-5);
  }

  .filters-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  // Row containing fields + submit actions (side by side)
  .filters-form__row {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-4);
    flex-wrap: wrap;
  }

  .filters-form__fields {
    display: flex;
    gap: var(--op-space-3);
    flex-wrap: wrap;
    flex: 1;
  }

  .filters-form__actions {
    display: flex;
    gap: var(--op-space-2);
    align-items: flex-end;
    margin-bottom: 2px;
  }

  .filters-form__date-range-error {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    width: 100%;
    font-size: var(--op-font-xs);
    color: var(--op-error);
    margin: var(--op-space-1) 0 0;
  }

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------

  .reportes__loading {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-6);
  }

  // ---------------------------------------------------------------------------
  // Grid de paneles
  // ---------------------------------------------------------------------------

  .reportes__panels {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: var(--op-space-5);
  }

  // ---------------------------------------------------------------------------
  // Panel de reporte
  // ---------------------------------------------------------------------------

  .report-panel {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4) var(--op-space-5);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);

    &--wide {
      grid-column: 1 / -1;
    }
  }

  .report-panel__title {
    font-size: var(--op-font-md);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
    padding-bottom: var(--op-space-3);
    border-bottom: 1px solid var(--op-border);
  }

  .report-panel__empty {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    font-style: italic;
    margin: var(--op-space-4) 0;
    text-align: center;
  }

  // ---------------------------------------------------------------------------
  // Tablas de reporte
  // ---------------------------------------------------------------------------

  .report-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    &__th {
      padding: var(--op-space-2) var(--op-space-3);
      text-align: left;
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      color: var(--op-text-secondary);
      background: var(--op-bg);
      border-bottom: 1px solid var(--op-border);
      white-space: nowrap;

      &--num {
        text-align: right;
        width: 80px;
      }
      &--bar {
        width: 180px;
      }
    }

    &__row {
      transition: background var(--op-transition);

      &:hover {
        background: var(--op-bg);
      }

      &:not(:last-child) .report-table__td {
        border-bottom: 1px solid var(--op-border);
      }
    }

    &__td {
      padding: var(--op-space-2) var(--op-space-3);
      vertical-align: middle;
      color: var(--op-text);

      &--num {
        text-align: right;
        font-variant-numeric: tabular-nums;
        color: var(--op-text);
      }

      &--bar {
        vertical-align: middle;
      }
    }

    &__total {
      background: var(--op-bg);
      border-top: 2px solid var(--op-border);

      .report-table__td {
        font-weight: var(--op-weight-bold);
        color: var(--op-text);
        padding: var(--op-space-2) var(--op-space-3);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Badges de tipo
  // ---------------------------------------------------------------------------

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

  // ---------------------------------------------------------------------------
  // Barras de porcentaje (pct-bar) y barras visuales (bar-visual)
  // ---------------------------------------------------------------------------

  .pct-bar {
    display: inline-flex;
    width: 60px;
    height: 6px;
    background: var(--op-bg);
    border-radius: 3px;
    overflow: hidden;
    vertical-align: middle;
    margin-right: var(--op-space-2);
    border: 1px solid var(--op-border);

    &__fill {
      display: block;
      height: 100%;
      background: var(--op-primary);
      border-radius: 3px;
      transition: width 0.3s ease;
    }
  }

  .pct-text {
    font-variant-numeric: tabular-nums;
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }

  .bar-visual {
    display: flex;
    height: 8px;
    width: 100%;
    max-width: 160px;
    background: var(--op-bg);
    border-radius: 4px;
    overflow: hidden;
    border: 1px solid var(--op-border);

    &__fill {
      display: block;
      height: 100%;
      background: var(--op-accent);
      border-radius: 4px;
      transition: width 0.3s ease;
    }
  }

  // ---------------------------------------------------------------------------
  // Responsive
  // ---------------------------------------------------------------------------

  @media (max-width: 900px) {
    .reportes__panels {
      grid-template-columns: 1fr;
    }

    .report-panel--wide {
      grid-column: 1;
    }
  }

  @media (max-width: 600px) {
    .reportes {
      padding: var(--op-space-4);
    }

    .reportes__header {
      flex-direction: column;
    }

    .filters-form {
      flex-direction: column;
      align-items: stretch;
    }

    .filters-form__actions {
      justify-content: flex-start;
    }
  }
</style>
