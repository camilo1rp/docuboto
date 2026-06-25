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
  /dashboard — Panel principal (T-07, RF-UI-06).

  Muestra métricas clave del tenant:
    - Total de radicados + desglose por tipo (E/S/I)
    - Desglose por estado (registered / distributed / in_progress / closed)
    - Top 5 dependencias con más radicados
    - Actividad mensual (últimos 6 meses)

  Los datos vienen pre-cargados desde +page.server.js (SSR) llamando al endpoint
  real GET /api/v1/reports/radicados (E09). Si el backend no está disponible,
  la vista degrada con EmptyState informativo.

  Conserva el resumen de sesión del usuario del placeholder T-03.
-->
<script>
  import { onMount } from "svelte";
  import { sessionUser, tenantSlug } from "$lib/stores/auth.js";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import Card from "$lib/components/ui/Card.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  onMount(() => {
    setBreadcrumbs([{ label: "Panel principal" }]);
  });

  // ---------------------------------------------------------------------------
  // Datos del resumen (vienen del server load)
  // ---------------------------------------------------------------------------

  /** @type {import('$lib/services/report.service.js').ReportSummary|null} */
  $: summary = data.summary;
  $: summaryError = data.summaryError;

  // Etiquetas legibles para tipos de radicado
  const DOC_TYPE_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };

  // Etiquetas y chips para estados
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

  // Top 5 dependencias (server puede devolver todas; limitamos en cliente)
  $: topDependencias = summary
    ? Object.entries(summary.by_dependencia ?? {})
        .sort(([, a], [, b]) => b - a)
        .slice(0, 5)
    : [];

  // Últimos 6 meses de actividad mensual (ordenados cronológicamente)
  $: monthActivity = summary
    ? [...(summary.by_month ?? [])].sort((a, b) => a.month.localeCompare(b.month)).slice(-6)
    : [];

  // Máximo para la barra de progreso de actividad mensual
  $: maxMonthCount = monthActivity.length ? Math.max(...monthActivity.map((m) => m.count), 1) : 1;

  // Porcentaje por tipo para la barra visual
  $: total = summary?.total ?? 0;
  $: docTypeEntries = summary
    ? Object.entries(summary.by_doc_type ?? {}).filter(([, v]) => v > 0)
    : [];
</script>

<svelte:head>
  <title>Panel principal — OrpycaMCP</title>
</svelte:head>

