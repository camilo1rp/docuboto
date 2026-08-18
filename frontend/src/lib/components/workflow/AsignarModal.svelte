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
  AsignarModal.svelte — Crea el PRIMER paso de flujo para un radicado que aún
  no tiene ninguno asignado (T-Fase1, RF-FLU-03). POST /bandeja/api/assign.

  A diferencia de Trasladar/Trámite/Revertir, este modal actúa sobre un
  radicado (documento), no sobre un paso de flujo (que por definición todavía
  no existe). El punto de entrada es la vista Lista de /bandeja (o el cajón de
  detalle de un radicado): el radicado llega ya resuelto por `radicado` — el
  identificador y el número de radicado son CONTEXTO, no datos que el
  operador deba teclear o pegar (antes de este cambio el modal pedía el UUID
  a mano, inusable en la práctica — ver E22 punto de entrada Asignar).

  Props:
    open          boolean              — controla la visibilidad
    radicado      {id, tracking_number, doc_type} | null — radicado destino (requerido si open)
    dependencias  Array<{value,label}> — catálogo SSR de dependencias activas

  Eventos:
    on:close    — se cerró sin completar la acción
    on:success  — la asignación se ejecutó; detail = FlowStepResponse del nuevo paso
-->
<script>
  import { createEventDispatcher, tick } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import UserPicker from "$lib/components/ui/UserPicker.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  export let open = false;
  /** @type {{id: string; tracking_number: string; doc_type?: string} | null} */
  export let radicado = null;
  /** @type {Array<{value: string; label: string}>} */
  export let dependencias = [];

  const dispatch = createEventDispatcher();

  let toDept = "";
  let notes = "";
  let dueAt = "";
  /** @type {string[]} */
  let assignedTo = [];
  let loading = false;

  let toDeptError = "";

  /** @type {Array<{id:string; username:string}>} */
  let users = [];
  let usersLoading = false;
  // I8 (revisión UX): un 500/503 al listar usuarios dejaba `users = []` sin
  // ninguna señal — el operador veía el UserPicker vacío y concluía que no
  // había ningún responsable disponible, cuando en realidad la carga falló.
  let usersError = "";

  $: if (open) resetForm();

  function resetForm() {
    toDept = "";
    notes = "";
    dueAt = "";
    assignedTo = [];
    toDeptError = "";
    usersError = "";
    loadUsers();
  }

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
          "No se pudo cargar la lista de responsables; el radicado se puede asignar igual, sin responsable.";
      }
    } catch {
      users = [];
      usersError =
        "No se pudo cargar la lista de responsables; el radicado se puede asignar igual, sin responsable.";
    } finally {
      usersLoading = false;
    }
  }

  function close() {
    dispatch("close");
  }

  async function submit() {
    if (!radicado || loading) return;

    toDeptError = "";
    if (!toDept.trim()) {
      toDeptError = "La dependencia destino es obligatoria.";
      tick().then(() => {
        document.getElementById("asignar-to-dept")?.focus();
      });
      return;
    }

    loading = true;
    try {
      /** @type {Record<string, unknown>} */
      const payload = {
        radicado_id: radicado.id,
        tracking_number: radicado.tracking_number,
        to_dept: toDept.trim(),
      };
      if (radicado.doc_type) payload.doc_type = radicado.doc_type;
      if (assignedTo[0]) payload.assigned_to = assignedTo[0];
      if (notes.trim()) payload.notes = notes.trim();
      // Menor (revisión UX): el backend compara `due_at` como un instante
      // exacto para el semáforo de vencimiento (`_semaforo`, workflow_service.py
      // — "vencido" si `due_at < now`). Con `T00:00:00` el plazo expiraba en
      // el PRIMER instante del día elegido — el radicado aparecía "vencido"
      // durante toda la jornada de su propia fecha límite. `T23:59:59` cubre
      // el día completo elegido como plazo, que es lo que el operador espera
      // al escoger una fecha en un `<input type="date">`.
      if (dueAt) payload.due_at = new Date(`${dueAt}T23:59:59`).toISOString();

      const res = await fetch("/bandeja/api/assign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      notifySuccess(`Radicado ${radicado.tracking_number} asignado a ${toDept.trim()}.`);
      dispatch("success", result);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo asignar el radicado al flujo."));
    } finally {
      loading = false;
    }
  }
</script>

<Modal {open} title="Asignar radicado a flujo" size="md" on:close={close}>
  {#if radicado}
    <!--
      «sin paso de flujo» y NO «no ha sido distribuido»: son dos cosas distintas
      y este texto confundía una con otra. El ESTADO del documento
      (`distributed`) y la existencia de un PASO de flujo son independientes —
      se puede distribuir un radicado y seguir sin paso—, así que el modal
      afirmaba «aún no ha sido distribuido» justo debajo de una etiqueta que
      decía «Distribuido». Se usa el mismo lenguaje que la hoja de ruta, que ya
      dice «Sin paso activo».
    -->
    <p class="modal-desc">
      Crea el primer paso de flujo para el radicado
      <strong>{radicado.tracking_number}</strong>, que todavía no tiene ninguno.
    </p>
  {/if}
  <div class="modal-form">
    <FormField
      id="asignar-radicado"
      label="Radicado"
      type="text"
      value={radicado?.tracking_number ?? ""}
      disabled={true}
      hint="El radicado se toma del contexto en el que se abrió esta acción."
    />
    <FormField
      id="asignar-to-dept"
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
        id="asignar-assigned-to"
        label="Responsable (opcional)"
        {users}
        bind:value={assignedTo}
        max={1}
        disabled={loading}
        hint="Seleccione como máximo un responsable de la asignación."
      />
    {/if}
    <FormField id="asignar-due-at" label="Fecha límite (opcional)" type="date" bind:value={dueAt} />
    <FormField
      id="asignar-notes"
      label="Notas (opcional)"
      type="textarea"
      bind:value={notes}
      rows={3}
      placeholder="Contexto adicional para la dependencia destino…"
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="primary" size="sm" {loading} disabled={!radicado} on:click={submit}>
      <i class="fas fa-inbox" aria-hidden="true"></i>
      Asignar
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
