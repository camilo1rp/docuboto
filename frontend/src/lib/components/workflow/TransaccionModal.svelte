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
  TransaccionModal.svelte — Ejecuta un trámite del catálogo genérico de
  transacciones (T-Fase1, RF-FLU-01). POST /bandeja/api/[id]/transactions.

  El catálogo (`GET /bandeja/api/transaction-types`) NO describe `detalles` con
  un JSON Schema — es un objeto libre (`dict` en el backend, sin
  `detalles_schema` en `TransactionTypeResponse`, verificado contra
  `workflow-service/app/schemas/workflow.py`). Por eso este modal NO usa
  SchemaField: ofrece un campo de texto JSON opcional, validado en cliente
  antes de enviar. Si en el futuro el catálogo incorpora un schema por tipo,
  este es el punto de reemplazo por SchemaField (ver expedientes/T-12 para el
  patrón ya usado en el proyecto).

  El permiso requerido varía POR TIPO (`requires_permission`, resuelto en
  runtime por el backend) — este modal no filtra el catálogo por permiso; deja
  que el backend responda 403 si corresponde (defensa en profundidad, D-05).

  A8 (revisión UX): `target_step_status` SÍ viene en la respuesta del catálogo
  (`TransactionTypeResponse`, workflow-service/app/schemas/workflow.py). Si el
  tipo elegido lo trae en `cancelled` (hoy solo `anular`, migración 007), el
  trámite cancela el paso activo — no es un trámite neutro. En ese caso el
  botón cambia a variante `danger`, se muestra un aviso explícito y el
  comentario deja de ser opcional (se exige describir el motivo, igual que en
  RevertirModal.svelte, con el que comparte la severidad de la acción).

  Props:
    open  boolean            — controla la visibilidad
    step  FlowStepResponse   — paso activo sobre el que se ejecuta el trámite

  Eventos:
    on:close    — se cerró sin completar la acción
    on:success  — el trámite se ejecutó; detail = FlowEventResponse
