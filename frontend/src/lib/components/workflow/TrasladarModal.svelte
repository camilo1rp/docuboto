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
  TrasladarModal.svelte — Traslada el paso activo de un radicado a otra
  dependencia (T-Fase1, RF-FLU-03). POST /bandeja/api/transfer.

  La dependencia viaja por CÓDIGO (`to_dept_code`, ADR-032): el selector tiene
  el código como valor y el nombre como etiqueta. El origen NO se envía: el
  servidor lo deriva del paso activo (si el paso cambió entre tanto responde
  409 `origen_desactualizado`). Aquí solo se MUESTRA, por nombre actual.

  Props:
    open          boolean            — controla la visibilidad
    step          FlowStepResponse   — paso activo a trasladar (requerido si open)
    dependencias  Array<{value,label}> — catálogo SSR de dependencias activas

  Eventos:
    on:close    — se cerró sin completar la acción
    on:success  — el traslado se ejecutó; detail = FlowStepResponse del nuevo paso
-->
<script>
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import { createEventDispatcher, tick } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import UserPicker from "$lib/components/ui/UserPicker.svelte";
  import { usarNombresDependencia } from "$lib/stores/dependencias.js";
  const nombresDependencia = usarNombresDependencia();
  import { nombreActual } from "$lib/utils/dependenciaNombre.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  export let open = false;
  /** @type {import('$lib/types/api.js').FlowStepResponse | null} */
  export let step = null;
  /** @type {Array<{value: string; label: string}>} */
  export let dependencias = [];

  const dispatch = createEventDispatcher();

  let toDept = "";
  let toDeptError = "";
  let notes = "";
  let dueAt = "";
  /** @type {string[]} */
  let assignedTo = [];
  let loading = false;

  /** @type {Array<{id:string; username:string}>} */
  let users = [];
  let usersLoading = false;
  // I8 (revisión UX): un 500/503 al listar usuarios dejaba `users = []` sin
  // ninguna señal — el operador veía el UserPicker vacío y concluía que no
  // había ningún responsable disponible, cuando en realidad la carga falló.
  let usersError = "";

  $: origen = nombreActual(step?.to_dept_code, step?.to_dept, $nombresDependencia);
  $: if (open) resetForm();

  function resetForm() {
    toDept = "";
    toDeptError = "";
    notes = "";
    dueAt = "";
    assignedTo = [];
    usersError = "";
    loadUsers();
  }

  /** Carga perezosa del universo de usuarios asignables (D-05 — proxy SSR). */
  async function loadUsers() {
    usersLoading = true;
    usersError = "";
    try {
      const res = await fetch("/bandeja/api/users?size=100");
      const result = await res.json();
      if (res.ok) {
        users = result.users ?? [];
      } else {
        users = [];
        usersError =
          "No se pudo cargar la lista de responsables; el traslado se puede hacer igual, sin responsable.";
      }
    } catch {
      users = [];
      usersError =
        "No se pudo cargar la lista de responsables; el traslado se puede hacer igual, sin responsable.";
    } finally {
      usersLoading = false;
    }
  }

  /** Nombre visible de la dependencia elegida (el valor del selector es su CÓDIGO). */
  function nombreDestino() {
    return dependencias.find((d) => d.value === toDept)?.label ?? toDept;
  }

  function close() {
    dispatch("close");
  }

  async function submit() {
    if (!step || loading) return;
    toDeptError = "";
    if (!toDept.trim()) {
      toDeptError = "La dependencia destino es obligatoria.";
      tick().then(() => {
        document.getElementById("traslado-to-dept")?.focus();
      });
      return;
    }

    loading = true;
    try {
      /** @type {Record<string, unknown>} */
      const payload = {
        radicado_id: step.radicado_id,
        tracking_number: step.tracking_number,
        to_dept_code: Number(toDept),
      };
      if (assignedTo[0]) payload.assigned_to = assignedTo[0];
      if (notes.trim()) payload.notes = notes.trim();
      if (step.doc_type) payload.doc_type = step.doc_type;
      // Menor (revisión UX): mismo criterio que AsignarModal — el backend
      // trata `due_at` como instante exacto para el semáforo (`_semaforo`,
      // workflow_service.py); `T00:00:00` marcaba el radicado "vencido"
      // durante toda la jornada de su propio plazo. `T23:59:59` cubre el día
      // completo elegido.
      if (dueAt) payload.due_at = new Date(`${dueAt}T23:59:59`).toISOString();

      const res = await fetch("/bandeja/api/transfer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      notifySuccess(`Radicado ${step.tracking_number} trasladado a ${nombreDestino()}.`);
      dispatch("success", result);
    } catch (err) {
      if (codigoError(err) === "origen_desactualizado") {
        notifyError(
          "El radicado ya cambió de dependencia mientras usted trabajaba. Recargue e intente de nuevo.",
        );
      } else {
        notifyError(mensajeError(err, "No se pudo trasladar el radicado."));
      }
    } finally {
      loading = false;
    }
  }
</script>

<Modal {open} title="Trasladar radicado" size="md" on:close={close}>
  {#if step}
    <p class="modal-desc">
      Traslada el radicado <strong>{step.tracking_number}</strong> desde
      <strong>{origen}</strong> hacia otra dependencia.
    </p>
    <div class="modal-form">
      <FormField
        id="traslado-from-dept"
        label="Dependencia de origen"
        type="text"
        value={origen}
        disabled={true}
        hint="Se deriva automáticamente de la ubicación actual del radicado."
      />
      <FormField
        id="traslado-to-dept"
        label="Dependencia destino"
        type="select"
        bind:value={toDept}
        required={true}
        options={dependencias}
        placeholder="Seleccione la dependencia destino…"
        error={toDeptError}
      />
      {#if usersLoading}
        <p class="modal-loader-inline">Cargando usuarios…</p>
      {:else}
        {#if usersError}
          <p class="modal-users-error" role="alert">
            <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            {usersError}
          </p>
        {/if}
        <UserPicker
          id="traslado-assigned-to"
          label="Responsable (opcional)"
          {users}
          bind:value={assignedTo}
          max={1}
          noun="responsable"
          nounPlural="responsables"
          listTitle="Responsable del traslado"
          disabled={loading}
          hint="Seleccione como máximo un responsable del traslado."
        />
      {/if}
      <FormField
        id="traslado-due-at"
        label="Fecha límite (opcional)"
        type="date"
        bind:value={dueAt}
      />
      <FormField
        id="traslado-notes"
        label="Notas (opcional)"
        type="textarea"
        bind:value={notes}
        rows={3}
        placeholder="Contexto adicional para la dependencia destino…"
      />
    </div>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="primary" size="sm" {loading} disabled={!step} on:click={submit}>
      <i class="fas fa-share" aria-hidden="true"></i>
      Trasladar
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
  .modal-loader-inline {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }
  .modal-users-error {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0;
    padding: var(--op-space-2) var(--op-space-3);
    background: var(--op-warning-bg);
    color: var(--op-warning-fg);
    border: 1px solid var(--op-warning);
    border-radius: var(--op-radius-sm);
    font-size: var(--op-font-xs);
  }
</style>