<div class="dashboard">
  <!-- -----------------------------------------------------------------------
    Encabezado con saludo y tenant
  ----------------------------------------------------------------------- -->
  <header class="dashboard__header">
    <div>
      <h1 class="dashboard__title">Panel principal</h1>
      {#if $sessionUser}
        <p class="dashboard__welcome">
          Bienvenido, <strong>{$sessionUser.name}</strong>
          {#if $tenantSlug}
            &mdash; <span class="tenant-chip">{$tenantSlug}</span>
          {/if}
        </p>
      {/if}
    </div>
    <a href="/bandeja" class="dashboard__action-link" aria-label="Ir a la bandeja de radicados">
      <i class="fas fa-inbox" aria-hidden="true"></i>
      Ir a la bandeja
    </a>
  </header>

  <!-- -----------------------------------------------------------------------
    Estado de error de carga
  ----------------------------------------------------------------------- -->
  {#if summaryError && !summary}
    <EmptyState
      icon="fas fa-exclamation-circle"
      title="No se pudieron cargar las métricas"
      description={summaryError}
    />
  {:else if !summary}
    <!-- Skeleton de carga inicial (renderizado antes de que el server load resuelva) -->
    <div class="dashboard__skeleton" aria-busy="true" aria-label="Cargando métricas…">
      {#each Array(4) as _, i (i)}
        <div class="skeleton-card">
          <div
            class="skeleton skeleton--sm"
            style="width: 40%; margin-bottom: var(--op-space-2);"
          ></div>
          <div class="skeleton skeleton--lg" style="width: 60%;"></div>
        </div>
      {/each}
    </div>
  {:else}
    <!-- ===================================================================
      Contenido real del dashboard
    =================================================================== -->

    <!-- Indicadores clave (KPIs) -->
    <section class="dashboard__kpis" aria-labelledby="kpis-heading">
      <h2 id="kpis-heading" class="sr-only">Indicadores clave</h2>

      <!-- Total radicados -->
      <Card shadow={true}>
        <div class="kpi">
          <div class="kpi__icon kpi__icon--primary" aria-hidden="true">
            <i class="fas fa-file-alt"></i>
          </div>
          <div class="kpi__data">
            <span class="kpi__value">{summary.total.toLocaleString("es-CO")}</span>
            <span class="kpi__label">Total radicados</span>
          </div>
        </div>
      </Card>

      <!-- Por tipo de radicado -->
      {#each docTypeEntries as [type, count] (type)}
        <Card shadow={true}>
          <div class="kpi">
            <div class="kpi__icon kpi__icon--neutral" aria-hidden="true">
              <i class="fas fa-tag"></i>
            </div>
            <div class="kpi__data">
              <span class="kpi__value">{count.toLocaleString("es-CO")}</span>
              <span class="kpi__label">{DOC_TYPE_LABELS[type] ?? type}</span>
            </div>
          </div>
        </Card>
      {/each}
    </section>

    <!-- Desglose por estado -->
    <section class="dashboard__section" aria-labelledby="status-heading">
      <Card title="Radicados por estado">
        <h2 id="status-heading" class="sr-only">Radicados por estado</h2>
        {#if Object.keys(summary.by_status).length === 0}
          <p class="dashboard__empty-msg">Sin radicados registrados.</p>
        {:else}
          <ul class="status-list" role="list">
            {#each Object.entries(summary.by_status) as [status, count] (status)}
              <li class="status-list__item">
                <StatusChip
                  status={STATUS_CHIP_MAP[status] ?? "neutral"}
                  label={STATUS_LABELS[status] ?? status}
                  dot={true}
                />
                <div class="status-list__bar" role="presentation">
                  <div
                    class="status-list__fill"
                    style="width: {total > 0 ? Math.round((count / total) * 100) : 0}%"
                  ></div>
                </div>
                <span class="status-list__count" aria-label="{count} radicados">
                  {count.toLocaleString("es-CO")}
                </span>
              </li>
            {/each}
          </ul>
        {/if}
      </Card>
    </section>

    <!-- Top dependencias + Actividad mensual -->
    <div class="dashboard__grid-2">
      <!-- Top 5 dependencias -->
      <section aria-labelledby="dep-heading">
        <Card title="Top dependencias">
          <h2 id="dep-heading" class="sr-only">Top dependencias con más radicados</h2>
          {#if topDependencias.length === 0}
            <p class="dashboard__empty-msg">Sin datos de dependencia.</p>
          {:else}
            <ol class="dep-list" role="list">
              {#each topDependencias as [dep, count], i (dep)}
                <li class="dep-list__item">
                  <span class="dep-list__rank" aria-hidden="true">{i + 1}</span>
                  <span class="dep-list__name" title={dep}>{dep}</span>
                  <span class="dep-list__count">{count.toLocaleString("es-CO")}</span>
                </li>
              {/each}
            </ol>
          {/if}
        </Card>
      </section>

      <!-- Actividad mensual -->
      <section aria-labelledby="month-heading">
        <Card title="Actividad mensual (últimos 6 meses)">
          <h2 id="month-heading" class="sr-only">Actividad mensual de radicados</h2>
          {#if monthActivity.length === 0}
            <p class="dashboard__empty-msg">Sin actividad mensual registrada.</p>
          {:else}
            <ul class="month-bars" role="list" aria-label="Barras de actividad mensual">
              {#each monthActivity as { month, count } (month)}
                <li class="month-bars__item">
                  <div
                    class="month-bars__bar"
                    role="meter"
                    aria-label="{month}: {count} radicados"
                    aria-valuenow={count}
                    aria-valuemin={0}
                    aria-valuemax={maxMonthCount}
                    style="height: {Math.max(Math.round((count / maxMonthCount) * 80), 4)}px"
                  ></div>
                  <span class="month-bars__label">{month.slice(5)}</span>
                  <span class="month-bars__count">{count}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </Card>
      </section>
    </div>
  {/if}
</div>

<style lang="scss">
  .dashboard {
    padding: var(--op-space-6);
    max-width: 1100px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);
  }

  // ---- Encabezado ----
  .dashboard__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--op-space-4);
    flex-wrap: wrap;
    padding-bottom: var(--op-space-4);
    border-bottom: 1px solid var(--op-border);
  }

  .dashboard__title {
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-1);
  }

  .dashboard__welcome {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    margin: 0;
  }

  .tenant-chip {
    display: inline-block;
    background: var(--op-primary-light);
    color: var(--op-primary-dark);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
  }

  .dashboard__action-link {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-2);
    background: var(--op-primary);
    color: var(--op-surface);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    padding: var(--op-space-2) var(--op-space-4);
    border-radius: var(--op-radius);
    text-decoration: none;
    white-space: nowrap;
    transition: background var(--op-transition);

    &:hover {
      background: var(--op-primary-dark);
    }
    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  // ---- Skeleton ----
  .dashboard__skeleton {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: var(--op-space-4);
  }

  .skeleton-card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-5);
  }

  .skeleton {
    background: linear-gradient(90deg, var(--op-bg) 25%, var(--op-border) 50%, var(--op-bg) 75%);
    background-size: 200% 100%;
    animation: skeleton-shimmer 1.4s ease infinite;
    border-radius: var(--op-radius-sm);

    &--sm {
      height: 14px;
    }
    &--lg {
      height: 28px;
    }
  }

  @keyframes skeleton-shimmer {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }

  // ---- KPIs ----
  .dashboard__kpis {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: var(--op-space-4);
  }

  .kpi {
    display: flex;
    align-items: center;
    gap: var(--op-space-4);
    padding: var(--op-space-4);
  }

  .kpi__icon {
    width: 44px;
    height: 44px;
    border-radius: var(--op-radius);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    flex-shrink: 0;

    &--primary {
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
    }
    &--neutral {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      border: 1px solid var(--op-border);
    }
  }

  .kpi__data {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .kpi__value {
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    line-height: 1.1;
  }

  .kpi__label {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  // ---- Sección genérica ----
  .dashboard__section {
    width: 100%;
  }
  .dashboard__empty-msg {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    font-style: italic;
    margin: 0;
    padding: var(--op-space-2) 0;
  }

  // ---- Estado por chips + barra ----
  .status-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .status-list__item {
    display: flex;
    align-items: center;
    gap: var(--op-space-3);
  }

  .status-list__bar {
    flex: 1;
    height: 6px;
    background: var(--op-bg);
    border-radius: 3px;
    overflow: hidden;
  }

  .status-list__fill {
    height: 100%;
    background: var(--op-primary);
    border-radius: 3px;
    transition: width 0.4s ease;
    min-width: 2px;
  }

  .status-list__count {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text);
    width: 48px;
    text-align: right;
    flex-shrink: 0;
  }

  // ---- Grilla 2 columnas ----
  .dashboard__grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--op-space-5);

    @media (max-width: 640px) {
      grid-template-columns: 1fr;
    }
  }

  // ---- Top dependencias ----
  .dep-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    counter-reset: dep-counter;
  }

  .dep-list__item {
    display: grid;
    grid-template-columns: 24px 1fr auto;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-2) 0;
    border-bottom: 1px solid var(--op-border);

    &:last-child {
      border-bottom: none;
    }
  }

  .dep-list__rank {
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-bold);
    color: var(--op-text-secondary);
    text-align: center;
  }

  .dep-list__name {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .dep-list__count {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    flex-shrink: 0;
  }

  // ---- Barras mensuales ----
  .month-bars {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-3);
    height: 100px;
    padding-top: var(--op-space-2);
  }

  .month-bars__item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    flex: 1;
  }

  .month-bars__bar {
    width: 100%;
    min-height: 4px;
    background: var(--op-primary);
    border-radius: var(--op-radius-sm) var(--op-radius-sm) 0 0;
    transition: height 0.4s ease;
  }

  .month-bars__label {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    white-space: nowrap;
  }

  .month-bars__count {
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
  }
</style>
