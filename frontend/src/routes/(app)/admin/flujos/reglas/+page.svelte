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
  /admin/flujos/reglas — CRUD de reglas de enrutamiento automático (E05,
  `workflow-service/app/routers/rules.py`).

  Modelo REAL verificado en el backend (`app/schemas/rules.py`):
    WorkflowRuleCreate: name (1..200, obligatorio), description?, is_active
    (default true), priority (1..999, default 100, menor = mayor prioridad),
    conditions[] ({field, op, value} — enums CERRADOS, ver
    `$lib/utils/workflowRuleConditions.js`), match_mode ('all'|'any'),
    assign_to_dept (obligatorio), assign_notes?.
  Existen 4 columnas "legadas" (doc_type/doc_class/dest_dept/subject_contains)
  de compatibilidad hacia atrás — esta UI NO las expone: `conditions[]` es
  estrictamente más expresivo (mismos campos + más operadores) y el propio
  backend prioriza `conditions` sobre las columnas legadas si ambas están
  presentes (`rule_matches`), así que no hay pérdida de capacidad al omitirlas.

  RBAC (espejo del backend, D-05 regla 3): esta pantalla se ofrece solo con
  USUA_PERM_ADMIN. `workflow-service/app/routers/rules.py` gatea con
  `require_permission("USUA_PERM_ADMIN")` en sus 5 endpoints de CRUD
  (create/list/get/update/delete) y también en los 2 de evaluación — la UI es
  un espejo de esa regla, no una segunda fuente de verdad: el backend
  siempre re-valida y responde 403 si el permiso falta, independientemente de
  lo que esta pantalla decida mostrar u ocultar.

  D-05 — access_token NUNCA en cliente: todas las escrituras van a
  /admin/flujos/reglas/api[/id].
