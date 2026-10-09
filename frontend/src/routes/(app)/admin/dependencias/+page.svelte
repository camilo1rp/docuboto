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
  /admin/dependencias — Organigrama institucional, vista árbol (E14, Fase 5
  cierre de desfase API↔UI, `tenant-service/app/routers/dependencias.py`).

  DEUDA DE MODELO DE DOMINIO (por eso el modal de advertencia más abajo, y no
  un simple tooltip): las dependencias se correlacionan por NOMBRE en otros
  servicios, NO por id/código —
    - `workflow_rules.assign_to_dept` es texto libre, sin FK a `dependencias`.
    - Los `flow_steps` en vuelo referencian la dependencia asignada también
      por su nombre/código en el momento en que se creó el paso.
  Renombrar o desactivar una dependencia NO actualiza esas referencias: un
  radicado que hoy está "en la bandeja de Tesorería" puede quedar apuntando a
  un nombre que ya no existe/ya no está activo, sin bandeja visible para
  nadie. Este archivo pide confirmación EXPLÍCITA (no un tooltip) antes de
  ambas acciones — ver `confirmarRiesgoOpen` / `RIESGO_TEXTO`.

  Árbol: no existía ningún componente de árbol reutilizable en el proyecto
  (se verificó antes de escribir `DependenciaTreeNode.svelte`) — el "árbol de
  expedientes" mencionado como referencia no es un componente visual, así que
  se construyó uno nuevo, pequeño, con `<details>/<summary>` nativos.

  Alcance: crear / editar / activar / desactivar. NO incluye borrado duro
  (el backend lo tiene — `DELETE /dependencias/{id}`, bloquea si hay hijos —
  pero el brief de esta pantalla no lo pide).
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissionLevels } from "$lib/stores/auth.js";
  import { puedeEscribirAdmin, mensajeEscrituraAdmin } from "$lib/utils/nivelAdmin.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import DependenciaTreeNode from "$lib/components/features/admin/DependenciaTreeNode.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  let tree = data.tree ?? [];
  let flat = data.flat ?? [];
  let treeLoading = false;

  $: isAdmin = puedeEscribirAdmin($permissionLevels);

  async function reload() {
    treeLoading = true;
    try {
      const [treeRes, flatRes] = await Promise.all([
        fetch("/admin/dependencias/api/tree"),
        fetch("/admin/dependencias/api"),
      ]);
      if (treeRes.ok) tree = await treeRes.json();
      if (flatRes.ok) flat = await flatRes.json();
    } catch {
      notifyError("No se pudo recargar el árbol de dependencias.");
    } finally {
      treeLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Formulario (crear / editar) — comparte estado, `editingNode` distingue
  // ---------------------------------------------------------------------------
  let modalForm = false;
  /** @type {Record<string, any>|null} */
  let editingNode = null;
  let fCodigo = "";
  let fSigla = "";
  let fNombre = "";
  let fParentId = "";
  let fDepeRadTp2 = false;
  let fActivo = true;
  let fCodigoError = "";
  let fSiglaError = "";
  let fNombreError = "";
  let actionLoading = false;

  function resetForm() {
    fCodigo = "";
    fSigla = "";
    fNombre = "";
    fParentId = "";
    fDepeRadTp2 = false;
    fActivo = true;
    fCodigoError = "";
    fSiglaError = "";
    fNombreError = "";
  }

  function openCreate() {
    editingNode = null;
    resetForm();
    modalForm = true;
  }

  function openEdit(node) {
    editingNode = node;
    fCodigo = String(node.codigo ?? "");
    fSigla = node.sigla ?? "";
    fNombre = node.nombre ?? "";
    fParentId = node.parent_id ?? "";
    fDepeRadTp2 = node.depe_rad_tp2 ?? false;
    fActivo = node.activo ?? true;
    fCodigoError = "";
    fSiglaError = "";
    fNombreError = "";
    modalForm = true;
  }

  // ---------------------------------------------------------------------------
  // Confirmación de riesgo — SIEMPRE en el flujo, nunca un tooltip (ver
  // cabecera del archivo). Se dispara si el guardado renombra o desactiva.
  // ---------------------------------------------------------------------------
  let confirmarRiesgoOpen = false;
  /** Payload pendiente de aplicar tras la confirmación. */
  let pendingPatch = null;
  /** @type {Record<string, any>|null} */
  let pendingTargetNode = null;

  const RIESGO_TEXTO_RENOMBRAR =
    "Vas a cambiar el nombre de esta dependencia. Otras partes del sistema (las reglas de enrutamiento y los pasos de flujo ya en curso) guardan la dependencia asignada por su NOMBRE, no por un identificador estable — no hay una referencia que las actualice automáticamente. Los radicados que ahora mismo están asignados a esta dependencia pueden quedar sin bandeja visible, y las reglas de enrutamiento que la mencionan por nombre dejarán de encontrarla.";
  const RIESGO_TEXTO_DESACTIVAR =
    "Vas a desactivar esta dependencia. Otras partes del sistema (las reglas de enrutamiento y los pasos de flujo ya en curso) la referencian por NOMBRE, no por un identificador estable. Los radicados que ahora mismo están asignados a esta dependencia pueden quedar sin bandeja visible para nadie, aunque la dependencia siga existiendo (solo inactiva).";

  $: riesgoTexto =
    pendingPatch?.__riesgo === "renombrar" ? RIESGO_TEXTO_RENOMBRAR : RIESGO_TEXTO_DESACTIVAR;

  async function aplicarPatch(id, payload) {
    actionLoading = true;
    try {
      const res = await fetch(`/admin/dependencias/api/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeEscrituraAdmin(resData, "No se pudo guardar la dependencia."));
        return false;
      }
      notifySuccess("Dependencia actualizada.");
      await reload();
      return true;
    } catch {
      notifyError("No se pudo conectar al servidor.");
      return false;
    } finally {
      actionLoading = false;
    }
  }

  async function crearDependencia(payload) {
    actionLoading = true;
    try {
      const res = await fetch("/admin/dependencias/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeEscrituraAdmin(resData, "No se pudo crear la dependencia."));
        return;
      }
      notifySuccess("Dependencia creada.");
      modalForm = false;
      await reload();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  async function guardarForm() {
    fCodigoError = "";
    fSiglaError = "";
    fNombreError = "";

    const codigoNum = Number(fCodigo);
    if (!Number.isInteger(codigoNum) || codigoNum < 1 || codigoNum > 999999) {
      fCodigoError = "El código debe ser un entero entre 1 y 999999.";
      return;
    }
    if (!fSigla.trim()) {
      fSiglaError = "La sigla es obligatoria.";
      return;
    }
    if (!fNombre.trim()) {
      fNombreError = "El nombre es obligatorio.";
      return;
    }

    const payload = {
      codigo: codigoNum,
      sigla: fSigla.trim(),
      nombre: fNombre.trim(),
      parent_id: fParentId || null,
      depe_rad_tp2: fDepeRadTp2,
      activo: fActivo,
    };

    if (!editingNode) {
      await crearDependencia(payload);
      return;
    }

    const renombra = payload.nombre !== editingNode.nombre;
    const desactiva = editingNode.activo === true && payload.activo === false;

    if (renombra || desactiva) {
      pendingPatch = { ...payload, __riesgo: renombra ? "renombrar" : "desactivar" };
      pendingTargetNode = editingNode;
      modalForm = false;
      confirmarRiesgoOpen = true;
      return;
    }

    const ok = await aplicarPatch(editingNode.id, payload);
    if (ok) modalForm = false;
  }

  async function confirmarRiesgoYGuardar() {
    if (!pendingTargetNode || !pendingPatch) return;
    const { __riesgo, ...payload } = pendingPatch;
    const ok = await aplicarPatch(pendingTargetNode.id, payload);
    confirmarRiesgoOpen = false;
    pendingPatch = null;
    pendingTargetNode = null;
    if (!ok) {
      // Reabrir el formulario para que el admin pueda reintentar sin perder
      // lo que ya había escrito (los campos siguen en las variables f*).
      modalForm = true;
    }
  }

  function cancelarRiesgo() {
    confirmarRiesgoOpen = false;
    pendingPatch = null;
    pendingTargetNode = null;
    // Vuelve a abrir el formulario: el admin decidió no arriesgarse a
    // renombrar/desactivar todavía, pero puede seguir editando otros campos.
    modalForm = true;
  }

  // Acción rápida "Desactivar"/"Activar" desde el árbol (sin abrir el modal
  // de edición completo). Activar es seguro (no hay pérdida de asignación
  // visible); desactivar pasa SIEMPRE por la misma confirmación de riesgo.
  function onToggleFromTree(event) {
    const node = event.detail;
    if (node.activo) {
      pendingPatch = { activo: false, __riesgo: "desactivar" };
      pendingTargetNode = node;
      confirmarRiesgoOpen = true;
    } else {
      aplicarPatch(node.id, { activo: true });
    }
  }

  function onEditFromTree(event) {
    openEdit(event.detail);
  }

  $: parentOptions = flat
    .filter((d) => !editingNode || d.id !== editingNode.id)
    .map((d) => ({ value: d.id, label: `${d.sigla} — ${d.nombre}` }));

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Dependencias" }]);
  });
</script>

<svelte:head>
  <title>Dependencias — OrpycaMCP</title>
</svelte:head>

<div class="dependencias-page">
  <div class="dependencias-page__header">
    <div>
      <h1 class="dependencias-page__title">Dependencias</h1>
      <p class="dependencias-page__subtitle">
        Organigrama institucional. La numeración de radicados y las reglas de enrutamiento dependen
        de este catálogo.
      </p>
    </div>
    {#if isAdmin}
      <Button icon="fas fa-plus" on:click={openCreate}>Nueva dependencia</Button>
    {/if}
  </div>

  {#if data.loadError}
    <div class="dependencias-page__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {data.loadError}
    </div>
  {/if}

  {#if treeLoading}
    <Loader type="skeleton" lines={6} label="Cargando árbol de dependencias…" />
  {:else if tree.length === 0 && !data.loadError}
    <EmptyState
      icon="fas fa-sitemap"
      title="Sin dependencias"
      description="Crea la primera con «Nueva dependencia»."
    />
  {:else}
    <ul class="dependencias-tree" aria-label="Árbol de dependencias">
      {#each tree as root (root.id)}
        <DependenciaTreeNode
          node={root}
          canWrite={isAdmin}
          on:edit={onEditFromTree}
          on:toggle={onToggleFromTree}
        />
      {/each}
    </ul>
  {/if}
</div>

<!-- =========================================================================
     Modal: crear / editar dependencia
     ========================================================================= -->
<Modal
  open={modalForm}
  title={editingNode ? "Editar dependencia" : "Nueva dependencia"}
  size="md"
  on:close={() => (modalForm = false)}
>
  <form
    class="dependencia-form"
    on:submit|preventDefault={guardarForm}
    aria-label="Formulario de dependencia"
    novalidate
  >
    <div class="dependencia-form__row">
      <FormField
        id="dep-codigo"
        label="Código"
        type="number"
        bind:value={fCodigo}
        required
        error={fCodigoError}
        hint="3-4 dígitos, único en el tenant."
      />
      <FormField id="dep-sigla" label="Sigla" bind:value={fSigla} required error={fSiglaError} />
    </div>

    <FormField id="dep-nombre" label="Nombre" bind:value={fNombre} required error={fNombreError} />

    <FormField
      id="dep-parent"
      label="Dependencia padre (opcional)"
      type="select"
      bind:value={fParentId}
      options={parentOptions}
      placeholder="— Raíz (sin padre) —"
    />

    <label class="dependencia-form__check">
      <input type="checkbox" bind:checked={fDepeRadTp2} />
      Habilitada para radicación tipo 2
    </label>

    <label class="dependencia-form__check">
      <input type="checkbox" bind:checked={fActivo} />
      Activa
    </label>

    {#if editingNode}
      <p class="dependencia-form__risk-hint">
        <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
        Si cambias el nombre o desmarcas "Activa", se te pedirá confirmar el riesgo antes de guardar.
      </p>
    {/if}
  </form>

  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalForm = false)}>Cancelar</Button>
    <Button icon="fas fa-save" loading={actionLoading} on:click={guardarForm}>
      {editingNode ? "Guardar cambios" : "Crear dependencia"}
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: confirmación de riesgo (renombrar / desactivar) — nunca un tooltip
     ========================================================================= -->
<Modal
  open={confirmarRiesgoOpen}
  title={pendingPatch?.__riesgo === "renombrar"
    ? "Confirmar cambio de nombre"
    : "Confirmar desactivación"}
  size="sm"
  closeOnBackdrop={false}
  on:close={cancelarRiesgo}
>
  <p class="dependencia-risk__text">{riesgoTexto}</p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cancelarRiesgo}>Cancelar</Button>
    <Button
      variant="danger"
      icon="fas fa-triangle-exclamation"
      loading={actionLoading}
      on:click={confirmarRiesgoYGuardar}
    >
      Entiendo el riesgo, continuar
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .dependencias-page {
    padding: var(--op-space-5);
    max-width: var(--op-reading-max);

    &__header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-5);
    }

    &__title {
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
  }

  .dependencias-tree {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .dependencia-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__row {
      display: grid;
      grid-template-columns: 1fr 2fr;
      gap: var(--op-space-3);

      @media (max-width: 640px) {
        grid-template-columns: 1fr;
      }
    }

    &__check {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
    }

    &__risk-hint {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-xs);
      color: var(--op-warning, var(--op-text-secondary));
      margin: 0;
    }
  }

  .dependencia-risk__text {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    line-height: var(--op-line-height);
    margin: 0;
  }
</style>
