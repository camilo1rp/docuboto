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
  DisposicionRadicadoDrawer.svelte — Ver/editar la disposición final (TRD)
  de UN radicado (RF-MET-08, Fase 4 cierre desfase API↔UI).

  GET/PATCH /bandeja/api/[id]/disposition (proxy SSR, D-05).

  Nota honesta (verificada contra el backend, NO inventada): un radicado
  individual no tiene un vínculo directo a una serie TRD — `doc_class` es
  texto libre sin FK, y el único vínculo documento↔TRD vive a nivel de
  EXPEDIENTE (`archive-service`), sin un endpoint inverso "expedientes que
  contienen este radicado". Por eso este drawer NO muestra una regla de
  retención automática — lo declara explícitamente en vez de simularla. Si el
  radicado pertenece a un expediente, la regla que justifica su disposición
  se consulta desde el propio expediente (Drawer de disposición en
  expedientes/[id]).

  Props:
    open              boolean
    radicado          {id, tracking_number} | null

  Eventos:
    on:close   — se cerró (guardado o cancelado)
    on:saved   — la disposición se guardó con éxito; detail = disposition actualizada
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  export let open = false;
  /** @type {{id: string; tracking_number: string} | null} */
  export let radicado = null;

  const dispatch = createEventDispatcher();

  const ACCION_OPTIONS = [
    { value: "", label: "Sin decidir" },
    { value: "conservar", label: "Conservar (conservación total)" },
    { value: "seleccionar", label: "Seleccionar (muestra representativa)" },
    { value: "transferir", label: "Transferir (archivo central/histórico)" },
    { value: "eliminar", label: "Eliminar" },
  ];

  let loading = false;
  // Distinción explícita fallo-de-red / dato-vacío: `loadError` solo se fija
  // si la petición GET falló de verdad; una disposición vacía ({}) es un
  // estado legítimo ("aún no se ha fijado disposición"), no un error.
  let loadError = "";
  let saving = false;

  let programa = "";
  let retentionUntil = "";
  let accion = "";
  let confirmado = false;
  let marcadoEliminacion = false;
  let destructiveAck = false;

  $: isDestructive = accion === "eliminar" || marcadoEliminacion;
  $: canSave = !isDestructive || destructiveAck;

  $: if (open && radicado) loadDisposition();

  function resetForm() {
    programa = "";
    retentionUntil = "";
    accion = "";
    confirmado = false;
    marcadoEliminacion = false;
    destructiveAck = false;
    loadError = "";
  }

  async function loadDisposition() {
    if (!radicado) return;
    resetForm();
    loading = true;
    try {
      const res = await fetch(`/bandeja/api/${encodeURIComponent(radicado.id)}/disposition`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        loadError = body.detail ?? `Error ${res.status} al cargar la disposición.`;
        return;
      }
      const body = await res.json();
      const disp = body.disposition ?? {};
      programa = disp.programa ?? "";
      retentionUntil = disp.retention_until ?? "";
      accion = disp.accion ?? "";
      confirmado = Boolean(disp.confirmado);
      marcadoEliminacion = Boolean(disp.marcado_eliminacion);
    } catch {
      loadError = "No se pudo conectar al servidor para consultar la disposición.";
    } finally {
      loading = false;
    }
  }

  function close() {
    dispatch("close");
  }

  async function submit() {
    if (!radicado || saving || !canSave) return;
    saving = true;
    try {
      const res = await fetch(`/bandeja/api/${encodeURIComponent(radicado.id)}/disposition`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          programa: programa.trim() || null,
          retention_until: retentionUntil || null,
          accion: accion || null,
          confirmado,
          marcado_eliminacion: marcadoEliminacion,
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Disposición actualizada para ${radicado.tracking_number}.`);
      dispatch("saved", result.disposition ?? {});
      dispatch("close");
    } catch (err) {
      if (err?.status === 404) {
        notifyError("No fue posible guardar: el radicado no existe o no tiene acceso a él.");
      } else if (err?.status === 422) {
        notifyError(err?.detail ?? "Los datos de disposición no son válidos.");
      } else {
        notifyError(err?.detail ?? "No se pudo guardar la disposición.");
      }
    } finally {
      saving = false;
    }
  }
</script>

<Drawer
  {open}
  title={radicado ? `Disposición final — ${radicado.tracking_number}` : "Disposición final"}
  side="right"
  width="440px"
  on:close={close}
>
  {#if loading}
    <Loader type="skeleton" lines={6} label="Cargando disposición…" />
  {:else if loadError}
    <p class="disp-error" role="alert">
      <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
      {loadError}
    </p>
    <Button variant="primary" size="sm" on:click={loadDisposition}>Reintentar</Button>
  {:else if radicado}
    <div class="disp-notice" role="note">
      <i class="fas fa-circle-info" aria-hidden="true"></i>
      <p>
        Este radicado no tiene un vínculo automático a una serie TRD: la regla de retención
        documental se rige a nivel de <strong>expediente</strong> (Ley 594/2000 art. 24). Si este radicado
        pertenece a un expediente, consulte allí la regla que justifica el plazo antes de fijar una fecha
        aquí. Lo que se guarda en este formulario es la disposición particular del radicado.
      </p>
    </div>

    <form class="disp-form" on:submit|preventDefault={submit}>
      <FormField
        id="disp-accion"
        label="Acción de disposición"
        type="select"
        bind:value={accion}
        options={ACCION_OPTIONS}
      />
      <FormField
        id="disp-programa"
        label="Programa / serie de referencia"
        type="text"
        bind:value={programa}
        placeholder="Ej. Serie Correspondencia (referencia informativa, texto libre)"
      />
      <FormField
        id="disp-retention-until"
        label="Fecha de disposición"
        type="date"
        bind:value={retentionUntil}
        hint="Fecha en la que se ejecutará la acción elegida."
      />
      <label class="disp-check">
        <input type="checkbox" bind:checked={confirmado} />
        <span>Disposición confirmada por el comité/responsable de archivo.</span>
      </label>
      <label class="disp-check">
        <input type="checkbox" bind:checked={marcadoEliminacion} />
        <span>Marcar para eliminación (paso previo a la purga física en storage).</span>
      </label>

      {#if isDestructive}
        <div class="disp-danger">
          <p class="disp-danger__text">
            <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            La acción <strong>eliminar</strong> / el marcado para eliminación decide sobre
            patrimonio documental de forma <strong>irreversible</strong>. La purga física no ocurre
            aquí, pero esta marca es el primer paso del proceso.
          </p>
          <label class="disp-check disp-check--danger">
            <input type="checkbox" bind:checked={destructiveAck} />
            <span>Entiendo que esta decisión puede ser irreversible y la confirmo.</span>
          </label>
        </div>
      {/if}
    </form>
  {/if}

  <svelte:fragment slot="footer">
    {#if radicado && !loading && !loadError}
      <Button
        variant={isDestructive ? "danger" : "primary"}
        size="sm"
        loading={saving}
        disabled={!canSave}
        on:click={submit}
      >
        <i class="fas fa-save" aria-hidden="true"></i>
        Guardar disposición
      </Button>
    {/if}
    <Button variant="ghost" size="sm" disabled={saving} on:click={close}>Cerrar</Button>
  </svelte:fragment>
</Drawer>

<style lang="scss">
  .disp-error {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    color: var(--op-error);
    font-size: var(--op-font-sm);
    margin: 0 0 var(--op-space-3);
  }

  .disp-notice {
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

  .disp-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .disp-check {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    cursor: pointer;
    padding: var(--op-space-1) 0;

    @media (pointer: coarse) {
      min-height: 44px;
    }

    input[type="checkbox"] {
      margin-top: 2px;
      width: 18px;
      height: 18px;
      flex-shrink: 0;

      @media (pointer: coarse) {
        width: 24px;
        height: 24px;
      }
    }

    &--danger input[type="checkbox"] {
      accent-color: var(--op-error);
    }
  }

  .disp-danger {
    border: 1px solid var(--op-error);
    background: var(--op-error-bg, var(--op-bg));
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
  }

  .disp-danger__text {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0;
    font-size: var(--op-font-sm);
    color: var(--op-text);

    i {
      color: var(--op-error);
      margin-top: 2px;
      flex-shrink: 0;
    }
  }
</style>
