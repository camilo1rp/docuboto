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
  CatalogCrudPanel.svelte — Patrón compartido de "pantalla CRUD de catálogo"
  (Fase 5, cierre de desfase API↔UI).

  Extraído de admin/plantillas (T-12c) y generalizado para las 10 subpáginas
  de administración planificadas (usuarios, grupos, dependencias, TRD/CCD,
  metadatos, catálogos, parámetros, …). Resuelve SIEMPRE: tabla con estados
  de carga/error/vacío explícitos, botón "Nuevo" gateado por permiso, modal de
  creación/edición con slots para los campos propios de cada entidad, y modal
  de confirmación de borrado con mensaje de riesgo personalizable.

  ## Por qué existe

  Diez pantallas casi idénticas (listar + crear/editar en Modal + borrar con
  confirmación + estados por faceta + gate de permiso) son deuda garantizada
  si se copian una a una. Este componente NO decide de dónde vienen los datos
  (cada página sigue teniendo su propio `+page.server.js`/proxy `api/+server.js`
  con el allowlist campo a campo, D-05) — solo gobierna el ESQUELETO visual e
  interactivo. La lógica de negocio (payloads, validación, llamadas fetch)
  vive en la página que lo consume.

  ## Uso mínimo

  ```svelte
  <script>
    import CatalogCrudPanel from "$lib/components/features/admin/CatalogCrudPanel.svelte";
    import FormField from "$lib/components/ui/FormField.svelte";
    import Button from "$lib/components/ui/Button.svelte";

    let items = data.items;
    let createOpen = false;
    let editOpen = false;
    let deleteOpen = false;
    let deleteTarget = null;
    let actionLoading = false;
    let fNombre = "";

    async function crear() { ... items = [nuevo, ...items]; createOpen = false; }
    async function confirmarBorrado() { ... items = items.filter(...); deleteOpen = false; }
  </script>

  <CatalogCrudPanel
    title="Grupos"
    ariaLabel="Lista de grupos"
    icon="fas fa-people-group"
    {items}
    rowKey={(g) => g.id}
    loading={false}
    loadError={data.loadError}
    canCreate={$permissions.includes('USUA_PERM_ADMIN')}
    createLabel="Nuevo grupo"
    emptyIcon="fas fa-people-group"
    emptyTitle="Sin grupos"
    emptyDescription='Crea el primero con "Nuevo grupo".'
    bind:createOpen
    bind:editOpen
    bind:deleteOpen
    {deleteTarget}
    deleteConfirmText={(g) => `¿Eliminar el grupo "${g.name}"? Esta acción no puede deshacerse.`}
    deleteLoading={actionLoading}
    on:create={openCreate}
    on:confirmDelete={confirmarBorrado}
  >
    <svelte:fragment slot="thead">
      <th scope="col">Nombre</th>
      <th scope="col">Descripción</th>
    </svelte:fragment>

    <svelte:fragment slot="row" let:item>
      <td>{item.name}</td>
      <td>{item.description ?? "—"}</td>
    </svelte:fragment>

    <svelte:fragment slot="row-actions" let:item>
      <Button variant="ghost" size="sm" icon="fas fa-pen" on:click={() => openEdit(item)}>Editar</Button>
      <Button variant="ghost" size="sm" icon="fas fa-trash" on:click={() => { deleteTarget = item; deleteOpen = true; }}>Eliminar</Button>
    </svelte:fragment>

    <svelte:fragment slot="create-form">
      <FormField id="grupo-nombre" label="Nombre" required bind:value={fNombre} />
    </svelte:fragment>
    <svelte:fragment slot="create-footer">
      <Button variant="ghost" on:click={() => (createOpen = false)}>Cancelar</Button>
      <Button icon="fas fa-save" loading={actionLoading} on:click={crear}>Crear</Button>
    </svelte:fragment>

    <svelte:fragment slot="edit-form" let:item>...</svelte:fragment>
    <svelte:fragment slot="edit-footer" let:item>...</svelte:fragment>
  </CatalogCrudPanel>
  ```

  ## Props

    title              string   — título de cabecera (h1)
    ariaLabel          string   — aria-label de la tabla (obligatorio, a11y)
    items              Array    — filas a listar
    rowKey             (item) => string|number — clave estable (default item.id)
    loading            boolean  — muestra Loader tipo skeleton en vez de tabla
    loadError          string|null — banner de error explícito (nunca confundir
                       con "0 resultados": si loadError está seteado, la tabla
                       NO se renderiza aunque items esté vacío)
    canCreate          boolean  — gate de permiso REAL (resuelto por la página
                       contra $permissions; este componente NO decide RBAC)
    createLabel        string   (default "Nuevo")
    emptyIcon/emptyTitle/emptyDescription — pasan a <EmptyState>
    createOpen         boolean, bindable — abre/cierra el modal de creación
    editOpen           boolean, bindable — abre/cierra el modal de edición
    editTarget         any, bindable    — fila en edición (para el slot edit-form)
    deleteOpen         boolean, bindable
    deleteTarget       any      — fila a borrar (la página la fija antes de abrir)
    deleteConfirmText  (item) => string — mensaje de la confirmación
    deleteLoading      boolean  — spinner en el botón "Eliminar" del modal

  ## Slots

    thead          — <th> del encabezado de tabla (sin slot props)
    row            let:item   — <td> de cada fila
    row-actions    let:item   — celda final de acciones por fila (opcional; si
                   no se usa, no se añade columna de acciones)
    toolbar-extra  — contenido adicional junto al botón "Nuevo" (filtros, etc.)
    create-form    — campos del formulario de creación (dentro del Modal)
    create-footer  — botones del pie del modal de creación
    edit-form      let:item   — campos del formulario de edición
    edit-footer    let:item   — botones del pie del modal de edición (solo se
                   renderiza si editOpen=true Y editTarget no es null)

  ## Eventos

    on:create        — el usuario pulsó "Nuevo". La página DEBE escucharlo para
                       limpiar su formulario (campos y errores de validación)
                       de forma SÍNCRONA: se emite en el mismo tick en que se
                       abre el modal. Abrir solo por `createOpen`, sin resetear,
                       reabre el modal con lo escrito la vez anterior.
    on:cancelDelete  — se cerró la confirmación de borrado sin confirmar
    on:confirmDelete — {item} — la página ejecuta el borrado

  ## Qué NO hace (a propósito)

    - No llama a fetch ni conoce proxies/gateway — cada página trae `items`
      ya cargados (SSR) y sus propios manejadores de submit/delete.
    - No decide permisos — recibe `canCreate` ya resuelto.
    - No valida campos — la página valida antes de cerrar el modal de creación.
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";

  export let title = "";
  export let ariaLabel;

  /** @type {Array<Record<string, any>>} */
  export let items = [];

  /** @type {(item: Record<string, any>) => string|number} */
  export let rowKey = (item) => item.id;

  export let loading = false;
  /** @type {string|null} */
  export let loadError = null;

  export let canCreate = false;
  export let createLabel = "Nuevo";

  export let emptyIcon = "fas fa-list";
  export let emptyTitle = "Sin registros";
  export let emptyDescription = "";

  export let createOpen = false;
  export let editOpen = false;
  /** @type {Record<string, any>|null} */
  export let editTarget = null;

  export let deleteOpen = false;
  /** @type {Record<string, any>|null} */
  export let deleteTarget = null;
  /** @type {(item: Record<string, any>) => string} */
  export let deleteConfirmText = (item) =>
    `¿Eliminar este registro? Esta acción no puede deshacerse.`;
  export let deleteLoading = false;

  // Si la página nunca usa el slot row-actions, la columna extra no se
  // pinta — se detecta por presencia del slot (Svelte 4: $$slots).
  $: hasRowActions = Boolean($$slots["row-actions"]);
  $: hasToolbarExtra = Boolean($$slots["toolbar-extra"]);

  const dispatch = createEventDispatcher();

  // La página escucha `create` para limpiar su formulario. Sin el evento, el
  // modal reabre con lo escrito (y con el error de validación) de la vez
  // anterior — incluidas casillas peligrosas como "ROOT" en /admin/usuarios.
  // El reset del oyente debe ser SÍNCRONO: se despacha en el mismo tick que la
  // apertura, así que un reset diferido pintaría el modal sucio.
  function openCreate() {
    dispatch("create");
    createOpen = true;
  }
  function closeCreate() {
    createOpen = false;
  }
  function closeEdit() {
    editOpen = false;
  }
  function closeDelete() {
    deleteOpen = false;
    dispatch("cancelDelete");
  }
  function confirmDelete() {
    dispatch("confirmDelete", { item: deleteTarget });
  }
