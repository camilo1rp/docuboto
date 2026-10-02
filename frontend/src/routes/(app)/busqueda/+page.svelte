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
    - dest_dept_code (entero) — dependencia de RADICACIÓN
    - ubicacion_dept_code (entero) — dónde está AHORA el radicado (ADR-029)
    - page, size

  PATRÓN elegido (coherente con bandeja):
    - SSR inicial vía +page.server.js (deep-link/bookmarkable).
    - Nueva búsqueda: goto() actualiza la URL y re-ejecuta el server load sin
      recarga completa del navegador (SvelteKit navigation).
    - Paginación: usa /busqueda/api (proxy SSR, como bandeja usa /bandeja/api)
      para navegar entre páginas sin perder el estado del formulario.

  Fase 6 (cierre desfase API↔UI, E21): tres pestañas — Exacta (esta, sin
  cambios) · Semántica · Antecedentes — resueltas por `?tab=`, igual patrón
  que el conmutador de vista de /bandeja (`?vista=`). Cada pestaña sobrevive
  a un F5 y es enlazable: el `load` de +page.server.js resuelve el backend de
  la pestaña activa según sus propios parámetros (`sq`/`stopk` para
  semántica, `aq`/`atr`/`atopk` para antecedentes). Las búsquedas
  interactivas de esas dos pestañas usan los proxies dedicados
  (/busqueda/api/semantica, /busqueda/api/antecedentes,
  /busqueda/api/by-tracking/[trackingNumber]) para un estado de carga real
  sin recargar toda la pestaña, y sincronizan la URL con `history.replaceState`
  (sin volver a disparar el `load` SSR, que ya tiene los datos).

  Honestidad de la interfaz (no negociable, ver prompt de la Fase 6):
    1. Un resultado semántico/antecedente NO es una coincidencia exacta — se
       presenta como "similitud" con una explicación visible, nunca como un
       acierto textual.
    2. "Sin resultados" y "no se pudo buscar" son estados DISTINTOS y nunca se
       confunden (mismo error real que ya se corrigió en otras pantallas).
    3. El recorte por clearance (ACL fail-closed de knowledge-service) es
       INVISIBLE por diseño: nunca se anuncia "N resultados ocultos" ni nada
       equivalente — eso convertiría la búsqueda en un oráculo de existencia
       de documentos reservados.
