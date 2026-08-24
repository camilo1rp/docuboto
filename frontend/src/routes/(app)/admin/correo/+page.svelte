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
  /admin/correo — Ingesta de correo (E19).

  **Capacidad construida y no ofrecida.** El backend radicaba correos desde F2 y
  no había ninguna pantalla: la ingesta solo se podía disparar con curl y su
  resultado solo se podía ver buscando los radicados a ciegas. Es el mismo hueco
  que tenían la ventanilla pública y la verificación por código, y se cierra
  igual — dando la puerta, no reimplementando lo de dentro.

  Tres cosas que esta pantalla tiene que decir bien:

  1. **Si hay buzón, y cuál.** Un administrador que ve "Ingesta de correo" en el
     índice tiene que poder saber si su entidad la tiene configurada sin
     ejecutarla. De ahí `GET /ingest/email/estado`, construido con la pantalla.

  2. **Que el acto no se deshace.** Cada correo consume un número de la secuencia
     oficial —inmutable por Ley 594— y queda marcado como leído en el buzón, así
     que el siguiente poll ya no lo verá. No es "actualizar": es radicar. Por eso
     va con confirmación explícita, como el resto de escrituras del sistema.

  3. **Qué pasó.** El resultado inmediato (los números creados, enlazados a su
     radicado) y el historial, que se lee de `audit_log` — el registro que ya
     existe— en vez de inventar un segundo libro que pudiera discrepar de él.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  $: estado = data.estado ?? null;
  $: estadoError = data.estadoError ?? null;
  $: historial = data.historial ?? [];
  $: historialError = data.historialError ?? null;

  /**
   * Los motivos del backend son códigos; cada uno se explica en términos de lo
   * que el administrador puede hacer al respecto, que no siempre es "arreglarlo"
   * — un buzón de otra entidad no es un error de configuración suyo.
   */
  const MOTIVOS = {
    imap_no_configurado: {
      titulo: "La ingesta de correo no está configurada",
      detalle:
        "Este despliegue no tiene un buzón IMAP declarado, así que ningún correo se radica automáticamente. Se habilita en la configuración del servicio de radicación (IMAP_HOST y credenciales del buzón).",
    },
    entidad_no_declarada: {
      titulo: "El buzón no declara a qué entidad pertenece",
      detalle:
        "Hay un buzón configurado, pero no dice en qué entidad debe radicar. Mientras no lo declare (IMAP_TENANT_SLUG) la ingesta queda cerrada a propósito: adivinar la entidad por la sesión de quien la dispara llevaría correos de una institución al archivo de otra, y eso no se deshace.",
    },
    buzon_de_otra_entidad: {
      titulo: "El buzón de ingesta es de otra entidad",
      detalle:
        "El buzón configurado en este despliegue pertenece a otra institución. Su correspondencia se radica en el archivo de esa entidad, no en el de la suya.",
    },
  };

  $: motivo = estado && !estado.habilitada ? (MOTIVOS[estado.motivo] ?? null) : null;

  let confirmando = false;
  let ejecutando = false;
  /** @type {{ingested: number, tracking_numbers: string[]} | null} */
  let resultado = null;

  async function traerCorreo() {
    ejecutando = true;
    try {
      const res = await fetch("/admin/correo/api/poll", { method: "POST" });
      const cuerpo = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeError(cuerpo, `No se pudo traer el correo (${codigoError(cuerpo)}).`));
        return;
      }
      resultado = cuerpo;
      confirmando = false;
      if (cuerpo.ingested === 0) {
        notifySuccess("No había correos sin leer en el buzón.");
      } else {
        notifySuccess(
          cuerpo.ingested === 1
            ? "Se radicó 1 correo."
            : `Se radicaron ${cuerpo.ingested} correos.`,
        );
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      ejecutando = false;
    }
  }

  function fecha(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("es-CO");
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Ingesta de correo" }]);
  });
</script>

<svelte:head>
  <title>Ingesta de correo — OrpycaMCP</title>
</svelte:head>

