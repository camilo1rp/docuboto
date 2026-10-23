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
  SoportesCitados.svelte — «Constancias que pierden su soporte» (C-5 / C14).

  Al proponer un lote de eliminación se congelan las constancias de firma
  inobtenible vigentes que citan, como soporte, un radicado del expediente. No
  es un rechazo: es lo que el Comité avala con el acto. Antes del aval el anexo
  sigue protegido; ejecutar el lote lo da de baja y la constancia firmada NO se
  modifica (ADR-030: append-only).

  Props:
    soportes  Array<{excepcion_id, expediente_code, transferencia_id, rol, ordinal}> (SoporteCitado de archive)
-->
<script>
  /** @type {any[]} */
  export let soportes = [];
</script>

{#if soportes?.length}
  <div class="soportes" role="note">
    <h3 class="soportes__titulo">Constancias que pierden su soporte</h3>
    <p class="soportes__texto">
      Estas constancias de firma inobtenible citan un anexo de este lote. Avalar el lote autoriza
      dar de baja ese soporte; la constancia firmada se conserva sin cambios.
    </p>
    <ul>
      {#each soportes as s (s.excepcion_id)}
        <li>
          {#if s.transferencia_id}
            <a href="/transferencias/{encodeURIComponent(s.transferencia_id)}"
              >{s.transferencia_codigo ?? "Constancia en la transferencia"}</a
            >
          {:else}
            <strong>{s.transferencia_codigo ?? "—"}</strong>
          {/if}
          · expediente {s.expediente_code ?? "—"} · rol {s.rol}{#if s.ordinal != null}
            · n.º {s.ordinal}{/if}
        </li>
      {/each}
    </ul>
  </div>
{/if}

<style>
  .soportes {
    border-left: 3px solid var(--op-warning);
    background: var(--op-bg);
    padding: var(--op-space-3, 0.75rem);
    margin: var(--op-space-3, 0.75rem) 0;
  }
  .soportes__titulo {
    font-weight: var(--op-weight-bold);
    margin: 0 0 var(--op-space-2, 0.5rem);
  }
  .soportes__texto {
    margin: 0 0 var(--op-space-2, 0.5rem);
  }
</style>
