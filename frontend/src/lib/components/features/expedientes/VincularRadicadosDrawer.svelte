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
  VincularRadicadosDrawer.svelte — Buscador + selección múltiple de radicados
  para vincularlos a un expediente (Fase 2 cierre desfase API↔UI, RF-UI-05).

  Es la pieza CENTRAL del módulo de expedientes: un expediente es, por
  definición, una agrupación de radicados relacionados — sin esto la
  capacidad no se puede ejercer desde la interfaz.

  Los radicados candidatos salen del buscador ya existente
  `GET /busqueda/api` (mismo proxy que usan /busqueda y la cola de
  anulaciones) — no se inventa un segundo camino de búsqueda. La respuesta
  real es `SearchHit` (document-service, `app/schemas/search.py`):
  { id, tracking_number, doc_type, subject, sender_name, sender_entity,
    dest_dept, status, registered_at, rank }. `id` es el `radicado_id` que
  exige `RadicadoLink`.

  El flujo prioriza el caso normal: seleccionar VARIOS resultados y
  vincularlos de una vez (POST .../radicados/batch). Con una sola selección
  usa el endpoint singular (POST .../radicados) — mismo resultado, sin forzar
  un lote de 1 al backend.

  Props:
    open         boolean  — controla la visibilidad (bindable vía on:close)
    expedienteId string   — id del expediente destino
    linkedIds    Set<string> — radicado_id ya vinculados, excluidos de resultados

  Eventos:
    on:close   — el Drawer se cerró (cancelado o tras vincular)
    on:linked  — la vinculación (simple o lote) se completó con éxito

  Selección entre búsquedas (Fase 2, corrección UX Crítico 2): un archivista
  busca por tandas (no todos los radicados de un lote caben en una sola
  consulta) — perder lo ya marcado cada vez que se lanza una búsqueda nueva es
  hostil para ese flujo. Por eso la selección real de este drawer NO vive en
  `results` (la página de resultados actual) sino en `selectedMap`
  (radicado_id → datos mínimos), que persiste mientras el drawer esté abierto:
  buscar de nuevo NUNCA descarta lo ya elegido. `DataTable` solo recibe, como
  prop de una vía, la intersección entre `selectedMap` y la página actual —
  nunca se usa `bind:selected` aquí para no pelear por la fuente de verdad.

  Paginación real (corrección UX Alto A1): el backend (`/busqueda/api`, que
  reenvía `X-Total-Count`/`total` de `GET /search`) sabe cuántos resultados
  hay en total; este drawer respeta ese número en vez de fingir que la
  primera página es todo lo que existe.
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import DataTable from "$lib/components/ui/DataTable.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { etiquetaEstadoRadicado } from "$lib/utils/radicado.js";

  export let open = false;
  export let expedienteId = "";
  /** @type {Set<string>} */
  export let linkedIds = new Set();

  const dispatch = createEventDispatcher();

  const PAGE_SIZE = 50;

  let query = "";
  let searching = false;
  let searchError = "";
  let hasSearched = false;
  /** @type {any[]} */
  let results = [];
  let total = 0;
  let page = 1;
  let linking = false;

  // Fuente de verdad de la selección — sobrevive a re-búsquedas y a cambios
  // de página (ver comentario del componente arriba).
  /** @type {Map<string, {radicado_id: string, tracking_number: string}>} */
  let selectedMap = new Map();
  $: selectedList = [...selectedMap.values()];
  // Lo que le pasamos a DataTable: solo los ids de la página actual que ya
  // están en `selectedMap` — es una proyección de un solo sentido, nunca
  // `bind:`, para que `selectedMap` sea la única fuente de verdad.
  $: pageSelectedIds = results.filter((r) => selectedMap.has(r.id)).map((r) => r.id);

  const COLUMNS = [
    { key: "tracking_number", label: "Radicado", width: "160px" },
    { key: "subject", label: "Asunto" },
    { key: "dest_dept", label: "Dependencia", width: "160px" },
    { key: "status", label: "Estado", width: "110px" },
  ];

  /** Reacciona a la selección que reporta DataTable para la página visible. */
  function handleSelectionChange(event) {
    const nowSelected = new Set(event.detail.selected);
    const next = new Map(selectedMap);
    for (const row of results) {
      if (nowSelected.has(row.id)) {
        next.set(row.id, { radicado_id: row.id, tracking_number: row.tracking_number });
      } else {
        next.delete(row.id);
      }
    }
    selectedMap = next;
  }

  /** Quita un radicado de la lista revisable de seleccionados (fuera de la tabla). */
  function removeFromSelection(radicadoId) {
    const next = new Map(selectedMap);
    next.delete(radicadoId);
    selectedMap = next;
  }

  async function doSearch(targetPage = 1) {
    searchError = "";
    const q = query.trim();
    if (q.length < 2) {
      searchError = "Escriba al menos 2 caracteres para buscar.";
      return;
    }
    searching = true;
    try {
      const params = new URLSearchParams({ q, page: String(targetPage), size: String(PAGE_SIZE) });
      const res = await fetch(`/busqueda/api?${params.toString()}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw data;
      // El estado llega como lo nombra la API (`in_progress`) y esta tabla lo
      // pintaba tal cual, mientras la bandeja —la misma información, dos clics
      // más allá— dice «En trámite». `etiquetaEstadoRadicado` ya existía con su
      // test; lo que faltaba era leerla aquí.
      results = (data.results ?? [])
        .filter((r) => !linkedIds.has(r.id))
        .map((r) => ({ ...r, status: etiquetaEstadoRadicado(r.status) }));
      total = Number(data.total ?? results.length);
      page = targetPage;
      hasSearched = true;
      // Nótese: `selectedMap` NO se toca aquí — buscar de nuevo (o cambiar de
      // página) nunca descarta la selección ya hecha (Crítico 2).
    } catch (err) {
      searchError = mensajeError(err, "No se pudo buscar radicados.");
      results = [];
      total = 0;
    } finally {
      searching = false;
    }
  }

  function handleSubmitSearch() {
    doSearch(1);
  }

  function handlePageChange(event) {
    doSearch(event.detail.page);
  }

  function reset() {
    query = "";
    results = [];
    total = 0;
    page = 1;
    selectedMap = new Map();
    searchError = "";
    hasSearched = false;
  }

  function handleClose() {
    reset();
    dispatch("close");
  }

  async function handleLink() {
    if (selectedList.length === 0) return;
    linking = true;
    try {
      const items = selectedList.map((r) => ({
        radicado_id: r.radicado_id,
        tracking_number: r.tracking_number,
      }));

      let res;
      let data;
      if (items.length === 1) {
        res = await fetch(`/expedientes/${expedienteId}/api/radicados`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(items[0]),
        });
        data = await res.json().catch(() => ({}));
        if (!res.ok) throw data;
        notifySuccess(`Radicado ${items[0].tracking_number} vinculado al expediente.`);
      } else {
        res = await fetch(`/expedientes/${expedienteId}/api/radicados/batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ radicados: items }),
        });
        data = await res.json().catch(() => ({}));
        if (!res.ok) throw data;
        // BatchLinkResult.vinculados/omitidos pueden ser null (M2, RF-SEG-08):
        // la vinculación siempre se ejecutó, solo se acota la respuesta.
        if (data.vinculados === null || data.vinculados === undefined) {
          notifySuccess(`Se procesó la vinculación de ${data.total ?? items.length} radicado(s).`);
        } else {
          const omitidosTxt = data.omitidos ? `, ${data.omitidos} ya estaban vinculado(s)` : "";
          notifySuccess(`${data.vinculados} radicado(s) vinculado(s)${omitidosTxt}.`);
        }
      }
      dispatch("linked");
      handleClose();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo vincular los radicados seleccionados."));
    } finally {
      linking = false;
    }
  }
