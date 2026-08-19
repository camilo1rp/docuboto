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
  PublicacionExpedienteDrawer.svelte — Acto de publicación de oficio del
  EXPEDIENTE (Ley 1712/2014 art. 12).

  POST /expedientes/[id]/api/publicacion (proxy SSR, D-05).

  Diferencias deliberadas con el gemelo del radicado
  (`PublicacionRadicadoDrawer`), que no se unificaron porque los objetos no son
  iguales:

  1. **No hace GET.** El estado (`publicable`, `publicado_at`, `nivel_seguridad`)
     viaja en `ExpedienteResponse`, que la página ya cargó. Pedirlo otra vez sería
     una llamada de más y una segunda fuente de verdad.
  2. **No bloquea por estado.** Un expediente `closed` o `transferred` no está
     retirado de la circulación —son estados normales de su ciclo de vida— y su
     difusión sigue siendo legítima. El único bloqueo es la reserva.
  3. Lo que difunde es distinto y se nombra tal cual: por OAI-PMH EAD salen el
     código, el nombre, la descripción, las fechas extremas y el **número** de
     radicados; nunca los números de radicado hijos, que podrían ser reservados.

  Props:
    open         boolean
    expediente   { id, code, publicable, publicado_at, nivel_seguridad } | null

  Eventos:
    on:close     — se cerró (ejecutado o cancelado)
    on:changed   — el acto se ejecutó; detail = { publicable, publicado_at }
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  export let open = false;
  /** @type {{id: string; code: string; publicable?: boolean; publicado_at?: string | null; nivel_seguridad?: number} | null} */
  export let expediente = null;

  const dispatch = createEventDispatcher();

  let saving = false;
  let fundamento = "";

  // El acto ofrecido es siempre el CONTRARIO del estado vigente.
  $: publicar = !expediente?.publicable;
  $: esReservado = (expediente?.nivel_seguridad ?? 1) > 1;
  // Publicar exige nivel público; retirar no. El backend hace exactamente esta
  // distinción en el `WHERE` del UPDATE, y la pantalla la refleja en vez de
  // bloquear las dos direcciones como hacía la primera versión del gemelo.
  $: actoDisponible = expediente != null && (!esReservado || !publicar);
  $: canSubmit = actoDisponible && fundamento.trim().length > 0 && !saving;

  $: if (open) fundamento = "";

  function close() {
    dispatch("close");
  }

  function formatoFecha(iso) {
    return formatearFechaHora(iso);
  }

  async function submit() {
    if (!expediente || !canSubmit) return;
    saving = true;
    try {
      const res = await fetch(`/expedientes/${encodeURIComponent(expediente.id)}/api/publicacion`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ publicar, fundamento: fundamento.trim() }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(
        publicar
          ? `El expediente ${expediente.code} queda publicado en el esquema de publicación.`
          : `Se retiró la publicación del expediente ${expediente.code}.`,
      );
      dispatch("changed", {
        publicable: Boolean(result.publicable),
        publicado_at: result.publicado_at ?? null,
      });
      dispatch("close");
    } catch (err) {
      if (err?.status === 404) {
        notifyError(
          "No fue posible ejecutar el acto: el expediente no existe, no tiene acceso a él, " +
            "o su nivel de acceso no lo admite.",
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
  title={expediente ? `Acto de publicación — ${expediente.code}` : "Acto de publicación"}
  side="right"
  width="460px"
  on:close={close}
>
  {#if expediente}
    <dl class="pub-estado">
      <dt>Estado</dt>
      <dd>
        <StatusChip
          status={expediente.publicable ? "success" : "neutral"}
          label={expediente.publicable ? "Publicado" : "No publicado"}
        />
      </dd>
      {#if expediente.publicable}
        <dt>Publicado el</dt>
        <dd>{formatoFecha(expediente.publicado_at)}</dd>
      {/if}
      <dt>Nivel de acceso</dt>
      <dd>{esReservado ? "Reservado / clasificado" : "Pública"}</dd>
    </dl>

    <div class="pub-notice" role="note">
      <i class="fas fa-scale-balanced" aria-hidden="true"></i>
      <p>
        Publicar es una <strong>decisión de la entidad</strong>, no la consecuencia de que un
        expediente no esté clasificado (Ley 1712 de 2014, art. 12). Ningún expediente se publica
        solo, y el acto exige un fundamento que queda registrado con su autor y su fecha.
      </p>
    </div>

    {#if !actoDisponible}
      <p class="pub-blocked" role="note">
        <i class="fas fa-ban" aria-hidden="true"></i>
        <span>
          Este expediente está <strong>clasificado o reservado</strong>: el esquema de publicación
          no se aplica sobre él. Si procede difundirlo, primero hay que desclasificarlo mediante el
          acto motivado correspondiente.
        </span>
      </p>
    {:else}
      <form class="pub-form" on:submit|preventDefault={submit}>
        <FormField
          id="pub-exp-fundamento"
          label={publicar ? "Fundamento de la publicación" : "Fundamento del retiro"}
          type="textarea"
          rows={4}
          required
          bind:value={fundamento}
          placeholder={publicar
            ? "Ej. Numeral 4.3 del esquema de publicación adoptado por Resolución 118 de 2026."
            : "Ej. Retiro por reserva sobrevenida sobre parte del expediente."}
          hint="Obligatorio en ambos sentidos: retirar una publicación también hay que poder explicarlo."
        />

        <div class="pub-consecuencia" class:pub-consecuencia--on={publicar}>
          <i
            class="fas {publicar ? 'fa-triangle-exclamation' : 'fa-circle-info'}"
            aria-hidden="true"
          ></i>
          {#if publicar}
            <p>
              Al publicar, el expediente queda <strong>cosechable por OAI-PMH</strong> en formato
              EAD: su código, nombre, descripción, fechas extremas y el <strong>número</strong> de radicados
              que contiene serán consultables desde internet. Los números de radicado individuales no
              se difunden nunca.
            </p>
          {:else}
            <p>
              Al retirar la publicación, el expediente deja de aparecer en los canales de cosecha.
              No se cierra, no se borra y no se clasifica.
            </p>
          {/if}
        </div>
      </form>
    {/if}
  {/if}

  <svelte:fragment slot="footer">
    {#if actoDisponible}
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
