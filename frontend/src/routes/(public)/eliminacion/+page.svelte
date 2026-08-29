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
  Publicidad previa de la eliminación documental (RF-TRD-07, fase 2).

  Esta pantalla existe para que un tercero pueda OPONERSE antes de que un
  documento se destruya. Por eso dice, sin rodeos, qué está pasando y hasta
  cuándo se puede objetar — y por eso el canal de oposición es la ventanilla
  única que la entidad ya tiene, no un formulario nuevo: una segunda bandeja
  sería una que nadie mira, y una oposición es exactamente lo que una ventanilla
  existe para recibir.
-->
<script>
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { formatearFecha } from "$lib/utils/fecha.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: lotes = data.lotes ?? [];

  const ESTADO = {
    publicado: { chip: "pending", texto: "En plazo de oposición" },
    avalado: { chip: "info", texto: "Avalado por el Comité" },
    ejecutado: { chip: "neutral", texto: "Eliminación ejecutada" },
    // Un lote publicado y luego retirado sigue siendo público: la constancia
    // de que algo NO se eliminó le importa a quien leyó que iba a eliminarse.
    anulado: { chip: "neutral", texto: "Retirado, no se eliminará" },
  };
  const info = (e) => ESTADO[e] ?? { chip: "neutral", texto: e };

  function enPlazo(l) {
    return l.estado === "publicado" && l.publicado_hasta >= new Date().toISOString().slice(0, 10);
  }
</script>

<svelte:head>
  <title>Eliminación documental — inventarios publicados</title>
</svelte:head>

<main class="elim">
  <h1>Eliminación documental</h1>
  <p class="elim__intro">
    Antes de eliminar documentos, esta entidad publica el inventario de lo que se va a eliminar. Si
    usted considera que alguno de estos expedientes no debe destruirse,
    <strong>puede oponerse dentro del plazo</strong> radicando su solicitud por la
    <a href="/radicacion">ventanilla única</a>, citando el código del lote.
  </p>
  <p class="elim__nota">
    La eliminación se hace conforme a la Tabla de Retención Documental convalidada de la entidad
    (Ley 594 de 2000, artículo 24). Los expedientes de <strong>conservación total</strong> nunca se eliminan:
    se transfieren al archivo histórico.
  </p>

  {#if !data.disponible}
    <EmptyState
      icon="fas fa-circle-info"
      title="Publicidad no disponible"
      description="Esta instalación no tiene configurado el portal público."
    />
  {:else if data.loadError}
    <p class="elim__error" role="alert">{data.loadError}</p>
  {:else if lotes.length === 0}
    <EmptyState
      icon="fas fa-folder-open"
      title="No hay inventarios publicados"
      description="La entidad no tiene ningún lote de eliminación en publicidad ni ejecutado."
    />
  {:else}
    <ul class="elim__lista">
      {#each lotes as l (l.id)}
        <li>
          <a href="/eliminacion/{l.id}">
            <span class="elim__codigo">{l.codigo}</span>
            <StatusChip status={info(l.estado).chip} label={info(l.estado).texto} />
            <span>{l.total_items} expedientes</span>
            {#if enPlazo(l)}
              <span class="elim__plazo">
                Puede oponerse hasta el {formatearFecha(l.publicado_hasta)}
              </span>
            {:else if l.estado === "publicado"}
              <span>Plazo de oposición cerrado el {formatearFecha(l.publicado_hasta)}</span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</main>

<style lang="scss">
  .elim {
    max-width: 60rem;
    margin: 0 auto;
    padding: var(--op-space-6) var(--op-space-4);
  }

  .elim__intro,
  .elim__nota {
    margin-bottom: var(--op-space-3);
  }

  .elim__nota {
    color: var(--op-text-secondary);
  }

  .elim__error {
    color: var(--op-error);
  }

  .elim__lista {
    list-style: none;
    padding: 0;

    li a {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--op-space-3);
      padding: var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      margin-bottom: var(--op-space-2);
    }
  }

  .elim__codigo {
    font-weight: 600;
  }

  .elim__plazo {
    color: var(--op-warning);
  }
</style>
