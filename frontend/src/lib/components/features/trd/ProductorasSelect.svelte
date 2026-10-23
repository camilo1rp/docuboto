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
  ProductorasSelect.svelte — selector MÚLTIPLE de dependencias productoras de
  una serie TRD (C-4, ADR-040). Se identifica por `dependencias.codigo` (entero,
  ADR-032), nunca por nombre. Grupo de casillas dentro de un `fieldset` con
  leyenda: navegable por teclado y anunciado como grupo por lectores de pantalla.

  `options`: [{ codigo, nombre }] (de GET /dependencias?activo=true).
  `selected`: number[] enlazable. Un código seleccionado que ya no está en el
  catálogo activo se sigue mostrando (marcado «inactiva o desconocida») para que
  la persona lo vea y lo quite: el backend lo rechazaría con 422.
-->
<script>
  export let id = "productoras";
  export let legend = "Dependencias productoras";
  /** @type {{ codigo: number, nombre: string }[]} */
  export let options = [];
  /** @type {number[]} */
  export let selected = [];
  export let disabled = false;
  export let error = "";
  export let hint = "";
  export let required = false;

  let filtro = "";

  $: huerfanos = selected.filter((c) => !options.some((o) => Number(o.codigo) === Number(c)));
  $: visibles = options.filter(
    (o) =>
      !filtro.trim() ||
      String(o.codigo).includes(filtro.trim()) ||
      (o.nombre ?? "").toLowerCase().includes(filtro.trim().toLowerCase()),
  );

  /** @param {number} codigo @param {boolean} on */
  function alternar(codigo, on) {
    const c = Number(codigo);
    selected = on ? [...selected.filter((x) => x !== c), c] : selected.filter((x) => x !== c);
  }
</script>

<fieldset
  class="prod"
  {disabled}
  aria-describedby={error ? `${id}-err` : hint ? `${id}-hint` : undefined}
>
  <legend class="prod__legend">
    {legend}{#if required}<span aria-hidden="true"> *</span>{/if}
  </legend>
  {#if hint}<p id="{id}-hint" class="prod__hint">{hint}</p>{/if}

  {#if options.length === 0 && huerfanos.length === 0}
    <p class="prod__vacio" role="status">
      No hay dependencias activas disponibles. Cree o active una en «Dependencias».
    </p>
  {:else}
    {#if options.length > 8}
      <input
        id="{id}-filtro"
        class="input is-small prod__filtro"
        type="search"
        placeholder="Filtrar por código o nombre"
        aria-label="Filtrar dependencias"
        bind:value={filtro}
      />
    {/if}
    <ul class="prod__lista">
      {#each huerfanos as codigo (codigo)}
        <li>
          <label class="prod__item prod__item--alerta">
            <input
              type="checkbox"
              name="{id}[]"
              value={codigo}
              checked
              on:change={(e) => alternar(codigo, e.currentTarget.checked)}
            />
            <span>{codigo} — inactiva o desconocida</span>
          </label>
        </li>
      {/each}
      {#each visibles as dep (dep.codigo)}
        <li>
          <label class="prod__item">
            <input
              type="checkbox"
              name="{id}[]"
              value={dep.codigo}
              checked={selected.includes(Number(dep.codigo))}
              on:change={(e) => alternar(dep.codigo, e.currentTarget.checked)}
            />
            <span><code>{dep.codigo}</code> {dep.nombre}</span>
          </label>
        </li>
      {/each}
    </ul>
    <p class="prod__resumen" aria-live="polite">
      {selected.length === 0
        ? "Ninguna seleccionada."
        : `${selected.length} seleccionada${selected.length === 1 ? "" : "s"}.`}
    </p>
  {/if}
  {#if error}<p id="{id}-err" class="prod__error" role="alert">{error}</p>{/if}
</fieldset>

<style lang="scss">
  .prod {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
    margin-bottom: var(--op-space-4);
    min-width: 0;

    &__legend {
      font-size: var(--op-font-sm);
      font-weight: 600;
      color: var(--op-text);
      padding: 0 var(--op-space-2);
    }
    &__hint,
    &__resumen,
    &__vacio {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin-bottom: var(--op-space-2);
    }
    &__filtro {
      margin-bottom: var(--op-space-2);
    }
    &__lista {
      list-style: none;
      margin: 0 0 var(--op-space-2);
      padding: 0;
      max-height: 12rem;
      overflow-y: auto;
    }
    &__item {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      padding: var(--op-space-1) 0;
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;

      &--alerta {
        color: var(--op-error);
      }
    }
    &__error {
      color: var(--op-error);
      font-size: var(--op-font-sm);
    }
  }
</style>
