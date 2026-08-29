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
  El inventario publicado de un lote (RF-TRD-07, fase 2).

  La descripción de un expediente clasificado NO llega desde el backend, y la
  fila SÍ. Aquí se DICE por qué falta en vez de dejar un hueco mudo: quien lee
  esta página tiene que poder distinguir «no tiene nombre» de «su nombre está
  reservado por ley», porque de eso depende que sepa si tiene algo que objetar.
-->
<script>
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { formatearFecha } from "$lib/utils/fecha.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: lote = data.lote;
  $: items = lote?.items ?? [];
  $: hayReservados = items.some((i) => i.nombre_reservado);
  $: enPlazo =
    lote?.estado === "publicado" && lote?.publicado_hasta >= new Date().toISOString().slice(0, 10);

  const DISPOSICION = {
    E: "Eliminación",
    S: "Selección",
    M: "Microfilmación / digitalización",
    CT: "Conservación total",
  };
</script>

<svelte:head>
  <title>Inventario de eliminación {lote?.codigo ?? ""}</title>
</svelte:head>

<main class="elim">
  <p><a href="/eliminacion">← Todos los inventarios publicados</a></p>
  <h1>Lote {lote.codigo}</h1>

  {#if enPlazo}
    <p class="elim__plazo" role="note">
      <strong>Puede oponerse hasta el {formatearFecha(lote.publicado_hasta)}.</strong>
      Si considera que alguno de estos expedientes no debe eliminarse, radique su solicitud por la
      <a href="/radicacion">ventanilla única</a> citando el código <strong>{lote.codigo}</strong>.
    </p>
  {:else if lote.estado === "publicado"}
    <p role="note">
      El plazo para oponerse terminó el {formatearFecha(lote.publicado_hasta)}.
    </p>
  {:else if lote.estado === "anulado"}
    <p class="elim__plazo" role="note">
      <strong>Esta eliminación se retiró y no se va a ejecutar.</strong>
      {#if lote.anulado_motivo}Motivo: {lote.anulado_motivo}{/if}
      El inventario se conserva publicado como constancia: quien leyó que estos expedientes iban a eliminarse
      tiene que poder ver en qué quedó.
    </p>
  {:else if lote.estado === "ejecutado"}
    <p role="note">
      Esta eliminación <strong>ya se ejecutó</strong> el {formatearFecha(lote.ejecutado_at)}. El
      inventario se conserva como constancia de qué se eliminó.
    </p>
  {/if}

  {#if lote.acto_administrativo}
    <p>
      Avalada por el Comité el {formatearFecha(lote.fecha_aval)} mediante
      {lote.acto_administrativo}.
    </p>
  {/if}

  {#if hayReservados}
    <p class="elim__reservados" role="note">
      Algunos expedientes aparecen <strong>sin descripción</strong>. No es un dato que falte: su
      denominación está clasificada o reservada y no puede publicarse (Ley 1712 de 2014). La entidad
      publica igualmente su código, su serie, sus fechas extremas y su volumen, para que cualquiera
      pueda oponerse también sobre ellos.
    </p>
  {/if}

  <div class="elim__tabla">
    <table>
      <caption class="is-sr-only">Expedientes incluidos en el lote {lote.codigo}</caption>
      <thead>
        <tr>
          <th scope="col">Expediente</th>
          <th scope="col">Descripción</th>
          <th scope="col">Serie</th>
          <th scope="col">Fechas extremas</th>
          <th scope="col">Documentos</th>
          <th scope="col">Disposición</th>
        </tr>
      </thead>
      <tbody>
        {#each items as i (i.expediente_code)}
          <tr>
            <td>{i.expediente_code}</td>
            <td>
              {#if i.nombre_reservado}
                <span class="elim__reservado">Denominación reservada por ley</span>
              {:else}
                {i.expediente_nombre ?? "—"}
              {/if}
            </td>
            <td>{i.trd_serie_code ?? "—"}</td>
            <td>
              {formatearFecha(i.fecha_extrema_inicial)} — {formatearFecha(i.fecha_extrema_final)}
            </td>
            <td>{i.radicados_incluidos ?? "—"}</td>
            <td>
              <StatusChip
                status={i.disposition_code === "CT" ? "active" : "error"}
                label={DISPOSICION[i.disposition_code] ?? i.disposition_code}
              />
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</main>

<style lang="scss">
  .elim {
    max-width: 70rem;
    margin: 0 auto;
    padding: var(--op-space-6) var(--op-space-4);
  }

  .elim__plazo,
  .elim__reservados {
    background: var(--op-bg);
    border-left: 3px solid var(--op-warning);
    padding: var(--op-space-2) var(--op-space-3);
    margin: var(--op-space-3) 0;
  }

  .elim__reservado {
    color: var(--op-text-secondary);
    font-style: italic;
  }

  .elim__tabla {
    overflow-x: auto;

    table {
      width: 100%;
      border-collapse: collapse;
    }

    th,
    td {
      padding: var(--op-space-2);
      border-bottom: 1px solid var(--op-border);
      text-align: left;
    }
  }
</style>
