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

  REDISEÑO (Sistema de Diseño §10): el tablero es FIJO y muestra exclusivamente
  cuatro módulos centrados en el usuario, en este orden de prioridad (también
  el orden de apilado en móvil):

    01 Radicados pendientes — lo que requiere acción inmediata (primer plano).
    02 Actividad reciente   — continuidad: dónde se quedó el usuario.
    03 Alertas              — vencimientos, con jerarquía clara (icono + texto,
                               nunca solo color).
    04 Favoritos            — accesos configurables por el usuario mismo.

  Las métricas agregadas del tenant (antes protagonistas) bajan a una sección
  secundaria colapsable, CERRADA por defecto (progressive disclosure) — no
  compiten por la atención con los módulos personales.

  Datos: ver +page.server.js. El módulo 03 "Alertas" usa `due_date` de los
  radicados abiertos como proxy real (RF-RAD-04) — no hay hoy un endpoint de
  alertas dedicado; ver la nota de alcance allí y el followup registrado.
-->
<script>
  import { onMount } from "svelte";
  import { browser } from "$app/environment";
  import { navigating } from "$app/stores";
  import { sessionUser, tenantSlug, permissions, roles } from "$lib/stores/auth.js";
  import { formatearFecha } from "$lib/utils/fecha.js";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { favoriteHrefs, initFavorites, pickFavoriteNavItems } from "$lib/stores/favorites.js";
  import { NAV_ITEMS, filterVisibleNavItems } from "$lib/utils/navItems.js";
  import Card from "$lib/components/ui/Card.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  onMount(() => {
    setBreadcrumbs([{ label: "Panel principal" }]);
  });

  // ---------------------------------------------------------------------------
  // Datos de los cuatro módulos (server load, T-07)
  // ---------------------------------------------------------------------------
  $: pendientes = data.pendientes;
  $: alertas = data.alertas;
  $: recientes = data.recientes;
  $: summary = data.summary;
  $: summaryError = data.summaryError;

  // Muestra un indicador de carga breve mientras SvelteKit re-ejecuta el load
  // de esta misma ruta (p. ej. al volver a "Panel principal" desde el breadcrumb).
  $: isReloading = $navigating?.to?.url?.pathname === "/dashboard";

  // Etiquetas legibles
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
  // El módulo 01 lista PASOS del flujo, no radicados: sus estados son otros.
  const STEP_LABELS = { pending: "Pendiente", in_progress: "En trámite" };
  const STEP_CHIP_MAP = { pending: "pending", in_progress: "active" };

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    return formatearFecha(iso, { estilo: "medio" });
  }

  // ---------------------------------------------------------------------------
  // Módulo 04 — Favoritos (§10): MISMA API que SidebarNav, no duplicada
  // (navItems.js + favorites.js). Se re-inicializa aquí también (idempotente)
  // para no depender del orden de montaje frente a SidebarNav.
  // ---------------------------------------------------------------------------
  let favoritesInitKey = null;
  $: if (browser) {
    const nextKey = `${$tenantSlug ?? ""}:${$sessionUser?.sub ?? ""}`;
    if (nextKey !== favoritesInitKey) {
      favoritesInitKey = nextKey;
      initFavorites($tenantSlug, $sessionUser?.sub);
    }
  }

  $: visibleNavItems = filterVisibleNavItems(NAV_ITEMS, $permissions, $roles);
  $: favoriteItems = pickFavoriteNavItems(visibleNavItems, $favoriteHrefs);

  // ---------------------------------------------------------------------------
  // Sección secundaria: métricas agregadas (progressive disclosure, cerrada
  // por defecto)
  // ---------------------------------------------------------------------------
  let metricsOpen = false;

  $: total = summary?.total ?? 0;
  $: docTypeEntries = summary
    ? Object.entries(summary.by_doc_type ?? {}).filter(([, v]) => v > 0)
    : [];
  $: topDependencias = summary
    ? Object.entries(summary.by_dependencia ?? {})
        .sort(([, a], [, b]) => b - a)
        .slice(0, 5)
    : [];
  $: monthActivity = summary
    ? [...(summary.by_month ?? [])].sort((a, b) => a.month.localeCompare(b.month)).slice(-6)
    : [];
  $: maxMonthCount = monthActivity.length ? Math.max(...monthActivity.map((m) => m.count), 1) : 1;
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

  {#if isReloading}
    <div class="dashboard__reloading">
      <Loader type="spinner" size="sm" label="Actualizando panel…" />
      <span aria-hidden="true">Actualizando…</span>
    </div>
  {/if}

  <!-- -----------------------------------------------------------------------
    Módulos personales (§10) — contenido principal, visible sin scroll.
    Orden de prioridad = orden en el DOM = orden de apilado en móvil.
  ----------------------------------------------------------------------- -->
  <div class="dashboard__modules">
    <!-- ===================== 01 Radicados pendientes ===================== -->
    <section
      class="dashboard__module dashboard__module--pendientes"
      aria-labelledby="pendientes-heading"
    >
      <Card>
        <svelte:fragment slot="header">
          <h2 id="pendientes-heading" class="module-title">
            <i class="fas fa-inbox module-title__icon" aria-hidden="true"></i>
            Radicados pendientes
          </h2>
          {#if pendientes.total > 0}
            <span class="module-count" aria-label="{pendientes.total} radicados pendientes">
              {pendientes.total.toLocaleString("es-CO")}
            </span>
          {/if}
        </svelte:fragment>

        {#if pendientes.error}
          <EmptyState
            icon="fas fa-exclamation-circle"
            title="No se pudieron cargar los pendientes"
            description={pendientes.error}
          />
        {:else if pendientes.items.length === 0}
          <EmptyState
            icon="fas fa-check-circle"
            title="Vas al día"
            description="No tienes radicados pendientes de trámite en este momento."
          />
        {:else}
          <ul class="doc-list" role="list">
            {#each pendientes.items as doc (doc.id)}
              <li class="doc-list__item">
                <div class="doc-list__main">
                  <span class="doc-list__tracking">{doc.tracking_number}</span>
                  <span class="doc-list__subject" title={doc.detalle}>{doc.detalle}</span>
                </div>
                <div class="doc-list__meta">
                  <StatusChip
                    status={STEP_CHIP_MAP[doc.status] ?? "neutral"}
                    label={STEP_LABELS[doc.status] ?? doc.status}
                    dot={true}
                  />
                  {#if doc.due_date}
                    <span class="doc-list__due">Vence {formatDate(doc.due_date)}</span>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
        {/if}

        <svelte:fragment slot="footer">
          <!-- Al tablero, no a `?status=distributed`: el flujo no lleva el radicado
               a ese estado, así que ese filtro abría la bandeja vacía. -->
          <a href="/bandeja" class="module-link">
            Ver toda la bandeja
            <i class="fas fa-arrow-right" aria-hidden="true"></i>
          </a>
        </svelte:fragment>
      </Card>
    </section>

    <!-- ===================== 02 Actividad reciente ===================== -->
    <section
      class="dashboard__module dashboard__module--recientes"
      aria-labelledby="recientes-heading"
    >
      <Card>
        <svelte:fragment slot="header">
          <h2 id="recientes-heading" class="module-title">
            <i class="fas fa-clock-rotate-left module-title__icon" aria-hidden="true"></i>
            Actividad reciente
          </h2>
        </svelte:fragment>

        {#if recientes.error}
          <EmptyState
            icon="fas fa-exclamation-circle"
            title="No se pudo cargar la actividad reciente"
            description={recientes.error}
          />
        {:else if recientes.items.length === 0}
          <EmptyState
            icon="fas fa-clock-rotate-left"
            title="Sin actividad reciente"
            description="Los últimos radicados que registres o consultes aparecerán aquí."
          />
        {:else}
          <ul class="doc-list" role="list">
            {#each recientes.items as doc (doc.id)}
              <li class="doc-list__item">
                <div class="doc-list__main">
                  <span class="doc-list__tracking">{doc.tracking_number}</span>
                  <span class="doc-list__subject" title={doc.subject}>{doc.subject}</span>
                </div>
                <div class="doc-list__meta">
                  <StatusChip
                    status={STATUS_CHIP_MAP[doc.status] ?? "neutral"}
                    label={STATUS_LABELS[doc.status] ?? doc.status}
                    dot={true}
                  />
                  <span class="doc-list__date">{formatDate(doc.updated_at)}</span>
                </div>
              </li>
            {/each}
          </ul>
        {/if}
      </Card>
    </section>

    <!-- ===================== 03 Alertas ===================== -->
    <section class="dashboard__module dashboard__module--alertas" aria-labelledby="alertas-heading">
      <Card>
        <svelte:fragment slot="header">
          <h2 id="alertas-heading" class="module-title">
            <i class="fas fa-triangle-exclamation module-title__icon" aria-hidden="true"></i>
            Alertas
          </h2>
        </svelte:fragment>

        {#if alertas.error}
          <EmptyState
            icon="fas fa-exclamation-circle"
            title="No se pudieron cargar las alertas"
            description={alertas.error}
          />
        {:else if alertas.items.length === 0}
          <EmptyState
            icon="fas fa-bell-slash"
            title="Sin vencimientos próximos"
            description="No hay radicados con fecha límite de respuesta cercana."
          />
        {:else}
          <ul class="alert-list" role="list">
            {#each alertas.items as doc (doc.id)}
              {@const vencido = doc.diasRestantes < 0}
              {@const proximo = !vencido && doc.diasRestantes <= 3}
              <li
                class="alert-list__item"
                class:alert-list__item--critical={vencido}
                class:alert-list__item--warning={proximo}
              >
                <span class="alert-list__icon" aria-hidden="true">
                  <i class={vencido ? "fas fa-circle-exclamation" : "fas fa-clock"}></i>
                </span>
                <div class="alert-list__body">
                  <span class="alert-list__tracking">{doc.tracking_number}</span>
                  <span class="alert-list__subject" title={doc.subject}>{doc.subject}</span>
                </div>
                <span class="alert-list__due">
                  {#if vencido}
                    Vencido hace {Math.abs(doc.diasRestantes)}
                    {Math.abs(doc.diasRestantes) === 1 ? "día" : "días"}
                  {:else if doc.diasRestantes === 0}
                    Vence hoy
                  {:else}
                    Vence en {doc.diasRestantes} {doc.diasRestantes === 1 ? "día" : "días"}
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
        {/if}
      </Card>
    </section>

    <!-- ===================== 04 Favoritos ===================== -->
    <section
      class="dashboard__module dashboard__module--favoritos"
      aria-labelledby="favoritos-heading"
    >
      <Card>
        <svelte:fragment slot="header">
          <h2 id="favoritos-heading" class="module-title">
            <i class="fas fa-star module-title__icon" aria-hidden="true"></i>
            Favoritos
          </h2>
        </svelte:fragment>

        {#if favoriteItems.length === 0}
          <EmptyState
            icon="fas fa-star"
            title="Sin accesos fijados"
            description="Fija tus pantallas de uso frecuente desde el icono de estrella en el menú lateral."
          />
        {:else}
          <ul class="favorites-grid" role="list">
            {#each favoriteItems as item (item.href)}
              <li>
                <a href={item.href} class="favorites-grid__item">
                  <i class="{item.icon} favorites-grid__icon" aria-hidden="true"></i>
                  <span>{item.label}</span>
                </a>
              </li>
            {/each}
          </ul>
        {/if}
      </Card>
    </section>
  </div>

  <!-- -----------------------------------------------------------------------
    Sección secundaria: métricas agregadas del tenant (progressive disclosure)
    Espejo del RBAC (ADR-013/020): mismo permiso que gatea la entrada de menú
    "Reportes" (navItems.js). Si el usuario no lo tiene, el gateway rechazaría
    GET /reports/radicados con 403 — la UI no debe ofrecer lo que la API niega.
  ----------------------------------------------------------------------- -->
  {#if $permissions.includes("SGD_PERM_ESTADISTICA")}
    <section class="dashboard__metrics" aria-labelledby="metrics-heading">
      <h2 id="metrics-heading" class="dashboard__metrics-heading">
        <Button
          variant="ghost"
          icon={metricsOpen ? "fas fa-chevron-down" : "fas fa-chevron-right"}
          on:click={() => (metricsOpen = !metricsOpen)}
          aria-expanded={metricsOpen}
          aria-controls="metrics-panel"
        >
          Métricas del tenant
        </Button>
      </h2>

      <!--
        El panel se mantiene SIEMPRE en el árbol y se oculta con `hidden`, en vez
        de existir solo desplegado. El botón lo declara con `aria-controls`, y un
        `aria-controls` que apunta a un identificador inexistente no es un
        detalle de purismo: el lector de pantalla anuncia un control que gobierna
        algo que no puede encontrar, así que la persona no tiene forma de saltar
        a lo que acaba de abrir. `hidden` lo saca del árbol de accesibilidad
        igual de bien cuando está plegado.
      -->
      <div id="metrics-panel" class="dashboard__metrics-panel" hidden={!metricsOpen}>
        {#if metricsOpen}
          {#if summaryError && !summary}
            <EmptyState
              icon="fas fa-exclamation-circle"
              title="No se pudieron cargar las métricas"
              description={summaryError}
            />
          {:else if !summary}
            <div class="dashboard__skeleton" aria-busy="true" aria-label="Cargando métricas…">
              <Loader type="skeleton" lines={4} label="Cargando métricas…" />
            </div>
          {:else}
            <!-- Indicadores clave (KPIs) -->
            <!--
              `role="group"` y no `role="list"`: una lista promete elementos de
              lista, y los hijos aquí son `Card` —que renderiza un `<div>` y no
              acepta rol—. Un `role="list"` sin `listitem` dentro es peor que no
              declarar nada: el lector anuncia una lista y luego no encuentra sus
              elementos, así que no se puede navegar por ella. `group` con
              etiqueta dice lo que de verdad son: cuatro indicadores que van
              juntos y tienen un nombre.
            -->
            <div class="dashboard__kpis" role="group" aria-label="Indicadores clave">
              <Card shadow={false}>
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

              {#each docTypeEntries as [type, count] (type)}
                <Card shadow={false}>
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
            </div>

            <!-- Desglose por estado -->
            <Card title="Radicados por estado" shadow={false}>
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

            <!-- Top dependencias + Actividad mensual -->
            <div class="dashboard__grid-2">
              <Card title="Top dependencias" shadow={false}>
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

              <Card title="Actividad mensual (últimos 6 meses)" shadow={false}>
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
            </div>
          {/if}
        {/if}
      </div>
    </section>
  {/if}
</div>

<style lang="scss">
  .dashboard {
    padding: var(--op-space-6);
    max-width: var(--op-content-max);
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
    background: var(--op-primary-dark);
    color: var(--op-surface);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    padding: var(--op-space-2) var(--op-space-4);
    border-radius: var(--op-radius);
    text-decoration: none;
    white-space: nowrap;
    transition: background var(--op-transition);

    &:hover {
      background: var(--op-brand-700);
    }
    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  .dashboard__reloading {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
  }

  // ---- Módulos personales (§10) ----
  .dashboard__modules {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--op-space-5);

    @media (max-width: 900px) {
      grid-template-columns: 1fr;
    }
  }

  // 01 Pendientes en primer plano: fila completa arriba.
  .dashboard__module--pendientes {
    grid-column: 1 / -1;
    order: 1;
  }
  .dashboard__module--recientes {
    order: 2;
  }
  .dashboard__module--alertas {
    order: 3;
  }
  .dashboard__module--favoritos {
    grid-column: 1 / -1;
    order: 4;
  }

  @media (max-width: 900px) {
    // En móvil, orden de prioridad = orden de apilado: 01, 02, 03, 04.
    .dashboard__module--pendientes {
      order: 1;
    }
    .dashboard__module--recientes {
      order: 2;
    }
    .dashboard__module--alertas {
      order: 3;
    }
    .dashboard__module--favoritos {
      order: 4;
    }
  }

  .module-title {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-md);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
    flex: 1;

    &__icon {
      color: var(--op-primary);
      font-size: 0.9em;
    }
  }

  .module-count {
    background: var(--op-primary-light);
    color: var(--op-primary-dark);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-bold);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-full, 999px);
  }

  .module-link {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  // ---- Listas de radicados (pendientes / recientes) ----
  .doc-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .doc-list__item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--op-space-3);
    flex-wrap: wrap;
    padding-bottom: var(--op-space-3);
    border-bottom: 1px solid var(--op-border);

    &:last-child {
      border-bottom: none;
      padding-bottom: 0;
    }
  }

  .doc-list__main {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .doc-list__tracking {
    // --op-font-mono es un TAMAÑO (12px), no una familia: usar el stack real
    // hasta que exista un token de familia monoespaciada en _tokens.scss.
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }

  .doc-list__subject {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 320px;
  }

  .doc-list__meta {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    flex-shrink: 0;
  }

  .doc-list__due,
  .doc-list__date {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    white-space: nowrap;
  }

  // ---- Alertas: jerarquía por icono + texto, no solo color ----
  .alert-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .alert-list__item {
    display: flex;
    align-items: center;
    gap: var(--op-space-3);
    padding: var(--op-space-2) var(--op-space-3);
    border-radius: var(--op-radius-sm);
    border: 1px solid var(--op-border);

    &--warning {
      border-color: var(--op-warning);
      background: var(--op-warning-bg);
    }
    &--critical {
      border-color: var(--op-error);
      background: var(--op-error-bg);
    }
  }

  .alert-list__icon {
    flex-shrink: 0;
    color: var(--op-text-secondary);
    font-size: 1rem;
  }
  .alert-list__item--warning .alert-list__icon {
    color: var(--op-warning-fg);
  }
  .alert-list__item--critical .alert-list__icon {
    color: var(--op-error-fg);
  }

  .alert-list__body {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    flex: 1;
  }

  .alert-list__tracking {
    // --op-font-mono es un TAMAÑO (12px), no una familia: usar el stack real
    // hasta que exista un token de familia monoespaciada en _tokens.scss.
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }
  .alert-list__item--warning .alert-list__tracking {
    color: var(--op-warning-fg);
  }
  .alert-list__item--critical .alert-list__tracking {
    color: var(--op-error-fg);
  }

  .alert-list__subject {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .alert-list__due {
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .alert-list__item--warning .alert-list__due {
    color: var(--op-warning-fg);
  }
  .alert-list__item--critical .alert-list__due {
    color: var(--op-error-fg);
  }

  // ---- Favoritos ----
  .favorites-grid {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: var(--op-space-3);
  }

  .favorites-grid__item {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-3);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    color: var(--op-text);
    text-decoration: none;
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    min-height: 44px;
    transition:
      background var(--op-transition),
      border-color var(--op-transition);

    &:hover {
      background: var(--op-primary-light);
      border-color: var(--op-primary);
    }
    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  .favorites-grid__icon {
    color: var(--op-primary);
  }

  // ---- Sección secundaria: métricas ----
  .dashboard__metrics {
    padding-top: var(--op-space-4);
    border-top: 1px solid var(--op-border);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .dashboard__metrics-heading {
    margin: 0;
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
  }

  .dashboard__metrics-panel {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);
  }

  .dashboard__skeleton {
    padding: var(--op-space-5);
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

  // Respeta prefers-reduced-motion (Sistema de Diseño): las transiciones de
  // ancho/alto de las barras arriba ya usan --op-transition/duraciones cortas
  // en el resto de la hoja; la regla global vive en styles/global.scss.
</style>
