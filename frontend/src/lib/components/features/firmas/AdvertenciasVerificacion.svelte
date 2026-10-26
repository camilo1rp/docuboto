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
  AdvertenciasVerificacion.svelte — Avisos de una firma que SIGUE siendo válida.

  `advertencias[]` de `/signature/verify`, de `/indice/verify` y de
  `/acta/verificacion` (ADR-016 addendum 2026-10-04): un verificador que los
  calla da por limpia una firma con reservas. No se pinta nada si no hay avisos.
  Cada código se traduce; uno desconocido se muestra con su código, nunca se omite.
-->
<script>
  import { etiquetaAdvertencia } from "$lib/utils/firmaInobtenible.js";

  /** @type {string[]|null|undefined} */
  export let advertencias = [];

  $: lista = Array.isArray(advertencias) ? advertencias.filter(Boolean) : [];
</script>

{#if lista.length > 0}
  <div class="advertencias-verificacion" role="status" data-testid="advertencias-verificacion">
    <p class="advertencias-verificacion__title">
      <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
      La firma es válida, pero con reservas
    </p>
    <ul>
      {#each lista as codigo (codigo)}
        <li>{etiquetaAdvertencia(codigo)}</li>
      {/each}
    </ul>
  </div>
{/if}

<style lang="scss">
  .advertencias-verificacion {
    border: 1px solid var(--op-warning);
    border-radius: var(--op-radius-sm);
    padding: var(--op-space-3);
    font-size: var(--op-font-sm);
    color: var(--op-text);

    &__title {
      font-weight: var(--op-weight-bold);
      display: flex;
      align-items: center;
      gap: var(--op-space-2);

      i {
        color: var(--op-warning);
      }
    }

    ul {
      margin: var(--op-space-2) 0 0 var(--op-space-4);
      list-style: disc;
      color: var(--op-text-secondary);
    }
  }
</style>
