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
  SchemaValue.svelte — Metadatos de un tipo documental / serie TRD en SOLO LECTURA.

  Contraparte de `SchemaField` (que los captura): usa el mismo schema para pintar
  la etiqueta (`title`) y el valor legible (Sí/No, etiqueta de enum, fecha, lista,
  grupo). Los valores se escapan: nada de `{@html}` (vienen del usuario).

  Las claves que la plantilla vigente no declara (cambió de versión) salen en
  «Otros datos registrados», con la clave cruda: nunca se ocultan.

  Props:
    schema     object|null — `json_schema` completo de la plantilla (con `properties`)
    value      object      — metadatos guardados
    loading    boolean     — la plantilla se está cargando
    loadError  string      — la plantilla no se pudo cargar: se DICE, y los datos
                             se muestran igual con su clave cruda
-->
<script context="module">
  let contador = 0;
</script>

<script>
  import { repartirMetadatos } from "$lib/utils/schemaField.js";
  import SchemaValueNode from "./SchemaValueNode.svelte";

  /** @type {any} */
  export let schema = null;
  /** @type {Record<string, unknown>|null|undefined} */
  export let value = null;
  export let loading = false;
  export let loadError = "";

  // Id estable por instancia para `aria-labelledby` (puede haber varias en pantalla).
  const uid = `sv-${++contador}`;

  $: reparto = repartirMetadatos(schema, value);
</script>

{#if loading}
  <p class="schema-value__nota" role="status">Cargando las etiquetas de los metadatos…</p>
{:else if loadError}
  <p class="schema-value__nota schema-value__nota--aviso" role="status">{loadError}</p>
{/if}

{#if reparto.declarados.length > 0}
  <dl class="schema-value">
    {#each reparto.declarados as e (e.key)}
      <div class="schema-value__item">
        <dt>{e.label}</dt>
        <dd><SchemaValueNode valor={e.valor} /></dd>
      </div>
    {/each}
  </dl>
{/if}

{#if reparto.sistema.length > 0}
  <h4 class="schema-value__otros" id="{uid}-sistema">Datos del sistema</h4>
  <dl class="schema-value" aria-labelledby="{uid}-sistema">
    {#each reparto.sistema as e (e.key)}
      <div class="schema-value__item">
        <dt>{e.label}</dt>
        <dd><SchemaValueNode valor={e.valor} /></dd>
      </div>
    {/each}
  </dl>
{/if}

{#if reparto.otros.length > 0}
  <h4 class="schema-value__otros" id="{uid}-otros">Otros datos registrados</h4>
  <dl class="schema-value" aria-labelledby="{uid}-otros">
    {#each reparto.otros as e (e.key)}
      <div class="schema-value__item">
        <dt>{e.label}</dt>
        <dd><SchemaValueNode valor={e.valor} /></dd>
      </div>
    {/each}
  </dl>
{/if}

<style lang="scss">
  // Los hijos (`SchemaValueNode`, anidados) llevan las mismas clases y no
  // heredan el ámbito de este componente: se alcanzan con `:global` acotado a
  // descendientes de `.schema-value`.
  .schema-value {
    margin: 0;
    display: grid;
    gap: var(--op-space-2);

    :global(.schema-value--anidada) {
      margin: 0;
      display: grid;
      gap: var(--op-space-2);
    }

    :global(.schema-value__lista) {
      margin: 0;
      padding-left: var(--op-space-4);
      list-style: disc;
    }

    :global(.schema-value__item) {
      display: grid;
      grid-template-columns: minmax(8rem, 12rem) minmax(0, 1fr);
      gap: var(--op-space-3);

      @media (max-width: 480px) {
        grid-template-columns: minmax(0, 1fr);
        gap: var(--op-space-1);
      }
    }

    :global(dt) {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    :global(dd) {
      margin: 0;
      overflow-wrap: anywhere;
      color: var(--op-text);
    }
  }

  .schema-value__nota {
    margin: 0 0 var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);

    &--aviso {
      color: var(--op-warning-fg);
    }
  }

  .schema-value__otros {
    margin: var(--op-space-3) 0 var(--op-space-2);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-text-secondary);
  }
</style>
