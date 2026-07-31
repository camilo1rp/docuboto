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
  EditarMetadatosModal.svelte — edición de los metadatos del expediente
  (Fase 2 cierre desfase API↔UI).

  CONTRATO VERIFICADO (`ExpedienteUpdate`, archive-service
  `app/schemas/archive.py`, `extra="forbid"`): el PATCH del expediente SOLO
  acepta `name`, `description`, `trd_serie_id` — el diccionario `metadata`
  (los campos derivados del json_schema de la plantilla de la serie TRD, ver
  SchemaField/T-12) NO es un campo de este endpoint y por lo tanto NO se
  ofrece aquí para editar: la UI nunca ofrece lo que la API rechazaría. Los
  metadatos ya capturados al crear el expediente se muestran de solo lectura
  en la pestaña Resumen del workspace (`+page.svelte`, sin cambios).

  `status` fue retirado a propósito del schema del backend (comentario del
  router): el ciclo de vida transita EXCLUSIVAMENTE por
  POST /{id}/close y /{id}/transfer — este modal jamás lo toca.

  Props:
    open        boolean — controla la visibilidad
    expediente  { id, name, description, trd_serie_id } — valores actuales
    trdSeries   Array<{id, code, name}> — catálogo de series TRD activas
                (faceta 5 de +page.server.js; puede venir vacío si falló)

  Eventos:
    on:close   — modal cerrado (cancelado o tras guardar)
    on:saved   — PATCH aplicado con éxito
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  export let open = false;
  export let expediente = /** @type {any} */ (null);
  /** @type {Array<{id: string, code: string, name: string}>} */
  export let trdSeries = [];

  const dispatch = createEventDispatcher();

  let name = "";
  let description = "";
  let trdSerieId = "";
  let saving = false;
  let formError = "";
  let nameError = "";

  // Resincroniza el formulario cada vez que se abre con un expediente distinto
  // (o el mismo, tras cerrar) — nunca arrastra el estado de una edición anterior.
  let syncedFor = null;
  $: if (open && expediente && syncedFor !== expediente.id) {
    syncedFor = expediente.id;
    name = expediente.name ?? "";
    description = expediente.description ?? "";
    trdSerieId = expediente.trd_serie_id ?? "";
    formError = "";
    nameError = "";
  }
  $: if (!open) {
    syncedFor = null;
  }

  $: serieOptions = [
    { value: "", label: "— sin serie —" },
    ...trdSeries.map((s) => ({ value: s.id, label: `${s.code} — ${s.name}` })),
    // Si la serie actual del expediente no está en el catálogo activo (p. ej.
    // se desactivó después de asignarla), se conserva como opción para no
    // forzar un cambio no solicitado por el usuario al abrir el modal.
    ...(trdSerieId && !trdSeries.some((s) => s.id === trdSerieId)
      ? [{ value: trdSerieId, label: `${trdSerieId} (serie actual, no listada)` }]
      : []),
  ];

  function handleClose() {
    dispatch("close");
  }

  async function handleSave() {
    formError = "";
    nameError = "";
    if (!name.trim()) {
      nameError = "El nombre del expediente es obligatorio.";
      return;
    }
    if (name.length > 500) {
      nameError = "El nombre no puede superar 500 caracteres.";
      return;
    }

    // Solo se envían los campos que cambiaron — allowlist ya aplicada por el
    // proxy, aquí se evita mandar ruido innecesario.
    /** @type {Record<string, unknown>} */
    const payload = {};
    if (name.trim() !== (expediente.name ?? "")) payload.name = name.trim();
    if (description.trim() !== (expediente.description ?? ""))
      payload.description = description.trim();
    if (trdSerieId !== (expediente.trd_serie_id ?? "")) {
      payload.trd_serie_id = trdSerieId || null;
    }

    if (Object.keys(payload).length === 0) {
      formError = "No hay cambios para guardar.";
      return;
    }

    saving = true;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/metadata`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw data;
      notifySuccess("Metadatos del expediente actualizados.");
      dispatch("saved");
      handleClose();
    } catch (err) {
      formError = err?.detail ?? "No se pudieron guardar los cambios.";
      notifyError(formError);
    } finally {
      saving = false;
    }
  }
</script>

<Modal {open} title="Editar metadatos del expediente" size="md" on:close={handleClose}>
  {#if formError}
    <p class="metadatos-error" role="alert">{formError}</p>
  {/if}

  <!-- M12 (revisión UX): explica por qué los metadatos de la plantilla TRD
       no aparecen aquí, en vez de simplemente omitirlos sin decir nada. -->
  {#if expediente?.metadata && Object.keys(expediente.metadata).length}
    <p class="metadatos-hint">
      <i class="fas fa-info-circle" aria-hidden="true"></i>
      Los metadatos de la serie TRD (definidos al crear el expediente) son de solo lectura y no se editan
      desde aquí.
    </p>
  {/if}

  <form id="editar-metadatos-form" on:submit|preventDefault={handleSave}>
    <div class="metadatos-form">
      <FormField
        id="edit-name"
        label="Nombre"
        type="text"
        required
        bind:value={name}
        hint="Obligatorio, máximo 500 caracteres."
        error={nameError}
      />
      <FormField
        id="edit-description"
        label="Descripción (opcional)"
        type="textarea"
        bind:value={description}
        rows={3}
      />
      <FormField
        id="edit-trd"
        label="Serie TRD (opcional)"
        type="select"
        bind:value={trdSerieId}
        options={serieOptions}
      />
    </div>
  </form>

  <svelte:fragment slot="footer">
    <Button
      type="submit"
      form="editar-metadatos-form"
      variant="primary"
      size="sm"
      loading={saving}
      disabled={saving || !name.trim()}
    >
      <i class="fas fa-save" aria-hidden="true"></i>
      Guardar cambios
    </Button>
    <Button variant="ghost" size="sm" disabled={saving} on:click={handleClose}>Cancelar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .metadatos-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .metadatos-error {
    background: var(--op-error-bg);
    color: var(--op-error-fg);
    border: 1px solid var(--op-error);
    border-radius: var(--op-radius);
    padding: var(--op-space-3) var(--op-space-4);
    font-size: var(--op-font-sm);
    margin: 0 0 var(--op-space-4);
  }

  .metadatos-hint {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    background: var(--op-bg);
    border-radius: var(--op-radius-sm);
    padding: var(--op-space-2) var(--op-space-3);
    margin: 0 0 var(--op-space-4);

    i {
      margin-right: var(--op-space-1);
    }
  }
</style>