-->
<script>
  import { onMount, tick } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import {
    RULE_FIELD_OPTIONS,
    RULE_OP_OPTIONS,
    FIELD_VALUES,
    OP_VALUES,
  } from "$lib/utils/workflowRuleConditions.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  /** @type {Array<any>} */
  let reglas = data.reglas ?? [];
  let actionLoading = false;

  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  /** @type {Array<{key: string, label: string, sortable?: boolean, width?: string, filterType?: 'text'|'select'|'dateRange', filterOptions?: Array<{value: string, label: string}>}>} */
  const columns = [
    { key: "priority", label: "Prioridad", sortable: true, width: "100px" },
    { key: "name", label: "Nombre", sortable: true },
    {
      key: "is_active",
      label: "Estado",
      filterType: "select",
      filterOptions: [
        { value: "true", label: "Activa" },
        { value: "false", label: "Inactiva" },
      ],
    },
    { key: "assign_to_dept", label: "Asigna a" },
    { key: "conditions_count", label: "Condiciones" },
  ];

  // Fila derivada para la tabla: conteo legible de condiciones sin mutar
  // `reglas` (que conserva la forma exacta de WorkflowRuleResponse).
  $: tableRows = reglas.map((r) => ({
    ...r,
    conditions_count: `${(r.conditions ?? []).length} (${r.match_mode === "any" ? "alguna" : "todas"})`,
  }));

  // ---------------------------------------------------------------------------
  // Formulario (crear / editar) — estado compartido entre ambos modales
  // ---------------------------------------------------------------------------
  let modalForm = false;
  let editingId = null; // null = creación
  let fName = "";
  let fDescription = "";
  let fIsActive = true;
  // FormField.value es siempre string (input HTML nativo); se convierte a
  // número recién al construir el payload (`saveRule`).
  let fPriority = "100";
  let fPriorityError = "";
  let fMatchMode = "all";
  let fAssignToDept = "";
  let fAssignNotes = "";
  /** @type {Array<{id: string, field: string, op: string, value: string}>} */
  let fConditions = [];
  let fNameError = "";
  let fAssignToDeptError = "";
  // Mensaje + fila culpable cuando una condición queda inválida al guardar
  // (ver `saveRule`) — se identifica por `id` estable de la fila, no por
  // índice, para que sobreviva a un `removeCondition` intermedio.
  let fConditionsError = "";
  /** @type {string|null} */
  let fConditionErrorId = null;

  // Confirmación explícita al guardar una regla SIN condiciones — de lo
  // contrario se aplicaría a cualquier radicado sin que el administrador se
  // entere (ver cabecera de este archivo y `workflowRuleConditions.js`).
  let modalNoConditions = false;

  let conditionSeq = 0;
  function nextConditionId() {
    conditionSeq += 1;
    return `cond-${conditionSeq}`;
  }

  /** Mueve el foco al control con este id (tras el próximo tick, ya en el DOM). */
  function focusField(id) {
    if (typeof document === "undefined") return;
    tick().then(() => document.getElementById(id)?.focus());
  }

  function resetForm() {
    fName = "";
    fDescription = "";
    fIsActive = true;
    fPriority = "100";
    fPriorityError = "";
    fMatchMode = "all";
    fAssignToDept = "";
    fAssignNotes = "";
    fConditions = [];
    fNameError = "";
    fAssignToDeptError = "";
    fConditionsError = "";
    fConditionErrorId = null;
  }

  function openCreate() {
    editingId = null;
    resetForm();
    modalForm = true;
  }

  function openEdit(regla) {
    editingId = regla.id;
    fName = regla.name ?? "";
    fDescription = regla.description ?? "";
    fIsActive = regla.is_active ?? true;
    fPriority = String(regla.priority ?? 100);
    fPriorityError = "";
    fMatchMode = regla.match_mode === "any" ? "any" : "all";
    fAssignToDept = regla.assign_to_dept ?? "";
    fAssignNotes = regla.assign_notes ?? "";
    fConditions = (regla.conditions ?? []).map((c) => ({
      id: nextConditionId(),
      field: c.field,
      op: c.op,
      value: Array.isArray(c.value) ? c.value.join(", ") : String(c.value ?? ""),
    }));
    fNameError = "";
    fAssignToDeptError = "";
    fConditionsError = "";
    fConditionErrorId = null;
    modalForm = true;
  }

  function addCondition() {
    fConditions = [
      ...fConditions,
      { id: nextConditionId(), field: RULE_FIELD_OPTIONS[0].value, op: "eq", value: "" },
    ];
  }

  function removeCondition(id) {
    fConditions = fConditions.filter((c) => c.id !== id);
    if (fConditionErrorId === id) {
      fConditionsError = "";
      fConditionErrorId = null;
    }
  }

  /** Construye y envía el payload validado — llamado directo o tras confirmar el modal de "sin condiciones". */
  async function persistRule() {
    const payload = {
      name: fName.trim(),
      description: fDescription.trim() || null,
      is_active: fIsActive,
      priority: Number(fPriority),
      conditions: fConditions.map(({ field, op, value }) => ({ field, op, value })),
      match_mode: fMatchMode,
      assign_to_dept: fAssignToDept.trim(),
      assign_notes: fAssignNotes.trim() || null,
    };

    actionLoading = true;
    try {
      const url = editingId ? `/admin/flujos/reglas/api/${editingId}` : "/admin/flujos/reglas/api";
      const res = await fetch(url, {
        method: editingId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo guardar la regla."));
        return;
      }
      if (editingId) {
        reglas = reglas.map((r) => (r.id === resData.id ? resData : r));
        notifySuccess("Regla actualizada.");
      } else {
        reglas = [...reglas, resData];
        notifySuccess("Regla creada.");
      }
      modalForm = false;
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  /**
   * Valida el formulario y, si todo está en orden, guarda. Antes de este
   * cambio, `buildConditionsPayload` (server-side) descartaba en silencio
   * cualquier fila incompleta o fuera de catálogo — un administrador podía
   * creer que guardó 3 condiciones y en realidad guardó 2, o incluso 0 (lo
   * que hace que la regla case CUALQUIER radicado). Ahora:
   *   - una fila con field/op fuera de catálogo o value vacío bloquea el
   *     guardado y señala cuál es, con el foco puesto ahí;
   *   - si no queda ninguna condición, se pide confirmación explícita antes
   *     de guardar el "atrapa-todo".
   */
  async function saveRule() {
    fNameError = "";
    fAssignToDeptError = "";
    fPriorityError = "";
    fConditionsError = "";
    fConditionErrorId = null;

    if (!fName.trim()) {
      fNameError = "El nombre es obligatorio.";
      focusField("regla-nombre");
      return;
    }

    const priorityNum = Number(fPriority);
    if (!Number.isFinite(priorityNum) || priorityNum < 1 || priorityNum > 999) {
      fPriorityError = "La prioridad debe ser un número entre 1 y 999.";
      focusField("regla-prioridad");
      return;
    }

    if (!fAssignToDept.trim()) {
      fAssignToDeptError = "La dependencia de destino es obligatoria.";
      focusField("regla-asigna-a");
      return;
    }

    for (let i = 0; i < fConditions.length; i++) {
      const cond = fConditions[i];
      if (!FIELD_VALUES.has(cond.field) || !OP_VALUES.has(cond.op)) {
        fConditionsError = `La condición ${i + 1} tiene un campo u operador que ya no existe en el catálogo.`;
        fConditionErrorId = cond.id;
        focusField(`regla-cond-field-${cond.id}`);
        return;
      }
      if (!String(cond.value ?? "").trim()) {
        fConditionsError = `La condición ${i + 1} no tiene valor.`;
        fConditionErrorId = cond.id;
        focusField(`regla-cond-value-${cond.id}`);
        return;
      }
    }

    if (fConditions.length === 0) {
      modalNoConditions = true;
      return;
    }

    await persistRule();
  }

  function confirmNoConditionsAndSave() {
    modalNoConditions = false;
    persistRule();
  }

  // ---------------------------------------------------------------------------
  // Borrado
  // ---------------------------------------------------------------------------
  let deletingRule = null;
  let modalDelete = false;

  function confirmDelete(regla) {
    deletingRule = regla;
    modalDelete = true;
  }

  async function deleteRule() {
    if (!deletingRule) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/flujos/reglas/api/${deletingRule.id}`, { method: "DELETE" });
      if (res.status === 204 || res.ok) {
        reglas = reglas.filter((r) => r.id !== deletingRule.id);
        notifySuccess("Regla eliminada.");
        modalDelete = false;
        deletingRule = null;
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(mensajeError(err, "No se pudo eliminar la regla."));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Flujos y reglas" }]);
  });
</script>

<svelte:head>
  <title>Reglas de enrutamiento — OrpycaMCP</title>
</svelte:head>

<div class="reglas-page">
  <div class="reglas-page__header">
    <div>
      <h1 class="reglas-page__title">Reglas de enrutamiento</h1>
      <p class="reglas-page__subtitle">
        Asignan automáticamente un radicado a una dependencia según sus condiciones. La primera
        regla activa que casa, en orden de prioridad (menor número primero), gana.
      </p>
    </div>
    <div class="reglas-page__header-actions">
      <Button variant="secondary" icon="fas fa-people-arrows" href="/admin/flujos/reasignacion">
        Reasignación en cascada
      </Button>
      {#if isAdmin}
        <Button icon="fas fa-plus" on:click={openCreate}>Nueva regla</Button>
      {/if}
    </div>
  </div>

  {#if data.loadError}
    <!-- Aviso persistente desde el montaje (no un evento que ocurra después),
         `role="alert"` es para lo segundo — un contenedor con estilo de
         aviso basta y no interrumpe al lector de pantalla al cargar. -->
    <div class="reglas-page__error">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {data.loadError}
    </div>
  {/if}

  {#if !isAdmin}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de administración"
      description="Tu usuario no tiene el permiso de administración necesario para gestionar reglas de enrutamiento."
    />
  {:else if reglas.length === 0 && !data.loadError}
    <EmptyState
      icon="fas fa-diagram-project"
      title="Sin reglas de enrutamiento"
      description="Crea la primera regla con el botón «Nueva regla»."
    />
  {:else}
    <DataTable
      {columns}
      rows={tableRows}
      totalCount={tableRows.length}
      pageSize={Math.max(tableRows.length, 1)}
      enableFilters
      caption="Reglas de enrutamiento"
    >
      <svelte:fragment slot="row" let:row>
        <td>{row.priority}</td>
        <td class="reglas-page__name">{row.name}</td>
        <td>
          <StatusChip
            status={row.is_active ? "success" : "neutral"}
            label={row.is_active ? "Activa" : "Inactiva"}
            dot
          />
        </td>
        <td>{row.assign_to_dept}</td>
        <td>{row.conditions_count}</td>
        <td class="reglas-page__actions">
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-pen"
            aria-label="Editar regla {row.name}"
            on:click={() => openEdit(row)}
          >
            Editar
          </Button>
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-trash"
            aria-label="Eliminar regla {row.name}"
            on:click={() => confirmDelete(row)}
          >
            Eliminar
          </Button>
        </td>
      </svelte:fragment>
    </DataTable>
  {/if}
</div>

<!-- =========================================================================
     Modal: crear / editar regla
     ========================================================================= -->
<Modal
  open={modalForm}
  title={editingId ? "Editar regla" : "Nueva regla"}
  size="lg"
  on:close={() => (modalForm = false)}
>
  <form
    class="regla-form"
    on:submit|preventDefault={saveRule}
    aria-label="Formulario de regla de enrutamiento"
    novalidate
  >
    <div class="regla-form__row">
      <FormField
        id="regla-nombre"
        label="Nombre"
        type="text"
        bind:value={fName}
        required
        error={fNameError}
      />
      <FormField
        id="regla-prioridad"
        label="Prioridad"
        type="number"
        bind:value={fPriority}
        hint="1 a 999. Menor número = se evalúa primero."
        error={fPriorityError}
      />
    </div>

    <FormField
      id="regla-descripcion"
      label="Descripción"
      type="textarea"
      rows={2}
      bind:value={fDescription}
    />

    <div class="regla-form__check-row">
      <label class="regla-form__check-label" for="regla-activa">
        <input
          id="regla-activa"
          type="checkbox"
          class="regla-form__check"
          bind:checked={fIsActive}
        />
        Activa (se evalúa al radicar)
      </label>
    </div>

    <fieldset class="regla-form__conditions">
      <legend class="regla-form__conditions-legend">Condiciones</legend>
      <p class="regla-form__conditions-hint">
        Si no agrega ninguna condición, la regla se aplica a cualquier radicado (según su prioridad
        y estado activo).
      </p>

      <FormField
        id="regla-match-mode"
        label="Combinar condiciones con"
        type="select"
        options={[
          { value: "all", label: "Todas (Y)" },
          { value: "any", label: "Alguna (O)" },
        ]}
        bind:value={fMatchMode}
      />

      {#each fConditions as cond, idx (cond.id)}
        <div class="regla-form__condition-row">
          <FormField
            id={`regla-cond-field-${cond.id}`}
            label="Campo"
            type="select"
            options={RULE_FIELD_OPTIONS}
            bind:value={cond.field}
            error={fConditionErrorId === cond.id && fConditionsError.includes("catálogo")
              ? fConditionsError
              : ""}
          />
          <FormField
            id={`regla-cond-op-${cond.id}`}
            label="Operador"
            type="select"
            options={RULE_OP_OPTIONS}
            bind:value={cond.op}
          />
          <FormField
            id={`regla-cond-value-${cond.id}`}
            label="Valor"
            type="text"
            bind:value={cond.value}
            hint={cond.op === "in" ? "Lista separada por comas" : ""}
            error={fConditionErrorId === cond.id && !fConditionsError.includes("catálogo")
              ? fConditionsError
              : ""}
          />
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-trash"
            aria-label="Quitar condición {idx + 1}"
            on:click={() => removeCondition(cond.id)}
          >
            Quitar
          </Button>
        </div>
      {/each}

      <Button variant="secondary" size="sm" icon="fas fa-plus" on:click={addCondition}>
        Agregar condición
      </Button>
    </fieldset>

    <div class="regla-form__row">
      <FormField
        id="regla-asigna-a"
        label="Dependencia de destino"
        type="text"
        bind:value={fAssignToDept}
        required
        error={fAssignToDeptError}
        hint="Dependencia a la que se asigna el radicado cuando la regla casa."
      />
    </div>

    <FormField
      id="regla-notas"
      label="Notas de la asignación"
      type="textarea"
      rows={2}
      bind:value={fAssignNotes}
    />
  </form>

  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalForm = false)}>Cancelar</Button>
    <Button icon="fas fa-save" loading={actionLoading} on:click={saveRule}>
      {editingId ? "Guardar cambios" : "Crear regla"}
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: confirmar borrado
     ========================================================================= -->
<Modal open={modalDelete} title="Eliminar regla" size="sm" on:close={() => (modalDelete = false)}>
  <p class="modal-confirm__text">
    ¿Está seguro de que desea eliminar la regla
    <strong>{deletingRule?.name}</strong>? Esta acción no puede deshacerse.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalDelete = false)}>Cancelar</Button>
    <Button variant="danger" icon="fas fa-trash" loading={actionLoading} on:click={deleteRule}>
      Eliminar
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: confirmar regla SIN condiciones (se aplicaría a TODO radicado)
     ========================================================================= -->
<Modal
  open={modalNoConditions}
  title="Regla sin condiciones"
  size="sm"
  on:close={() => (modalNoConditions = false)}
>
  <p class="modal-confirm__text">
    Esta regla no tiene ninguna condición: se aplicará a <strong>cualquier radicado</strong> que entre
    (según su prioridad y estado activo), sin importar tipo, dependencia, asunto ni ningún otro criterio.
    ¿Confirma que esto es intencional?
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalNoConditions = false)}>Cancelar</Button>
    <Button
      variant="danger"
      icon="fas fa-check"
      loading={actionLoading}
      on:click={confirmNoConditionsAndSave}
    >
      Confirmar y guardar
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .reglas-page {
    padding: var(--op-space-5);
    max-width: 1100px;

    &__header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-5);
    }

    &__header-actions {
      display: flex;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-1);
    }

    &__subtitle {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
      max-width: 60ch;
    }

    &__error {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      background: var(--op-error-bg);
      color: var(--op-error-fg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
      margin-bottom: var(--op-space-4);
    }

    &__name {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
      white-space: nowrap;
    }
  }

  .regla-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-3);

      @media (max-width: 640px) {
        grid-template-columns: 1fr;
      }
    }

    &__check-row {
      display: flex;
      align-items: center;
    }

    &__check-label {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      // El label completo (no solo el cuadro de 16px) es el objetivo de
      // click/tap real, con relleno vertical para acercarse al mínimo
      // táctil de 44px sin agrandar el checkbox visualmente en escritorio.
      padding: var(--op-space-2) 0;
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
      user-select: none;

      @media (pointer: coarse) {
        min-height: 44px;
      }
    }

    &__check {
      width: 16px;
      height: 16px;
      accent-color: var(--op-primary);
      cursor: pointer;

      // El cuadro en sí crece al mínimo táctil en dispositivos táctiles;
      // en mouse/trackpad mantiene la densidad visual actual.
      @media (pointer: coarse) {
        width: 24px;
        height: 24px;
      }
    }

    &__conditions {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      padding: var(--op-space-4);
      margin: 0;
    }

    &__conditions-legend {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      padding: 0 var(--op-space-2);
    }

    &__conditions-hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__condition-row {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr auto;
      gap: var(--op-space-2);
      align-items: end;
      padding-bottom: var(--op-space-2);
      border-bottom: 1px solid var(--op-border);

      @media (max-width: 720px) {
        grid-template-columns: 1fr;
      }
    }
  }

  .modal-confirm__text {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    line-height: var(--op-line-height);
    margin: 0;
  }
</style>