-->
<script>
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

  let tipoTx = "";
  let comentario = "";
  let comentarioError = "";
  let detallesJson = "";
  let tipoTxError = "";
  let detallesJsonError = "";
  let loading = false;

  /** @type {Array<{code:string; nombre:string; descripcion?:string; target_step_status?:string|null}>} */
  let catalog = [];
  let catalogLoading = false;
  let catalogError = "";

  $: catalogOptions = catalog.map((t) => ({ value: t.code, label: t.nombre }));
  $: selectedType = catalog.find((t) => t.code === tipoTx) ?? null;
  // A8: este trámite cancela el paso activo — no es un trámite neutro más.
  $: cancelsStep = selectedType?.target_step_status === "cancelled";

  $: if (open) resetForm();

  function resetForm() {
    tipoTx = "";
    comentario = "";
    comentarioError = "";
    detallesJson = "";
    tipoTxError = "";
    detallesJsonError = "";
    loadCatalog();
  }

  async function loadCatalog() {
    catalogLoading = true;
    catalogError = "";
    try {
      const res = await fetch("/bandeja/api/transaction-types");
      const result = await res.json();
      if (!res.ok) throw result;
      catalog = (Array.isArray(result) ? result : []).filter((t) => t.activo !== false);
      if (catalog.length === 0) {
        catalogError = "No hay tipos de trámite disponibles.";
      }
    } catch (err) {
      catalog = [];
      catalogError = mensajeError(err, "No se pudo cargar el catálogo de trámites.");
    } finally {
      catalogLoading = false;
    }
  }

  function close() {
    dispatch("close");
  }

  async function submit() {
    if (!step || loading) return;

    tipoTxError = "";
    detallesJsonError = "";
    comentarioError = "";
    if (!tipoTx) {
      tipoTxError = "Seleccione el tipo de trámite.";
      return;
    }

    // A8: un trámite que cancela el paso activo exige describir el motivo —
    // el mismo estándar que RevertirModal para cualquier acción irreversible.
    if (cancelsStep && !comentario.trim()) {
      comentarioError = "Describa el motivo: este trámite cancela el paso activo.";
      tick().then(() => {
        document.getElementById("tramite-comentario")?.focus();
      });
      return;
    }

    /** @type {Record<string, unknown>} */
    let detalles;
    if (detallesJson.trim()) {
      try {
        const parsed = JSON.parse(detallesJson);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new Error("not_object");
        }
        detalles = parsed;
      } catch {
        detallesJsonError = 'Los detalles deben ser un objeto JSON válido, ej. {"clave": "valor"}.';
        return;
      }
    }

    loading = true;
    try {
      /** @type {Record<string, unknown>} */
      const payload = { tipo_tx: tipoTx };
      if (comentario.trim()) payload.comentario = comentario.trim();
      if (detalles) payload.detalles = detalles;

      const res = await fetch(`/bandeja/api/${encodeURIComponent(step.radicado_id)}/transactions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      notifySuccess(
        `Trámite "${selectedType?.nombre ?? tipoTx}" registrado en ${step.tracking_number}.`,
      );
      dispatch("success", result);
    } catch (err) {
      if (err?.status === 403) {
        notifyError("No tiene permiso para ejecutar este trámite.");
      } else {
        notifyError(mensajeError(err, "No se pudo ejecutar el trámite."));
      }
    } finally {
      loading = false;
    }
  }
</script>

<Modal {open} title="Ejecutar trámite" size="md" on:close={close}>
  {#if step}
    <p class="modal-desc">
      Registra un trámite sobre el radicado <strong>{step.tracking_number}</strong>.
    </p>
    {#if catalogLoading}
      <p class="modal-loader-inline">Cargando catálogo de trámites…</p>
    {:else}
      <div class="modal-form">
        <FormField
          id="tramite-tipo"
          label="Tipo de trámite"
          type="select"
          bind:value={tipoTx}
          required={true}
          options={catalogOptions}
          placeholder="Seleccione un tipo de trámite…"
          error={tipoTxError || catalogError}
        />
        {#if selectedType?.descripcion}
          <p class="modal-hint">{selectedType.descripcion}</p>
        {/if}
        {#if cancelsStep}
          <p class="modal-warning" role="alert">
            <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            <span>
              Este trámite <strong>cancelará el paso activo</strong> de {step.tracking_number}.
              Describa el motivo antes de continuar.
            </span>
          </p>
        {/if}
        <FormField
          id="tramite-comentario"
          label={cancelsStep ? "Motivo de la cancelación" : "Comentario (opcional)"}
          type="textarea"
          bind:value={comentario}
          required={cancelsStep}
          rows={3}
          placeholder="Contexto del trámite…"
          error={comentarioError}
        />
        <details class="modal-advanced">
          <summary>Detalles avanzados</summary>
          <FormField
            id="tramite-detalles"
            label="Detalles adicionales — JSON (opcional)"
            type="textarea"
            bind:value={detallesJson}
            rows={3}
            placeholder={"{ }"}
            hint="El catálogo no define un formulario específico por tipo; use este campo solo si el trámite lo requiere."
            error={detallesJsonError}
          />
        </details>
      </div>
    {/if}
  {/if}
  <svelte:fragment slot="footer">
    <Button
      variant={cancelsStep ? "danger" : "primary"}
      size="sm"
      {loading}
      disabled={!step || catalogLoading || catalog.length === 0}
      on:click={submit}
    >
      <i class="fas fa-gears" aria-hidden="true"></i>
      Ejecutar trámite
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
  .modal-hint {
    margin: calc(var(--op-space-4) * -1) 0 0;
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }
  .modal-loader-inline {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }
  .modal-warning {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0;
    padding: var(--op-space-2) var(--op-space-3);
    background: var(--op-error-bg);
    color: var(--op-error-fg);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);

    i {
      margin-top: 2px;
      flex-shrink: 0;
    }
  }
  .modal-advanced {
    summary {
      cursor: pointer;
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    > :global(.op-field) {
      margin-top: var(--op-space-3);
    }
  }
</style>
