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
  Índice público de información clasificada y reservada, por serie documental
  (Ley 1712 de 2014, art. 20; ADR-039). Sin sesión.

  Cada fila dice QUÉ categoría de información está protegida en una serie, con
  qué causal de la ley, desde cuándo y hasta cuándo, y quién responde por ella.
  Nunca dice cuántos documentos son ni cuáles: el índice es content-free.
-->
<script>
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { formatearFecha } from "$lib/utils/fecha.js";
  import { POR_CLASIFICAR, POR_CLASIFICAR_ETIQUETA } from "$lib/utils/reserva.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: filas = data.filas ?? [];

  const SIN_SERIE = "Sin serie documental asignada";

  /** @param {any} f */
  function serieTexto(f) {
    if (f.serie_codigo == null) return SIN_SERIE;
    return `${f.serie_codigo} — ${f.serie_nombre}`;
  }

  /** @param {any} f */
  function subserieTexto(f) {
    if (f.subserie_codigo == null && !f.subserie_nombre) return "—";
    return [f.subserie_codigo, f.subserie_nombre].filter(Boolean).join(" — ");
  }

  /** @param {any} f */
  function causalTexto(f) {
    if (f.causal_codigo === POR_CLASIFICAR) return POR_CLASIFICAR_ETIQUETA;
    const lit = f.literal ? ` lit. ${f.literal}` : "";
    return `${f.articulo ?? ""}${lit}: ${f.causal_descripcion}`;
  }

  /** @param {any} f */
  function plazoTexto(f) {
    return f.plazo_maximo_meses == null
      ? "Mientras subsista el derecho"
      : `${f.plazo_maximo_meses} meses`;
  }

  /** @param {string|null|undefined} d */
  const fecha = (d) => (d ? formatearFecha(d) : "—");
</script>

<svelte:head>
  <title>Índice de información clasificada y reservada</title>
</svelte:head>

<main class="indice">
  <h1>Índice de información clasificada y reservada</h1>
  <p class="indice__intro">
    En cumplimiento del artículo 20 de la Ley 1712 de 2014, esta entidad publica las categorías de
    información que tiene <strong>clasificada</strong> (artículo 18) o <strong>reservada</strong>
    (artículo 19), organizadas por serie documental. El índice no revela el contenido ni los números de
    los documentos protegidos.
  </p>

  {#if !data.disponible}
    <EmptyState
      icon="fas fa-circle-info"
      title="Índice no disponible"
      description="Esta entidad no tiene portal público disponible."
    />
  {:else if data.loadError}
    <p class="indice__error" role="alert">{data.loadError}</p>
  {:else if filas.length === 0}
    <EmptyState
      icon="fas fa-folder-open"
      title="No hay información clasificada ni reservada"
      description="La entidad no tiene, por ahora, información clasificada o reservada para publicar."
    />
  {:else}
    <p class="indice__acciones">
      <a class="indice__csv" href="/indice-clasificados/csv" download>
        <i class="fas fa-file-csv" aria-hidden="true"></i>
        Descargar el índice en CSV
      </a>
    </p>
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      class="indice__tabla op-table-scroll"
      tabindex="0"
      role="region"
      aria-label="Índice de información clasificada y reservada"
    >
      <table>
        <caption class="sr-only">
          Información clasificada y reservada por serie documental
        </caption>
        <thead>
          <tr>
            <th scope="col">Serie</th>
            <th scope="col">Subserie</th>
            <th scope="col">Categoría</th>
            <th scope="col">Causal</th>
            <th scope="col">Fundamento legal</th>
            <th scope="col">Excepción</th>
            <th scope="col">Generada desde</th>
            <th scope="col">Generada hasta</th>
            <th scope="col">Calificada desde</th>
            <th scope="col">Calificada hasta</th>
            <th scope="col">Plazo máximo</th>
            <th scope="col">Responsable de la información</th>
          </tr>
        </thead>
        <tbody>
          {#each filas as f, i (`${f.serie_codigo}|${f.subserie_codigo}|${f.causal_codigo}|${i}`)}
            <tr>
              <th scope="row" class:indice__sinserie={f.serie_codigo == null}>{serieTexto(f)}</th>
              <td>{subserieTexto(f)}</td>
              <td>
                <StatusChip
                  status={f.categoria === "clasificada" ? "error" : "warning"}
                  label={f.categoria === "clasificada" ? "Clasificada" : "Reservada"}
                />
              </td>
              <td>
                {#if f.causal_codigo === POR_CLASIFICAR}
                  <StatusChip status="warning" label={causalTexto(f)} />
                {:else}
                  {causalTexto(f)}
                {/if}
              </td>
              <td>{f.fundamento_norma ?? "—"}</td>
              <td>{f.excepcion === "total" ? "Total" : (f.excepcion ?? "—")}</td>
              <td>{fecha(f.fecha_generacion_desde)}</td>
              <td>{fecha(f.fecha_generacion_hasta)}</td>
              <td>{fecha(f.fecha_calificacion_desde)}</td>
              <td>{fecha(f.fecha_calificacion_hasta)}</td>
              <td>{plazoTexto(f)}</td>
              <td>{f.responsable_informacion ?? "—"}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</main>

<style lang="scss">
  .indice {
    max-width: 80rem;
    margin: 0 auto;
    padding: var(--op-space-6) var(--op-space-4);
  }

  .indice__intro {
    margin-bottom: var(--op-space-4);
  }

  .indice__error {
    color: var(--op-error);
  }

  .indice__acciones {
    margin-bottom: var(--op-space-3);
  }

  .indice__csv {
    color: var(--op-primary-dark);
    text-decoration: underline;
  }

  .indice__tabla {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-surface);

    table {
      width: 100%;
      border-collapse: collapse;
    }

    th,
    td {
      padding: var(--op-space-2) var(--op-space-3);
      text-align: left;
      vertical-align: top;
      border-bottom: 1px solid var(--op-border);
    }

    thead th {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      white-space: nowrap;
    }

    tbody th {
      font-weight: 600;
    }
  }

  .indice__sinserie {
    color: var(--op-text-secondary);
    font-style: italic;
  }
</style>
