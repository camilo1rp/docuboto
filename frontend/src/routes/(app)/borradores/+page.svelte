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
  /borradores — Gestor de borradores de Salida/Interno (T-12c).

  Criterio de hecho: crear un borrador desde una plantilla, aprobarlo y radicarlo
  genera el radicado real — su tracking_number se muestra en pantalla.

  Acciones del flujo:
    borrador → [editar] → [aprobar] → aprobado → [radicar] → radicado (DocumentResponse)

  RBAC (espejo del backend, D-11):
    - Ver lista, crear, editar, aprobar, radicar → PERM_RADI
    - Acciones de escritura deshabilitadas si el usuario no tiene PERM_RADI.
    El backend re-valida en cada llamada; la UI solo filtra la visibilidad.

  D-05 — access_token NUNCA en cliente:
    Todas las llamadas van a /borradores/api[/id/accion] que leen locals.accessToken
    en el servidor. El token nunca llega al JS del cliente.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import RichEditor from "$lib/components/editor/RichEditor.svelte";
  import RagSuggestButton from "$lib/components/features/rag/RagSuggestButton.svelte";
  import { appendGeneratedHtml } from "$lib/utils/ragInsert.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Estado de la lista
  // ---------------------------------------------------------------------------

  /** @type {import('$lib/types/api.js').BorradorResponse[]} */
  let borradores = data.borradores ?? [];
  let plantillas = data.plantillas ?? [];
  let loading = false;
  let estadoFilter = data.initialEstado ?? "";

  const ESTADOS = [
    { value: "", label: "Todos" },
    { value: "borrador", label: "Borrador" },
    { value: "aprobado", label: "Aprobado" },
    { value: "radicado", label: "Radicado" },
  ];

  // ---------------------------------------------------------------------------
  // Drawer de creación / edición
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  let drawerMode = /** @type {'create'|'edit'} */ ("create");
  /** @type {import('$lib/types/api.js').BorradorResponse|null} */
  let editingBorrador = null;
  let actionLoading = false;

  // Campos del formulario
  let fDocType = "S";
  let fSubject = "";
  let fDestDept = "";
  let fCuerpo = "";
  let fPlantillaId = "";
  let fSubjectError = "";

  /**
   * Precarga el cuerpo del editor cuando el usuario selecciona una plantilla.
   * Solo activo en modo creación; en edición el cambio de plantilla NO pisa el
   * cuerpo existente del borrador (ALTO #1 — pérdida de datos).
   */
  function handlePlantillaChange() {
    if (drawerMode !== "create" || !fPlantillaId) return;
    const found = plantillas.find((p) => p.id === fPlantillaId);
    // BAJO #6: sembrar el editor desde el cuerpo YA saneado (cuerpo_html).
    if (found) fCuerpo = found.cuerpo_html ?? found.cuerpo ?? "";
  }

  // ---------------------------------------------------------------------------
  // Modal de confirmación de radicación
  // ---------------------------------------------------------------------------
  let modalRadicar = false;
  /** @type {string|null} — tracking_number tras radicación exitosa */
  let trackingNumberResult = null;
  let modalRadicadoOpen = false;

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Borradores" }]);
  });

  // ---------------------------------------------------------------------------
  // Filtrar lista
  // ---------------------------------------------------------------------------
  async function applyFilter(estado) {
    estadoFilter = estado;
    loading = true;
    try {
      const params = new URLSearchParams();
      if (estado) params.set("estado", estado);
      const res = await fetch(`/borradores/api?${params}`);
      if (res.ok) {
        borradores = await res.json();
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(err.detail ?? "Error al cargar borradores.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      loading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Abrir drawer para crear
  // ---------------------------------------------------------------------------
  function openCreate() {
    drawerMode = "create";
    editingBorrador = null;
    fDocType = "S";
    fSubject = "";
    fDestDept = "";
    fCuerpo = "";
    fPlantillaId = "";
    fSubjectError = "";
    drawerOpen = true;
  }

  // ---------------------------------------------------------------------------
  // Abrir drawer para editar
  // ---------------------------------------------------------------------------
  function openEdit(borrador) {
    drawerMode = "edit";
    editingBorrador = borrador;
    fDocType = borrador.doc_type ?? "S";
    fSubject = borrador.subject ?? "";
    fDestDept = borrador.dest_dept ?? "";
    // BAJO #6: sembrar el editor desde el cuerpo YA saneado (cuerpo_html).
    fCuerpo = borrador.cuerpo_html ?? borrador.cuerpo ?? "";
    fPlantillaId = borrador.plantilla_id ?? "";
    fSubjectError = "";
    drawerOpen = true;
  }

  function closeDrawer() {
    drawerOpen = false;
  }

  // ---------------------------------------------------------------------------
  // Guardar (crear o editar)
  // ---------------------------------------------------------------------------
  async function guardar() {
    fSubjectError = "";
    if (!fSubject.trim()) {
      fSubjectError = "El asunto es obligatorio.";
      return;
    }

    actionLoading = true;
    try {
      let res;
      if (drawerMode === "create") {
        res = await fetch("/borradores/api", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            doc_type: fDocType,
            subject: fSubject.trim(),
            dest_dept: fDestDept.trim() || null,
            cuerpo: fCuerpo || null,
            plantilla_id: fPlantillaId || null,
          }),
        });
      } else {
        res = await fetch(`/borradores/api/${editingBorrador.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            subject: fSubject.trim(),
            dest_dept: fDestDept.trim() || null,
            cuerpo: fCuerpo || null,
          }),
        });
      }

      const data = await res.json();
      if (!res.ok) {
        notifyError(data.detail ?? "Error al guardar el borrador.");
        return;
      }

      if (drawerMode === "create") {
        borradores = [data, ...borradores];
        notifySuccess("Borrador creado.");
      } else {
        borradores = borradores.map((b) => (b.id === data.id ? data : b));
        editingBorrador = data;
        notifySuccess("Borrador actualizado.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Aprobar
  // ---------------------------------------------------------------------------
  async function aprobar() {
    if (!editingBorrador) return;
    actionLoading = true;
    try {
      const res = await fetch(`/borradores/api/${editingBorrador.id}/aprobar`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) {
        notifyError(data.detail ?? "No se pudo aprobar el borrador.");
        return;
      }
      borradores = borradores.map((b) => (b.id === data.id ? data : b));
      editingBorrador = data;
      notifySuccess("Borrador aprobado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Radicar (con confirmación)
  // ---------------------------------------------------------------------------
  function pedirConfirmacionRadicar() {
    modalRadicar = true;
  }

  async function confirmarRadicar() {
    if (!editingBorrador) return;
    modalRadicar = false;
    actionLoading = true;
    try {
      const res = await fetch(`/borradores/api/${editingBorrador.id}/radicar`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) {
        notifyError(data.detail ?? "No se pudo radicar el borrador.");
        return;
      }
      // data es DocumentResponse con tracking_number
      trackingNumberResult = data.tracking_number ?? null;
      // MEDIO #4: persistir tracking_number y radicado_id en la fila de la lista
      // para que sigan visibles tras cerrar el modal.
      // Cast explícito para que `estado` mantenga la unión literal, no `string`.
      /** @type {import('$lib/types/api.js').BorradorResponse} */
      const updatedBorrador = {
        ...editingBorrador,
        estado: /** @type {'borrador'|'aprobado'|'radicado'} */ ("radicado"),
        radicado_id: data.id ?? editingBorrador.radicado_id,
        _tracking_number: data.tracking_number ?? null,
      };
      borradores = borradores.map((b) =>
        b.id === editingBorrador.id ? updatedBorrador : b,
      );
      editingBorrador = updatedBorrador;
      modalRadicadoOpen = true;
      drawerOpen = false;
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers de presentación
  // ---------------------------------------------------------------------------
  const ESTADO_CHIP = {
    borrador: "neutral",
    aprobado: "warning",
    radicado: "success",
  };

  const ESTADO_LABEL = {
    borrador: "Borrador",
    aprobado: "Aprobado",
    radicado: "Radicado",
  };

  const DOCTYPE_LABEL = { S: "Salida", I: "Interno" };

  function formatDate(iso) {
    if (!iso) return "—";
    try {
      return new Intl.DateTimeFormat("es-CO", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      }).format(new Date(iso));
    } catch {
      return iso.slice(0, 10);
    }
  }

  // ¿El borrador activo se puede editar? Solo si estado === 'borrador'
  $: canEdit = editingBorrador?.estado === "borrador";
  $: canAprobar = editingBorrador?.estado === "borrador";
  $: canRadicar = editingBorrador?.estado === "aprobado";
  $: hasPerm = $permissions.includes("PERM_RADI");

  // Opciones de plantillas para el select
  $: plantillaOptions = [
    { value: "", label: "Sin plantilla" },
    ...plantillas.map((p) => ({ value: p.id, label: p.nombre })),
  ];
</script>

<svelte:head>
  <title>Borradores — OrpycaMCP</title>
</svelte:head>

<!-- =========================================================================
     Página principal
     ========================================================================= -->
<div class="borradores-page">
  <!-- Cabecera -->
  <div class="borradores-page__header">
    <div class="borradores-page__title-row">
      <h1 class="borradores-page__title">Borradores</h1>
      {#if hasPerm}
        <Button icon="fas fa-plus" on:click={openCreate}>Nuevo borrador</Button>
      {/if}
    </div>

    <!-- Filtros de estado -->
    <div class="borradores-page__filters" role="group" aria-label="Filtrar por estado">
      {#each ESTADOS as e (e.value)}
        <button
          type="button"
          class="borradores-page__filter-btn"
          class:borradores-page__filter-btn--active={estadoFilter === e.value}
          aria-pressed={estadoFilter === e.value}
          on:click={() => applyFilter(e.value)}
        >
          {e.label}
        </button>
      {/each}
    </div>
  </div>

  <!-- Error de carga -->
  {#if data.loadError}
    <div class="borradores-page__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {data.loadError}
    </div>
  {/if}

  <!-- Estado de carga -->
  {#if loading}
    <div class="borradores-page__loader">
      <Loader />
    </div>
  {:else if borradores.length === 0}
    <EmptyState
      icon="fa-file-alt"
      title="No hay borradores"
      description={estadoFilter
        ? `No hay borradores en estado "${ESTADO_LABEL[estadoFilter] ?? estadoFilter}".`
        : "Crea el primer borrador con el botón «Nuevo borrador»."}
    />
  {:else}
    <!-- Tabla de borradores -->
    <div class="borradores-table-wrap" role="region" aria-label="Lista de borradores">
      <table class="borradores-table" aria-label="Borradores">
        <thead>
          <tr>
            <th scope="col">Asunto</th>
            <th scope="col">Tipo</th>
            <th scope="col">Destino</th>
            <th scope="col">Estado</th>
            <th scope="col">N.° Radicado</th>
            <th scope="col">Creado</th>
            <th scope="col"><span class="sr-only">Acciones</span></th>
          </tr>
        </thead>
        <tbody>
          {#each borradores as borrador (borrador.id)}
            <tr class="borradores-table__row">
              <td class="borradores-table__subject">{borrador.subject}</td>
              <td>{DOCTYPE_LABEL[borrador.doc_type] ?? borrador.doc_type}</td>
              <td>{borrador.dest_dept ?? "—"}</td>
              <td>
                <StatusChip
                  status={ESTADO_CHIP[borrador.estado] ?? "neutral"}
                  label={ESTADO_LABEL[borrador.estado] ?? borrador.estado}
                  dot
                />
              </td>
              <td class="borradores-table__tracking">
                {#if borrador._tracking_number}
                  <span class="borradores-table__tracking-num">{borrador._tracking_number}</span>
                {:else}
                  <span class="borradores-table__tracking-none">—</span>
                {/if}
              </td>
              <td class="borradores-table__date">{formatDate(borrador.created_at)}</td>
              <td class="borradores-table__actions">
                <Button
                  variant="ghost"
                  size="sm"
                  icon="fas fa-pencil-alt"
                  aria-label="Editar borrador {borrador.subject}"
                  on:click={() => openEdit(borrador)}
                >
                  Abrir
                </Button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<!-- =========================================================================
     Drawer: crear / editar borrador
     ========================================================================= -->
<Drawer
  open={drawerOpen}
  title={drawerMode === "create" ? "Nuevo borrador" : "Editar borrador"}
  width="600px"
  on:close={closeDrawer}
>
  <form
    class="borrador-form"
    on:submit|preventDefault={guardar}
    aria-label={drawerMode === "create" ? "Formulario de nuevo borrador" : "Formulario de edición"}
    novalidate
  >
    <!-- Estado visible (solo en edición) -->
    {#if editingBorrador}
      <div class="borrador-form__estado">
        <span class="borrador-form__estado-label">Estado:</span>
        <StatusChip
          status={ESTADO_CHIP[editingBorrador.estado] ?? "neutral"}
          label={ESTADO_LABEL[editingBorrador.estado] ?? editingBorrador.estado}
          dot
        />
      </div>
    {/if}

    <!-- Tipo de documento -->
    <FormField
      id="borrador-doctype"
      label="Tipo de documento"
      type="select"
      bind:value={fDocType}
      required
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
      options={[
        { value: "S", label: "Salida" },
        { value: "I", label: "Interno" },
      ]}
    />

    <!-- Asunto -->
    <FormField
      id="borrador-subject"
      label="Asunto"
      type="text"
      bind:value={fSubject}
      placeholder="Descripción breve del documento"
      required
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
      error={fSubjectError}
    />

    <!-- Destino -->
    <FormField
      id="borrador-destdept"
      label="Dependencia destino"
      type="text"
      bind:value={fDestDept}
      placeholder="Nombre o código de la dependencia receptora"
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
    />

    <!-- Selector de plantilla (solo en creación) -->
    {#if drawerMode === "create" && plantillas.length > 0}
      <FormField
        id="borrador-plantilla"
        label="Plantilla de contenido"
        type="select"
        bind:value={fPlantillaId}
        disabled={!hasPerm}
        options={plantillaOptions}
        hint="Al elegir una plantilla, su contenido se carga en el editor."
        on:change={handlePlantillaChange}
      />
    {/if}

    <!-- F6: ayuda de redacción con RAG — opcional, degrada sin romper crear/editar
         el borrador si el servicio de conocimiento no responde. -->
    <RagSuggestButton
      endpoint="/borradores/api/rag/suggest"
      query={fSubject}
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
      on:insert={(e) => (fCuerpo = appendGeneratedHtml(fCuerpo, e.detail.html))}
    />

    <!-- Editor de cuerpo -->
    <RichEditor
      id="borrador-cuerpo"
      label="Contenido del documento"
      bind:value={fCuerpo}
      placeholder="Redacte el cuerpo del documento aquí…"
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
    />

    <!-- Botón guardar -->
    {#if hasPerm && (editingBorrador === null || canEdit)}
      <div class="borrador-form__save-row">
        <Button type="submit" icon="fas fa-save" loading={actionLoading}>
          {drawerMode === "create" ? "Crear borrador" : "Guardar cambios"}
        </Button>
      </div>
    {/if}
  </form>

  <!-- Acciones de flujo (solo en edición) -->
  {#if editingBorrador && hasPerm}
    <div class="borrador-form__flow-actions">
      <hr class="borrador-form__divider" aria-hidden="true" />
      <p class="borrador-form__flow-title">Acciones de flujo</p>

      <!-- Aprobar -->
      {#if canAprobar}
        <Button
          variant="secondary"
          icon="fas fa-check-circle"
          loading={actionLoading}
          on:click={aprobar}
        >
          Aprobar borrador
        </Button>
      {/if}

      <!-- Radicar (solo si aprobado) -->
      {#if canRadicar}
        <Button
          icon="fas fa-paper-plane"
          loading={actionLoading}
          on:click={pedirConfirmacionRadicar}
        >
          Radicar
        </Button>
      {/if}

      <!-- Radicado — estado final, tracking_number visible (MEDIO #4) -->
      {#if editingBorrador.estado === "radicado"}
        <div class="borrador-form__flow-done" role="status">
          <i class="fas fa-check-double" aria-hidden="true"></i>
          <span>Este borrador ya fue radicado.</span>
          {#if editingBorrador._tracking_number}
            <span>
              N.° <strong>{editingBorrador._tracking_number}</strong>
            </span>
          {:else if editingBorrador.radicado_id}
            <span class="sr-only">ID de radicado: {editingBorrador.radicado_id}</span>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</Drawer>

<!-- =========================================================================
     Modal: confirmación de radicación
     ========================================================================= -->
<Modal
  open={modalRadicar}
  title="Confirmar radicación"
  size="sm"
  on:close={() => (modalRadicar = false)}
>
  <p class="modal-confirm__text">
    ¿Confirma la radicación de este borrador? Esta acción generará un radicado real con número de
    control inmutable y no puede deshacerse.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalRadicar = false)}>Cancelar</Button>
    <Button icon="fas fa-paper-plane" loading={actionLoading} on:click={confirmarRadicar}>
      Sí, radicar
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: resultado de radicación exitosa
     ========================================================================= -->
<Modal
  open={modalRadicadoOpen}
  title="Radicado generado"
  size="sm"
  on:close={() => (modalRadicadoOpen = false)}
>
  <div class="modal-success">
    <i class="fas fa-check-circle modal-success__icon" aria-hidden="true"></i>
    <p class="modal-success__text">El borrador fue radicado exitosamente.</p>
    {#if trackingNumberResult}
      <p class="modal-success__tracking" aria-live="polite">
        Número de radicado:
        <strong>{trackingNumberResult}</strong>
      </p>
    {/if}
  </div>
  <svelte:fragment slot="footer">
    <Button on:click={() => (modalRadicadoOpen = false)}>Cerrar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .borradores-page {
    padding: var(--op-space-5);
    max-width: 1100px;

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-5);
    }

    &__title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__filters {
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-2);
    }

    &__filter-btn {
      padding: var(--op-space-1) var(--op-space-3);
      font-size: var(--op-font-sm);
      font-family: var(--op-font-family);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-full);
      background: var(--op-surface);
      color: var(--op-text-secondary);
      cursor: pointer;
      transition:
        background var(--op-transition),
        color var(--op-transition),
        border-color var(--op-transition);

      &:hover {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
        border-color: var(--op-primary-light);
      }

      &--active {
        background: var(--op-primary);
        color: var(--op-surface);
        border-color: var(--op-primary);

        &:hover {
          background: var(--op-primary-dark);
          border-color: var(--op-primary-dark);
        }
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
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

    &__loader {
      display: flex;
      justify-content: center;
      padding: var(--op-space-7);
    }
  }

  // ---------------------------------------------------------------------------
  // Tabla de borradores
  // ---------------------------------------------------------------------------
  .borradores-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
  }

  .borradores-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th,
    td {
      padding: var(--op-space-3) var(--op-space-4);
      text-align: left;
      border-bottom: 1px solid var(--op-border);
    }

    th {
      background: var(--op-bg);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    tbody tr:last-child td {
      border-bottom: none;
    }

    &__row:hover td {
      background: var(--op-primary-light);
    }

    &__subject {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      max-width: 280px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__date {
      color: var(--op-text-secondary);
      white-space: nowrap;
    }

    &__tracking {
      white-space: nowrap;
    }

    &__tracking-num {
      font-weight: var(--op-weight-medium);
      color: var(--op-primary-dark);
      font-size: var(--op-font-xs);
      background: var(--op-primary-light);
      padding: 2px var(--op-space-2);
      border-radius: var(--op-radius-sm);
    }

    &__tracking-none {
      color: var(--op-text-secondary);
    }

    &__actions {
      width: 80px;
    }
  }

  // ---------------------------------------------------------------------------
  // Formulario del drawer
  // ---------------------------------------------------------------------------
  .borrador-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__estado {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-bg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
    }

    &__estado-label {
      color: var(--op-text-secondary);
    }

    &__save-row {
      display: flex;
      justify-content: flex-end;
    }
  }

  // Acciones de flujo debajo del formulario
  .borrador-form__flow-actions {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding-top: var(--op-space-2);
  }

  .borrador-form__divider {
    border: none;
    border-top: 1px solid var(--op-border);
    margin: 0;
  }

  .borrador-form__flow-title {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    margin: 0;
  }

  .borrador-form__flow-done {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-success-fg);
    background: var(--op-success-bg);
    padding: var(--op-space-2) var(--op-space-3);
    border-radius: var(--op-radius);
    margin: 0;

    strong {
      font-weight: var(--op-weight-bold);
    }
  }

  // ---------------------------------------------------------------------------
  // Modales
  // ---------------------------------------------------------------------------
  .modal-confirm__text {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    line-height: var(--op-line-height);
    margin: 0;
  }

  .modal-success {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--op-space-3);
    text-align: center;
    padding: var(--op-space-3) 0;

    &__icon {
      font-size: var(--op-font-2xl);
      color: var(--op-success);
    }

    &__text {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      margin: 0;
    }

    &__tracking {
      font-size: var(--op-font-md);
      color: var(--op-text);
      background: var(--op-primary-light);
      padding: var(--op-space-2) var(--op-space-4);
      border-radius: var(--op-radius);
      margin: 0;

      strong {
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);
      }
    }
  }
</style>
