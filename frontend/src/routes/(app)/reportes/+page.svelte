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

  El botón "Descargar CSV" del encabezado apunta a /reportes/api/csv (proxy SSR)
  que lee el token de la cookie httpOnly — el token nunca alcanza el cliente
  (D-05). Exporta el reporte COMPLETO desde el backend, con los mismos filtros
  de fecha activos.

  Cada uno de los cuatro paneles usa además el DataTable compartido (Sistema de
  Diseño §12): exportación CSV LOCAL de las filas visibles del panel (con
  filtro/orden ya aplicados) y ordenamiento. Ambas exportaciones son
  complementarias: la del encabezado trae todo el período desde el gateway; la
  de cada panel exporta justo lo que el usuario está mirando en ese momento.
  No se usan librerías de charting: las barras de proporción son CSS con
  tokens --op-*, dibujadas dentro de una celda controlada vía slot="row" (el
  renderizado automático de DataTable no sabe dibujar una barra).
-->
<script>
  import { onMount, tick } from "svelte";
  import { goto } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notifyError } from "$lib/stores/notifications.js";
  import Loader from "$lib/components/ui/Loader.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import DataTable from "$lib/components/ui/DataTable.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { getNivelChip } from "$lib/utils/nivelSeguridad.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Pestañas del reporte (T-Fase4, E08): "Radicados" siempre visible;
  // "Índice de clasificados" solo si el backend la autorizaría
  // (`data.canSeeIndice`, espejo de PERM_RECLASIFICAR) — la UI nunca ofrece
  // lo que la API rechazaría.
  // ---------------------------------------------------------------------------
  $: REPORT_TABS = [
    { id: "radicados", label: "Radicados" },
    ...(data.canSeeIndice ? [{ id: "indice-clasificados", label: "Índice de clasificados" }] : []),
  ];
  let activeReportTab = "radicados";

  /** @param {KeyboardEvent} e @param {string} tabId */
  function handleReportTabKeydown(e, tabId) {
    const idx = REPORT_TABS.findIndex((t) => t.id === tabId);
    if (idx === -1) return;
    /** @param {number} nextIdx */
    const focusTab = (nextIdx) => {
      activeReportTab = REPORT_TABS[nextIdx].id;
      tick().then(() => document.getElementById(`report-tab-${REPORT_TABS[nextIdx].id}`)?.focus());
    };
    if (e.key === "ArrowRight") {
      e.preventDefault();
      focusTab((idx + 1) % REPORT_TABS.length);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      focusTab((idx - 1 + REPORT_TABS.length) % REPORT_TABS.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusTab(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusTab(REPORT_TABS.length - 1);
    }
  }

  // ---------------------------------------------------------------------------
  // Índice de información clasificada y reservada (E08, Ley 1712/2014 art. 20)
  // — carga perezosa al activar la pestaña (GET /reportes/api/indice-reservado).
  // ---------------------------------------------------------------------------
  let indiceLoaded = false;
  let indiceLoading = false;
  // Distinción explícita fallo-de-red / dato-vacío (hallazgo recurrente de esta
  // sesión): `indiceError` solo se fija cuando la petición realmente falló;
  // un índice sin filas se representa con `indiceItems = []` e `indiceError = ""`,
  // nunca con el mismo estado.
  let indiceError = "";
  /** @type {Array<Record<string, unknown>>} */
  let indiceItems = [];
  let indiceCsvLoading = false;

  $: if (activeReportTab === "indice-clasificados" && !indiceLoaded && !indiceLoading) {
    loadIndiceClasificados();
  }

  async function loadIndiceClasificados() {
    indiceLoading = true;
    indiceError = "";
    try {
      const res = await fetch("/reportes/api/indice-reservado");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        indiceError = body.detail ?? `Error ${res.status} al cargar el índice.`;
        return;
      }
      const body = await res.json();
      indiceItems = body.items ?? [];
      indiceLoaded = true;
    } catch {
      indiceError = "No se pudo conectar al servidor para consultar el índice de clasificados.";
    } finally {
      indiceLoading = false;
    }
  }

  async function retryIndice() {
    indiceLoaded = false;
    await loadIndiceClasificados();
  }

  /**
   * Formatea una fecha ISO (con o sin hora) a texto legible es-CO.
   * @param {string|null|undefined} iso
   */
  function fmtIndiceDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("es-CO", { year: "numeric", month: "short", day: "2-digit" });
  }

  const TIPO_RADICADO_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };

  $: indiceRows = indiceItems.map((it) => ({
    ...it,
    tipo_label: TIPO_RADICADO_LABELS[/** @type {string} */ (it.tipo_radicado)] ?? it.tipo_radicado,
    clasificacion_label: it.clasificacion === "clasificada" ? "Clasificada" : "Reservada",
    fecha_clasificacion_label: fmtIndiceDate(/** @type {string} */ (it.fecha_clasificacion)),
    fecha_radicacion_label: fmtIndiceDate(/** @type {string} */ (it.fecha_radicacion)),
    plazo_label:
      it.plazo_reserva_meses === null || it.plazo_reserva_meses === undefined
        ? "Sin plazo fijado"
        : `${it.plazo_reserva_meses} meses`,
  }));

  const indiceColumns = [
    {
      key: "numero_radicado",
      label: "Radicado",
      sortable: true,
      filterType: /** @type {const} */ ("text"),
    },
    { key: "tipo_label", label: "Tipo", sortable: true },
    { key: "clasificacion_label", label: "Clasificación", sortable: true },
    { key: "excepcion_ley_1712", label: "Excepción legal", sortable: true },
    { key: "fundamento_juridico", label: "Fundamento jurídico" },
    { key: "causal_reserva", label: "Causal de reserva" },
    { key: "plazo_label", label: "Plazo de reserva" },
    { key: "fecha_clasificacion_label", label: "Fecha de clasificación", sortable: true },
    { key: "fecha_radicacion_label", label: "Fecha de radicación", sortable: true },
  ];

  /**
   * Descarga el CSV COMPLETO del índice desde el backend (proxy SSR,
   * `_csv_safe` ya neutraliza la inyección de fórmulas en el servidor) — trae
   * TODO el registro, no solo lo que está paginado/visible en el DataTable.
   */
  async function downloadIndiceCsv() {
    if (indiceCsvLoading) return;
    indiceCsvLoading = true;
    try {
      const res = await fetch("/reportes/api/indice-reservado/csv");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        notifyError(body.detail ?? "Error al descargar el índice en CSV.");
        return;
      }
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = "indice-reservado.csv";
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(objectUrl);
    } catch {
      notifyError("No se pudo descargar el índice en CSV. Intente de nuevo.");
    } finally {
      indiceCsvLoading = false;
    }
  }

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
  // Filas derivadas para cada DataTable — cada fila lleva ya los campos de
  // TEXTO que la exportación CSV va a leer (`buildCsv` lee `row[col.key]`
  // literal; si dejáramos solo `count`/`doc_type` crudos, exportar perdería la
  // etiqueta legible y el % ya calculado que el usuario ve en pantalla).
  // ---------------------------------------------------------------------------

  $: typeRows = (data.summary?.by_type ?? []).map((row) => {
    const count = row.count ?? 0;
    const pct = grandTotal > 0 ? Math.round((count / grandTotal) * 100) : 0;
    return {
      doc_type: row.doc_type,
      type_label: DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type,
      count,
      pct,
      pct_label: `${pct}%`,
    };
  });

  $: statusRows = (data.summary?.by_status ?? []).map((row) => {
    const count = row.count ?? 0;
    const pct = grandTotal > 0 ? Math.round((count / grandTotal) * 100) : 0;
    return {
      status: row.status,
      status_label: STATUS_LABELS[row.status] ?? row.status,
      count,
      pct,
      pct_label: `${pct}%`,
    };
  });

  $: monthRows = (data.summary?.by_month ?? []).map((row) => {
    const count = row.count ?? 0;
    const share = maxMonthCount > 0 ? Math.round((count / maxMonthCount) * 100) : 0;
    return {
      month: row.month,
      month_label: formatMonth(row.month),
      count,
      share_label: `${share}%`,
    };
  });

  $: deptRows = (data.summary?.by_dept ?? []).map((row) => {
    const count = row.count ?? 0;
    const share = maxDeptCount > 0 ? Math.round((count / maxDeptCount) * 100) : 0;
    return {
      dept: row.dept ?? row.dest_dept ?? null,
      dept_label: row.dept ?? row.dest_dept ?? "Sin dependencia",
      count,
      share_label: `${share}%`,
    };
  });

  const typeColumns = [
    { key: "type_label", label: "Tipo", sortable: true },
    { key: "count", label: "Cantidad", sortable: true },
    { key: "pct_label", label: "%", sortable: true },
  ];

  const statusColumns = [
    { key: "status_label", label: "Estado", sortable: true },
    { key: "count", label: "Cantidad", sortable: true },
    { key: "pct_label", label: "%", sortable: true },
  ];

  const monthColumns = [
    { key: "month_label", label: "Mes", sortable: true, filterType: /** @type {const} */ ("text") },
    { key: "count", label: "Radicados", sortable: true },
    { key: "share_label", label: "Distribución", sortable: true },
  ];

  const deptColumns = [
    {
      key: "dept_label",
      label: "Dependencia",
      sortable: true,
      filterType: /** @type {const} */ ("text"),
    },
    { key: "count", label: "Radicados", sortable: true },
    { key: "share_label", label: "Distribución", sortable: true },
  ];

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
    Pestañas (tablist ARIA) — "Radicados" siempre; "Índice de clasificados"
    solo si el backend la autorizaría (data.canSeeIndice, PERM_RECLASIFICAR).
  ------------------------------------------------------------------------- -->
  {#if REPORT_TABS.length > 1}
    <div class="reportes__tabs" role="tablist" aria-label="Tipo de reporte">
      {#each REPORT_TABS as tab (tab.id)}
        <button
          id="report-tab-{tab.id}"
          class="reportes__tab"
          class:reportes__tab--active={activeReportTab === tab.id}
          role="tab"
          aria-selected={activeReportTab === tab.id}
          aria-controls="report-panel-{tab.id}"
          tabindex={activeReportTab === tab.id ? 0 : -1}
          on:click={() => (activeReportTab = tab.id)}
          on:keydown={(e) => handleReportTabKeydown(e, tab.id)}
        >
          {tab.label}
        </button>
      {/each}
    </div>
  {/if}

  <div
    id="report-panel-radicados"
    role="tabpanel"
    aria-labelledby="report-tab-radicados"
    hidden={activeReportTab !== "radicados"}
    tabindex="0"
    class="reportes__panel-wrap"
  >
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
         No usar <a download> porque guardaría el JSON de error como archivo .csv.
         Trae el reporte COMPLETO del período desde el backend (a diferencia de
         la exportación por-panel de cada DataTable, que solo exporta las filas
         visibles de ese panel). -->
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
            <FormField
              label="Fecha desde"
              type="date"
              id="report-date-from"
              bind:value={dateFrom}
            />
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
    <!--
    El aria-live vive en un nodo pequeño y estático (Fix #6, patrón replicado de
    busqueda/+page.svelte) para no reanunciar los cuatro paneles/tablas completos
    en cada filtro/orden/skeleton; cada DataTable ya trae su propia región
    aria-live interna para sus propios cambios.
  -->
    <p class="sr-only" aria-live="polite" aria-atomic="true">
      {#if loading}
        Cargando reporte…
      {:else if data.loadError}
        No se pudo cargar el reporte.
      {:else}
        Reporte actualizado: {grandTotal.toLocaleString("es-CO")} radicado{grandTotal !== 1
          ? "s"
          : ""} en el período.
      {/if}
    </p>

    <div class="reportes__content" aria-busy={loading}>
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
            <DataTable
              tableId="reportes-tipo"
              columns={typeColumns}
              rows={typeRows}
              totalCount={typeRows.length}
              pageSize={Math.max(typeRows.length, 1)}
              enableSort
              enableExport
              exportFilename="reportes-por-tipo.csv"
              caption="Radicados por tipo"
            >
              <svelte:fragment slot="row" let:row>
                <td class="op-datatable__td">
                  <span class="doc-type-badge doc-type-badge--{(row.doc_type ?? '').toLowerCase()}">
                    {row.type_label}
                  </span>
                </td>
                <td class="op-datatable__td report-cell--num"
                  >{row.count.toLocaleString("es-CO")}</td
                >
                <td class="op-datatable__td">
                  <span class="pct-bar" aria-hidden="true">
                    <span class="pct-bar__fill" style="width: {row.pct}%"></span>
                  </span>
                  <span class="pct-text">{row.pct_label}</span>
                </td>
              </svelte:fragment>
            </DataTable>
            {#if typeRows.length > 0}
              <p class="report-panel__total">
                Total: <strong>{grandTotal.toLocaleString("es-CO")}</strong> (100%)
              </p>
            {/if}
          </section>

          <!-- =================================================================
          Panel: Por estado
        ================================================================= -->
          <section class="report-panel" aria-labelledby="panel-estado-heading">
            <h2 id="panel-estado-heading" class="report-panel__title">Por estado</h2>
            <DataTable
              tableId="reportes-estado"
              columns={statusColumns}
              rows={statusRows}
              totalCount={statusRows.length}
              pageSize={Math.max(statusRows.length, 1)}
              enableSort
              enableExport
              exportFilename="reportes-por-estado.csv"
              caption="Radicados por estado"
            >
              <svelte:fragment slot="row" let:row>
                <td class="op-datatable__td">{row.status_label}</td>
                <td class="op-datatable__td report-cell--num"
                  >{row.count.toLocaleString("es-CO")}</td
                >
                <td class="op-datatable__td">
                  <span class="pct-bar" aria-hidden="true">
                    <span class="pct-bar__fill" style="width: {row.pct}%"></span>
                  </span>
                  <span class="pct-text">{row.pct_label}</span>
                </td>
              </svelte:fragment>
            </DataTable>
          </section>

          <!-- =================================================================
          Panel: Por mes (actividad mensual)
        ================================================================= -->
          <section class="report-panel report-panel--wide" aria-labelledby="panel-mes-heading">
            <h2 id="panel-mes-heading" class="report-panel__title">Actividad mensual</h2>
            <DataTable
              tableId="reportes-mes"
              columns={monthColumns}
              rows={monthRows}
              totalCount={monthRows.length}
              pageSize={Math.max(monthRows.length, 1)}
              enableSort
              enableFilters
              enableExport
              exportFilename="reportes-actividad-mensual.csv"
              caption="Radicados por mes"
            >
              <svelte:fragment slot="row" let:row>
                <td class="op-datatable__td">
                  <time datetime={row.month}>{row.month_label}</time>
                </td>
                <td class="op-datatable__td report-cell--num"
                  >{row.count.toLocaleString("es-CO")}</td
                >
                <td class="op-datatable__td">
                  <span class="bar-visual" aria-hidden="true">
                    <span
                      class="bar-visual__fill"
                      style="width: {barWidth(row.count, maxMonthCount)}"
                    ></span>
                  </span>
                  <span class="pct-text">{row.share_label}</span>
                </td>
              </svelte:fragment>
            </DataTable>
          </section>

          <!-- =================================================================
          Panel: Por dependencia destino
        ================================================================= -->
          <section class="report-panel report-panel--wide" aria-labelledby="panel-dept-heading">
            <h2 id="panel-dept-heading" class="report-panel__title">Por dependencia destino</h2>
            <DataTable
              tableId="reportes-dependencia"
              columns={deptColumns}
              rows={deptRows}
              totalCount={deptRows.length}
              pageSize={Math.max(deptRows.length, 1)}
              enableSort
              enableFilters
              enableExport
              virtualize
              exportFilename="reportes-por-dependencia.csv"
              caption="Radicados por dependencia destino"
            >
              <svelte:fragment slot="row" let:row>
                <td class="op-datatable__td">{row.dept_label}</td>
                <td class="op-datatable__td report-cell--num"
                  >{row.count.toLocaleString("es-CO")}</td
                >
                <td class="op-datatable__td">
                  <span class="bar-visual" aria-hidden="true">
                    <span
                      class="bar-visual__fill"
                      style="width: {barWidth(row.count, maxDeptCount)}"
                    ></span>
                  </span>
                  <span class="pct-text">{row.share_label}</span>
                </td>
              </svelte:fragment>
            </DataTable>
          </section>
        </div>
      {/if}
    </div>
  </div>

  <!-- -------------------------------------------------------------------------
    Pestaña: Índice de información clasificada y reservada (E08, Ley 1712/2014
    art. 20 + Decreto 1081/2015). Solo se renderiza si el backend autorizaría
    la pestaña (data.canSeeIndice); el backend re-valida igual (defensa en
    profundidad).
  ------------------------------------------------------------------------- -->
  {#if data.canSeeIndice}
    <div
      id="report-panel-indice-clasificados"
      role="tabpanel"
      aria-labelledby="report-tab-indice-clasificados"
      hidden={activeReportTab !== "indice-clasificados"}
      tabindex="0"
      class="reportes__panel-wrap"
    >
      <div class="indice-legal-banner" role="note" aria-label="Fundamento legal del índice">
        <i class="fas fa-scale-balanced" aria-hidden="true"></i>
        <div>
          <p class="indice-legal-banner__title">
            Índice de información clasificada y reservada (Ley 1712/2014, art. 20)
          </p>
          <p class="indice-legal-banner__body">
            Este es un <strong>instrumento de transparencia</strong> que la ley obliga a publicar,
            no un listado interno: enumera QUÉ radicados están clasificados o reservados, con su
            fundamento jurídico y el plazo de la reserva —
            <strong>nunca el asunto ni el contenido protegido</strong> (registro content-free por diseño).
            Toda entidad pública debe mantener y divulgar este índice para que la ciudadanía pueda ejercer
            el derecho de acceso a la información y conocer las excepciones aplicadas.
          </p>
        </div>
      </div>

      <div class="indice-toolbar">
        <p class="sr-only" aria-live="polite" aria-atomic="true">
          {#if indiceLoading}
            Cargando índice de clasificados…
          {:else if indiceError}
            No se pudo cargar el índice de clasificados.
          {:else if indiceLoaded}
            Índice cargado: {indiceRows.length} registro{indiceRows.length !== 1 ? "s" : ""}.
          {/if}
        </p>
        {#if indiceLoaded && !indiceError}
          <span class="indice-toolbar__count">
            {indiceRows.length} registro{indiceRows.length !== 1 ? "s" : ""} clasificado{indiceRows.length !==
            1
              ? "s"
              : ""}/reservado{indiceRows.length !== 1 ? "s" : ""}
          </span>
        {/if}
        <button
          type="button"
          class="csv-download-btn"
          disabled={indiceCsvLoading}
          aria-label={indiceCsvLoading
            ? "Descargando índice completo en CSV…"
            : "Descargar el índice COMPLETO en formato CSV (todo el registro, no solo lo visible en pantalla)"}
          on:click={downloadIndiceCsv}
        >
          <i class="fas fa-file-csv" aria-hidden="true"></i>
          {indiceCsvLoading ? "Descargando…" : "Descargar índice completo (CSV)"}
        </button>
      </div>

      {#if indiceLoading}
        <Loader type="skeleton" lines={6} label="Cargando índice de clasificados…" />
      {:else if indiceError}
        <EmptyState
          icon="fas fa-exclamation-circle"
          title="Error al cargar el índice"
          description={indiceError}
        >
          <Button variant="primary" on:click={retryIndice}>Reintentar</Button>
        </EmptyState>
      {:else if indiceLoaded && indiceRows.length === 0}
        <EmptyState
          icon="fas fa-shield-halved"
          title="Sin radicados clasificados o reservados"
          description="Actualmente no hay radicados con nivel de seguridad reservado o clasificado en este tenant."
        />
      {:else if indiceLoaded}
        <DataTable
          tableId="reportes-indice-clasificados"
          columns={indiceColumns}
          rows={indiceRows}
          totalCount={indiceRows.length}
          pageSize={Math.max(indiceRows.length, 1)}
          enableSort
          enableFilters
          enableExport
          virtualize
          exportFilename="indice-reservado-visible.csv"
          caption="Índice de información clasificada y reservada"
        >
          <svelte:fragment slot="row" let:row>
            {@const chip = getNivelChip(row.nivel_seguridad)}
            <td class="op-datatable__td">
              <code>{row.numero_radicado}</code>
            </td>
            <td class="op-datatable__td">{row.tipo_label}</td>
            <td class="op-datatable__td">
              <StatusChip status={chip.status} label={row.clasificacion_label} dot />
            </td>
            <td class="op-datatable__td">{row.excepcion_ley_1712}</td>
            <td class="op-datatable__td">{row.fundamento_juridico ?? "—"}</td>
            <td class="op-datatable__td">{row.causal_reserva ?? "—"}</td>
            <td class="op-datatable__td">{row.plazo_label}</td>
            <td class="op-datatable__td">{row.fecha_clasificacion_label}</td>
            <td class="op-datatable__td">{row.fecha_radicacion_label}</td>
          </svelte:fragment>
        </DataTable>
        <p class="indice-export-note">
          <i class="fas fa-circle-info" aria-hidden="true"></i>
          La exportación CSV de esta tabla (icono de la barra de herramientas del listado) exporta solo
          las filas cargadas/visibles en el navegador; use "Descargar índice completo (CSV)" arriba para
          el registro íntegro.
        </p>
      {/if}
    </div>
  {/if}
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

  // ---------------------------------------------------------------------------
  // Pestañas de reporte
  // ---------------------------------------------------------------------------

  .reportes__tabs {
    display: flex;
    gap: var(--op-space-2);
    border-bottom: 1px solid var(--op-border);
  }

  .reportes__tab {
    padding: var(--op-space-3) var(--op-space-4);
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    cursor: pointer;

    @media (pointer: coarse) {
      min-height: 44px;
    }

    &:hover {
      color: var(--op-text);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
      border-radius: var(--op-radius-sm);
    }

    &--active {
      color: var(--op-primary-dark);
      border-bottom-color: var(--op-primary-dark);
      font-weight: var(--op-weight-bold);
    }
  }

  .reportes__panel-wrap {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);

    &:focus-visible {
      outline: none;
    }
  }

  // ---------------------------------------------------------------------------
  // Índice de clasificados — banner legal + toolbar
  // ---------------------------------------------------------------------------

  .indice-legal-banner {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-3);
    background: var(--op-primary-light);
    border: 1px solid var(--op-primary);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4) var(--op-space-5);

    i {
      color: var(--op-primary-dark);
      font-size: var(--op-font-lg);
      margin-top: 2px;
      flex-shrink: 0;
    }
  }

  .indice-legal-banner__title {
    margin: 0 0 var(--op-space-1);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    font-size: var(--op-font-sm);
  }

  .indice-legal-banner__body {
    margin: 0;
    color: var(--op-text);
    font-size: var(--op-font-sm);
    line-height: 1.5;
  }

  .indice-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--op-space-3);
    flex-wrap: wrap;
  }

  .indice-toolbar__count {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
  }

  .indice-export-note {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    margin: 0;

    i {
      color: var(--op-info);
      flex-shrink: 0;
    }
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
    background: var(--op-primary-dark);
    color: var(--op-surface);
    border: none;
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    cursor: pointer;
    transition: background var(--op-transition);
    flex-shrink: 0;

    &:hover:not(:disabled) {
      background: var(--op-brand-700);
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

    // El DataTable trae su propio scroll horizontal (§12); dentro de un panel
    // con padding lateral no debe desbordar la tarjeta.
    :global(.op-datatable) {
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      overflow: hidden;
    }

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

  // Total del panel "Por tipo" — el DataTable no tiene fila de pie (tfoot);
  // esta es la divergencia declarada, ver informe de la fase.
  .report-panel__total {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    text-align: right;
    margin: 0;
    padding-top: var(--op-space-2);
    border-top: 1px solid var(--op-border);
  }

  // ---------------------------------------------------------------------------
  // Celdas numéricas (renderizado manual vía slot="row")
  // ---------------------------------------------------------------------------

  .report-cell--num {
    text-align: right;
    font-variant-numeric: tabular-nums;
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
