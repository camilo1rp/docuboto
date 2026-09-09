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
  DataTable.svelte — Tabla paginada con soporte para X-Total-Count, ampliada
  con las 8 capacidades del Sistema de Diseño §12. TODO lo nuevo es opt-in por
  prop: con los valores por defecto el comportamiento es IDÉNTICO al de antes
  (compatibilidad hacia atrás obligatoria — otras pantallas ya consumen esto).

  Props existentes (sin cambios):
    columns      Array<{ key: string; label: string; width?: string }>
    rows         Array<Record<string, any>>
    totalCount   number   — valor de X-Total-Count del backend
    page         number   — página actual (1-indexed), bindable
    pageSize     number   — registros por página (default 20)
    loading      boolean  — muestra skeleton
    caption      string   — descripción accesible de la tabla

  Props nuevas de columns[] (todas opcionales):
    sortable     boolean            — habilita el orden en esta columna (con enableSort)
    filterType   'text'|'select'|'dateRange'  — habilita el filtro en esta columna (con enableFilters)
    filterOptions Array<{value,label}>        — opciones para filterType:'select'
    editable     boolean            — habilita edición rápida en esta celda (solo en
                                       renderizado automático, NO en slot="row" — el
                                       llamador que usa slot="row" controla sus celdas)
    type         'text'|'number'|'date'       — tipo del <input> de edición

  Props nuevas de la tabla (todas opt-in, default = comportamiento actual):
    tableId            string|null  — identificador estable de esta tabla, requerido
                                       para persistir columnas/vistas (p. ej. "bandeja-entrada")
    disableNav          boolean (default false) — oculta los botones ‹anterior/siguiente›
                                       mientras conserva el resumen "X–Y de Z"; para
                                       consumidores que conocen el `totalCount` real del
                                       backend pero SOLO cargaron una página fija (sin
                                       `on:pageChange` conectado a un refetch) — evita un
                                       botón "Siguiente" que no hace nada (ver anulaciones/+page.svelte)
    enableFilters       boolean (default false)
    enableSort          boolean (default false)
    enableExport        boolean (default false) — exporta displayRows visibles a CSV
    enableColumnToggle  boolean (default false)
    enableSavedViews    boolean (default false) — requiere tableId
    enableSelection     boolean (default false)
    virtualize          boolean (default false) — ver divergencia de accesibilidad abajo
    virtualizeThreshold number  (default 150)
    rowKey              string|(row, idx) => string|number — clave estable de fila para
                         selección (default: row.id ?? row.radicado ?? idx)
    selected            Array<string|number> — filas seleccionadas, bindable (solo se
                         sincroniza desde el padre en el montaje inicial, ver nota abajo)
    exportFilename      string (default "export.csv")

  Eventos:
    on:pageChange       { detail: { page: number } }                    (existente)
    on:filterChange     { detail: { filters: Record<string, any> } }
    on:sortChange       { detail: { key: string|null; direction: 'asc'|'desc'|null } }
    on:columnsChange    { detail: { hiddenColumnKeys: string[] } }
    on:selectionChange  { detail: { selected: Array<string|number> } }
    on:cellEdit         { detail: { row, rowIdx, key, value, oldValue } } — el llamador
                         decide si confirma el cambio contra el gateway; DataTable NO
                         muta `rows` por sí misma.

  Filtros y orden operan sobre las `rows` que ya tiene la tabla (la página actual
  servida por el backend), no re-consultan el gateway — son un refinamiento LOCAL
  de la página visible. Un consumidor que quiera filtrar/ordenar contra el
  servidor completo debe escuchar `filterChange`/`sortChange` y volver a pedir
  `rows` con esos criterios (patrón `?field=value` de las API conventions).

  DIVERGENCIA DECLARADA (capacidad 7, virtualización): NO se implementa
  virtualización por remoción de filas del DOM. Ese patrón (recorte real de
  <tr>) rompe la semántica nativa de <table> para lectores de pantalla —
  desalinea el recuento de filas anunciado (aria-rowcount) y la navegación por
  tabla de NVDA/JAWS/VoiceOver dejaría de coincidir con las filas reales, y el
  DOM ya sufrió un incidente real por asumir comportamientos de entorno no
  verificados (D-16, SSR). Priorizando accesibilidad sobre rendimiento bruto
  (instrucción explícita del Sistema de Diseño ante el conflicto), la tabla
  activa `content-visibility: auto` por fila cuando `virtualize` está activo y
  `displayRows.length` supera `virtualizeThreshold`: el navegador salta el
  layout/paint de las filas fuera de viewport sin retirarlas del árbol de
  accesibilidad. Es virtualización "a nivel de render", no de DOM — más lenta
  que una virtualización agresiva con miles de filas, pero sin regresión de
  accesibilidad. Además, este componente ya pagina server-side (`totalCount`);
  `virtualize` solo importa cuando un consumidor pasa un `rows` completo sin
  paginar (modo "todas las filas" de una vista pequeña/local).

  Accesibilidad:
    - <caption> con descripción
    - scope="col" en encabezados
    - aria-busy en estado de carga
    - aria-sort en encabezados ordenables (no solo un icono)
    - aria-label en botones de paginación/orden/filtro
    - región aria-live discreta que anuncia cambios de filtro/orden/selección/edición
    - checkbox de selección con estado indeterminado real (`el.indeterminate`)
