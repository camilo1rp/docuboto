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
  SchemaValueNode.svelte — Pinta UN valor ya formateado (`formatearValorSchema`),
  recursivo para listas y grupos. Interno de `SchemaValue`. Todo es texto
  escapado; los grupos son `<dl>` anidadas para que el lector de pantalla
  anuncie la estructura.
-->
<script>
  /** @type {import('$lib/utils/schemaField.js').ValorPresentado} */
  export let valor;
</script>

{#if valor.kind === "list"}
  <ul class="schema-value__lista">
    {#each valor.items as item, i (i)}
      <li><svelte:self valor={item} /></li>
    {/each}
  </ul>
{:else if valor.kind === "group"}
  <dl class="schema-value schema-value--anidada">
    {#each valor.entries as e (e.key)}
      <div class="schema-value__item">
        <dt>{e.label}</dt>
        <dd><svelte:self valor={e.valor} /></dd>
      </div>
    {/each}
  </dl>
{:else if valor.kind === "code"}
  <code>{valor.text}</code>
{:else}
  {valor.text}
{/if}
