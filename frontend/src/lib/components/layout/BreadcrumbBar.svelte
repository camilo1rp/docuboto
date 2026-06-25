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
  BreadcrumbBar.svelte — Barra de migas de pan.

  Recibe los crumbs del store ui.js ($breadcrumbs) o como prop.
  El último ítem no es enlace (es la página actual, aria-current="page").
  Accesible: rol "navigation" + aria-label + aria-current en el ítem activo.
-->
<script>
  import { breadcrumbs as storeCrumbs } from "$lib/stores/ui.js";

  /**
   * Permite sobreescribir los crumbs del store (para páginas que no usan setBreadcrumbs).
   * @type {Array<{label: string; href?: string}>|undefined}
   */
  export let crumbs = undefined;

  $: displayCrumbs = crumbs ?? $storeCrumbs;
</script>

{#if displayCrumbs.length > 0}
  <nav class="breadcrumb-bar" aria-label="Ruta de navegación">
    <ol class="breadcrumb-bar__list" aria-label="Ruta de navegación">
      {#each displayCrumbs as crumb, i (i)}
        {@const isLast = i === displayCrumbs.length - 1}
        <li class="breadcrumb-bar__item">
          {#if !isLast && crumb.href}
            <a href={crumb.href} class="breadcrumb-bar__link">{crumb.label}</a>
          {:else}
            <span class="breadcrumb-bar__current" aria-current={isLast ? "page" : undefined}
              >{crumb.label}</span
            >
          {/if}

          {#if !isLast}
            <span class="breadcrumb-bar__sep" aria-hidden="true">/</span>
          {/if}
        </li>
      {/each}
    </ol>
  </nav>
{/if}

<style lang="scss">
  .breadcrumb-bar {
    height: var(--op-breadcrumb-height);
    display: flex;
    align-items: center;
    padding: 0 var(--op-space-5);
    background: var(--op-bg);
    border-bottom: 1px solid var(--op-border);

    &__list {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      list-style: none;
      margin: 0;
      padding: 0;
    }

    &__item {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
    }

    &__link {
      font-size: var(--op-font-sm);
      color: var(--op-primary);
      text-decoration: none;
      font-weight: var(--op-weight-medium);

      &:hover {
        text-decoration: underline;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        border-radius: var(--op-radius-sm);
      }
    }

    &__current {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-regular);
    }

    &__sep {
      color: var(--op-border);
      font-size: var(--op-font-sm);
      user-select: none;
    }
  }
</style>
