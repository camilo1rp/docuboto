<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  Loader.svelte — Indicador de carga y skeletons.

  Props:
    type     'spinner'|'skeleton'|'page'  (default: 'spinner')
    size     'sm'|'md'|'lg'              (default: 'md', solo spinner)
    lines    number                       (default: 3, solo skeleton)
    label    string                       — texto para aria-label
-->
<script>
  /** @type {'spinner'|'skeleton'|'page'} */
  export let type = "spinner";

  /** @type {'sm'|'md'|'lg'} */
  export let size = "md";

  export let lines = 3;
  export let label = "Cargando…";
</script>

{#if type === "spinner"}
  <div class="op-loader op-loader--{size}" role="status" aria-label={label} aria-busy="true">
    <span class="op-loader__spin" aria-hidden="true"></span>
    <span class="sr-only">{label}</span>
  </div>
{:else if type === "skeleton"}
  <div role="status" aria-label={label} aria-busy="true">
    <span class="sr-only">{label}</span>
    {#each Array(lines) as _, i (i)}
      <div
        class="skeleton skeleton--text"
        style="width: {i % 3 === 2 ? '60%' : '100%'}; margin-bottom: var(--op-space-2);"
        aria-hidden="true"
      ></div>
    {/each}
  </div>
{:else if type === "page"}
  <div class="op-loader-page" role="status" aria-label={label} aria-busy="true">
    <span class="op-loader__spin op-loader__spin--lg" aria-hidden="true"></span>
    <p class="op-loader-page__text text-secondary text-sm">{label}</p>
  </div>
{/if}

<style lang="scss">
  .op-loader {
    display: inline-flex;
    align-items: center;
    justify-content: center;

    &--sm .op-loader__spin {
      width: 16px;
      height: 16px;
    }
    &--md .op-loader__spin {
      width: 24px;
      height: 24px;
    }
    &--lg .op-loader__spin {
      width: 36px;
      height: 36px;
    }

    &__spin {
      border: 2px solid var(--op-border);
      border-top-color: var(--op-primary);
      border-radius: 50%;
      animation: op-spin 0.7s linear infinite;

      &--lg {
        width: 36px;
        height: 36px;
        border-width: 3px;
      }
    }
  }

  .op-loader-page {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--op-space-4);
    padding: var(--op-space-8) var(--op-space-5);

    &__text {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      margin: 0;
    }
  }

  @keyframes op-spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