-->
<script context="module">
  /**
   * Determina si `row` cumple TODOS los filtros activos. Función pura
   * exportada para test directo (sin montar el componente).
   * @param {Record<string, any>} row
   * @param {Record<string, any>} filters
   * @param {Array<{key: string; filterType?: string}>} columns
   * @returns {boolean}
   */
  export function matchesFilters(row, filters, columns) {
    return Object.entries(filters).every(([key, value]) => {
      if (value === undefined || value === null || value === "") return true;
      const col = columns.find((c) => c.key === key);
      const cellValue = row[key];

      if (col?.filterType === "select") {
        return String(cellValue ?? "") === String(value);
      }

      if (col?.filterType === "dateRange") {
        const { from, to } = value || {};
        if (!from && !to) return true;
        if (cellValue === undefined || cellValue === null || cellValue === "") return false;
        const cellDate = new Date(cellValue);
        if (from && cellDate < new Date(from)) return false;
        if (to && cellDate > new Date(to)) return false;
        return true;
      }

      // Texto libre: subcadena, insensible a mayúsculas/tildes básicas.
      return String(cellValue ?? "")
        .toLowerCase()
        .includes(String(value).toLowerCase());
    });
  }

  /**
   * Ordena `inputRows` según `sortState`. Función pura exportada para test
   * directo. Sin `sortState.key`, devuelve `inputRows` sin copiar (identidad
   * preservada — importante para no romper `{#each rows as row (rowIdx)}`
   * innecesariamente cuando el orden está desactivado).
   * @param {Array<Record<string, any>>} inputRows
   * @param {{key: string|null; direction: 'asc'|'desc'|null}} sortState
   * @returns {Array<Record<string, any>>}
   */
  export function sortRows(inputRows, sortState) {
    if (!sortState?.key) return inputRows;
    const sorted = [...inputRows].sort((a, b) => {
      const av = a[sortState.key];
      const bv = b[sortState.key];
      if (av === undefined || av === null) return bv === undefined || bv === null ? 0 : -1;
      if (bv === undefined || bv === null) return 1;
      if (typeof av === "number" && typeof bv === "number") return av - bv;
      return String(av).localeCompare(String(bv), "es", { numeric: true, sensitivity: "base" });
    });
    return sortState.direction === "desc" ? sorted.reverse() : sorted;
  }

  /**
   * Clave de selección por defecto: prioriza un identificador de dominio
   * estable (`id`/`radicado`) sobre el índice, que se desplaza al filtrar.
   * @param {Record<string, any>} row
   * @param {number} idx
   * @returns {string|number}
   */
  export function defaultRowKey(row, idx) {
    return row?.id ?? row?.radicado ?? idx;
  }
</script>

