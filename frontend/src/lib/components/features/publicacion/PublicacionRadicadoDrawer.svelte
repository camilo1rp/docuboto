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
  PublicacionRadicadoDrawer.svelte — Acto de publicación de oficio de UN
  radicado (Ley 1712/2014 art. 12).

  GET/POST /bandeja/api/[id]/publicacion (proxy SSR, D-05).

  Qué NO hace, a propósito: no ofrece publicar un radicado reservado ni uno
  anulado. El backend lo rechaza con un 404 neutro (el mismo que da un radicado
  inexistente, para no ser un oráculo), así que la pantalla lo dice ANTES en vez
  de dejar que el usuario redacte un fundamento para recibir después un "no
  existe" incomprensible. Cuando el estado no permite publicar, el motivo se
  explica y el botón no está.

  Publicar no es lo contrario de clasificar: retirar la reserva de un documento
  NO lo publica. Por eso el aviso del drawer nombra las dos consecuencias
  reales — que el radicado queda cosechable por OAI-PMH y CMIS — en vez de
  hablar en abstracto de "hacerlo público".

  Props:
    open       boolean
    radicado   {id, tracking_number} | null

  Eventos:
    on:close    — se cerró (ejecutado o cancelado)
    on:changed  — el acto se ejecutó; detail = { publicable, publicado_at }
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  export let open = false;
  /** @type {{id: string; tracking_number: string} | null} */
  export let radicado = null;

  const dispatch = createEventDispatcher();

  let loading = false;
  // Igual que en el drawer de disposición: `loadError` solo se fija si el GET
  // falló de verdad. "No publicado" es un estado legítimo, no un error.
  let loadError = "";
  let saving = false;

  /** @type {{tracking_number: string; nivel_seguridad: number | null; status: string; publicable: boolean; publicado_at: string | null} | null} */
  let estado = null;
  let fundamento = "";

  // El acto que ofrece el drawer es siempre el CONTRARIO del estado actual:
  // si está publicado, retirar; si no, publicar.
  $: publicar = !estado?.publicable;
  $: esReservado = estado != null && estado.nivel_seguridad != null && estado.nivel_seguridad > 1;
  $: esAnulado = estado?.status === "anulado";
  // Un radicado reservado o anulado no admite el acto en NINGÚN sentido: si
  // llegó a publicarse y después se clasificó, retirarlo tampoco pasa por aquí
  // (el backend exige nivel público en el propio WHERE). Se dice, no se simula.
  $: actoDisponible = estado != null && !esReservado && !esAnulado;
  $: canSubmit = actoDisponible && fundamento.trim().length > 0 && !saving;

  $: if (open && radicado) cargarEstado();

  async function cargarEstado() {
    if (!radicado) return;
    estado = null;
    fundamento = "";
    loadError = "";
    loading = true;
    try {
      const res = await fetch(`/bandeja/api/${encodeURIComponent(radicado.id)}/publicacion`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        loadError = mensajeError(
          body,
          `Error ${res.status} al consultar el estado de publicación.`,
        );
        return;
      }
      estado = await res.json();
    } catch {
      loadError = "No se pudo conectar al servidor para consultar el estado de publicación.";
    } finally {
      loading = false;
    }
  }

  function close() {
    dispatch("close");
  }

  function formatoFecha(iso) {
    return formatearFechaHora(iso);
  }

  async function submit() {
    if (!radicado || !canSubmit) return;
    saving = true;
    try {
      const res = await fetch(`/bandeja/api/${encodeURIComponent(radicado.id)}/publicacion`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ publicar, fundamento: fundamento.trim() }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(
        publicar
          ? `${radicado.tracking_number} queda publicado en el esquema de publicación.`
          : `Se retiró la publicación de ${radicado.tracking_number}.`,
      );
      dispatch("changed", {
        publicable: Boolean(result.publicable),
        publicado_at: result.publicado_at ?? null,
      });
      dispatch("close");
    } catch (err) {
      if (err?.status === 404) {
        notifyError(
          "No fue posible ejecutar el acto: el radicado no existe, no tiene acceso a él, " +
            "o su estado no lo admite.",
        );
      } else if (err?.status === 403) {
        notifyError("No tiene el permiso necesario para decidir sobre la publicación.");
      } else {
        notifyError(mensajeError(err, "No se pudo ejecutar el acto de publicación."));
      }
    } finally {
      saving = false;
    }
  }
</script>

<Drawer
  {open}
  title={radicado ? `Acto de publicación — ${radicado.tracking_number}` : "Acto de publicación"}
  side="right"
  width="460px"
  on:close={close}
