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
  FinalizarTramiteModal.svelte — Finaliza el trámite de un radicado (ADR-028).
  POST /bandeja/api/[id]/finalizar.

  Es el ÚNICO acto que cierra un radicado y NO se deshace: cierra el paso
  activo y el radicado pasa a `closed` (terminal). Mismo patrón que
  RevertirModal para los actos irreversibles: comentario obligatorio
  (5-500 caracteres, queda en la traza) + casilla de confirmación explícita
  antes de habilitar el botón, que usa la variante `danger`.

  Solo se abre para el POSEEDOR del paso activo (ver $lib/utils/tenencia.js);
  el backend re-valida y un no-poseedor recibe 404 `no_active_step`.

  Pide `motivo_cierre` (obligatorio: respondido | nrr | traslado_competencia |
  otro) además del comentario; el backend da 422 si falta.

  Props:
    open  boolean            — visibilidad
    step  FlowStepResponse   — paso activo que se cierra (o el último `completed`
                               cuando el poseedor cierra un radicado sin paso activo)

  Eventos:
    on:close    — se cerró sin completar el acto
    on:success  — el trámite quedó finalizado; detail = FlowStepResponse
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
  import {
    FINALIZAR_COMENTARIO_MIN,
    FINALIZAR_COMENTARIO_MAX,
    MOTIVOS_CIERRE,
    esMotivoCierre,
  } from "$lib/utils/tenencia.js";

  export let open = false;
  /** @type {import('$lib/types/api.js').FlowStepResponse | null} */
  export let step = null;

  const dispatch = createEventDispatcher();

  const COMENTARIO_MIN_LENGTH = FINALIZAR_COMENTARIO_MIN;
  const COMENTARIO_MAX_LENGTH = FINALIZAR_COMENTARIO_MAX;

  let comentario = "";
  let comentarioError = "";
  let motivoCierre = "";
  let motivoError = "";
  let confirmed = false;
  let loading = false;

  $: if (open) resetForm();

  // El backend rechaza (422) un comentario por encima del límite — recortar en
  // el cliente evita que ese 422 se dispare en el uso normal; el contador
  // (hint de FormField, abajo) avisa antes de llegar al límite.
  $: if (comentario.length > COMENTARIO_MAX_LENGTH) {
    comentario = comentario.slice(0, COMENTARIO_MAX_LENGTH);
  }

  function resetForm() {
    comentario = "";
    comentarioError = "";
    motivoCierre = "";
    motivoError = "";
    confirmed = false;
  }

  function close() {
    dispatch("close");
  }

  function onComentarioInput() {
    if (comentarioError) comentarioError = "";
  }

  async function submit() {
    if (!step || loading) return;

    comentarioError = "";
    motivoError = "";
    if (!esMotivoCierre(motivoCierre)) {
      motivoError = "Seleccione el motivo de cierre.";
      tick().then(() => {
        document.getElementById("finalizar-motivo")?.focus();
      });
      return;
    }
    if (comentario.trim().length < COMENTARIO_MIN_LENGTH) {
      comentarioError = `Describa el comentario de cierre (mínimo ${COMENTARIO_MIN_LENGTH} caracteres).`;
      // Llevar el foco al campo que falló en vez de dejarlo en el botón.
      tick().then(() => {
        document.getElementById("finalizar-comentario")?.focus();
      });
      return;
    }

    loading = true;
    try {
      const res = await fetch(`/bandeja/api/${encodeURIComponent(step.radicado_id)}/finalizar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comentario: comentario.trim(), motivo_cierre: motivoCierre }),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      notifySuccess(`Se finalizó el trámite de ${step.tracking_number}.`);
      dispatch("success", result);
    } catch (err) {
      if (err?.status === 409) {
        notifyError(mensajeError(err, "No se puede finalizar este radicado en su estado actual."));
      } else if (err?.status === 422) {
        notifyError(mensajeError(err, "El motivo o el comentario de cierre no son válidos."));
      } else if (err?.status === 403) {
        notifyError("Finalizar exige permiso de trámite con nivel de creación; no lo tiene.");
      } else {
        notifyError(mensajeError(err, "No se pudo finalizar el trámite."));
      }
    } finally {
      loading = false;
    }
  }
</script>

<Modal {open} title="Finalizar trámite" size="sm" on:close={close}>
  {#if step}
    <p class="modal-desc modal-desc--danger">
      <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
      <span>
        Vas a finalizar el trámite del radicado
        <strong>{step.tracking_number}</strong> ({step.status === "completed"
          ? "último paso en"
          : "actualmente en"}
        {nombreActual(step.to_dept_code, step.to_dept, $nombresDependencia)}). El radicado pasa a
        <strong>Cerrado</strong>
        y esta acción <strong>no se puede deshacer</strong>.
      </span>
    </p>
    <div class="modal-form">
      <FormField
        id="finalizar-motivo"
        label="Motivo de cierre"
        type="select"
        bind:value={motivoCierre}
        required={true}
        options={MOTIVOS_CIERRE}
        placeholder="Seleccione el motivo"
        error={motivoError}
        on:change={() => (motivoError = "")}
      />
      <FormField
        id="finalizar-comentario"
        label="Comentario de cierre"
        type="textarea"
        bind:value={comentario}
        required={true}
        rows={3}
        placeholder="Ej. Se respondió la solicitud y no hay más actuaciones pendientes."
        error={comentarioError}
        hint={!comentarioError
          ? `${comentario.length}/${COMENTARIO_MAX_LENGTH} caracteres — queda registrado en la traza del radicado.`
          : ""}
        on:input={onComentarioInput}
      />
      <label class="confirm-check">
        <input type="checkbox" bind:checked={confirmed} />
        <span>Entiendo que esta acción no se puede deshacer.</span>
      </label>
    </div>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="danger" size="sm" {loading} disabled={!step || !confirmed} on:click={submit}>
      <i class="fas fa-flag-checkered" aria-hidden="true"></i>
      Finalizar trámite
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