<script>
  import EmptyState from "./EmptyState.svelte";
  import Button from "./Button.svelte";
  import { createEventDispatcher, onDestroy } from "svelte";
  import { sessionUser } from "$lib/stores/auth.js";
  import { buildCsv, downloadCsv } from "$lib/utils/csvExport.js";
  import { buildStorageKey, readJson, writeJson } from "$lib/utils/tablePersistence.js";

  /** @type {Array<{key: string; label: string; width?: string; sortable?: boolean; filterType?: 'text'|'select'|'dateRange'; filterOptions?: Array<{value:string;label:string}>; editable?: boolean; type?: 'text'|'number'|'date'}>} */
  export let columns = [];

  /** @type {Array<Record<string,any>>} */
  export let rows = [];

  export let totalCount = 0;
  /**
   * El backend NO expone un total: `totalCount` es solo cuántas filas trajo
   * esta consulta, no cuántas hay. Sin esto, el resumen pinta «1–50 de 50»
   * sobre una ventana de las últimas 50 notificaciones de 420 — y afirmar un
   * total falso es peor que no darlo, porque quien lo lee deja de buscar. El
   * historial de envíos lo declara así; `/anulaciones` NO, porque su total sí
   * es real aunque también desactive la navegación.
   */
  export let totalDesconocido = false;
  export let page = 1;
  export let pageSize = 20;
  export let loading = false;
  export let caption = "Tabla de datos";

  // --- Capacidades nuevas (todas opt-in) ---------------------------------
  /** Identificador estable de esta tabla — requerido para persistir columnas/vistas. */
  export let tableId = /** @type {string|null} */ (null);
  export let disableNav = false;
  export let enableFilters = false;
  export let enableSort = false;
  export let enableExport = false;
  export let enableColumnToggle = false;
  export let enableSavedViews = false;
  export let enableSelection = false;
  export let virtualize = false;
  export let virtualizeThreshold = 150;
  /** @type {string | ((row: Record<string,any>, idx: number) => string|number)} */
  export let rowKey = defaultRowKey;
  /** @type {Array<string|number>} */
  export let selected = [];
  export let exportFilename = "export.csv";

  const dispatch = createEventDispatcher();
  const instanceId = `dt-${Math.random().toString(36).slice(2, 9)}`;

  $: totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  $: hasPrev = page > 1;
  $: hasNext = page < totalPages;

  $: start = totalCount > 0 ? (page - 1) * pageSize + 1 : 0;
  $: end = Math.min(page * pageSize, totalCount);

  function goTo(p) {
    if (p < 1 || p > totalPages) return;
    page = p;
    dispatch("pageChange", { page: p });
  }

  // --- Región aria-live (anuncia filtro/orden/selección/edición) ---------
  // I5b (revisión UX): se limpia sola tras ~1s (mismo patrón que
  // WorkflowBoard.svelte) — dos acciones idénticas seguidas (p. ej. exportar
  // dos veces la misma tabla) repetían el mismo texto y el lector de
  // pantalla podía no anunciar la segunda vez sin este reinicio.
  let liveMessage = "";
  let liveMessageTimer;
  function announce(message) {
    liveMessage = message;
    clearTimeout(liveMessageTimer);
    liveMessageTimer = setTimeout(() => {
      liveMessage = "";
    }, 1000);
  }

  onDestroy(() => clearTimeout(liveMessageTimer));

  // --- Persistencia (columnas visibles + vistas guardadas) ---------------
  // Clave espaciada por tenant + usuario + tabla (ver tablePersistence.js).
  // `sessionUser` puede resolver tenant/usuario DESPUÉS del primer render (la
  // sesión se hidrata desde `load` de SvelteKit) — por eso esto es un bloque
  // reactivo, no un onMount de una sola vez.
  $: tenant = $sessionUser?.tenant_slug ?? null;
  $: userId = $sessionUser?.sub ?? null;
  $: columnsStorageKey =
    tableId && (enableColumnToggle || enableSavedViews)
      ? buildStorageKey({ tenant, user: userId, tableId, kind: "columns" })
      : null;
  $: viewsStorageKey =
    tableId && enableSavedViews
      ? buildStorageKey({ tenant, user: userId, tableId, kind: "views" })
      : null;

  /** @type {Set<string>} */
  let hiddenColumnKeys = new Set();
  /** @type {Array<{name: string; filters: Record<string,any>; sortState: {key:string|null;direction:string|null}; hiddenColumnKeys: string[]}>} */
  let savedViews = [];

  // Guardia SSR (D-16/D-05): `readJson`/`writeJson` ya se protegen a sí mismas
  // con `typeof window === "undefined"`, así que este bloque reactivo NUNCA
  // toca `localStorage` en el servidor — puede correr durante el render SSR
  // (igual que el `$: if (typeof document !== "undefined" ...)` de
  // Drawer.svelte) sin lanzar. Solo se ejecuta una vez por clave distinta.
  let loadedForKey = null;
  $: if (typeof window !== "undefined" && columnsStorageKey && loadedForKey !== columnsStorageKey) {
    loadedForKey = columnsStorageKey;
    const storedHidden = readJson(columnsStorageKey);
    hiddenColumnKeys = new Set(Array.isArray(storedHidden) ? storedHidden : []);
    if (viewsStorageKey) {
      const storedViews = readJson(viewsStorageKey);
      savedViews = Array.isArray(storedViews) ? storedViews : [];
    }
  }

  function toggleColumn(key) {
    const next = new Set(hiddenColumnKeys);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    hiddenColumnKeys = next;
    if (columnsStorageKey) writeJson(columnsStorageKey, [...hiddenColumnKeys]);
    dispatch("columnsChange", { hiddenColumnKeys: [...hiddenColumnKeys] });
    const col = columns.find((c) => c.key === key);
    announce(`Columna ${col?.label ?? key} ${next.has(key) ? "ocultada" : "mostrada"}.`);
  }

  $: visibleColumns = columns.filter((col) => !hiddenColumnKeys.has(col.key));
  $: totalColspan = visibleColumns.length + (enableSelection ? 1 : 0);

  // --- Filtros -------------------------------------------------------------
  let filtersOpen = false;
  /** @type {Record<string, any>} */
  let filters = {};

  function setFilter(key, value) {
    filters = { ...filters, [key]: value };
    dispatch("filterChange", { filters });
  }

  function clearFilters() {
    filters = {};
    dispatch("filterChange", { filters });
    announce("Filtros eliminados.");
  }

  $: activeFilterCount = Object.entries(filters).filter(([, v]) => {
    if (v === undefined || v === null || v === "") return false;
    if (typeof v === "object") return Boolean(v.from || v.to);
    return true;
  }).length;

  $: filteredRows = enableFilters
    ? rows.filter((row) => matchesFilters(row, filters, columns))
    : rows;

  // --- Orden -----------------------------------------------------------------
  /** @type {{key: string|null; direction: 'asc'|'desc'|null}} */
  let sortState = { key: null, direction: null };

  function toggleSort(col) {
    if (!col.sortable) return;
    if (sortState.key !== col.key) {
      sortState = { key: col.key, direction: "asc" };
    } else if (sortState.direction === "asc") {
      sortState = { key: col.key, direction: "desc" };
    } else {
      sortState = { key: null, direction: null };
    }
    dispatch("sortChange", { ...sortState });
    announce(
      sortState.key
        ? `Ordenado por ${col.label}, ${sortState.direction === "asc" ? "ascendente" : "descendente"}.`
        : `Orden eliminado.`,
    );
  }

  // `sortState` se pasa explícitamente (en vez de leerse por closure) porque
  // Svelte 4 no rastrea dependencias reactivas dentro de funciones llamadas
  // desde una expresión de plantilla: si `sortState` no aparece como
  // identificador literal en la expresión del marcado, el compilador no
  // vuelve a evaluar ese atributo cuando `sortState` cambia (aria-sort se
  // quedaría congelado en "none" tras el primer clic).
  function ariaSortFor(col, currentSortState) {
    if (!col.sortable) return undefined;
    if (currentSortState.key !== col.key) return "none";
    return currentSortState.direction === "asc" ? "ascending" : "descending";
  }

  function sortIconFor(col, currentSortState) {
    if (currentSortState.key !== col.key) return "fa-sort";
    return currentSortState.direction === "asc" ? "fa-sort-up" : "fa-sort-down";
  }

  $: sortedRows = enableSort ? sortRows(filteredRows, sortState) : filteredRows;

  // Fila final a mostrar. Sin filtros/orden activos, es literalmente `rows`
  // (misma referencia) — cero cambio de comportamiento para consumidores que
  // no activan estas capacidades.
  $: displayRows = sortedRows;

  $: useVirtualization = virtualize && displayRows.length > virtualizeThreshold;

  // --- Selección múltiple ------------------------------------------------
  function resolveRowKey(row, idx) {
    return typeof rowKey === "function" ? rowKey(row, idx) : (row?.[rowKey] ?? idx);
  }

  /** @type {Set<string|number>} */
  let selectedKeys = new Set();
  // Huella (firma por contenido, sin importar el orden) de la última `selected`
  // que esta tabla ya reflejó en `selectedKeys`. Antes esto se sembraba UNA
  // sola vez (flag `selectionInitialized`) y luego la tabla ignoraba cambios
  // externos de `selected` — si el padre limpiaba/reponía la prop (p. ej. tras
  // una nueva búsqueda), la tabla seguía mostrando la selección vieja mientras
  // el padre creía que ya no había nada seleccionado: "Vincular (4)" con 0
  // filas reales marcadas. Ahora se compara CONTENIDO en cada cambio de
  // `selected` y se re-siembra si difiere. No hay bucle: `emitSelection()`
  // asigna `selected = [...selectedKeys]`, lo que sí dispara este bloque de
  // nuevo, pero la firma calculada coincidirá con `lastSelectedSignature`
  // (se fija ANTES de que `selectedKeys` cambie más), así que no se re-siembra.
  let lastSelectedSignature = /** @type {string | null} */ (null);
  function signatureOf(list) {
    return [...(list ?? [])].map(String).sort().join(" ");
  }
  $: if (enableSelection) {
    const incomingSignature = signatureOf(selected);
    if (incomingSignature !== lastSelectedSignature) {
      lastSelectedSignature = incomingSignature;
      selectedKeys = new Set(selected ?? []);
    }
  }

  $: currentPageKeys = displayRows.map((row, idx) => resolveRowKey(row, idx));

  $: headerCheckboxState = (() => {
    if (currentPageKeys.length === 0) return "none";
    const selectedCount = currentPageKeys.filter((k) => selectedKeys.has(k)).length;
    if (selectedCount === 0) return "none";
    return selectedCount === currentPageKeys.length ? "all" : "some";
  })();

  function emitSelection() {
    selected = [...selectedKeys];
    dispatch("selectionChange", { selected: [...selectedKeys] });
    announce(
      `${selectedKeys.size} fila${selectedKeys.size === 1 ? "" : "s"} seleccionada${selectedKeys.size === 1 ? "" : "s"}.`,
    );
  }

  function toggleRowSelection(key) {
    const next = new Set(selectedKeys);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    selectedKeys = next;
    emitSelection();
  }

  function toggleAllSelection() {
    const next = new Set(selectedKeys);
    if (headerCheckboxState === "all") {
      currentPageKeys.forEach((k) => next.delete(k));
    } else {
      currentPageKeys.forEach((k) => next.add(k));
    }
    selectedKeys = next;
    emitSelection();
  }

  function clearSelection() {
    selectedKeys = new Set();
    emitSelection();
  }

  /** Action: sincroniza `el.indeterminate` (no expresable como atributo declarativo). */
  function indeterminateCheckbox(node, state) {
    node.indeterminate = state === "some";
    return {
      update(newState) {
        node.indeterminate = newState === "some";
      },
    };
  }

  // --- Vistas guardadas ----------------------------------------------------
  let newViewName = "";

  function saveCurrentView() {
    const name = newViewName.trim();
    if (!name) return;
    const view = {
      name,
      filters: { ...filters },
      sortState: { ...sortState },
      hiddenColumnKeys: [...hiddenColumnKeys],
    };
    savedViews = [...savedViews.filter((v) => v.name !== name), view];
    if (viewsStorageKey) writeJson(viewsStorageKey, savedViews);
    newViewName = "";
    announce(`Vista "${name}" guardada.`);
  }

  function applyView(view) {
    filters = { ...view.filters };
    sortState = { ...view.sortState };
    hiddenColumnKeys = new Set(view.hiddenColumnKeys ?? []);
    dispatch("filterChange", { filters });
    dispatch("sortChange", { ...sortState });
    dispatch("columnsChange", { hiddenColumnKeys: [...hiddenColumnKeys] });
    announce(`Vista "${view.name}" aplicada.`);
  }

  function deleteView(name) {
    savedViews = savedViews.filter((v) => v.name !== name);
    if (viewsStorageKey) writeJson(viewsStorageKey, savedViews);
    announce(`Vista "${name}" eliminada.`);
  }

  // --- Exportación CSV -------------------------------------------------------
  // I6 (revisión UX): `exportCsv` SIEMPRE serializó `displayRows` — la página
  // visible, no el `totalCount` del backend — pero el botón decía "Exportar
  // CSV" a secas junto a un resumen "1–20 de 137": quien lo pulsaba se
  // llevaba 20 filas creyendo que exportó las 137. La etiqueta ahora declara
  // el alcance real y, si hay más filas en el backend que las visibles, el
  // anuncio accesible también lo advierte (no solo el texto del botón).
  $: exportLabel = `Exportar página (${displayRows.length})`;

  function exportCsv() {
    // Disparado únicamente por un clic real (nunca en SSR: no hay eventos DOM
    // en el servidor), así que `downloadCsv` puede tocar `document`/`URL` sin
    // guardia adicional.
    const csv = buildCsv(visibleColumns, displayRows);
    downloadCsv(csv, exportFilename);
    announce(
      totalCount > displayRows.length
        ? `Exportadas ${displayRows.length} filas de ${totalCount} a CSV (solo la página visible).`
        : `Exportadas ${displayRows.length} filas a CSV.`,
    );
  }

  // --- Edición rápida en celda ---------------------------------------------
  /** @type {{rowIdx: number; key: string} | null} */
  let editingCell = null;
  let editValue = "";

  function startEdit(rowIdx, col, currentValue) {
    if (!col.editable) return;
    editingCell = { rowIdx, key: col.key };
    editValue = currentValue ?? "";
  }

  function commitEdit(row, rowIdx, col) {
    const oldValue = row[col.key];
    dispatch("cellEdit", { row, rowIdx, key: col.key, value: editValue, oldValue });
    editingCell = null;
    // I7 (revisión UX): DataTable NO muta `rows` — es el consumidor quien
    // confirma (o revierte) el cambio contra el gateway (ver doc del evento
    // `cellEdit` arriba). Anunciar "actualizada" aquí afirmaba un éxito que
    // todavía no ocurrió: si el gateway rechaza, la celda vuelve al valor
    // anterior pero el lector de pantalla ya dijo que se guardó.
    announce(`Cambio en ${col.label} enviado…`);
  }

  function cancelEdit() {
    editingCell = null;
  }

  function onEditKeydown(event, row, rowIdx, col) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitEdit(row, rowIdx, col);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelEdit();
    }
  }

  function autofocusInput(node) {
    node.focus();
    node.select?.();
  }

  $: showToolbar =
    enableFilters || enableColumnToggle || enableSavedViews || enableExport || enableSelection;