</script>

<div class="catalog-crud">
  <div class="catalog-crud__header">
    {#if title}<h1 class="catalog-crud__title">{title}</h1>{/if}
    <div class="catalog-crud__toolbar">
      {#if hasToolbarExtra}<slot name="toolbar-extra" />{/if}
      {#if canCreate}
        <Button icon="fas fa-plus" on:click={openCreate}>{createLabel}</Button>
      {/if}
    </div>
  </div>

  {#if loadError}
    <div class="catalog-crud__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {loadError}
    </div>
  {:else if loading}
    <Loader type="skeleton" lines={5} label="Cargando {ariaLabel}…" />
  {:else if items.length === 0}
    <EmptyState icon={emptyIcon} title={emptyTitle} description={emptyDescription} />
  {:else}
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con
         teclado (WCAG 2.1.1) — el linter de Svelte no reconoce esta excepción. -->
    <div class="catalog-crud__table-wrap" role="region" aria-label={ariaLabel} tabindex="0">
      <table class="catalog-crud__table" aria-label={ariaLabel}>
        <thead>
          <tr>
            <slot name="thead" />
            {#if hasRowActions}<th scope="col"><span class="sr-only">Acciones</span></th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each items as item (rowKey(item))}
            <tr class="catalog-crud__row">
              <slot name="row" {item} />
              {#if hasRowActions}
                <td>
                  <div class="catalog-crud__actions">
                    <slot name="row-actions" {item} />
                  </div>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<!-- Modal de creación -->
<Modal open={createOpen} title={createLabel} size="md" on:close={closeCreate}>
  <slot name="create-form" />
  <svelte:fragment slot="footer">
    <slot name="create-footer" />
  </svelte:fragment>
</Modal>

<!-- Modal de edición (solo si hay editTarget) -->
{#if editTarget}
  <Modal open={editOpen} title="Editar" size="md" on:close={closeEdit}>
    <slot name="edit-form" item={editTarget} />
    <svelte:fragment slot="footer">
      <slot name="edit-footer" item={editTarget} />
    </svelte:fragment>
  </Modal>
{/if}

<!-- Modal de confirmación de borrado -->
{#if deleteTarget}
  <Modal open={deleteOpen} title="Confirmar eliminación" size="sm" on:close={closeDelete}>
    <p class="catalog-crud__delete-text">{deleteConfirmText(deleteTarget)}</p>
    <svelte:fragment slot="footer">
      <Button variant="ghost" on:click={closeDelete}>Cancelar</Button>
      <Button variant="danger" icon="fas fa-trash" loading={deleteLoading} on:click={confirmDelete}>
        Eliminar
      </Button>
    </svelte:fragment>
  </Modal>
{/if}

<style lang="scss">
  .catalog-crud {
    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-5);
    }

    &__title {
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__toolbar {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
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

    &__delete-text {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      line-height: var(--op-line-height);
      margin: 0;
    }
  }

  .catalog-crud__table-wrap {
    overflow-x: auto;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }
  }

  .catalog-crud__table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    :global(th),
    :global(td) {
      padding: var(--op-space-3) var(--op-space-4);
      text-align: left;
      border-bottom: 1px solid var(--op-border);
    }

    thead th {
      background: var(--op-bg);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    tbody tr:last-child :global(td) {
      border-bottom: none;
    }
  }

  .catalog-crud__row:hover {
    :global(td) {
      background: var(--op-primary-light);
    }
  }

  .catalog-crud__actions {
    display: flex;
    gap: var(--op-space-2);
    white-space: nowrap;
  }
</style>