-->
<script>
  import { onMount } from "svelte";
  import { browser } from "$app/environment";
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFecha } from "$lib/utils/fecha.js";
  import { notifyError } from "$lib/stores/notifications.js";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import DataTable from "$lib/components/ui/DataTable.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { ubicacionTexto, ubicacionActual } from "$lib/utils/ubicacionRadicado.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Pestañas — ?tab=exacta|semantica|antecedentes (deep-link, T-Fase6)
  // ---------------------------------------------------------------------------
  const TABS = [
    { id: "exacta", label: "Exacta", icon: "fas fa-magnifying-glass" },
    { id: "semantica", label: "Semántica", icon: "fas fa-brain" },
    { id: "antecedentes", label: "Antecedentes", icon: "fas fa-diagram-project" },
  ];
  $: activeTab = data.tab ?? "exacta";

  /** href de una pestaña: conserva TODOS los parámetros vigentes (cada pestaña
   * usa su propio espacio de nombres de query params, así que cambiar de
   * pestaña y volver no descarta lo que el operador ya había buscado). */
  function tabHref(targetTab) {
    const params = new URLSearchParams($page.url.searchParams);
    params.set("tab", targetTab);
    return `?${params.toString()}`;
  }

  /** Navegación por teclado del tablist (patrón APG, activación manual: las
   * flechas mueven el foco, Enter/clic activa la pestaña vía el <a> nativo). */
  function onTabsKeydown(event) {
    const idx = TABS.findIndex((t) => t.id === activeTab);
    let nextIdx = null;
    if (event.key === "ArrowRight") nextIdx = (idx + 1) % TABS.length;
    else if (event.key === "ArrowLeft") nextIdx = (idx - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") nextIdx = 0;
    else if (event.key === "End") nextIdx = TABS.length - 1;
    if (nextIdx === null) return;
    event.preventDefault();
    const nextId = TABS[nextIdx].id;
    document.getElementById(`busqueda-tab-${nextId}`)?.focus();
  }

  // ---------------------------------------------------------------------------
  // Estado del formulario (inicializado desde la URL vía server load)
  // ---------------------------------------------------------------------------
  let q = data.initialFilters?.q ?? "";
  let filterDocType = data.initialFilters?.docType ?? "";
  let filterStatus = data.initialFilters?.status ?? "";
  let filterDateFrom = data.initialFilters?.dateFrom ?? "";
  let filterDateTo = data.initialFilters?.dateTo ?? "";
  let filterDestDept = data.initialFilters?.destDeptCode ?? "";
  let filterUbicacion = data.initialFilters?.ubicacionDeptCode ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  const pageSize = 20;

  let showAdvanced = !!(
    filterDocType ||
    filterStatus ||
    filterDateFrom ||
    filterDateTo ||
    filterDestDept ||
    filterUbicacion
  );

  // ---------------------------------------------------------------------------
  // Estado de resultados
  // ---------------------------------------------------------------------------

  /** @type {any[]} */
  let results = [];
  let total = 0;
  let loading = false;

  // REACTIVO, no una asignación de una sola vez. Buscar navega con `goto()` a la
  // misma ruta con otros parámetros: SvelteKit reutiliza el componente y solo
  // sustituye `data`. Con `let results = data.results` —que es lo que había— el
  // valor quedaba congelado en el del primer render, así que quien entraba por
  // el menú y buscaba veía SIEMPRE «Sin resultados», con el servidor
  // devolviendo las coincidencias correctas. La búsqueda solo funcionaba
  // recargando la página entera con la consulta ya en la URL, que no es como
  // nadie la usa.
  //
  // Depende solo de `data`, así que paginar (`loadPage`, que muta `results` sin
  // navegar) no lo dispara y no pisa la página cargada.
  $: {
    results = data.results ?? [];
    total = data.total ?? 0;
  }
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
    { key: "ubicacion_dept", label: "Ubicación actual", width: "180px" },
    { key: "registered_at", label: "Fecha", width: "110px" },
    { key: "status", label: "Estado", width: "140px" },
    { key: "actions", label: "Acciones", width: "150px" },
  ];

  // La columna "Ubicación actual" se oculta en viewports estrechos — se
  // calcula en la FUENTE (no solo por CSS) para que el <th> generado por
  // DataTable y el <td> de la fila desaparezcan juntos y no se rompa la
  // asociación encabezado↔dato para lectores de pantalla (mismo breakpoint
  // que el resto del layout responsive de esta pantalla, 768px).
  // Catálogo de dependencias activas para el filtro «Ubicación actual» (nombre — código).
  // Sin catálogo (fallo de carga) el selector queda solo con «Cualquiera».
  $: ubicacionOptions = [
    { value: "", label: "Cualquiera" },
    ...(data.dependencias ?? [])
      .filter((d) => d?.codigo != null)
      .map((d) => ({ value: String(d.codigo), label: `${d.nombre} — ${d.codigo}` })),
  ];

  let isNarrowViewport = false;
  $: tableColumns = isNarrowViewport
    ? TABLE_COLUMNS.filter((col) => col.key !== "ubicacion_dept")
    : TABLE_COLUMNS;

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    return formatearFecha(iso);
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
    // ubicacion_dept_code (ADR-029): mismo criterio, entero positivo
    const ubicCode = parseInt(filterUbicacion, 10);
    if (Number.isFinite(ubicCode) && ubicCode > 0)
      params.set("ubicacion_dept_code", String(ubicCode));
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
    filterUbicacion = "";
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
      notifyError(mensajeError(err, "Error al cargar los resultados."));
    } finally {
      loading = false;
    }
  }

  $: hasActiveFilters = !!(
    filterDocType ||
    filterStatus ||
    filterDateFrom ||
    filterDateTo ||
    filterDestDept ||
    filterUbicacion
  );
  $: hasQuery = q.trim().length >= 2;
  $: searched = !!data.initialFilters?.q;

  // ---------------------------------------------------------------------------
  // Pestaña Semántica — POST /api/v1/knowledge/search vía /busqueda/api/semantica
  // ---------------------------------------------------------------------------
  let sq = data.semanticaInitial?.sq ?? "";
  let sTopK = data.semanticaInitial?.stopk ?? 5;
  /** @type {any[] | null} */
  let semanticaResults = data.semanticaResults ?? null;
  let semanticaError = data.semanticaError ?? null;
  let semanticaLoading = false;
  let semanticaQError = "";
  $: semanticaSearched = semanticaResults !== null || !!semanticaError;

  /** Sincroniza la URL sin re-disparar el `load` SSR (los datos ya están en
   * memoria) — `history.replaceState` nativo, no `goto()`. */
  function syncUrlReplace(paramSetter) {
    if (!browser) return;
    const params = new URLSearchParams($page.url.searchParams);
    paramSetter(params);
    const qs = params.toString();
    window.history.replaceState(window.history.state, "", qs ? `?${qs}` : "?");
  }

  async function buscarSemantica() {
    semanticaQError = "";
    const query = sq.trim();
    if (query.length < 1) {
      semanticaQError = "Introduce un texto para la búsqueda semántica.";
      return;
    }
    semanticaLoading = true;
    try {
      const res = await fetch("/busqueda/api/semantica", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, top_k: sTopK }),
      });
      const body = await res.json();
      if (!res.ok) {
        semanticaResults = null;
        semanticaError = mensajeError(body, "No se pudo completar la búsqueda semántica.");
      } else {
        semanticaResults = body.results ?? [];
        semanticaError = null;
      }
    } catch {
      semanticaResults = null;
      semanticaError = "No se pudo conectar al servicio de conocimiento.";
    } finally {
      semanticaLoading = false;
      syncUrlReplace((params) => {
        params.set("tab", "semantica");
        params.set("sq", query);
        params.set("stopk", String(sTopK));
      });
    }
  }

  /** Similitud (0–1, coseno) → banda cualitativa; nunca se presenta como un
   * porcentaje de "acierto" textual (Fase 6, honestidad de la interfaz #1). */
  function similitudLabel(score) {
    if (typeof score !== "number") return { label: "—", variant: "neutral" };
    if (score >= 0.75) return { label: "Similitud alta", variant: "success" };
    if (score >= 0.5) return { label: "Similitud media", variant: "warning" };
    return { label: "Similitud baja", variant: "neutral" };
  }

  function similitudPercent(score) {
    if (typeof score !== "number") return 0;
    return Math.round(Math.min(1, Math.max(0, score)) * 100);
  }

  // ---------------------------------------------------------------------------
  // Pestaña Antecedentes — POST /api/v1/knowledge/antecedentes vía
  // /busqueda/api/antecedentes; modo "radicado" primero resuelve el pivote con
  // /busqueda/api/by-tracking/[trackingNumber] para que el operador confirme
  // que es el documento correcto antes de lanzar la búsqueda de antecedentes.
  // ---------------------------------------------------------------------------
  /** @type {'texto'|'radicado'} */
  let aMode = data.antecedentesInitial?.atr ? "radicado" : "texto";
  let aq = data.antecedentesInitial?.aq ?? "";
  let atr = data.antecedentesInitial?.atr ?? "";
  let aTopK = data.antecedentesInitial?.atopk ?? 5;
  /** @type {{antecedentes: any[], advisory: string} | null} */
  let antecedentesData = data.antecedentesData ?? null;
  let antecedentesError = data.antecedentesError ?? null;
  let antecedentesLoading = false;
  let antecedentesFieldError = "";
  $: antecedentesSearched = antecedentesData !== null || !!antecedentesError;

  // Preview del radicado pivote (modo "radicado") — confirmación antes de
  // buscar, nunca se lanza la búsqueda de antecedentes a ciegas sobre un
  // número que el operador pudo haber tecleado mal.
  /** @type {any | null} */
  let pivotPreview = null;
  let pivotError = "";
  let pivotLoading = false;

  async function verificarPivote() {
    pivotError = "";
    pivotPreview = null;
    const tracking = atr.trim();
    if (!tracking) {
      pivotError = "Introduce un número de radicado.";
      return;
    }
    pivotLoading = true;
    try {
      const res = await fetch(`/busqueda/api/by-tracking/${encodeURIComponent(tracking)}`);
      const body = await res.json();
      if (!res.ok) {
        pivotError =
          res.status === 404
            ? "No se encontró ese radicado."
            : mensajeError(body, "No se pudo verificar el radicado.");
      } else {
        pivotPreview = body;
      }
    } catch {
      pivotError = "No se pudo conectar al servidor de documentos.";
    } finally {
      pivotLoading = false;
    }
  }

  async function buscarAntecedentes() {
    antecedentesFieldError = "";
    const query = aq.trim();
    const tracking = atr.trim();
    if (aMode === "texto" && query.length < 1) {
      antecedentesFieldError = "Introduce un texto para buscar antecedentes.";
      return;
    }
    if (aMode === "radicado" && !tracking) {
      antecedentesFieldError = "Introduce un número de radicado.";
      return;
    }
    antecedentesLoading = true;
    try {
      const payload =
        aMode === "texto" ? { query, top_k: aTopK } : { radicado_ref: tracking, top_k: aTopK };
      const res = await fetch("/busqueda/api/antecedentes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (!res.ok) {
        antecedentesData = null;
        antecedentesError =
          res.status === 404
            ? "No se encontró un antecedente indexado para ese radicado."
            : mensajeError(body, "No se pudo completar la búsqueda de antecedentes.");
      } else {
        antecedentesData = body;
        antecedentesError = null;
      }
    } catch {
      antecedentesData = null;
      antecedentesError = "No se pudo conectar al servicio de conocimiento.";
    } finally {
      antecedentesLoading = false;
      syncUrlReplace((params) => {
        params.set("tab", "antecedentes");
        params.set("atopk", String(aTopK));
        if (aMode === "texto") {
          params.set("aq", query);
          params.delete("atr");
        } else {
          params.set("atr", tracking);
          params.delete("aq");
        }
      });
    }
  }

  function cambiarModoAntecedentes(mode) {
    aMode = mode;
    antecedentesFieldError = "";
    pivotPreview = null;
    pivotError = "";
  }
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
    {#if activeTab === "exacta" && searched && !loading}
      <span class="busqueda__total" aria-live="polite">
        {total.toLocaleString("es-CO")} resultado{total !== 1 ? "s" : ""}
      </span>
    {/if}
  </header>

  <!-- -------------------------------------------------------------------------
    Pestañas — Exacta / Semántica / Antecedentes (T-Fase6, deep-link por ?tab=)
  ------------------------------------------------------------------------- -->
  <div
    class="busqueda__tabs"
    role="tablist"
    aria-label="Tipo de búsqueda"
    tabindex="-1"
    on:keydown={onTabsKeydown}
  >
    {#each TABS as t (t.id)}
      <a
        id="busqueda-tab-{t.id}"
        href={tabHref(t.id)}
        role="tab"
        aria-selected={activeTab === t.id}
        aria-controls="busqueda-panel-{t.id}"
        tabindex={activeTab === t.id ? 0 : -1}
        class="busqueda__tab"
        class:busqueda__tab--active={activeTab === t.id}
      >
        <i class={t.icon} aria-hidden="true"></i>
        {t.label}
      </a>
    {/each}
  </div>

  <!-- -------------------------------------------------------------------------
    Pestaña Exacta — sin cambios funcionales, solo envuelta como tabpanel
  ------------------------------------------------------------------------- -->
  <div
    id="busqueda-panel-exacta"
    role="tabpanel"
    aria-labelledby="busqueda-tab-exacta"
    hidden={activeTab !== "exacta"}
    class="busqueda__panel"
  >
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
                label="Ubicación actual"
                type="select"
                id="filter-ubicacion"
                bind:value={filterUbicacion}
                options={ubicacionOptions}
                hint="Dependencia donde está ahora el radicado. Filtra por el código de la dependencia: un radicado cuyo nombre coincide con varias dependencias no tiene código único y no aparece en este filtro."
              />
              <FormField
                label="Código de dependencia de radicación"
                type="number"
                id="filter-dest-dept"
                bind:value={filterDestDept}
                placeholder="Ej. 12"
                hint="Dependencia a la que iba dirigido al radicarlo; no cambia con el trámite."
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
                <!-- Sin columna «Ubicación actual» (móvil): sube bajo el asunto. -->
                {#if isNarrowViewport && ubicacionActual(row)}
                  <span class="subject-ubicacion" data-testid="fila-ubicacion-movil"
                    >En {ubicacionActual(row)}</span
                  >
                {/if}
              </td>
              {#if !isNarrowViewport}
                <td class="op-datatable__td search-table__td--dep">
                  {ubicacionTexto(row)}
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
                <!--
                  El enlace lleva el radicado consigo. Antes era `/bandeja` a
                  secas: quien buscaba un documento y pulsaba aquí aterrizaba en
                  la bandeja de SU dependencia, donde ese radicado casi nunca
                  está —la bandeja lista lo que la dependencia tramita, no lo que
                  se acaba de buscar— y sin ningún aviso de que el destino no era
                  el prometido. El `aria-label` sí decía «ver radicado X», así
                  que el enlace anunciaba una cosa y hacía otra.
                -->
                <a
                  href="/bandeja?radicado={encodeURIComponent(row.id ?? '')}"
                  class="action-link"
                  aria-label="Ver radicado {row.tracking_number} en la bandeja"
                  title="Ver detalle del radicado"
                >
                  <i class="fas fa-external-link-alt" aria-hidden="true"></i>
                  Ver detalle
                </a>
              </td>
            </svelte:fragment>
          </DataTable>
        </div>
      {/if}
    </section>
  </div>

  <!-- -------------------------------------------------------------------------
    Pestaña Semántica — /busqueda/api/semantica (POST /api/v1/knowledge/search)
  ------------------------------------------------------------------------- -->
  <div
    id="busqueda-panel-semantica"
    role="tabpanel"
    aria-labelledby="busqueda-tab-semantica"
    hidden={activeTab !== "semantica"}
    class="busqueda__panel"
  >
    <section class="busqueda__search-panel" aria-label="Formulario de búsqueda semántica">
      <form
        class="search-form"
        on:submit|preventDefault={buscarSemantica}
        aria-label="Buscar por significado"
      >
        <div class="search-form__main">
          <label for="semantica-q" class="search-form__label">
            Describe lo que buscas (no necesitas las palabras exactas)
          </label>
          <div class="search-form__input-row">
            <input
              id="semantica-q"
              type="search"
              class="search-form__input"
              class:search-form__input--error={!!semanticaQError}
              bind:value={sq}
              placeholder="Ej. quejas sobre demoras en el trámite de becas…"
              autocomplete="off"
              aria-describedby={semanticaQError ? "semantica-q-error" : "semantica-q-hint"}
            />
            <Button
              type="submit"
              variant="primary"
              disabled={semanticaLoading}
              aria-label="Buscar por significado"
            >
              <i class="fas fa-brain" aria-hidden="true"></i>
              <span>Buscar</span>
            </Button>
          </div>
          {#if semanticaQError}
            <p id="semantica-q-error" class="search-form__field-error" role="alert">
              {semanticaQError}
            </p>
          {:else}
            <p id="semantica-q-hint" class="search-form__hint">
              Encuentra documentos <strong>parecidos</strong> por significado, no por coincidencia literal
              de texto — útil cuando no recuerdas las palabras exactas del radicado.
            </p>
          {/if}
        </div>
        <div class="search-form__topk">
          <label for="semantica-topk" class="search-form__label-inline"
            >Cantidad de resultados</label
          >
          <select id="semantica-topk" class="search-form__select" bind:value={sTopK}>
            <option value={5}>5</option>
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
          </select>
        </div>
      </form>
    </section>

    <p class="sr-only" aria-live="polite" aria-atomic="true">
      {#if semanticaLoading}Buscando documentos parecidos…{:else if semanticaSearched && !semanticaError}{(
          semanticaResults ?? []
        ).length} resultado{(semanticaResults ?? []).length !== 1 ? "s" : ""} encontrado{(
          semanticaResults ?? []
        ).length !== 1
          ? "s"
          : ""}{/if}
    </p>
    <section
      class="busqueda__results"
      aria-label="Resultados de búsqueda semántica"
      aria-busy={semanticaLoading}
    >
      {#if semanticaLoading}
        <div class="busqueda__loading">
          <Loader type="skeleton" lines={4} label="Buscando documentos parecidos…" />
        </div>
      {:else if semanticaError}
        <EmptyState
          icon="fas fa-exclamation-circle"
          title="No se pudo buscar"
          description={semanticaError}
        >
          <Button variant="primary" on:click={buscarSemantica}>Reintentar</Button>
        </EmptyState>
      {:else if !semanticaSearched}
        <EmptyState
          icon="fas fa-brain"
          title="Busca por significado"
          description="Describe el contenido que buscas; no hace falta que uses las palabras exactas del documento."
        />
      {:else if (semanticaResults ?? []).length === 0}
        <EmptyState
          icon="fas fa-file-search"
          title="Sin resultados"
          description="No se encontraron documentos parecidos a tu búsqueda."
        />
      {:else}
        <p class="busqueda__advisory">
          <i class="fas fa-circle-info" aria-hidden="true"></i>
          <span>
            Estos son documentos <strong>parecidos</strong> por significado, no coincidencias exactas
            de texto. Verifica cada uno contra el radicado original antes de darlo por válido.
          </span>
        </p>
        <ul class="result-cards">
          {#each semanticaResults as hit (hit.id)}
            {@const sim = similitudLabel(hit.score)}
            <li class="result-card">
              <div class="result-card__head">
                <span class="result-card__ref">{hit.source_ref}</span>
                <StatusChip status={sim.variant} label={sim.label} dot={true} />
              </div>
              <p class="result-card__snippet">{hit.texto}</p>
              <div class="result-card__score" title="Similitud vectorial, no coincidencia textual">
                <div class="result-card__score-track">
                  <div
                    class="result-card__score-fill"
                    style="width: {similitudPercent(hit.score)}%"
                  ></div>
                </div>
                <span class="result-card__score-label"
                  >{similitudPercent(hit.score)}% de similitud</span
                >
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </div>

  <!-- -------------------------------------------------------------------------
    Pestaña Antecedentes — /busqueda/api/antecedentes (POST /api/v1/knowledge/antecedentes)
    y /busqueda/api/by-tracking/[trackingNumber] (preview del radicado pivote)
  ------------------------------------------------------------------------- -->
  <div
    id="busqueda-panel-antecedentes"
    role="tabpanel"
    aria-labelledby="busqueda-tab-antecedentes"
    hidden={activeTab !== "antecedentes"}
    class="busqueda__panel"
  >
    <section class="busqueda__search-panel" aria-label="Formulario de búsqueda de antecedentes">
      <form
        class="search-form"
        on:submit|preventDefault={buscarAntecedentes}
        aria-label="Buscar antecedentes"
      >
        <fieldset class="search-form__mode">
          <legend class="sr-only">Modo de búsqueda de antecedentes</legend>
          <div class="search-form__mode-toggle" role="radiogroup" aria-label="Buscar por">
            <button
              type="button"
              class="mode-btn"
              class:mode-btn--active={aMode === "texto"}
              role="radio"
              aria-checked={aMode === "texto"}
              on:click={() => cambiarModoAntecedentes("texto")}
            >
              Por texto
            </button>
            <button
              type="button"
              class="mode-btn"
              class:mode-btn--active={aMode === "radicado"}
              role="radio"
              aria-checked={aMode === "radicado"}
              on:click={() => cambiarModoAntecedentes("radicado")}
            >
              Por número de radicado
            </button>
          </div>
        </fieldset>

        {#if aMode === "texto"}
          <div class="search-form__main">
            <label for="antecedentes-q" class="search-form__label">Texto de búsqueda</label>
            <input
              id="antecedentes-q"
              type="search"
              class="search-form__input"
              class:search-form__input--error={!!antecedentesFieldError}
              bind:value={aq}
              placeholder="Ej. renovación de convenio interadministrativo…"
              autocomplete="off"
            />
          </div>
        {:else}
          <div class="search-form__main">
            <label for="antecedentes-atr" class="search-form__label">Número de radicado</label>
            <div class="search-form__input-row">
              <input
                id="antecedentes-atr"
                type="text"
                class="search-form__input"
                class:search-form__input--error={!!antecedentesFieldError}
                bind:value={atr}
                placeholder="Ej. 2026-ICETEX-E-000123"
                autocomplete="off"
              />
              <Button
                type="button"
                variant="secondary"
                disabled={pivotLoading}
                on:click={verificarPivote}
              >
                {pivotLoading ? "Verificando…" : "Verificar"}
              </Button>
            </div>
            {#if pivotError}
              <p class="search-form__field-error" role="alert">{pivotError}</p>
            {:else if pivotPreview}
              <div class="pivot-preview" role="status">
                <i class="fas fa-check-circle" aria-hidden="true"></i>
                <span class="doc-type-badge doc-type-badge--{pivotPreview.doc_type?.toLowerCase()}">
                  {DOC_TYPE_LABELS[pivotPreview.doc_type] ?? pivotPreview.doc_type ?? "—"}
                </span>
                <span class="pivot-preview__subject" title={pivotPreview.subject}
                  >{pivotPreview.subject}</span
                >
              </div>
            {/if}
          </div>
        {/if}

        {#if antecedentesFieldError}
          <p class="search-form__field-error" role="alert">{antecedentesFieldError}</p>
        {/if}

        <div class="search-form__topk">
          <label for="antecedentes-topk" class="search-form__label-inline"
            >Cantidad de resultados</label
          >
          <select id="antecedentes-topk" class="search-form__select" bind:value={aTopK}>
            <option value={5}>5</option>
            <option value={10}>10</option>
            <option value={20}>20</option>
          </select>
        </div>

        <div class="search-form__filters-actions">
          <Button type="submit" variant="primary" disabled={antecedentesLoading}>
            <i class="fas fa-diagram-project" aria-hidden="true"></i>
            <span>Buscar antecedentes</span>
          </Button>
        </div>
      </form>
    </section>

    <p class="sr-only" aria-live="polite" aria-atomic="true">
      {#if antecedentesLoading}Buscando antecedentes…{:else if antecedentesSearched && !antecedentesError}{(
          antecedentesData?.antecedentes ?? []
        ).length} antecedente{(antecedentesData?.antecedentes ?? []).length !== 1 ? "s" : ""} encontrado{(
          antecedentesData?.antecedentes ?? []
        ).length !== 1
          ? "s"
          : ""}{/if}
    </p>
    <section
      class="busqueda__results"
      aria-label="Resultados de antecedentes"
      aria-busy={antecedentesLoading}
    >
      {#if antecedentesLoading}
        <div class="busqueda__loading">
          <Loader type="skeleton" lines={4} label="Buscando antecedentes…" />
        </div>
      {:else if antecedentesError}
        <EmptyState
          icon="fas fa-exclamation-circle"
          title="No se pudo buscar"
          description={antecedentesError}
        >
          <Button variant="primary" on:click={buscarAntecedentes}>Reintentar</Button>
        </EmptyState>
      {:else if !antecedentesSearched}
        <EmptyState
          icon="fas fa-diagram-project"
          title="Busca antecedentes"
          description="Busca por texto o a partir de un radicado existente para encontrar trámites relacionados."
        />
      {:else if (antecedentesData?.antecedentes ?? []).length === 0}
        <EmptyState
          icon="fas fa-file-search"
          title="Sin antecedentes"
          description="No se encontraron antecedentes relacionados."
        />
      {:else}
        <p class="busqueda__advisory">
          <i class="fas fa-circle-info" aria-hidden="true"></i>
          {antecedentesData.advisory}
        </p>
        <ul class="result-cards">
          {#each antecedentesData.antecedentes as ant, i (ant.source_ref + i)}
            {@const sim = similitudLabel(ant.score)}
            <li class="result-card">
              <div class="result-card__head">
                <span class="result-card__ref">{ant.source_ref}</span>
                <StatusChip status={sim.variant} label={sim.label} dot={true} />
              </div>
              <p class="result-card__snippet">{ant.snippet}</p>
              <div class="result-card__score" title="Similitud vectorial, no coincidencia textual">
                <div class="result-card__score-track">
                  <div
                    class="result-card__score-fill"
                    style="width: {similitudPercent(ant.score)}%"
                  ></div>
                </div>
                <span class="result-card__score-label"
                  >{similitudPercent(ant.score)}% de similitud</span
                >
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </div>
</div>

<style lang="scss">
  // ---------------------------------------------------------------------------
  // Layout principal
  // ---------------------------------------------------------------------------

  .busqueda {
    padding: var(--op-space-6);
    max-width: var(--op-content-max);
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
    font-family: var(--op-font-family-mono);
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

  // Ubicación actual bajo el asunto (solo se renderiza sin la columna, en móvil)
  .subject-ubicacion {
    display: block;
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
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
  // Pestañas (Exacta / Semántica / Antecedentes)
  // ---------------------------------------------------------------------------

  .busqueda__tabs {
    display: flex;
    gap: var(--op-space-2);
    border-bottom: 1px solid var(--op-border);
    flex-wrap: wrap;
  }

  .busqueda__tab {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-3) var(--op-space-4);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    text-decoration: none;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    min-height: 44px;

    &:hover {
      color: var(--op-primary-dark);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
      border-radius: var(--op-radius-sm);
    }

    &--active {
      color: var(--op-primary-dark);
      border-bottom-color: var(--op-primary-dark);
    }
  }

  .busqueda__panel {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);
  }

  // ---------------------------------------------------------------------------
  // Formulario — extensiones para semántica/antecedentes
  // ---------------------------------------------------------------------------

  .search-form__hint {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    margin: 0;
  }

  .search-form__topk {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
  }

  .search-form__label-inline {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text);
  }

  .search-form__select {
    height: 36px;
    padding: 0 var(--op-space-2);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-bg);
    color: var(--op-text);
    font-size: var(--op-font-sm);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  .search-form__mode {
    border: none;
    padding: 0;
    margin: 0;
  }

  .search-form__mode-toggle {
    display: inline-flex;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    overflow: hidden;
    width: fit-content;
  }

  .mode-btn {
    padding: var(--op-space-2) var(--op-space-4);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    background: var(--op-surface);
    border: none;
    cursor: pointer;
    min-height: 40px;

    &:not(:last-child) {
      border-right: 1px solid var(--op-border);
    }

    &:hover {
      background: var(--op-bg);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }

    &--active {
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
    }
  }

  .pivot-preview {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-2) var(--op-space-3);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    color: var(--op-text);

    i {
      color: var(--op-primary-dark);
    }

    &__subject {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .busqueda__loading {
    padding: var(--op-space-5);
  }

  // Aviso de honestidad — nunca insinuar exactitud sobre resultados derivados
  // (Fase 6, criterio duro #1/#2). Fondo/texto tomados directo de los tokens
  // info (op-info-fg SÍ pasa AA sobre op-info-bg; op-text-secondary no).
  .busqueda__advisory {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    padding: var(--op-space-3) var(--op-space-4);
    background: var(--op-info-bg);
    color: var(--op-info-fg);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    margin: 0 0 var(--op-space-4);

    i {
      margin-top: 2px;
      flex-shrink: 0;
    }
  }

  // ---------------------------------------------------------------------------
  // Tarjetas de resultado (semántica/antecedentes) — sin tabla: cada resultado
  // es un documento "parecido", no una fila de coincidencia exacta.
  // ---------------------------------------------------------------------------

  .result-cards {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .result-card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__ref {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
      color: var(--op-primary-dark);
      background: var(--op-primary-light);
      padding: 2px var(--op-space-2);
      border-radius: var(--op-radius-sm);
    }

    &__snippet {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      margin: 0;
      line-height: 1.5;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    &__score {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
    }

    &__score-track {
      flex: 1;
      height: 6px;
      border-radius: var(--op-radius-full);
      background: var(--op-bg);
      overflow: hidden;
      max-width: 200px;
    }

    &__score-fill {
      height: 100%;
      background: var(--op-primary);
      border-radius: var(--op-radius-full);
    }

    &__score-label {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      white-space: nowrap;
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

    .busqueda__tabs {
      overflow-x: auto;
      // `position: relative` NO es cosmético: sin bloque contenedor propio, un
      // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
      // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
      // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
      position: relative;
      flex-wrap: nowrap;
    }

    .busqueda__tab {
      white-space: nowrap;
    }
  }
</style>
