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
  /bandeja — Bandeja unificada de radicados (T-08, RF-UI-03).

  Criterio de hecho: filtrar y abrir el detalle (drawer) NO provoca
  navegación a otra pantalla. Todo ocurre en esta vista.

  Filtros soportados por el backend (/api/v1/documents):
    - doc_type: E (Entrada) | S (Salida) | I (Interno)
    - status: registered | distributed | in_progress | closed

  Filtros NO disponibles en /documents pero sí en /search (T-11):
    - date_from, date_to, dest_dept_code, q (texto libre)
    → Implementar en T-11; aquí solo los que el backend soporta directamente.

  Acciones inline implementadas (T-08, sin T-08b):
    - Ver detalle en drawer (sin navegar)
    - PATCH /documents/{id} para actualizar estado (distributed / in_progress)
      → Solo si el usuario tiene permiso GESTIONAR_RADICADO o RADICAR_ENTRADA.
    - TODO T-08b: anular, devolver, responder, VoBo.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import {
    fetchDocuments,
    fetchDocumentById,
    updateDocument,
  } from "$lib/services/document.service.js";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Estado local de la bandeja
  // ---------------------------------------------------------------------------

  /** @type {import('$lib/services/document.service.js').DocumentResponse[]} */
  let documents = data.documents ?? [];
  let total = data.total ?? 0;
  let loading = false;

  // Filtros activos (inicializados desde los query params del server load)
  let filterDocType = data.initialFilters?.docType ?? "";
  let filterStatus = data.initialFilters?.status ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  const pageSize = 20;

  // Tokens de autenticación (del server load, para llamadas cliente)
  const token = data.token;
  const tenant = data.tenant;

  // ---------------------------------------------------------------------------
  // Drawer de detalle
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  /** @type {import('$lib/services/document.service.js').DocumentResponse|null} */
  let selectedDoc = null;
  let detailLoading = false;

  // ---------------------------------------------------------------------------
  // Acciones inline
  // ---------------------------------------------------------------------------
  let actionLoading = false;

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Bandeja de radicados" }]);
  });

  // ---------------------------------------------------------------------------
  // Helpers de presentación
  // ---------------------------------------------------------------------------
  const DOC_TYPE_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };
  const STATUS_LABELS = {
    registered: "Radicado",
    distributed: "Distribuido",
    in_progress: "En trámite",
    closed: "Cerrado",
  };
  const STATUS_CHIP_MAP = {
    registered: "info",
    distributed: "pending",
    in_progress: "active",
    closed: "closed",
  };

  /**
   * Formatea una fecha ISO 8601 como fecha legible en español.
   * @param {string} iso
   */
  function formatDate(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }

  // ---------------------------------------------------------------------------
  // Carga de datos
  // ---------------------------------------------------------------------------

  /**
   * Carga la página actual de radicados con los filtros activos.
   * Usada tanto en la carga inicial-cliente (si los datos SSR son parciales)
   * como en cada cambio de filtro/página.
   */
  async function loadDocuments() {
    loading = true;
    try {
      const result = await fetchDocuments({
        token,
        tenant,
        docType: filterDocType || undefined,
        status: filterStatus || undefined,
        page: currentPage,
        size: pageSize,
      });
      documents = result.documents;
      total = result.total;
    } catch (err) {
      notifyError(err.detail ?? "Error al cargar los radicados.");
    } finally {
      loading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Filtros
  // ---------------------------------------------------------------------------

  function applyFilters() {
    currentPage = 1;
    loadDocuments();
  }

  function clearFilters() {
    filterDocType = "";
    filterStatus = "";
    currentPage = 1;
    loadDocuments();
  }

  // ---------------------------------------------------------------------------
  // Paginación
  // ---------------------------------------------------------------------------

  $: totalPages = Math.ceil(total / pageSize);

  function goToPage(page) {
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    loadDocuments();
  }

  // ---------------------------------------------------------------------------
  // Drawer de detalle
  // ---------------------------------------------------------------------------

  /**
   * Abre el drawer con el detalle completo del radicado.
   * Si el item ya está en el listado usamos esos datos; luego cargamos el
   * detalle completo (con anexos, metadata) en background.
   * @param {import('$lib/services/document.service.js').DocumentResponse} doc
   */
  async function openDrawer(doc) {
    selectedDoc = doc;
    drawerOpen = true;

    // Cargar detalle completo (incluye anexos y metadata)
    detailLoading = true;
    try {
      selectedDoc = await fetchDocumentById(doc.id, { token, tenant });
    } catch (err) {
      notifyError(err.detail ?? "No se pudo cargar el detalle del radicado.");
    } finally {
      detailLoading = false;
    }
  }

  function closeDrawer() {
    drawerOpen = false;
    selectedDoc = null;
  }

  // ---------------------------------------------------------------------------
  // Acciones inline (T-08 scope — sin T-08b)
  // ---------------------------------------------------------------------------

  /**
   * Avanza el estado de un radicado (solo transiciones válidas).
   * Solo disponible si el usuario tiene permisos de operador.
   * El backend re-valida la autorización.
   * @param {string} docId
   * @param {'distributed'|'in_progress'} newStatus
   */
  async function advanceStatus(docId, newStatus) {
    if (actionLoading) return;
    actionLoading = true;
    try {
      const updated = await updateDocument(docId, { status: newStatus }, { token, tenant });
      // Actualizar en la lista local para evitar reload completo
      documents = documents.map((d) => (d.id === docId ? { ...d, status: updated.status } : d));
      // Actualizar en el drawer si está abierto
      if (selectedDoc?.id === docId) {
        selectedDoc = { ...selectedDoc, status: updated.status };
      }
      notifySuccess(`Estado actualizado a "${STATUS_LABELS[newStatus] ?? newStatus}".`);
    } catch (err) {
      notifyError(err.detail ?? "No se pudo actualizar el estado.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Autorización (la UI filtra; el backend re-valida)
  // ---------------------------------------------------------------------------
  $: canManage =
    $permissions.includes("GESTIONAR_RADICADO") || $permissions.includes("RADICAR_ENTRADA");
</script>

<svelte:head>
  <title>Bandeja de radicados — OrpycaMCP</title>
</svelte:head>

<div class="bandeja">
  <!-- -----------------------------------------------------------------------
    Encabezado
  ----------------------------------------------------------------------- -->
  <header class="bandeja__header">
    <h1 class="bandeja__title">Bandeja de radicados</h1>
    <span class="bandeja__total" aria-live="polite">
      {#if !loading}
        {total.toLocaleString("es-CO")} radicado{total !== 1 ? "s" : ""}
      {/if}
    </span>
  </header>

  <!-- -----------------------------------------------------------------------
    Filtros
  ----------------------------------------------------------------------- -->
  <section class="bandeja__filters" aria-label="Filtros de la bandeja">
    <form
      class="filters-form"
      on:submit|preventDefault={applyFilters}
      role="search"
      aria-label="Filtrar radicados"
    >
      <div class="filters-form__fields">
        <FormField
          label="Tipo"
          type="select"
          id="filter-type"
          bind:value={filterDocType}
          options={[
            { value: "", label: "Todos los tipos" },
            { value: "E", label: "Entrada" },
            { value: "S", label: "Salida" },
            { value: "I", label: "Interno" },
          ]}
        />

        <FormField
          label="Estado"
          type="select"
          id="filter-status"
          bind:value={filterStatus}
          options={[
            { value: "", label: "Todos los estados" },
            { value: "registered", label: "Radicado" },
            { value: "distributed", label: "Distribuido" },
            { value: "in_progress", label: "En trámite" },
            { value: "closed", label: "Cerrado" },
          ]}
        />
      </div>

      <div class="filters-form__actions">
        <Button type="submit" variant="primary" size="sm">
          <i class="fas fa-filter" aria-hidden="true"></i>
          Filtrar
        </Button>
        {#if filterDocType || filterStatus}
          <Button type="button" variant="ghost" size="sm" on:click={clearFilters}>
            <i class="fas fa-times" aria-hidden="true"></i>
            Limpiar
          </Button>
        {/if}
      </div>
    </form>

    <!-- Nota: filtros por fecha y dependencia disponibles en Búsqueda avanzada (T-11) -->
    <p class="filters-note">
      Para filtrar por fecha, dependencia o texto libre usa
      <a href="/busqueda" class="filters-note__link">Búsqueda avanzada</a>.
    </p>
  </section>

  <!-- -----------------------------------------------------------------------
    Error de carga inicial
  ----------------------------------------------------------------------- -->
  {#if data.loadError && documents.length === 0}
    <EmptyState
      icon="fas fa-exclamation-circle"
      title="Error al cargar la bandeja"
      description={data.loadError}
    >
      <Button variant="primary" on:click={loadDocuments}>Reintentar</Button>
    </EmptyState>
  {:else}
    <!-- =====================================================================
      Tabla de radicados
    ===================================================================== -->
    <section class="bandeja__table-wrapper" aria-label="Lista de radicados">
      {#if loading}
        <div class="bandeja__loading" aria-live="polite">
          <Loader type="skeleton" lines={5} label="Cargando radicados…" />
        </div>
      {:else if documents.length === 0}
        <EmptyState
          icon="fas fa-inbox"
          title="Sin radicados"
          description="No hay radicados que coincidan con los filtros aplicados."
        >
          {#if filterDocType || filterStatus}
            <Button variant="ghost" on:click={clearFilters}>Limpiar filtros</Button>
          {/if}
        </EmptyState>
      {:else}
        <table class="bandeja-table" aria-label="Radicados">
          <thead>
            <tr>
              <th scope="col" class="bandeja-table__th bandeja-table__th--tracking">Radicado</th>
              <th scope="col" class="bandeja-table__th">Tipo</th>
              <th scope="col" class="bandeja-table__th">Asunto</th>
              <th scope="col" class="bandeja-table__th bandeja-table__th--dep"
                >Dependencia destino</th
              >
              <th scope="col" class="bandeja-table__th">Fecha</th>
              <th scope="col" class="bandeja-table__th">Estado</th>
              <th scope="col" class="bandeja-table__th bandeja-table__th--actions">
                <span class="sr-only">Acciones</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {#each documents as doc (doc.id)}
              <tr
                class="bandeja-table__row"
                class:bandeja-table__row--selected={selectedDoc?.id === doc.id}
              >
                <td class="bandeja-table__td">
                  <span class="tracking-number">{doc.tracking_number}</span>
                </td>
                <td class="bandeja-table__td">
                  <span class="doc-type-badge doc-type-badge--{doc.doc_type.toLowerCase()}">
                    {DOC_TYPE_LABELS[doc.doc_type] ?? doc.doc_type}
                  </span>
                </td>
                <td class="bandeja-table__td bandeja-table__td--subject">
                  <span class="subject-text" title={doc.subject}>{doc.subject}</span>
                </td>
                <td class="bandeja-table__td bandeja-table__td--dep">
                  {doc.dest_dept ?? "—"}
                </td>
                <td class="bandeja-table__td bandeja-table__td--date">
                  <time datetime={doc.registered_at}>{formatDate(doc.registered_at)}</time>
                </td>
                <td class="bandeja-table__td">
                  <StatusChip
                    status={STATUS_CHIP_MAP[doc.status] ?? "neutral"}
                    label={STATUS_LABELS[doc.status] ?? doc.status}
                    dot={true}
                  />
                </td>
                <td class="bandeja-table__td bandeja-table__td--actions">
                  <button
                    class="action-btn action-btn--detail"
                    type="button"
                    aria-label="Ver detalle de radicado {doc.tracking_number}"
                    on:click={() => openDrawer(doc)}
                  >
                    <i class="fas fa-eye" aria-hidden="true"></i>
                    Detalle
                  </button>

                  <!-- Acción inline: avanzar estado (T-08 básico) -->
                  {#if canManage}
                    {#if doc.status === "registered"}
                      <button
                        class="action-btn action-btn--advance"
                        type="button"
                        aria-label="Distribuir radicado {doc.tracking_number}"
                        disabled={actionLoading}
                        on:click={() => advanceStatus(doc.id, "distributed")}
                      >
                        <i class="fas fa-paper-plane" aria-hidden="true"></i>
                        Distribuir
                      </button>
                    {:else if doc.status === "distributed"}
                      <button
                        class="action-btn action-btn--advance"
                        type="button"
                        aria-label="Iniciar trámite del radicado {doc.tracking_number}"
                        disabled={actionLoading}
                        on:click={() => advanceStatus(doc.id, "in_progress")}
                      >
                        <i class="fas fa-play" aria-hidden="true"></i>
                        Iniciar trámite
                      </button>
                    {/if}
                  {/if}
                  <!-- TODO T-08b: anular, devolver, responder, VoBo -->
                </td>
              </tr>
            {/each}
          </tbody>
        </table>

        <!-- ==============================================================
          Paginación
        ============================================================== -->
        {#if totalPages > 1}
          <nav class="bandeja-pagination" aria-label="Paginación de radicados">
            <button
              class="pagination-btn"
              type="button"
              aria-label="Página anterior"
              disabled={currentPage <= 1 || loading}
              on:click={() => goToPage(currentPage - 1)}
            >
              <i class="fas fa-chevron-left" aria-hidden="true"></i>
            </button>

            <span class="pagination-info" aria-live="polite" aria-atomic="true">
              Página {currentPage} de {totalPages}
            </span>

            <button
              class="pagination-btn"
              type="button"
              aria-label="Página siguiente"
              disabled={currentPage >= totalPages || loading}
              on:click={() => goToPage(currentPage + 1)}
            >
              <i class="fas fa-chevron-right" aria-hidden="true"></i>
            </button>
          </nav>
        {/if}
      {/if}
    </section>
  {/if}
</div>

<!-- =========================================================================
  Drawer de detalle del radicado
  No navega a otra pantalla: la URL no cambia, el foco queda en el drawer.
========================================================================= -->
<Drawer
  open={drawerOpen}
  title={selectedDoc ? selectedDoc.tracking_number : "Detalle del radicado"}
  side="right"
  width="480px"
  on:close={closeDrawer}
>
  {#if detailLoading}
    <Loader type="skeleton" lines={8} label="Cargando detalle…" />
  {:else if selectedDoc}
    <article class="doc-detail" aria-label="Detalle del radicado {selectedDoc.tracking_number}">
      <!-- Encabezado del detalle -->
      <header class="doc-detail__header">
        <h3 class="doc-detail__tracking">{selectedDoc.tracking_number}</h3>
        <StatusChip
          status={STATUS_CHIP_MAP[selectedDoc.status] ?? "neutral"}
          label={STATUS_LABELS[selectedDoc.status] ?? selectedDoc.status}
          dot={true}
        />
      </header>

      <!-- Asunto -->
      <section class="doc-detail__section" aria-labelledby="detail-subject-heading">
        <h4 id="detail-subject-heading" class="doc-detail__section-title">Asunto</h4>
        <p class="doc-detail__subject">{selectedDoc.subject}</p>
      </section>

      <!-- Metadatos principales -->
      <section class="doc-detail__section" aria-labelledby="detail-meta-heading">
        <h4 id="detail-meta-heading" class="doc-detail__section-title">Información</h4>
        <dl class="doc-detail__dl">
          <dt>Tipo</dt>
          <dd>{DOC_TYPE_LABELS[selectedDoc.doc_type] ?? selectedDoc.doc_type}</dd>

          <dt>Fecha de radicación</dt>
          <dd>
            <time datetime={selectedDoc.registered_at}>{formatDate(selectedDoc.registered_at)}</time
            >
          </dd>

          {#if selectedDoc.due_date}
            <dt>Fecha de vencimiento</dt>
            <dd>
              <time datetime={selectedDoc.due_date} class="due-date">{selectedDoc.due_date}</time>
            </dd>
          {/if}

          <dt>Dependencia origen</dt>
          <dd>{selectedDoc.origin_dept ?? "—"}</dd>

          <dt>Dependencia destino</dt>
          <dd>{selectedDoc.dest_dept ?? "—"}</dd>

          {#if selectedDoc.sender_name}
            <dt>Remitente</dt>
            <dd>{selectedDoc.sender_name}</dd>
          {/if}

          {#if selectedDoc.sender_entity}
            <dt>Entidad remitente</dt>
            <dd>{selectedDoc.sender_entity}</dd>
          {/if}

          <dt>Páginas</dt>
          <dd>{selectedDoc.pages}</dd>

          {#if selectedDoc.doc_class}
            <dt>Clase documental</dt>
            <dd>{selectedDoc.doc_class}</dd>
          {/if}
        </dl>
      </section>

      <!-- Observaciones -->
      {#if selectedDoc.observations}
        <section class="doc-detail__section" aria-labelledby="detail-obs-heading">
          <h4 id="detail-obs-heading" class="doc-detail__section-title">Observaciones</h4>
          <p class="doc-detail__obs">{selectedDoc.observations}</p>
        </section>
      {/if}

      <!-- Anexos -->
      {#if selectedDoc.anexos?.length > 0}
        <section class="doc-detail__section" aria-labelledby="detail-anexos-heading">
          <h4 id="detail-anexos-heading" class="doc-detail__section-title">
            Anexos ({selectedDoc.anexos.length})
          </h4>
          <ul class="anexos-list" role="list">
            {#each selectedDoc.anexos as anexo (anexo.id)}
              <li class="anexos-list__item">
                <i class="fas fa-paperclip anexos-list__icon" aria-hidden="true"></i>
                <span class="anexos-list__name" title={anexo.filename}>{anexo.filename}</span>
                {#if anexo.file_size}
                  <span class="anexos-list__size">
                    ({(anexo.file_size / 1024).toFixed(1)} KB)
                  </span>
                {/if}
              </li>
            {/each}
          </ul>
        </section>
      {/if}
    </article>
  {/if}

  <!-- Footer de acciones del drawer -->
  <svelte:fragment slot="footer">
    {#if selectedDoc && canManage}
      {#if selectedDoc.status === "registered"}
        <Button
          variant="primary"
          size="sm"
          disabled={actionLoading}
          on:click={() => advanceStatus(selectedDoc.id, "distributed")}
        >
          <i class="fas fa-paper-plane" aria-hidden="true"></i>
          Distribuir
        </Button>
      {:else if selectedDoc.status === "distributed"}
        <Button
          variant="primary"
          size="sm"
          disabled={actionLoading}
          on:click={() => advanceStatus(selectedDoc.id, "in_progress")}
        >
          <i class="fas fa-play" aria-hidden="true"></i>
          Iniciar trámite
        </Button>
      {/if}
      <!-- TODO T-08b: anular, devolver, responder, VoBo -->
    {/if}
    <Button variant="ghost" size="sm" on:click={closeDrawer}>Cerrar</Button>
  </svelte:fragment>
</Drawer>

<style lang="scss">
  // ---------------------------------------------------------------------------
  // Bandeja principal
  // ---------------------------------------------------------------------------

  .bandeja {
    padding: var(--op-space-6);
    max-width: 1200px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .bandeja__header {
    display: flex;
    align-items: baseline;
    gap: var(--op-space-4);
    flex-wrap: wrap;
    padding-bottom: var(--op-space-4);
    border-bottom: 1px solid var(--op-border);
  }

  .bandeja__title {
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
  }

  .bandeja__total {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    flex-shrink: 0;
  }

  // ---------------------------------------------------------------------------
  // Filtros
  // ---------------------------------------------------------------------------

  .bandeja__filters {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4) var(--op-space-5);
  }

  .filters-form {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-4);
    flex-wrap: wrap;
  }

  .filters-form__fields {
    display: flex;
    gap: var(--op-space-3);
    flex-wrap: wrap;
    flex: 1;
  }

  .filters-form__actions {
    display: flex;
    gap: var(--op-space-2);
    align-items: flex-end;
    padding-bottom: 0;
    // Alinear verticalmente con los controles de formulario
    margin-bottom: 2px;
  }

  .filters-note {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    margin: var(--op-space-2) 0 0;
  }

  .filters-note__link {
    color: var(--op-primary);
    text-decoration: underline;

    &:hover {
      color: var(--op-primary-dark);
    }
    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
      border-radius: var(--op-radius-sm);
    }
  }

  // ---------------------------------------------------------------------------
  // Carga
  // ---------------------------------------------------------------------------

  .bandeja__loading {
    padding: var(--op-space-6);
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
  }

  // ---------------------------------------------------------------------------
  // Tabla
  // ---------------------------------------------------------------------------

  .bandeja__table-wrapper {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    overflow: hidden;
  }

  .bandeja-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    &__th {
      padding: var(--op-space-3) var(--op-space-4);
      text-align: left;
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      color: var(--op-text-secondary);
      background: var(--op-bg);
      border-bottom: 1px solid var(--op-border);
      white-space: nowrap;

      &--tracking {
        width: 160px;
      }
      &--dep {
        width: 160px;
      }
      &--actions {
        width: 180px;
        text-align: right;
      }
    }

    &__row {
      transition: background var(--op-transition);

      &:hover {
        background: var(--op-bg);
      }
      &--selected {
        background: var(--op-primary-light);
      }

      &:not(:last-child) .bandeja-table__td {
        border-bottom: 1px solid var(--op-border);
      }
    }

    &__td {
      padding: var(--op-space-3) var(--op-space-4);
      vertical-align: middle;
      color: var(--op-text);

      &--subject {
        max-width: 280px;
      }
      &--dep {
        max-width: 160px;
      }
      &--date {
        white-space: nowrap;
        color: var(--op-text-secondary);
      }
      &--actions {
        text-align: right;
        white-space: nowrap;
      }
    }
  }

  // Número de radicado
  .tracking-number {
    font-family: monospace;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    background: var(--op-primary-light);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    white-space: nowrap;
  }

  // Badge de tipo de radicado
  .doc-type-badge {
    display: inline-block;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-bold);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    white-space: nowrap;

    &--e {
      background: var(--op-info-bg);
      color: var(--op-info-fg);
    }
    &--s {
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);
    }
    &--i {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      border: 1px solid var(--op-border);
    }
  }

  // Asunto truncado
  .subject-text {
    display: block;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  // Botones de acción inline
  .action-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-1);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    padding: 3px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    border: 1px solid transparent;
    cursor: pointer;
    transition:
      background var(--op-transition),
      color var(--op-transition);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }

    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    &--detail {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      border-color: var(--op-border);
      margin-right: var(--op-space-1);

      &:hover:not(:disabled) {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
        border-color: var(--op-primary-light);
      }
    }

    &--advance {
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      border-color: var(--op-primary-light);

      &:hover:not(:disabled) {
        background: var(--op-primary);
        color: var(--op-surface);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Paginación
  // ---------------------------------------------------------------------------

  .bandeja-pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--op-space-4);
    padding: var(--op-space-3) var(--op-space-5);
    border-top: 1px solid var(--op-border);
  }

  .pagination-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: var(--op-radius-sm);
    border: 1px solid var(--op-border);
    background: var(--op-surface);
    color: var(--op-text-secondary);
    cursor: pointer;
    transition: background var(--op-transition);

    &:hover:not(:disabled) {
      background: var(--op-bg);
      color: var(--op-text);
    }
    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
    &:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
  }

  .pagination-info {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
  }

  // ---------------------------------------------------------------------------
  // Detalle del drawer
  // ---------------------------------------------------------------------------

  .doc-detail {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .doc-detail__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--op-space-3);
    flex-wrap: wrap;
  }

  .doc-detail__tracking {
    font-family: monospace;
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-primary-dark);
    background: var(--op-primary-light);
    padding: var(--op-space-1) var(--op-space-3);
    border-radius: var(--op-radius-sm);
    margin: 0;
  }

  .doc-detail__section {
    border-top: 1px solid var(--op-border);
    padding-top: var(--op-space-3);
  }

  .doc-detail__section-title {
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-bold);
    color: var(--op-text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin: 0 0 var(--op-space-3);
  }

  .doc-detail__subject {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    margin: 0;
    line-height: var(--op-line-height);
  }

  .doc-detail__dl {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: var(--op-space-2) var(--op-space-3);
    font-size: var(--op-font-sm);
    margin: 0;

    dt {
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
    }

    dd {
      color: var(--op-text);
      margin: 0;
      overflow-wrap: break-word;
    }
  }

  .due-date {
    color: var(--op-warning-fg);
    font-weight: var(--op-weight-medium);
  }

  .doc-detail__obs {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin: 0;
    font-style: italic;
    line-height: var(--op-line-height);
  }

  .anexos-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
  }

  .anexos-list__item {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    padding: var(--op-space-2) var(--op-space-3);
    background: var(--op-bg);
    border-radius: var(--op-radius-sm);
    border: 1px solid var(--op-border);
  }

  .anexos-list__icon {
    color: var(--op-text-secondary);
    flex-shrink: 0;
    font-size: var(--op-font-xs);
  }

  .anexos-list__name {
    flex: 1;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    color: var(--op-text);
  }

  .anexos-list__size {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    flex-shrink: 0;
  }

  // ---------------------------------------------------------------------------
  // Responsive
  // ---------------------------------------------------------------------------

  @media (max-width: 768px) {
    .bandeja {
      padding: var(--op-space-4);
    }

    .bandeja-table {
      // Ocultar columnas menos críticas en pantallas pequeñas
      &__th--dep,
      &__td--dep {
        display: none;
      }
    }

    .filters-form {
      flex-direction: column;
      align-items: stretch;
    }

    .filters-form__actions {
      justify-content: flex-start;
    }
  }
</style>
