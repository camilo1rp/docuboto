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
  RevertirModal.svelte — Revierte la última asignación de un radicado
  (T-Fase1, RF-FLU-08). POST /bandeja/api/[id]/rollback.

  Acción destructiva: exige motivo (obligatorio, 5-500 caracteres) + una
  segunda confirmación explícita (casilla) antes de habilitar el botón. El
  botón que EJECUTA la reversión usa la variante `danger`; "Cancelar" usa
  `ghost` — el color de alerta es siempre para la acción irreversible, nunca
  para la conservadora.

  NOTA DE CONTRATO (verificado contra `workflow-service/app/schemas/workflow.py`
  y `bandeja/api/[id]/rollback/+server.js`): `POST /workflows/{id}/rollback`
  EXIGE `{ motivo: string (5..500) }` — el backend lo asienta de forma atómica
  en `flow_events.comentario` y en `public.audit_log` junto con la mutación.
  El proxy valida los mismos límites antes de llamar al gateway. El motivo
  que el operador escribe aquí SÍ queda en la traza de auditoría del
  radicado — por eso se le informa explícitamente.

  Props:
    open  boolean            — controla la visibilidad
    step  FlowStepResponse   — paso activo cuya asignación se revierte

  Eventos:
    on:close    — se cerró sin completar la acción
    on:success  — la reversión se ejecutó; detail = FlowStepResponse del paso anterior
-->
<script>
  import { usarNombresDependencia } from "$lib/stores/dependencias.js";
  const nombresDependencia = usarNombresDependencia();
  import { nombreActual } from "$lib/utils/dependenciaNombre.js";
  import { createEventDispatcher, tick } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  export let open = false;
  /** @type {import('$lib/types/api.js').FlowStepResponse | null} */
  export let step = null;

  const dispatch = createEventDispatcher();

  const MOTIVO_MIN_LENGTH = 5;
  const MOTIVO_MAX_LENGTH = 500;

  let motivo = "";
  let motivoError = "";
  let confirmed = false;
  let loading = false;

  $: if (open) resetForm();

  // El backend rechaza (422) un motivo por encima del límite — recortar en
  // el cliente evita que ese 422 se dispare en el uso normal; el contador
  // (hint de FormField, abajo) avisa antes de llegar al límite.
  $: if (motivo.length > MOTIVO_MAX_LENGTH) {
    motivo = motivo.slice(0, MOTIVO_MAX_LENGTH);
  }

  function resetForm() {
    motivo = "";
    motivoError = "";
    confirmed = false;
  }

  function close() {
    dispatch("close");
  }

  function onMotivoInput() {
    if (motivoError) motivoError = "";
  }

  async function submit() {
    if (!step || loading) return;

    motivoError = "";
    if (motivo.trim().length < MOTIVO_MIN_LENGTH) {
      motivoError = `Describa el motivo de la reversión (mínimo ${MOTIVO_MIN_LENGTH} caracteres).`;
      // Llevar el foco al campo que falló en vez de dejarlo en el botón.
      tick().then(() => {
        document.getElementById("revertir-motivo")?.focus();
      });
      return;
    }

    loading = true;
    try {
      const res = await fetch(`/bandeja/api/${encodeURIComponent(step.radicado_id)}/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ motivo: motivo.trim() }),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      notifySuccess(`Se revirtió la última asignación de ${step.tracking_number}.`);
      dispatch("success", result);
    } catch (err) {
      if (err?.status === 409) {
        notifyError(mensajeError(err, "No se puede revertir este radicado en su estado actual."));
      } else if (err?.status === 422) {
        notifyError(mensajeError(err, "El motivo de la reversión no es válido."));
      } else {
        notifyError(mensajeError(err, "No se pudo revertir la asignación."));
      }
    } finally {
      loading = false;
    }
  }
</script>

<Modal {open} title="Revertir asignación" size="sm" on:close={close}>
  {#if step}
    <p class="modal-desc modal-desc--danger">
      <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
      <span>
        Vas a revertir la última asignación del radicado
        <strong>{step.tracking_number}</strong> (actualmente en {nombreActual(
          step.to_dept_code,
          step.to_dept,
          $nombresDependencia,
        )}). Esta acción
        <strong>no se puede deshacer</strong>.
      </span>
    </p>
    <div class="modal-form">
      <FormField
        id="revertir-motivo"
        label="Motivo de la reversión"
        type="textarea"
        bind:value={motivo}
        required={true}
        rows={3}
        placeholder="Ej. Se asignó a la dependencia incorrecta por error de digitación."
        error={motivoError}
        hint={!motivoError
          ? `${motivo.length}/${MOTIVO_MAX_LENGTH} caracteres — queda registrado en la traza del radicado.`
          : ""}
        on:input={onMotivoInput}
      />
      <label class="confirm-check">
        <input type="checkbox" bind:checked={confirmed} />
        <span>Entiendo que esta acción no se puede deshacer.</span>
      </label>
    </div>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="danger" size="sm" {loading} disabled={!step || !confirmed} on:click={submit}>
      <i class="fas fa-rotate-left" aria-hidden="true"></i>
      Revertir
    </Button>
    <Button variant="ghost" size="sm" disabled={loading} on:click={close}>Cancelar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .modal-desc {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0 0 var(--op-space-4);
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);

    &--danger i {
      color: var(--op-error);
      margin-top: 2px;
      flex-shrink: 0;
    }
  }
  .modal-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }
  .confirm-check {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    cursor: pointer;
    // El label completo (no solo la casilla) es el objetivo de click/tap
    // real: relleno vertical para acercarse al mínimo táctil de 44px sin
    // agrandar la casilla en escritorio (mismo patrón que admin/flujos/reglas).
    padding: var(--op-space-1) 0;

    @media (pointer: coarse) {
      min-height: 44px;
    }

    input[type="checkbox"] {
      margin-top: 2px;
      width: 18px;
      height: 18px;
      flex-shrink: 0;
      accent-color: var(--op-error);

      // La casilla en sí crece al mínimo táctil en dispositivos táctiles;
      // en mouse/trackpad mantiene la densidad visual actual.
      @media (pointer: coarse) {
        width: 24px;
        height: 24px;
      }
    }
  }
</style>