</script>

<Drawer {open} title="Vincular radicados" width="640px" on:close={handleClose}>
  <p class="vinc-hint">
    Busque por número de radicado o asunto y seleccione uno o varios para vincularlos a este
    expediente. Los radicados ya vinculados no aparecen en los resultados.
  </p>

  <form
    class="vinc-search"
    role="search"
    aria-label="Buscar radicados"
    on:submit|preventDefault={handleSubmitSearch}
  >
    <FormField
      id="vinc-q"
      label="Buscar radicados"
      type="text"
      bind:value={query}
      placeholder="Número de radicado o asunto…"
      error={searchError}
    />
    <Button type="submit" variant="primary" size="sm" icon="fas fa-search" disabled={searching}>
      Buscar
    </Button>
  </form>

  {#if selectedList.length > 0}
    <div class="vinc-selected" role="region" aria-label="Seleccionados para vincular">
      <h3 class="vinc-selected__title">
        Seleccionados para vincular
        <span class="vinc-selected__count">{selectedList.length}</span>
      </h3>
      <ul class="vinc-selected__list">
        {#each selectedList as item (item.radicado_id)}
          <li class="vinc-selected__item">
            <span class="vinc-selected__number">{item.tracking_number}</span>
            <button
              type="button"
              class="vinc-selected__remove"
              aria-label="Quitar {item.tracking_number} de la selección"
              on:click={() => removeFromSelection(item.radicado_id)}
            >
              <i class="fas fa-xmark" aria-hidden="true"></i>
            </button>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if !hasSearched && !searching}
    <EmptyState
      icon="fas fa-search"
      title="Busque un radicado"
      description="Escriba un número de radicado o texto del asunto para empezar."
    />
  {:else if results.length === 0 && !searching}
    <EmptyState
      icon="fas fa-inbox"
      title="Sin resultados"
      description="No se encontraron radicados que coincidan y que no estén ya vinculados a este expediente."
    />
  {:else}
    <div class="vinc-table">
      <DataTable
        columns={COLUMNS}
        rows={results}
        totalCount={total}
        {page}
        pageSize={PAGE_SIZE}
        loading={searching}
        caption="Radicados encontrados"
        enableSelection
        rowKey="id"
        selected={pageSelectedIds}
        on:selectionChange={handleSelectionChange}
        on:pageChange={handlePageChange}
      />
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <span class="vinc-selection-count" aria-live="polite">
      {selectedList.length} radicado{selectedList.length === 1 ? "" : "s"} seleccionado{selectedList.length ===
      1
        ? ""
        : "s"}
    </span>
    <Button variant="ghost" size="sm" on:click={handleClose} disabled={linking}>Cancelar</Button>
    <Button
      variant="primary"
      size="sm"
      on:click={handleLink}
      disabled={selectedList.length === 0 || linking}
      loading={linking}
    >
      Vincular{selectedList.length > 1 ? ` (${selectedList.length})` : ""}
    </Button>
  </svelte:fragment>
</Drawer>

<style lang="scss">
  .vinc-hint {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin: 0 0 var(--op-space-4);
  }

  .vinc-search {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-4);

    :global(.op-field) {
      flex: 1;
    }
  }

  .vinc-table {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-sm);
    overflow: hidden;
  }

  .vinc-selected {
    background: var(--op-primary-light);
    border: 1px solid var(--op-primary);
    border-radius: var(--op-radius-sm);
    padding: var(--op-space-3);
    margin-bottom: var(--op-space-4);

    &__title {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
      margin: 0 0 var(--op-space-2);
    }

    &__count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: var(--op-surface);
      color: var(--op-primary-dark);
      font-size: var(--op-font-xs);
      border-radius: var(--op-radius-full);
      min-width: 20px;
      height: 20px;
      padding: 0 var(--op-space-1);
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-2);
      max-height: 140px;
      overflow-y: auto;
    }

    &__item {
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-1) var(--op-space-2);
      font-size: var(--op-font-xs);
    }

    &__number {
      font-family: monospace;
      color: var(--op-text);
    }

    &__remove {
      background: none;
      border: none;
      padding: 2px;
      color: var(--op-text-secondary);
      cursor: pointer;
      line-height: 1;

      &:hover {
        color: var(--op-error);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        border-radius: 2px;
      }
    }
  }

  .vinc-selection-count {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin-right: auto;
  }
</style>
