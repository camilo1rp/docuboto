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
  DataTable.svelte — Tabla paginada con soporte para X-Total-Count.

  Props:
    columns      Array<{ key: string; label: string; width?: string }>
    rows         Array<Record<string, any>>
    totalCount   number   — valor de X-Total-Count del backend
    page         number   — página actual (1-indexed), bindable
    pageSize     number   — registros por página (default 20)
    loading      boolean  — muestra skeleton
    caption      string   — descripción accesible de la tabla

  Slots:
    row   — slot con let:row para personalizar cada fila completa.
            Si no se proporciona, se renderizan las celdas con row[col.key].
            Ejemplo:
              <DataTable ...>
                <svelte:fragment slot="row" let:row>
                  <td>{row.radicado}</td>
                  <td><StatusChip status={row.estado} /></td>
                </svelte:fragment>
              </DataTable>

  Eventos:
    on:pageChange  { detail: { page: number } }

  Accesibilidad:
    - <caption> con descripción
    - scope="col" en encabezados
    - aria-busy en estado de carga
    - aria-label en botones de paginación
-->
<script>
  import EmptyState from "./EmptyState.svelte";

  /** @type {Array<{key: string; label: string; width?: string}>} */
  export let columns = [];

  /** @type {Array<Record<string,any>>} */
  export let rows = [];

  export let totalCount = 0;
  export let page = 1;
  export let pageSize = 20;
  export let loading = false;
  export let caption = "Tabla de datos";

  import { createEventDispatcher } from "svelte";
  const dispatch = createEventDispatcher();

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
</script>

<div class="op-datatable" aria-busy={loading}>
  <div class="op-datatable__wrapper">
    <table class="op-datatable__table" aria-label={caption}>
      <caption class="sr-only">{caption}</caption>
      <thead>
        <tr>
          {#each columns as col (col.key)}
            <th scope="col" class="op-datatable__th" style={col.width ? `width: ${col.width}` : ""}
              >{col.label}</th
            >
          {/each}
        </tr>
      </thead>
      <tbody>
        {#if loading}
          {#each Array(5) as _, i (i)}
            <tr class="op-datatable__row" aria-hidden="true">
              {#each columns as col (col.key)}
                <td class="op-datatable__td">
                  <span class="skeleton skeleton--text"></span>
                </td>
              {/each}
            </tr>
          {/each}
        {:else if rows.length === 0}
          <tr>
            <td colspan={columns.length} class="op-datatable__td">
              <EmptyState
                icon="fas fa-inbox"
                title="Sin resultados"
                description="No hay registros que coincidan con los criterios seleccionados."
              />
            </td>
          </tr>
        {:else}
          {#each rows as row, rowIdx (rowIdx)}
            <tr class="op-datatable__row">
              {#if $$slots.row}
                <!-- Personalización completa de celdas por el llamador -->
                <slot name="row" {row} {rowIdx} />
              {:else}
                <!-- Renderizado automático por columns[].key -->
                {#each columns as col (col.key)}
                  <td class="op-datatable__td" data-label={col.label}>
                    {row[col.key] ?? "—"}
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
        {start}–{end} de {totalCount}
      </span>

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
    </nav>
  {/if}
</div>

<style lang="scss">
  .op-datatable {
    display: flex;
    flex-direction: column;

    &__wrapper {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
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
    }

    &__row {
      &:not(:last-child) {
        border-bottom: 1px solid var(--op-border);
      }

      &:hover {
        background: var(--op-bg);
      }
    }

    &__td {
      padding: var(--op-space-3) var(--op-space-4);
      color: var(--op-text);
      vertical-align: middle;
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