<div class="correo">
  <header class="correo__header">
    <h1 class="correo__title">Ingesta de correo</h1>
    <p class="correo__subtitle">
      Radica como documento de Entrada cada correo sin leer del buzón institucional.
    </p>
  </header>

  {#if estadoError}
    <EmptyState
      icon="fas fa-triangle-exclamation"
      title={estadoError.status === 403
        ? "Sin permiso para ver la ingesta de correo"
        : "No se pudo consultar el estado de la ingesta"}
      description="{estadoError.detail} Esto no significa que la ingesta esté apagada: no se pudo preguntar."
    />
  {:else if motivo}
    <Card>
      <div class="correo__aviso">
        <span class="correo__aviso-icono" aria-hidden="true"
          ><i class="fas fa-plug-circle-xmark"></i></span
        >
        <div class="correo__aviso-texto">
          <h2 class="correo__aviso-titulo">{motivo.titulo}</h2>
          <p>{motivo.detalle}</p>
        </div>
      </div>
    </Card>
  {:else if estado?.habilitada}
    <Card title="Buzón configurado">
      <dl class="correo__datos">
        <dt>Cuenta</dt>
        <dd><code class="op-dato">{estado.cuenta || "—"}</code></dd>
        <dt>Servidor</dt>
        <dd><code class="op-dato">{estado.servidor || "—"}</code></dd>
        <dt>Carpeta</dt>
        <dd><code class="op-dato">{estado.buzon || "—"}</code></dd>
      </dl>
      <div class="correo__acciones">
        <Button icon="fas fa-inbox-in" on:click={() => (confirmando = true)}>
          Traer y radicar el correo sin leer
        </Button>
      </div>
    </Card>
  {/if}

  {#if resultado}
    <Card title="Resultado de la última ingesta">
      {#if resultado.ingested === 0}
        <p>No había correos sin leer en el buzón. No se creó ningún radicado.</p>
      {:else}
        <p>
          Se radicaron {resultado.ingested}
          {resultado.ingested === 1 ? "correo" : "correos"}. Cada uno es ya un documento de Entrada
          con número oficial:
        </p>
        <ul class="correo__radicados" role="list">
          {#each resultado.tracking_numbers as numero (numero)}
            <li>
              <a href="/busqueda?q={encodeURIComponent(numero)}"
                ><code class="op-dato">{numero}</code></a
              >
            </li>
          {/each}
        </ul>
      {/if}
    </Card>
  {/if}

  <Card title="Historial de ingestas">
    {#if historialError}
      <EmptyState
        icon="fas fa-triangle-exclamation"
        title={historialError.status === 403
          ? "Sin permiso para consultar el registro de auditoría"
          : "No se pudo cargar el historial"}
        description={historialError.detail}
      />
    {:else if historial.length === 0}
      <EmptyState
        icon="fas fa-clock-rotate-left"
        title="Todavía no se ha ejecutado ninguna ingesta"
        description="Cada ejecución queda registrada en la auditoría inmutable, con quién la disparó y qué números de radicado consumió."
      />
    {:else}
      <table class="table is-fullwidth is-striped">
        <caption class="is-sr-only">Ejecuciones de la ingesta de correo</caption>
        <thead>
          <tr>
            <th scope="col">Fecha</th>
            <th scope="col">Buzón</th>
            <th scope="col">Correos radicados</th>
          </tr>
        </thead>
        <tbody>
          {#each historial as asiento (asiento.id)}
            <tr>
              <td>{fecha(asiento.ts)}</td>
              <td><code class="op-dato">{asiento.object_ref || "—"}</code></td>
              <td>{asiento.payload?.ingeridos ?? "—"}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      <p class="correo__nota">
        El detalle de cada ejecución —incluidos los números de radicado que creó— está en
        <a href="/admin/auditoria">Auditoría</a>.
      </p>
    {/if}
  </Card>
</div>

<Modal bind:open={confirmando} title="Confirmar la ingesta de correo" size="md">
  <div class="correo__confirmacion">
    <p>
      Cada correo sin leer del buzón se convertirá en un <strong
        >documento de Entrada con número de radicado oficial</strong
      >. Los números que consuma no se pueden reutilizar ni liberar.
    </p>
    <p>
      Además, los correos quedarán <strong>marcados como leídos</strong> en el buzón: si algo sale mal,
      una segunda ejecución ya no los volverá a ver.
    </p>
    <p>Este acto no se deshace.</p>
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (confirmando = false)} disabled={ejecutando}>
      Cancelar
    </Button>
    <Button loading={ejecutando} on:click={traerCorreo}>Traer y radicar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .correo {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);
  }

  .correo__title {
    font-size: var(--op-font-xl);
    font-weight: 700;
    color: var(--op-text);
  }

  .correo__subtitle {
    color: var(--op-text-secondary);
  }

  .correo__aviso {
    display: flex;
    gap: var(--op-space-4);
    align-items: flex-start;
  }

  .correo__aviso-icono {
    color: var(--op-warning);
    font-size: var(--op-font-lg);
  }

  .correo__aviso-titulo {
    font-weight: 700;
    margin-bottom: var(--op-space-2);
  }

  .correo__datos {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: var(--op-space-2) var(--op-space-4);
    align-items: center;
    margin-bottom: var(--op-space-4);

    dt {
      color: var(--op-text-secondary);
    }
  }

  .correo__radicados {
    margin-top: var(--op-space-3);
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-2);
  }

  .correo__nota {
    margin-top: var(--op-space-3);
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }

  /*
    Cada párrafo es un hijo directo; el contenedor NO es flex en fila por eso
    mismo — la advertencia de un acto irreversible troceada en columnas fue un
    defecto real de este repo (ver `prosa-en-flex.test.js`).
  */
  .correo__confirmacion {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }
</style>
