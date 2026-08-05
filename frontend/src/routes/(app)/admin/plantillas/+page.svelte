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
  /admin/plantillas — CRUD de plantillas de contenido reutilizables (T-12c).

  RBAC (espejo del backend, D-11):
    - Crear / borrar plantillas → USUA_PERM_ADMIN
      (`services/document-service/app/routers/borradores.py`, POST/DELETE
      `/api/v1/plantillas`).
    - Ver la lista → en realidad PERM_RADI a nivel de backend (mismo router,
      GET `/api/v1/plantillas`) — NO USUA_PERM_ADMIN como afirmaba este
      comentario (revisión UX 2026-08-02, hallazgo residual del punto 2,
      NO corregido: un USUA_PERM_ADMIN sin PERM_RADI ve la tarjeta en /admin
      pero la carga SSR de la lista falla con 403/loadError; un PERM_RADI sin
      USUA_PERM_ADMIN nunca ve la tarjeta, así que tampoco llega aquí. Fuera
      de alcance del lote 1 — requiere decidir el gate correcto en el
      backend, no solo reflejarlo).

  D-05 — access_token NUNCA en cliente:
    Todas las escrituras van a /admin/plantillas/api[/id] que leen locals.accessToken.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import RichEditor from "$lib/components/editor/RichEditor.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  /** @type {import('$lib/types/api.js').PlantillaResponse[]} */
  let plantillas = data.plantillas ?? [];
  let actionLoading = false;

  // ---------------------------------------------------------------------------
  // Modal de creación
  // ---------------------------------------------------------------------------
  let modalCreate = false;
  let fNombre = "";
  let fTipoDocumental = "";
  let fCuerpo = "";
  let fActivo = true;
  let fNombreError = "";
  let fCuerpoError = "";

  function openCreate() {
    fNombre = "";
    fTipoDocumental = "";
    fCuerpo = "";
    fActivo = true;
    fNombreError = "";
    fCuerpoError = "";
    modalCreate = true;
  }

  async function crearPlantilla() {
    fNombreError = "";
    fCuerpoError = "";

    if (!fNombre.trim()) {
      fNombreError = "El nombre es obligatorio.";
      return;
    }
    if (!fCuerpo.trim()) {
      fCuerpoError = "El contenido no puede estar vacío.";
      return;
    }

    actionLoading = true;
    try {
      const res = await fetch("/admin/plantillas/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nombre: fNombre.trim(),
          tipo_documental: fTipoDocumental.trim() || null,
          cuerpo: fCuerpo,
          activo: fActivo,
        }),
      });

      const dataRes = await res.json();
      if (!res.ok) {
        notifyError(dataRes.detail ?? "Error al crear la plantilla.");
        return;
      }

      plantillas = [dataRes, ...plantillas];
      modalCreate = false;
      notifySuccess("Plantilla creada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Confirmación de borrado
  // ---------------------------------------------------------------------------
  /** @type {import('$lib/types/api.js').PlantillaResponse|null} */
  let deletingPlantilla = null;
  let modalDelete = false;

  function confirmDelete(plantilla) {
    deletingPlantilla = plantilla;
    modalDelete = true;
  }

  async function deletePlantilla() {
    if (!deletingPlantilla) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/plantillas/api/${deletingPlantilla.id}`, {
        method: "DELETE",
      });

      if (res.status === 204 || res.ok) {
        plantillas = plantillas.filter((p) => p.id !== deletingPlantilla.id);
        notifySuccess("Plantilla eliminada.");
        modalDelete = false;
        deletingPlantilla = null;
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(err.detail ?? "No se pudo eliminar la plantilla.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
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

  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Plantillas" }]);
  });
</script>

<svelte:head>
  <title>Plantillas de contenido — OrpycaMCP</title>
</svelte:head>

<div class="plantillas-page">
  <!-- Cabecera -->
  <div class="plantillas-page__header">
    <h1 class="plantillas-page__title">Plantillas de contenido</h1>
    {#if isAdmin}
      <Button icon="fas fa-plus" on:click={openCreate}>Nueva plantilla</Button>
    {/if}
  </div>

  <!-- Error de carga -->
  {#if data.loadError}
    <div class="plantillas-page__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {data.loadError}
    </div>
  {/if}

  <!-- Lista vacía -->
  {#if plantillas.length === 0 && !data.loadError}
    <EmptyState
      icon="fa-copy"
      title="Sin plantillas"
      description="Crea la primera plantilla de contenido con el botón «Nueva plantilla»."
    />
  {:else if plantillas.length > 0}
    <!-- Tabla -->
    <div class="plantillas-table-wrap" role="region" aria-label="Lista de plantillas">
      <table class="plantillas-table" aria-label="Plantillas de contenido">
        <thead>
          <tr>
            <th scope="col">Nombre</th>
            <th scope="col">Tipo documental</th>
            <th scope="col">Estado</th>
            <th scope="col">Creada</th>
            {#if isAdmin}
              <th scope="col"><span class="sr-only">Acciones</span></th>
            {/if}
          </tr>
        </thead>
        <tbody>
          {#each plantillas as plantilla (plantilla.id)}
            <tr class="plantillas-table__row">
              <td class="plantillas-table__nombre">{plantilla.nombre}</td>
              <td>{plantilla.tipo_documental ?? "—"}</td>
              <td>
                <StatusChip
                  status={plantilla.activo ? "success" : "neutral"}
                  label={plantilla.activo ? "Activa" : "Inactiva"}
                  dot
                />
              </td>
              <td class="plantillas-table__date">{formatDate(plantilla.created_at)}</td>
              {#if isAdmin}
                <td class="plantillas-table__actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="fas fa-trash"
                    aria-label="Eliminar plantilla {plantilla.nombre}"
                    on:click={() => confirmDelete(plantilla)}
                  >
                    Eliminar
                  </Button>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<!-- =========================================================================
     Modal: crear plantilla
     ========================================================================= -->
<Modal open={modalCreate} title="Nueva plantilla" size="lg" on:close={() => (modalCreate = false)}>
  <form
    class="plantilla-form"
    on:submit|preventDefault={crearPlantilla}
    aria-label="Formulario de nueva plantilla"
    novalidate
  >
    <FormField
      id="plantilla-nombre"
      label="Nombre"
      type="text"
      bind:value={fNombre}
      placeholder="Identificador de la plantilla"
      required
      error={fNombreError}
    />

    <FormField
      id="plantilla-tipo"
      label="Tipo documental"
      type="text"
      bind:value={fTipoDocumental}
      placeholder="Ej. Circular, Oficio, Resolución (opcional)"
    />

    <div class="plantilla-form__check-row">
      <label class="plantilla-form__check-label" for="plantilla-activo">
        <input
          id="plantilla-activo"
          type="checkbox"
          class="plantilla-form__check"
          bind:checked={fActivo}
        />
        Activa (visible en el selector de borradores)
      </label>
    </div>

    <div class="plantilla-form__editor">
      <!--
        MEDIO #3: El id del mensaje de error se pasa a RichEditor vía ariaDescribedby
        para que el contenteditable de TipTap quede asociado al mensaje cuando hay error.
      -->
      <RichEditor
        id="plantilla-cuerpo"
        label="Contenido de la plantilla"
        bind:value={fCuerpo}
        placeholder="Escriba el contenido reutilizable de la plantilla…"
        ariaDescribedby={fCuerpoError ? "plantilla-cuerpo-error" : undefined}
      />
      <p
        id="plantilla-cuerpo-error"
        class="plantilla-form__editor-error"
        role="alert"
        aria-live="polite"
        aria-atomic="true"
      >
        {fCuerpoError || ""}
      </p>
    </div>
  </form>

  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalCreate = false)}>Cancelar</Button>
    <Button icon="fas fa-save" loading={actionLoading} on:click={crearPlantilla}>
      Crear plantilla
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: confirmar borrado
     ========================================================================= -->
<Modal
  open={modalDelete}
  title="Eliminar plantilla"
  size="sm"
  on:close={() => (modalDelete = false)}
>
  <p class="modal-confirm__text">
    ¿Está seguro de que desea eliminar la plantilla
    <strong>{deletingPlantilla?.nombre}</strong>? Esta acción no puede deshacerse.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalDelete = false)}>Cancelar</Button>
    <Button variant="danger" icon="fas fa-trash" loading={actionLoading} on:click={deletePlantilla}>
      Eliminar
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .plantillas-page {
    padding: var(--op-space-5);
    max-width: 1000px;

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-5);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
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

  // ---------------------------------------------------------------------------
  // Tabla
  // ---------------------------------------------------------------------------
  .plantillas-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
  }

  .plantillas-table {
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

    &__nombre {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__date {
      color: var(--op-text-secondary);
      white-space: nowrap;
    }

    &__actions {
      width: 100px;
    }
  }

  // ---------------------------------------------------------------------------
  // Formulario de plantilla
  // ---------------------------------------------------------------------------
  .plantilla-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__check-row {
      display: flex;
      align-items: center;
    }

    &__check-label {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
      user-select: none;
    }

    &__check {
      width: 16px;
      height: 16px;
      accent-color: var(--op-primary);
      cursor: pointer;
    }

    &__editor {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__editor-error {
      font-size: var(--op-font-xs);
      color: var(--op-error);
      margin: 0;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal de confirmación
  // ---------------------------------------------------------------------------
  .modal-confirm__text {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    line-height: var(--op-line-height);
    margin: 0;
  }
</style>