>
  {#if loading}
    <Loader type="skeleton" lines={5} label="Consultando estado de publicación…" />
  {:else if loadError}
    <p class="pub-error" role="alert">
      <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
      {loadError}
    </p>
    <Button variant="primary" size="sm" on:click={cargarEstado}>Reintentar</Button>
  {:else if estado}
    <dl class="pub-estado">
      <dt>Estado</dt>
      <dd>
        <StatusChip
          status={estado.publicable ? "success" : "neutral"}
          label={estado.publicable ? "Publicado" : "No publicado"}
        />
      </dd>
      {#if estado.publicable}
        <dt>Publicado el</dt>
        <dd>{formatoFecha(estado.publicado_at)}</dd>
      {/if}
      <dt>Nivel de acceso</dt>
      <dd>{esReservado ? "Reservado / clasificado" : "Pública"}</dd>
    </dl>

    <div class="pub-notice" role="note">
      <i class="fas fa-scale-balanced" aria-hidden="true"></i>
      <p>
        Publicar es una <strong>decisión de la entidad</strong>, no la consecuencia de que un
        documento no esté clasificado (Ley 1712 de 2014, art. 12). Por eso ningún radicado se
        publica solo, y el acto exige un fundamento que quede registrado con su autor y su fecha.
      </p>
    </div>

    {#if !actoDisponible}
      <p class="pub-blocked" role="note">
        <i class="fas fa-ban" aria-hidden="true"></i>
        {#if esReservado}
          Este radicado está <strong>clasificado o reservado</strong>: el esquema de publicación no
          se aplica sobre él. Si procede difundirlo, primero hay que desclasificarlo mediante el
          acto motivado correspondiente.
        {:else}
          Este radicado está <strong>anulado</strong> y no admite actos de publicación.
        {/if}
      </p>
    {:else}
      <form class="pub-form" on:submit|preventDefault={submit}>
        <FormField
          id="pub-fundamento"
          label={publicar ? "Fundamento de la publicación" : "Fundamento del retiro"}
          type="textarea"
          rows={4}
          required
          bind:value={fundamento}
          placeholder={publicar
            ? "Ej. Numeral 4.2 del esquema de publicación adoptado por Resolución 118 de 2026."
            : "Ej. Retiro por corrección del acto administrativo publicado."}
          hint="Obligatorio en ambos sentidos: retirar una publicación también hay que poder explicarlo."
        />

        <div class="pub-consecuencia" class:pub-consecuencia--on={publicar}>
          <i
            class="fas {publicar ? 'fa-triangle-exclamation' : 'fa-circle-info'}"
            aria-hidden="true"
          ></i>
          {#if publicar}
            <p>
              Al publicar, el radicado queda <strong>cosechable por OAI-PMH y CMIS</strong>: su
              número, asunto y dependencia serán consultables por cualquiera desde internet, y sus
              anexos, descargables — siempre que la entidad tenga esos canales activados.
            </p>
          {:else}
            <p>
              Al retirar la publicación, el radicado deja de aparecer en los canales de cosecha. No
              se borra ni se clasifica: sigue siendo un documento de nivel público dentro del
              sistema.
            </p>
          {/if}
        </div>
      </form>
    {/if}
  {/if}

  <svelte:fragment slot="footer">
    {#if actoDisponible && !loading && !loadError}
      <Button
        variant={publicar ? "primary" : "secondary"}
        size="sm"
        loading={saving}
        disabled={!canSubmit}
        on:click={submit}
      >
        <i class="fas {publicar ? 'fa-bullhorn' : 'fa-eye-slash'}" aria-hidden="true"></i>
        {publicar ? "Publicar" : "Retirar publicación"}
      </Button>
    {/if}
    <Button variant="ghost" size="sm" disabled={saving} on:click={close}>Cerrar</Button>
  </svelte:fragment>
</Drawer>

<style lang="scss">
  .pub-error {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    color: var(--op-error);
    font-size: var(--op-font-sm);
    margin: 0 0 var(--op-space-3);
  }

  .pub-estado {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: var(--op-space-2) var(--op-space-4);
    margin: 0 0 var(--op-space-4);
    font-size: var(--op-font-sm);

    dt {
      color: var(--op-text-secondary);
      font-weight: 600;
    }

    dd {
      margin: 0;
      color: var(--op-text);
    }
  }

  .pub-notice {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    background: var(--op-bg);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
    margin-bottom: var(--op-space-4);

    i {
      color: var(--op-info);
      margin-top: 2px;
      flex-shrink: 0;
    }

    p {
      margin: 0;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      line-height: 1.5;
    }
  }

  .pub-blocked {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0;
    font-size: var(--op-font-sm);
    color: var(--op-text);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);

    i {
      color: var(--op-text-secondary);
      margin-top: 2px;
      flex-shrink: 0;
    }
  }

  .pub-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .pub-consecuencia {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);

    i {
      color: var(--op-text-secondary);
      margin-top: 2px;
      flex-shrink: 0;
    }

    p {
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text);
      line-height: 1.5;
    }

    &--on {
      border-color: var(--op-warning, var(--op-border));

      i {
        color: var(--op-warning, var(--op-text-secondary));
      }
    }
  }
</style>