</script>

<div class="op-datatable" aria-busy={loading}>
  <div class="sr-only" aria-live="polite" aria-atomic="true">{liveMessage}</div>

  {#if showToolbar}
    <div class="op-datatable__toolbar" role="toolbar" aria-label="Herramientas de la tabla">
      {#if enableFilters}
        <Button
          variant="ghost"
          size="sm"
          icon="fas fa-filter"
          aria-expanded={filtersOpen}
          aria-controls="{instanceId}-filter-row"
          on:click={() => (filtersOpen = !filtersOpen)}
        >
          Filtros{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}
        </Button>
        {#if activeFilterCount > 0}
          <Button variant="ghost" size="sm" on:click={clearFilters}>Limpiar filtros</Button>
        {/if}
      {/if}

      {#if enableColumnToggle}
        <details class="op-datatable__panel">
          <summary class="op-datatable__panel-summary">
            <i class="fas fa-table-columns" aria-hidden="true"></i> Columnas
          </summary>
          <div class="op-datatable__panel-body">
            {#each columns as col (col.key)}
              <label class="op-datatable__panel-option">
                <input
                  type="checkbox"
                  checked={!hiddenColumnKeys.has(col.key)}
                  on:change={() => toggleColumn(col.key)}
                />
                {col.label || col.key}
              </label>
            {/each}
          </div>
        </details>
      {/if}

      {#if enableSavedViews}
        <details class="op-datatable__panel">
          <summary class="op-datatable__panel-summary">
            <i class="fas fa-bookmark" aria-hidden="true"></i> Vistas guardadas
          </summary>
          <div class="op-datatable__panel-body">
            {#each savedViews as view (view.name)}
              <div class="op-datatable__view-row">
                <button
                  type="button"
                  class="op-datatable__view-apply"
                  on:click={() => applyView(view)}
                >
                  {view.name}
                </button>
                <button
                  type="button"
                  class="op-datatable__view-delete"
                  aria-label={`Eliminar vista ${view.name}`}
                  on:click={() => deleteView(view.name)}
                >
                  <i class="fas fa-trash" aria-hidden="true"></i>
                </button>
              </div>
            {:else}
              <p class="op-datatable__panel-empty">Sin vistas guardadas.</p>
            {/each}
            <form class="op-datatable__view-form" on:submit|preventDefault={saveCurrentView}>
              <label class="sr-only" for="{instanceId}-view-name">Nombre de la vista</label>
              <input
                id="{instanceId}-view-name"
                type="text"
                placeholder="Nombre de la vista"
                bind:value={newViewName}
              />
              <Button type="submit" size="sm" variant="secondary" disabled={!newViewName.trim()}>
                Guardar vista
              </Button>
            </form>
          </div>
        </details>
      {/if}

      {#if enableExport}
        <Button variant="secondary" size="sm" icon="fas fa-file-csv" on:click={exportCsv}>
          {exportLabel}
        </Button>
      {/if}

      {#if enableSelection && selectedKeys.size > 0}
        <span class="op-datatable__selection-count">
          {selectedKeys.size} seleccionada{selectedKeys.size === 1 ? "" : "s"}
        </span>
        <Button variant="ghost" size="sm" on:click={clearSelection}>Limpiar selección</Button>
      {/if}
    </div>
  {/if}

  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con
       teclado (WCAG 2.1.1) — el linter de Svelte no reconoce esta excepción. -->
  <div
    class="op-datatable__wrapper"
    tabindex="0"
    role="region"
    aria-label="{caption} — desplazable horizontalmente"
  >
    <table class="op-datatable__table" aria-label={caption}>
      <caption class="sr-only">{caption}</caption>
      <thead>
        <tr>
          {#if enableSelection}
            <th scope="col" class="op-datatable__th op-datatable__th--select">
              <input
                class="op-datatable__checkbox"
                type="checkbox"
                aria-label="Seleccionar todas las filas de esta página"
                checked={headerCheckboxState === "all"}
                use:indeterminateCheckbox={headerCheckboxState}
                on:change={toggleAllSelection}
              />
            </th>
          {/if}
          {#each visibleColumns as col (col.key)}
            <th
              scope="col"
              class="op-datatable__th"
              style={col.width ? `width: ${col.width}` : ""}
              aria-sort={enableSort ? ariaSortFor(col, sortState) : undefined}
            >
              {#if enableSort && col.sortable}
                <button
                  type="button"
                  class="op-datatable__sort-btn"
                  on:click={() => toggleSort(col)}
                >
                  <span>{col.label}</span>
                  <i class="fas {sortIconFor(col, sortState)}" aria-hidden="true"></i>
                </button>
              {:else}
                {col.label}
              {/if}
            </th>
          {/each}
        </tr>
        {#if enableFilters && filtersOpen}
          <!--
            I9 (revisión UX): esta fila NO es de encabezados — es un
            formulario inline de filtros. Con `<th scope="col">` cada celda
            de datos quedaba asociada a DOS encabezados de columna (el real y
            este), rompiendo la navegación por tabla de los lectores de
            pantalla. `<td>` es legal dentro de `<thead>` y no participa del
            modelo de encabezados.
          -->
          <tr id="{instanceId}-filter-row" class="op-datatable__filter-row">
            {#if enableSelection}
              <td class="op-datatable__td op-datatable__td--select"></td>
            {/if}
            {#each visibleColumns as col (col.key)}
              <td class="op-datatable__td op-datatable__filter-th">
                {#if col.filterType === "select"}
                  <select
                    class="op-datatable__filter-select"
                    aria-label={`Filtrar por ${col.label}`}
                    value={filters[col.key] ?? ""}
                    on:change={(e) => setFilter(col.key, e.currentTarget.value)}
                  >
                    <option value="">Todos</option>
                    {#each col.filterOptions || [] as opt (opt.value)}
                      <option value={opt.value}>{opt.label}</option>
                    {/each}
                  </select>
                {:else if col.filterType === "dateRange"}
                  <div class="op-datatable__filter-daterange">
                    <input
                      type="date"
                      aria-label={`${col.label} desde`}
                      value={filters[col.key]?.from ?? ""}
                      on:change={(e) =>
                        setFilter(col.key, {
                          ...(filters[col.key] || {}),
                          from: e.currentTarget.value,
                        })}
                    />
                    <input
                      type="date"
                      aria-label={`${col.label} hasta`}
                      value={filters[col.key]?.to ?? ""}
                      on:change={(e) =>
                        setFilter(col.key, {
                          ...(filters[col.key] || {}),
                          to: e.currentTarget.value,
                        })}
                    />
                  </div>
                {:else if col.filterType}
                  <input
                    type="text"
                    class="op-datatable__filter-input"
                    placeholder={`Filtrar ${col.label}`}
                    aria-label={`Filtrar por ${col.label}`}
                    value={filters[col.key] ?? ""}
                    on:input={(e) => setFilter(col.key, e.currentTarget.value)}
                  />
                {/if}
              </td>
            {/each}
          </tr>
        {/if}
      </thead>
      <tbody>
        {#if loading}
          {#each Array(5) as _, i (i)}
            <tr class="op-datatable__row" aria-hidden="true">
              {#each Array(totalColspan) as _col, colIdx (colIdx)}
                <td class="op-datatable__td">
                  <span class="skeleton skeleton--text"></span>
                </td>
              {/each}
            </tr>
          {/each}
        {:else if displayRows.length === 0}
          <tr>
            <td colspan={totalColspan} class="op-datatable__td">
              <EmptyState
                icon="fas fa-inbox"
                title="Sin resultados"
                description="No hay registros que coincidan con los criterios seleccionados."
              />
            </td>
          </tr>
        {:else}
          {#each displayRows as row, rowIdx (resolveRowKey(row, rowIdx))}
            <tr class="op-datatable__row" class:op-datatable__row--virtual={useVirtualization}>
              {#if enableSelection}
                {@const key = resolveRowKey(row, rowIdx)}
                <td class="op-datatable__td op-datatable__td--select">
                  <input
                    class="op-datatable__checkbox"
                    type="checkbox"
                    aria-label={`Seleccionar fila ${rowIdx + 1}`}
                    checked={selectedKeys.has(key)}
                    on:change={() => toggleRowSelection(key)}
                  />
                </td>
              {/if}
              {#if $$slots.row}
                <!-- Personalización completa de celdas por el llamador -->
                <slot name="row" {row} {rowIdx} columns={visibleColumns} />
              {:else}
                <!-- Renderizado automático por columns[].key -->
                {#each visibleColumns as col (col.key)}
                  <td class="op-datatable__td" data-label={col.label}>
                    {#if editingCell && editingCell.rowIdx === rowIdx && editingCell.key === col.key}
                      <input
                        class="op-datatable__edit-input"
                        type={col.type === "number"
                          ? "number"
                          : col.type === "date"
                            ? "date"
                            : "text"}
                        value={editValue}
                        use:autofocusInput
                        on:input={(e) => (editValue = e.currentTarget.value)}
                        on:keydown={(e) => onEditKeydown(e, row, rowIdx, col)}
                        on:blur={() => commitEdit(row, rowIdx, col)}
                      />
                    {:else if col.editable}
                      <button
                        type="button"
                        class="op-datatable__edit-trigger"
                        on:click={() => startEdit(rowIdx, col, row[col.key])}
                      >
                        <span>{row[col.key] ?? "—"}</span>
                        <i class="fas fa-pen op-datatable__edit-icon" aria-hidden="true"></i>
                      </button>
                    {:else}
                      {row[col.key] ?? "—"}
                    {/if}
                  </td>
                {/each}
              {/if}
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>

  <!-- Paginación -->
  {#if totalCount > 0 && !loading}
    <nav class="op-datatable__pagination" aria-label="Paginación de resultados">
      <span class="op-datatable__count">
        {#if totalDesconocido}
          {totalCount.toLocaleString("es-CO")}
          {totalCount === 1 ? "registro" : "registros"} en esta consulta
        {:else}
          {start}–{end} de {totalCount}
        {/if}
        {#if enableFilters && activeFilterCount > 0}
          <span class="op-datatable__count-filtered"
            >({displayRows.length} filtradas en esta página)</span
          >
        {/if}
      </span>

      {#if !disableNav}
        <div class="op-datatable__pages">
          <button
            type="button"
            class="op-datatable__page-btn"
            disabled={!hasPrev}
            aria-label="Página anterior"
            on:click={() => goTo(page - 1)}
          >
            <i class="fas fa-chevron-left" aria-hidden="true"></i>
          </button>

          <span class="op-datatable__page-info" aria-live="polite">
            {page} / {totalPages}
          </span>

          <button
            type="button"
            class="op-datatable__page-btn"
            disabled={!hasNext}
            aria-label="Página siguiente"
            on:click={() => goTo(page + 1)}
          >
            <i class="fas fa-chevron-right" aria-hidden="true"></i>
          </button>
        </div>
      {/if}
    </nav>
  {/if}
</div>

<style lang="scss">
  .op-datatable {
    display: flex;
    flex-direction: column;

    &__toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      border-bottom: 1px solid var(--op-border);
      background: var(--op-surface);
    }

    &__panel {
      position: relative;

      &-summary {
        list-style: none;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: var(--op-space-2);
        padding: var(--op-space-1) var(--op-space-3);
        font-size: var(--op-font-sm);
        color: var(--op-text-secondary);
        border: 1px solid var(--op-border);
        border-radius: var(--op-radius-sm);
        min-height: 36px;

        /* I5 (revisión UX): objetivo táctil >=44px en dispositivos táctiles
           (mismo patrón que Button/FormField/WorkflowCard/RevertirModal). */
        @media (pointer: coarse) {
          min-height: 44px;
        }

        &::-webkit-details-marker {
          display: none;
        }

        &:hover {
          color: var(--op-primary-dark);
          border-color: var(--op-primary);
        }

        &:focus-visible {
          outline: 2px solid var(--op-primary);
          outline-offset: 2px;
        }
      }

      &-body {
        position: absolute;
        z-index: 20;
        top: calc(100% + var(--op-space-1));
        left: 0;
        min-width: 220px;
        display: flex;
        flex-direction: column;
        gap: var(--op-space-2);
        padding: var(--op-space-3);
        background: var(--op-surface);
        border: 1px solid var(--op-border);
        border-radius: var(--op-radius);
        box-shadow: var(--op-shadow);
      }

      &-option {
        display: flex;
        align-items: center;
        gap: var(--op-space-2);
        font-size: var(--op-font-sm);
        color: var(--op-text);
      }

      &-empty {
        font-size: var(--op-font-xs);
        color: var(--op-text-secondary);
        margin: 0;
      }
    }

    &__view-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-2);
    }

    &__view-apply {
      background: none;
      border: none;
      color: var(--op-primary-dark);
      cursor: pointer;
      text-align: left;
      font-size: var(--op-font-sm);
      padding: var(--op-space-1) 0;

      &:hover {
        text-decoration: underline;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__view-delete {
      background: none;
      border: none;
      color: var(--op-text-secondary);
      cursor: pointer;
      padding: var(--op-space-1);

      &:hover {
        color: var(--op-error);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__view-form {
      display: flex;
      gap: var(--op-space-2);
      border-top: 1px solid var(--op-border);
      padding-top: var(--op-space-2);
      margin-top: var(--op-space-1);

      & input {
        flex: 1;
        min-width: 0;
        font-family: var(--op-font-family);
        font-size: var(--op-font-sm);
        border: 1px solid var(--op-border);
        border-radius: var(--op-radius-sm);
        padding: 0 var(--op-space-2);
      }
    }

    &__selection-count {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin-left: auto;
    }

    &__wrapper {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: -2px;
      }
    }

    &__table {
      width: 100%;
      border-collapse: collapse;
      font-size: var(--op-font-sm);
    }

    &__th {
      padding: var(--op-space-2) var(--op-space-4);
      text-align: left;
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      color: var(--op-text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      background: var(--op-bg);
      border-bottom: 2px solid var(--op-border);
      white-space: nowrap;

      &--select {
        width: 40px;
      }
    }

    &__sort-btn {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-1);
      background: none;
      border: none;
      padding: 0;
      margin: 0;
      font: inherit;
      text-transform: inherit;
      letter-spacing: inherit;
      color: inherit;
      cursor: pointer;

      &:hover {
        color: var(--op-primary-dark);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__filter-row {
      background: var(--op-surface);
    }

    &__filter-th {
      text-transform: none;
      letter-spacing: normal;
      padding: var(--op-space-2);
    }

    &__filter-input,
    &__filter-select {
      width: 100%;
      font-family: var(--op-font-family);
      font-size: var(--op-font-xs);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-1) var(--op-space-2);
      background: var(--op-surface);
      color: var(--op-text);
    }

    &__filter-daterange {
      display: flex;
      gap: var(--op-space-1);

      & input {
        min-width: 0;
        flex: 1;
        font-size: var(--op-font-xs);
        border: 1px solid var(--op-border);
        border-radius: var(--op-radius-sm);
        padding: var(--op-space-1);
      }
    }

    &__row {
      &:not(:last-child) {
        border-bottom: 1px solid var(--op-border);
      }

      &:hover {
        background: var(--op-bg);
      }

      /* Divergencia declarada (ver comentario del componente): virtualización
         "a nivel de render", no de DOM — preserva semántica de tabla/AT. */
      &--virtual {
        content-visibility: auto;
        contain-intrinsic-size: auto 44px;
      }
    }

    &__td {
      padding: var(--op-space-3) var(--op-space-4);
      color: var(--op-text);
      vertical-align: middle;

      &--select {
        width: 40px;

        @media (pointer: coarse) {
          min-height: 44px;
        }
      }
    }

    /* I5 (revisión UX): las casillas de selección no tenían dimensión propia
       — con el hitbox nativo del navegador (~13px) muy por debajo del
       mínimo táctil. Se dimensionan siempre y crecen al mínimo 44px en
       dispositivos táctiles (mismo patrón que RevertirModal). */
    &__checkbox {
      width: 18px;
      height: 18px;
      cursor: pointer;

      @media (pointer: coarse) {
        width: 24px;
        height: 24px;
      }
    }

    &__edit-trigger {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      background: none;
      border: 1px dashed transparent;
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-1) var(--op-space-2);
      margin: calc(var(--op-space-1) * -1) calc(var(--op-space-2) * -1);
      color: inherit;
      font: inherit;
      cursor: pointer;
      max-width: 100%;

      &:hover {
        border-color: var(--op-border);
        background: var(--op-bg);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__edit-icon {
      font-size: 0.75em;
      color: var(--op-text-secondary);
    }

    &__edit-input {
      width: 100%;
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      border: 1px solid var(--op-primary);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-1) var(--op-space-2);
      background: var(--op-surface);
      color: var(--op-text);
      outline: none;
      box-shadow: 0 0 0 3px var(--op-focus-ring);
    }

    &__pagination {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--op-space-3) var(--op-space-4);
      border-top: 1px solid var(--op-border);
      background: var(--op-bg);
    }

    &__count {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__count-filtered {
      margin-left: var(--op-space-2);
    }

    &__pages {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
    }

    &__page-btn {
      background: none;
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      width: 28px;
      height: 28px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);

      /* I5 (revisión UX): 28×28px es el único objetivo táctil de todo el
         sistema de diseño que no llegaba a 44px en dispositivos táctiles
         (mismo patrón que Button/FormField/WorkflowCard/RevertirModal). */
      @media (pointer: coarse) {
        min-width: 44px;
        min-height: 44px;
      }

      &:not(:disabled):hover {
        background: var(--op-surface);
        color: var(--op-primary);
        border-color: var(--op-primary);
      }

      &:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__page-info {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      min-width: 48px;
      text-align: center;
    }
  }
</style>
