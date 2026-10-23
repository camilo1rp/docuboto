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
  AipVersiones.svelte — versiones del AIP de un expediente (C-6, ADR-042).
  Solo lectura. El AIP no se reescribe: al renovarse el sello de archivo se
  construye una versión NUEVA que sustituye a la anterior, que queda intacta.
  `versiones`: [{ version, aip_id, sustituye_a_aip_id, motivo, firma_ordinal, created_at }].
  Una lista ausente o vacía no pinta nada (un AIP sin renovaciones no tiene historia que mostrar).
-->
<script>
  import { formatearFechaHora } from "$lib/utils/fecha.js";

  /** @type {any[]|null|undefined} */
  export let versiones = [];

  /** @type {Record<string,string>} */
  const MOTIVOS = {
    ingesta: "Ingesta inicial",
    renovacion_lta: "Renovación del sello de archivo (LTA)",
  };

  $: ordenadas = [...(versiones ?? [])].sort((a, b) => Number(b.version) - Number(a.version));
  $: vigente = ordenadas.length ? Number(ordenadas[0].version) : null;
</script>

{#if ordenadas.length > 0}
  <section class="aipv" aria-labelledby="aipv-titulo">
    <h3 id="aipv-titulo" class="aipv__titulo">Versiones del paquete ({ordenadas.length})</h3>
    <p class="aipv__nota">
      El paquete no se reescribe: cada renovación del sello de archivo genera una versión nueva y la
      anterior queda intacta.
    </p>
    <ol class="aipv__lista">
      {#each ordenadas as v (v.aip_id)}
        <li class="aipv__item">
          <strong>Versión {v.version}</strong>
          {#if Number(v.version) === vigente}<span class="aipv__vigente">vigente</span>{/if}
          <span class="aipv__motivo">{MOTIVOS[v.motivo] ?? v.motivo}</span>
          {#if v.firma_ordinal != null}
            <span class="aipv__dato">Sello n.º {v.firma_ordinal}</span>
          {/if}
          <time class="aipv__dato" datetime={v.created_at}>{formatearFechaHora(v.created_at)}</time>
          {#if v.sustituye_a_aip_id}
            <span class="aipv__dato" title={v.sustituye_a_aip_id}
              >Sustituye a la versión anterior</span
            >
          {/if}
        </li>
      {/each}
    </ol>
  </section>
{/if}

<style lang="scss">
  .aipv {
    margin-top: var(--op-space-4);
    &__titulo {
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
    }
    &__nota {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin-bottom: var(--op-space-2);
    }
    &__lista {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    &__item {
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-2) var(--op-space-3);
      align-items: baseline;
      padding: var(--op-space-2) 0;
      border-bottom: 1px solid var(--op-border);
    }
    &__vigente {
      font-size: var(--op-font-sm);
      color: var(--op-primary-dark);
      background: var(--op-primary-light);
      border-radius: var(--op-radius);
      padding: 0 var(--op-space-2);
    }
    &__motivo {
      color: var(--op-text);
    }
    &__dato {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }
  }
</style>
