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
  RevertirModal.svelte — Devuelve un radicado al paso anterior (T-Fase1,
  RF-FLU-08). POST /bandeja/api/[id]/rollback.

  Es un acto que se corrige con otro registro, no uno definitivo (ADR-057 §6,
  CR-001 UI-05): si la devolución fue un error, el radicado se traslada de
  nuevo. Por eso pide motivo (5 a 500 caracteres) y nada más: sin casilla de
  «Entiendo…» y sin decir que no se puede deshacer, que no era cierto. El
  botón usa la variante `primary`; el rojo queda para lo definitivo.

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
      motivoError = `Escriba el motivo de la devolución (mínimo ${MOTIVO_MIN_LENGTH} caracteres).`;
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

      notifySuccess(`El radicado ${step.tracking_number} volvió al paso anterior.`);
      dispatch("success", result);
    } catch (err) {
      if (err?.status === 409) {
        notifyError(mensajeError(err, "Este radicado no se puede devolver en su estado actual."));
      } else if (err?.status === 422) {
        notifyError(mensajeError(err, "El motivo de la devolución no es válido."));
      } else {
        notifyError(mensajeError(err, "No se pudo devolver el radicado al paso anterior."));
      }
    } finally {
      loading = false;
    }
  }
</script>

<Modal {open} title="Devolver al paso anterior" size="sm" on:close={close}>
  {#if step}
    <p class="modal-desc">
      El radicado <strong>{step.tracking_number}</strong> sale de {nombreActual(
        step.to_dept_code,
        step.to_dept,
        $nombresDependencia,
      )} y vuelve a quien lo tenía antes. Si fue un error, puede trasladarlo de nuevo.
    </p>
    <div class="modal-form">
      <FormField
        id="revertir-motivo"
        label="Motivo de la devolución"
        type="textarea"
        bind:value={motivo}
        required={true}
        rows={3}
        placeholder="Ej. Se asignó a la dependencia equivocada."
        error={motivoError}
        hint={!motivoError
          ? `${motivo.length}/${MOTIVO_MAX_LENGTH} caracteres. Queda registrado en la traza del radicado.`
          : ""}
        on:input={onMotivoInput}
      />
    </div>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="primary" size="sm" {loading} disabled={!step} on:click={submit}>
      <i class="fas fa-rotate-left" aria-hidden="true"></i>
      Devolver
    </Button>
    <Button variant="ghost" size="sm" disabled={loading} on:click={close}>Cancelar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .modal-desc {
    margin: 0 0 var(--op-space-4);
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
  }
  .modal-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }
</style>
