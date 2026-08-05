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
  /admin/metadatos — Plantillas de metadatos (expediente y documento) y
  elementos reutilizables (E03/ADR-007, Fase 5 cierre de desfase API↔UI).

  Reparto con /admin/trd (ver +page.server.js): TRD administra la
  clasificación (series/tipos), esta pantalla administra los CAMPOS de
  metadato de esos esquemas.

  Tres pestañas (patrón tablist/tab/tabpanel):
    - "Plantillas de expediente" (archive-service, por serie TRD)
    - "Plantillas de documento" (document-service, por tipo documental)
    - "Elementos de metadato" (document-service, catálogo reutilizable, CRUD completo)

  Las plantillas se editan como JSON Schema CRUDO (textarea): no existe un
  editor visual de JSON Schema en este incremento — SchemaField.svelte
  RELLENA un formulario a partir de un json_schema ya existente, no lo
  AUTORA. El textarea valida que el contenido parsee a un objeto JSON antes
  de enviarlo (honestidad de interfaz: un JSON roto se señala aquí, no como
  un 422 opaco del backend).

  Las plantillas son INMUTABLES una vez creadas (sin PATCH/DELETE en el
  backend): "editar" es crear una nueva versión con `version` incremental
  para la misma serie/tipo — no hay botón "Editar" en esas dos pestañas.

  RBAC: gate REAL confirmado (revisión UX 2026-08-02, punto 2, lote 1) —
  los TRES routers backend (`expediente-metadata/templates` en
  archive-service; `metadata/templates` y `metadata/elements` en
  document-service) exigen `USUA_PERM_TRD` en sus mutaciones (POST/PATCH/
  DELETE). El comentario previo aquí y en +page.server.js afirmaba que los
  dos routers de document-service no gateaban por permiso — era falso a la
  fecha de esta revisión (los tres YA declaran `require_permission
  ("USUA_PERM_TRD")`); esta pantalla se alinea con USUA_PERM_TRD en las tres
  pestañas en vez de USUA_PERM_ADMIN.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions, permissionLevels } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import CatalogCrudPanel from "$lib/components/features/admin/CatalogCrudPanel.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  // Gate REAL único para las tres pestañas: los tres routers backend exigen
  // USUA_PERM_TRD en sus mutaciones — ver comentario de cabecera. Crear
  // plantillas (ambos routers metadata.py) y crear/editar elemento
  // (metadata_elements.py create_element/update_element) son `min_crud=1`
  // (default): presencia basta.
  $: canWrite = $permissions.includes("USUA_PERM_TRD");
  $: canWriteExpTemplates = canWrite;
  // "Eliminar" elemento (`delete_element`, metadata_elements.py línea 154)
  // exige `require_permission("USUA_PERM_TRD", min_crud=3)` — antes solo
  // miraba presencia del permiso, igual que "Editar" (A6, revisión UX).
  $: canDeleteElement = ($permissionLevels["USUA_PERM_TRD"] ?? 0) >= 3;

  let expedienteTemplates = data.expedienteTemplates ?? [];
  let documentTemplates = data.documentTemplates ?? [];
  let elements = data.elements ?? [];
  const series = data.series ?? [];
  // Códigos de tipo documental ya definidos en /admin/trd — se ofrecen como
  // sugerencia (datalist) en el campo libre `tipo_documental` de la plantilla
  // de documento, sin forzar una FK que el backend no tiene (dos servicios
  // independientes, ADR-011).
  const tipoCodigos = (data.tipos ?? []).map((t) => t.code);

  $: serieOptions = series.map((s) => ({ value: s.id, label: `${s.code} — ${s.name}` }));
  function serieNombre(id) {
    return series.find((s) => s.id === id)?.name ?? null;
  }

  const TABS = [
    { id: "exp-templates", label: "Plantillas de expediente", icon: "fa-folder-open" },
    { id: "doc-templates", label: "Plantillas de documento", icon: "fa-file-lines" },
    { id: "elementos", label: "Elementos de metadato", icon: "fa-tags" },
  ];
  let activeTab = "exp-templates";

  function handleTabKeydown(e, tabId) {
    const idx = TABS.findIndex((t) => t.id === tabId);
    if (e.key === "ArrowRight") {
      e.preventDefault();
      activeTab = TABS[(idx + 1) % TABS.length].id;
      document.getElementById(`tab-${activeTab}`)?.focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      activeTab = TABS[(idx - 1 + TABS.length) % TABS.length].id;
      document.getElementById(`tab-${activeTab}`)?.focus();
    }
  }

  let actionLoading = false;

  function parseSchema(raw, setError) {
    if (!raw || !raw.trim()) {
      setError("El JSON Schema es obligatorio.");
      return undefined;
    }
    try {
      const parsed = JSON.parse(raw);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setError(
          'El JSON Schema debe ser un objeto (por ejemplo: { "type": "object", "properties": {...} }).',
        );
        return undefined;
      }
      return parsed;
    } catch (err) {
      setError(`JSON inválido: ${err instanceof Error ? err.message : "error de sintaxis"}`);
      return undefined;
    }
  }

  // ---------------------------------------------------------------------------
  // Plantillas de expediente (por serie TRD)
  // ---------------------------------------------------------------------------
  let expCreateOpen = false;
  let expSerieId = "";
  let expVersion = "1";
  let expSchemaRaw = '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}';
  let expActivo = true;
  let expSerieError = "";
  let expSchemaError = "";

  function openExpCreate() {
    expSerieId = "";
    expVersion = "1";
    expSchemaRaw = '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}';
    expActivo = true;
    expSerieError = "";
    expSchemaError = "";
    expCreateOpen = true;
  }

  async function crearExpTemplate() {
    expSerieError = "";
    expSchemaError = "";
    if (!expSerieId) {
      expSerieError = "Selecciona una serie TRD.";
      return;
    }
    const schema = parseSchema(expSchemaRaw, (msg) => (expSchemaError = msg));
    if (schema === undefined) return;

    actionLoading = true;
    try {
      const res = await fetch("/admin/metadatos/api/expediente-templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trd_serie_id: expSerieId,
          json_schema: schema,
          version: Number(expVersion) || 1,
          activo: expActivo,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo crear la plantilla de expediente.");
        return;
      }
      expedienteTemplates = [resData, ...expedienteTemplates];
      expCreateOpen = false;
      notifySuccess("Plantilla de expediente creada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Plantillas de documento (por tipo documental)
  // ---------------------------------------------------------------------------
  let docCreateOpen = false;
  let docTipo = "";
  let docVersion = "1";
  let docSchemaRaw = '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}';
  let docActivo = true;
  let docTipoError = "";
  let docSchemaError = "";

  function openDocCreate() {
    docTipo = "";
    docVersion = "1";
    docSchemaRaw = '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}';
    docActivo = true;
    docTipoError = "";
    docSchemaError = "";
    docCreateOpen = true;
  }

  async function crearDocTemplate() {
    docTipoError = "";
    docSchemaError = "";
    if (!docTipo.trim()) {
      docTipoError = "El tipo documental es obligatorio.";
      return;
    }
    const schema = parseSchema(docSchemaRaw, (msg) => (docSchemaError = msg));
    if (schema === undefined) return;

    actionLoading = true;
    try {
      const res = await fetch("/admin/metadatos/api/document-templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tipo_documental: docTipo.trim(),
          json_schema: schema,
          version: Number(docVersion) || 1,
          activo: docActivo,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo crear la plantilla de documento.");
        return;
      }
      documentTemplates = [resData, ...documentTemplates];
      docCreateOpen = false;
      notifySuccess("Plantilla de documento creada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Elementos de metadato (CRUD completo)
  // ---------------------------------------------------------------------------
  const TIPOS_DATO = [
    { value: "text", label: "Texto" },
    { value: "number", label: "Número" },
    { value: "date", label: "Fecha" },
    { value: "boolean", label: "Sí/No" },
    { value: "select", label: "Selección (opciones)" },
  ];

  let elCreateOpen = false;
  let elEditOpen = false;
  let elEditTarget = null;
  let elDeleteOpen = false;
  let elDeleteTarget = null;

  let elClave = "";
  let elEtiqueta = "";
  let elTipoDato = "text";
  let elSearchable = false;
  let elModificable = true;
  let elClaveError = "";
  let elEtiquetaError = "";

  function openElCreate() {
    elClave = "";
    elEtiqueta = "";
    elTipoDato = "text";
    elSearchable = false;
    elModificable = true;
    elClaveError = "";
    elEtiquetaError = "";
    elCreateOpen = true;
  }

  async function crearElemento() {
    elClaveError = "";
    elEtiquetaError = "";
    if (!elClave.trim()) {
      elClaveError = "La clave es obligatoria.";
      return;
    }
    if (!elEtiqueta.trim()) {
      elEtiquetaError = "La etiqueta es obligatoria.";
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch("/admin/metadatos/api/elements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clave: elClave.trim(),
          etiqueta: elEtiqueta.trim(),
          tipo_dato: elTipoDato,
          searchable: elSearchable,
          modificable: elModificable,
          activo: true,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo crear el elemento de metadato.");
        return;
      }
      elements = [resData, ...elements];
      elCreateOpen = false;
      notifySuccess("Elemento de metadato creado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  let eeEtiqueta = "";
  let eeTipoDato = "text";
  let eeSearchable = false;
  let eeModificable = true;
  let eeActivo = true;

  function openElEdit(el) {
    elEditTarget = el;
    eeEtiqueta = el.etiqueta ?? "";
    eeTipoDato = el.tipo_dato ?? "text";
    eeSearchable = !!el.searchable;
    eeModificable = el.modificable !== false;
    eeActivo = el.activo !== false;
    elEditOpen = true;
  }

  async function guardarElemento() {
    if (!elEditTarget) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/metadatos/api/elements/${elEditTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          etiqueta: eeEtiqueta.trim(),
          tipo_dato: eeTipoDato,
          searchable: eeSearchable,
          modificable: eeModificable,
          activo: eeActivo,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo actualizar el elemento de metadato.");
        return;
      }
      elements = elements.map((e) => (e.id === resData.id ? resData : e));
      elEditOpen = false;
      elEditTarget = null;
      notifySuccess("Elemento de metadato actualizado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  async function confirmarBorrarElemento(event) {
    const el = event.detail.item;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/metadatos/api/elements/${el.id}`, { method: "DELETE" });
      if (res.status === 204 || res.ok) {
        elements = elements.filter((e) => e.id !== el.id);
        notifySuccess("Elemento de metadato eliminado.");
        elDeleteOpen = false;
        elDeleteTarget = null;
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(err.detail ?? "No se pudo eliminar el elemento de metadato.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Metadatos" }]);
  });
</script>

<svelte:head>
  <title>Metadatos — OrpycaMCP</title>
</svelte:head>

<div class="meta-page">
  <header class="meta-page__header">
    <h1 class="meta-page__title">Metadatos</h1>
    <p class="meta-page__subtitle">
      Campos de metadato de expedientes y documentos (E03/ADR-007). La clasificación (series/tipos)
      se administra en <a href="/admin/trd">TRD / CCD</a>.
    </p>
  </header>

  <div class="meta-tabs" role="tablist" aria-label="Secciones de metadatos">
    {#each TABS as tab (tab.id)}
      <button
        id="tab-{tab.id}"
        class="meta-tabs__tab"
        class:meta-tabs__tab--active={activeTab === tab.id}
        role="tab"
        aria-selected={activeTab === tab.id}
        aria-controls="panel-{tab.id}"
        tabindex={activeTab === tab.id ? 0 : -1}
        on:click={() => (activeTab = tab.id)}
        on:keydown={(e) => handleTabKeydown(e, tab.id)}
      >
        <i class="fas {tab.icon}" aria-hidden="true"></i>
        {tab.label}
      </button>
    {/each}
  </div>

  <!-- ===================================================================
       Panel: Plantillas de expediente
       =================================================================== -->
  <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <div
    id="panel-exp-templates"
    role="tabpanel"
    aria-labelledby="tab-exp-templates"
    hidden={activeTab !== "exp-templates"}
    tabindex="0"
  >
    <div class="meta-page__panel-header">
      {#if canWriteExpTemplates}
        <Button icon="fas fa-plus" on:click={openExpCreate}>Nueva plantilla de expediente</Button>
      {/if}
    </div>
    {#if data.expedienteTemplatesError}
      <div class="meta-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {data.expedienteTemplatesError}
      </div>
    {:else if expedienteTemplates.length === 0}
      <EmptyState
        icon="fas fa-folder-open"
        title="Sin plantillas de expediente"
        description={canWriteExpTemplates
          ? 'Crea la primera con "Nueva plantilla de expediente".'
          : "No hay plantillas de expediente registradas. Se requiere USUA_PERM_TRD para crear una."}
      />
    {:else}
      <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
      <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
      <div
        class="meta-table-wrap"
        role="region"
        aria-label="Plantillas de metadatos de expediente"
        tabindex="0"
      >
        <table class="meta-table" aria-label="Plantillas de metadatos de expediente">
          <thead>
            <tr>
              <th scope="col">Serie TRD</th>
              <th scope="col">Versión</th>
              <th scope="col">Campos</th>
              <th scope="col">Estado</th>
              <th scope="col">Creada</th>
            </tr>
          </thead>
          <tbody>
            {#each expedienteTemplates as t (t.id)}
              <tr>
                <td>{serieNombre(t.trd_serie_id) ?? t.trd_serie_id}</td>
                <td>v{t.version}</td>
                <td>{Object.keys(t.json_schema?.properties ?? {}).length}</td>
                <td
                  ><StatusChip
                    status={t.activo ? "success" : "neutral"}
                    label={t.activo ? "Activa" : "Inactiva"}
                    dot
                  /></td
                >
                <td>{new Date(t.created_at).toLocaleDateString("es-CO")}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>

  <!-- ===================================================================
       Panel: Plantillas de documento
       =================================================================== -->
  <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <div
    id="panel-doc-templates"
    role="tabpanel"
    aria-labelledby="tab-doc-templates"
    hidden={activeTab !== "doc-templates"}
    tabindex="0"
  >
    <div class="meta-page__panel-header">
      {#if canWrite}
        <Button icon="fas fa-plus" on:click={openDocCreate}>Nueva plantilla de documento</Button>
      {/if}
    </div>
    {#if data.documentTemplatesError}
      <div class="meta-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {data.documentTemplatesError}
      </div>
    {:else if documentTemplates.length === 0}
      <EmptyState
        icon="fas fa-file-lines"
        title="Sin plantillas de documento"
        description="Crea la primera con «Nueva plantilla de documento»."
      />
    {:else}
      <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
      <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
      <div
        class="meta-table-wrap"
        role="region"
        aria-label="Plantillas de metadatos de documento"
        tabindex="0"
      >
        <table class="meta-table" aria-label="Plantillas de metadatos de documento">
          <thead>
            <tr>
              <th scope="col">Tipo documental</th>
              <th scope="col">Versión</th>
              <th scope="col">Campos</th>
              <th scope="col">Estado</th>
              <th scope="col">Creada</th>
            </tr>
          </thead>
          <tbody>
            {#each documentTemplates as t (t.id)}
              <tr>
                <td>{t.tipo_documental}</td>
                <td>v{t.version}</td>
                <td>{Object.keys(t.json_schema?.properties ?? {}).length}</td>
                <td
                  ><StatusChip
                    status={t.activo ? "success" : "neutral"}
                    label={t.activo ? "Activa" : "Inactiva"}
                    dot
                  /></td
                >
                <td>{new Date(t.created_at).toLocaleDateString("es-CO")}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>

  <!-- ===================================================================
       Panel: Elementos de metadato
       =================================================================== -->
  <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <div
    id="panel-elementos"
    role="tabpanel"
    aria-labelledby="tab-elementos"
    hidden={activeTab !== "elementos"}
    tabindex="0"
  >
    <CatalogCrudPanel
      ariaLabel="Elementos de metadato"
      items={elements}
      rowKey={(e) => e.id}
      loadError={data.elementsError}
      canCreate={canWrite}
      createLabel="Nuevo elemento"
      emptyIcon="fas fa-tags"
      emptyTitle="Sin elementos de metadato"
      emptyDescription="Crea el primero con «Nuevo elemento»."
      bind:createOpen={elCreateOpen}
      bind:editOpen={elEditOpen}
      editTarget={elEditTarget}
      bind:deleteOpen={elDeleteOpen}
      deleteTarget={elDeleteTarget}
      deleteConfirmText={(e) =>
        `¿Eliminar el elemento "${e.etiqueta}" (${e.clave})? Esta acción no puede deshacerse.`}
      deleteLoading={actionLoading}
      on:confirmDelete={confirmarBorrarElemento}
    >
      <svelte:fragment slot="thead">
        <th scope="col">Clave</th>
        <th scope="col">Etiqueta</th>
        <th scope="col">Tipo</th>
        <th scope="col">Buscable</th>
        <th scope="col">Estado</th>
      </svelte:fragment>

      <svelte:fragment slot="row" let:item>
        <td><code>{item.clave}</code></td>
        <td>{item.etiqueta}</td>
        <td>{TIPOS_DATO.find((t) => t.value === item.tipo_dato)?.label ?? item.tipo_dato}</td>
        <td>{item.searchable ? "Sí" : "No"}</td>
        <td
          ><StatusChip
            status={item.activo ? "success" : "neutral"}
            label={item.activo ? "Activo" : "Inactivo"}
            dot
          /></td
        >
      </svelte:fragment>

      <svelte:fragment slot="row-actions" let:item>
        {#if canWrite}
          <Button variant="ghost" size="sm" icon="fas fa-pen" on:click={() => openElEdit(item)}
            >Editar</Button
          >
        {/if}
        {#if canDeleteElement}
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-trash"
            on:click={() => {
              elDeleteTarget = item;
              elDeleteOpen = true;
            }}
          >
            Eliminar
          </Button>
        {/if}
      </svelte:fragment>

      <svelte:fragment slot="create-form">
        <form class="meta-form" on:submit|preventDefault={crearElemento} novalidate>
          <FormField
            id="el-clave"
            label="Clave"
            required
            bind:value={elClave}
            error={elClaveError}
            hint="Identificador técnico (usado en json_schema.properties)."
          />
          <FormField
            id="el-etiqueta"
            label="Etiqueta"
            required
            bind:value={elEtiqueta}
            error={elEtiquetaError}
          />
          <FormField
            id="el-tipo"
            label="Tipo de dato"
            type="select"
            bind:value={elTipoDato}
            options={TIPOS_DATO}
          />
          <label class="meta-form__check">
            <input type="checkbox" bind:checked={elSearchable} />
            Indexable en búsqueda
          </label>
          <label class="meta-form__check">
            <input type="checkbox" bind:checked={elModificable} />
            Modificable tras asignarse
          </label>
        </form>
      </svelte:fragment>
      <svelte:fragment slot="create-footer">
        <Button variant="ghost" on:click={() => (elCreateOpen = false)}>Cancelar</Button>
        <Button icon="fas fa-save" loading={actionLoading} on:click={crearElemento}
          >Crear elemento</Button
        >
      </svelte:fragment>

      <svelte:fragment slot="edit-form" let:item>
        <form class="meta-form" on:submit|preventDefault={guardarElemento} novalidate>
          <p class="meta-form__hint">Clave <code>{item.clave}</code> — inmutable.</p>
          <FormField id="el-edit-etiqueta" label="Etiqueta" required bind:value={eeEtiqueta} />
          <FormField
            id="el-edit-tipo"
            label="Tipo de dato"
            type="select"
            bind:value={eeTipoDato}
            options={TIPOS_DATO}
          />
          <label class="meta-form__check">
            <input type="checkbox" bind:checked={eeSearchable} />
            Indexable en búsqueda
          </label>
          <label class="meta-form__check">
            <input type="checkbox" bind:checked={eeModificable} />
            Modificable tras asignarse
          </label>
          <label class="meta-form__check">
            <input type="checkbox" bind:checked={eeActivo} />
            Activo
          </label>
        </form>
      </svelte:fragment>
      <svelte:fragment slot="edit-footer">
        <Button variant="ghost" on:click={() => (elEditOpen = false)}>Cancelar</Button>
        <Button icon="fas fa-save" loading={actionLoading} on:click={guardarElemento}
          >Guardar</Button
        >
      </svelte:fragment>
    </CatalogCrudPanel>
  </div>
</div>

<!-- =========================================================================
     Modal: nueva plantilla de expediente (por serie TRD)

     Fuera de CatalogCrudPanel: esta pestaña no tiene edición/borrado, solo
     alta (la plantilla es inmutable — nueva versión = nuevo registro).
     ========================================================================= -->
<Modal
  open={expCreateOpen}
  title="Nueva plantilla de expediente"
  size="lg"
  on:close={() => (expCreateOpen = false)}
>
  <form class="meta-form" on:submit|preventDefault={crearExpTemplate} novalidate>
    <FormField
      id="exp-serie"
      label="Serie TRD"
      type="select"
      required
      bind:value={expSerieId}
      options={serieOptions}
      placeholder="— seleccionar serie —"
      error={expSerieError}
    />
    <FormField id="exp-version" label="Versión" type="number" bind:value={expVersion} />
    <label class="meta-form__check">
      <input type="checkbox" bind:checked={expActivo} />
      Activa (aplica a los expedientes nuevos de esta serie)
    </label>
    <FormField
      id="exp-schema"
      label="JSON Schema de los metadatos"
      type="textarea"
      rows={10}
      required
      bind:value={expSchemaRaw}
      error={expSchemaError}
      hint={'Objeto JSON Schema (Draft 2020-12). Ejemplo de propiedad en "properties": { "asunto": { "type": "string", "title": "Asunto" } }'}
    />
  </form>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (expCreateOpen = false)}>Cancelar</Button>
    <Button icon="fas fa-save" loading={actionLoading} on:click={crearExpTemplate}
      >Crear plantilla</Button
    >
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: nueva plantilla de documento (por tipo documental)
     ========================================================================= -->
<Modal
  open={docCreateOpen}
  title="Nueva plantilla de documento"
  size="lg"
  on:close={() => (docCreateOpen = false)}
>
  <form class="meta-form" on:submit|preventDefault={crearDocTemplate} novalidate>
    <FormField
      id="doc-tipo"
      label="Tipo documental"
      required
      bind:value={docTipo}
      error={docTipoError}
      hint={tipoCodigos.length
        ? `Identificador libre. Códigos ya usados en TRD: ${tipoCodigos.join(", ")}.`
        : "Identificador libre (no hay FK con tipos_documentales.code de archive-service: servicios independientes, ADR-011)."}
    />
    <FormField id="doc-version" label="Versión" type="number" bind:value={docVersion} />
    <label class="meta-form__check">
      <input type="checkbox" bind:checked={docActivo} />
      Activa (aplica a los radicados nuevos de este tipo)
    </label>
    <FormField
      id="doc-schema"
      label="JSON Schema de los metadatos"
      type="textarea"
      rows={10}
      required
      bind:value={docSchemaRaw}
      error={docSchemaError}
      hint="Objeto JSON Schema (Draft 2020-12)."
    />
  </form>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (docCreateOpen = false)}>Cancelar</Button>
    <Button icon="fas fa-save" loading={actionLoading} on:click={crearDocTemplate}
      >Crear plantilla</Button
    >
  </svelte:fragment>
</Modal>

<style lang="scss">
  .meta-page {
    padding: var(--op-space-5);
    max-width: 1200px;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__title {
      font-size: var(--op-font-xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__subtitle {
      color: var(--op-text-secondary);
      margin: 0;

      a {
        color: var(--op-primary-dark);
      }
    }

    &__panel-header {
      display: flex;
      justify-content: flex-end;
      margin-bottom: var(--op-space-3);
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
    }
  }

  .meta-tabs {
    display: flex;
    background: var(--op-surface);
    border-bottom: 2px solid var(--op-border);
    overflow-x: auto;

    &__tab {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      margin-bottom: -2px;
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      cursor: pointer;
      white-space: nowrap;

      &:hover {
        color: var(--op-text);
        background: var(--op-bg);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: -2px;
      }

      &--active {
        color: var(--op-primary-dark);
        border-bottom-color: var(--op-primary);
        font-weight: var(--op-weight-bold);
      }
    }
  }

  .meta-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }
  }

  .meta-table {
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
  }

  .meta-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__hint {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__check {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
    }
  }
</style>
