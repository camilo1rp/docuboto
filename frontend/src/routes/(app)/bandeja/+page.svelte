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
      → Solo si el usuario tiene permiso PERM_RADI o PERM_RADI_ENTRADA.
    - TODO T-08b: anular, devolver, responder, VoBo.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  // Las llamadas al gateway se realizan a través de los endpoints locales /bandeja/api[/id]
  // que leen la cookie httpOnly en el servidor — el token nunca llega al cliente (D-05).
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
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

  /** @type {import('$lib/types/api.js').DocumentResponse[]} */
  let documents = data.documents ?? [];
  let total = data.total ?? 0;
  let loading = false;

  // Filtros activos (inicializados desde los query params del server load)
  let filterDocType = data.initialFilters?.docType ?? "";
  let filterStatus = data.initialFilters?.status ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  const pageSize = 20;

  // ---------------------------------------------------------------------------
  // Drawer de detalle
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  /** @type {import('$lib/types/api.js').DocumentResponse|null} */
  let selectedDoc = null;
  let detailLoading = false;

  // ---------------------------------------------------------------------------
  // Acciones inline
  // ---------------------------------------------------------------------------
  let actionLoading = false;

  // ---------------------------------------------------------------------------
  // T-08b: estado de modales de operador
  // ---------------------------------------------------------------------------
  let modalAnulacion = false;
  let modalDevolver = false;
  let modalResponder = false;
  let modalVoBo = false;

  // Anulacion — causal (req, ≤15 chars), motivo (opt, ≤2000)
  let anulCausal = "";
  let anulMotivo = "";
  let anulCausalError = "";

  // Devolver — to_dept (req, ≤100), causal (req, ≤15), comentario (opt)
  let devToDept = "";
  let devCausal = "";
  let devComentario = "";
  let devToDeptError = "";
  let devCausalError = "";

  // Responder — todos opcionales en RespuestaRapidaCreate
  let respSubject = "";
  let respDestDept = "";
  let respObservations = "";
  let respResponseDays = "";

  // VoBo — aprobar (req), comentario (opt)
  let voboAprobarStr = ""; // "true" | "false" | "" (vacío = sin seleccionar)
  let voboComentario = "";

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
   * Llama al endpoint local /bandeja/api que lee la cookie httpOnly en el servidor.
   * El token nunca alcanza el JS del cliente (D-05).
   */
  async function loadDocuments() {
    loading = true;
    try {
      const params = new URLSearchParams({
        page: String(currentPage),
        size: String(pageSize),
      });
      if (filterDocType) params.set("doc_type", filterDocType);
      if (filterStatus) params.set("status", filterStatus);

      const res = await fetch(`/bandeja/api?${params}`);
      const result = await res.json();
      if (!res.ok) {
        throw result;
      }
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
   * Llama al endpoint local /bandeja/api/[id] — el token permanece en el servidor.
   * @param {import('$lib/types/api.js').DocumentResponse} doc
   */
  async function openDrawer(doc) {
    selectedDoc = doc;
    drawerOpen = true;

    // Cargar detalle completo (incluye anexos y metadata)
    detailLoading = true;
    try {
      const res = await fetch(`/bandeja/api/${doc.id}`);
      const result = await res.json();
      if (!res.ok) throw result;
      selectedDoc = result;
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
  // T-08b: helpers para abrir modales (resetean estado del formulario)
  // ---------------------------------------------------------------------------

  /** Abre el modal de anulación para el radicado actualmente seleccionado. */
  function openModalAnulacion() {
    anulCausal = "";
    anulMotivo = "";
    anulCausalError = "";
    modalAnulacion = true;
  }

  /** Abre el modal de devolución para el radicado actualmente seleccionado. */
  function openModalDevolver() {
    devToDept = "";
    devCausal = "";
    devComentario = "";
    devToDeptError = "";
    devCausalError = "";
    modalDevolver = true;
  }

  /** Abre el modal de respuesta rápida (Salida vinculada). */
  function openModalResponder() {
    // Precargar asunto del Entrada como punto de partida
    respSubject = selectedDoc?.subject ? `RE: ${selectedDoc.subject}` : "";
    respDestDept = selectedDoc?.origin_dept ?? "";
    respObservations = "";
    respResponseDays = "";
    modalResponder = true;
  }

  /** Abre el modal de decisión de Visto Bueno. */
  function openModalVoBo() {
    voboAprobarStr = ""; // sin preselección — el operador elige explícitamente
    voboComentario = "";
    modalVoBo = true;
  }

  /**
   * Abre el modal de anulación desde la fila de la tabla sin abrir el drawer.
   * @param {import('$lib/types/api.js').DocumentResponse} doc
   */
  function openAnulacionFromRow(doc) {
    selectedDoc = doc; // target del modal
    openModalAnulacion();
  }

  // ---------------------------------------------------------------------------
  // T-08b: funciones de acción (llaman a los endpoints proxy SSR)
  // ---------------------------------------------------------------------------

  /**
   * Solicita la anulación del radicado seleccionado.
   * POST /bandeja/api/[id]/anulacion → /api/v1/documents/{id}/anulacion
   */
  async function requestAnulacion() {
    if (!selectedDoc || actionLoading) return;

    // Validación cliente
    anulCausalError = "";
    if (!anulCausal.trim()) {
      anulCausalError = "La causal es obligatoria.";
      return;
    }
    if (anulCausal.trim().length > 15) {
      anulCausalError = "Máximo 15 caracteres.";
      return;
    }

    actionLoading = true;
    try {
      const res = await fetch(`/bandeja/api/${selectedDoc.id}/anulacion`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          causal: anulCausal.trim(),
          ...(anulMotivo.trim() ? { motivo: anulMotivo.trim() } : {}),
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      modalAnulacion = false;
      notifySuccess("Solicitud de anulación registrada correctamente.");
      // Recargar lista para reflejar el cambio de estado
      await loadDocuments();
      if (drawerOpen) closeDrawer();
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo registrar la solicitud de anulación.");
    } finally {
      actionLoading = false;
    }
  }

  /**
   * Devuelve el radicado seleccionado en flujo.
   * POST /bandeja/api/[id]/devolver → /api/v1/workflows/{id}/devolver
   */
  async function devolver() {
    if (!selectedDoc || actionLoading) return;

    // Validación cliente
    devToDeptError = "";
    devCausalError = "";
    let valid = true;
    if (!devToDept.trim()) {
      devToDeptError = "La dependencia destino es obligatoria.";
      valid = false;
    } else if (devToDept.trim().length > 100) {
      devToDeptError = "Máximo 100 caracteres.";
      valid = false;
    }
    if (!devCausal.trim()) {
      devCausalError = "La causal es obligatoria.";
      valid = false;
    } else if (devCausal.trim().length > 15) {
      devCausalError = "Máximo 15 caracteres.";
      valid = false;
    }
    if (!valid) return;

    actionLoading = true;
    try {
      const res = await fetch(`/bandeja/api/${selectedDoc.id}/devolver`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to_dept: devToDept.trim(),
          causal: devCausal.trim(),
          ...(devComentario.trim() ? { comentario: devComentario.trim() } : {}),
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      modalDevolver = false;
      notifySuccess("Radicado devuelto correctamente.");
      await loadDocuments();
      if (drawerOpen) closeDrawer();
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo devolver el radicado.");
    } finally {
      actionLoading = false;
    }
  }

  /**
   * Crea un radicado de Salida vinculado al Entrada seleccionado.
   * POST /bandeja/api/[id]/respuesta → /api/v1/documents/{id}/respuesta
   */
  async function responder() {
    if (!selectedDoc || actionLoading) return;

    actionLoading = true;
    try {
      const days = parseInt(respResponseDays, 10);
      const res = await fetch(`/bandeja/api/${selectedDoc.id}/respuesta`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(respSubject.trim() ? { subject: respSubject.trim() } : {}),
          ...(respDestDept.trim() ? { dest_dept: respDestDept.trim() } : {}),
          ...(respObservations.trim() ? { observations: respObservations.trim() } : {}),
          ...(!isNaN(days) && days > 0 ? { response_days: days } : {}),
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      modalResponder = false;
      const newTracking = result.tracking_number ?? "";
      notifySuccess(
        newTracking
          ? `Respuesta radicada: ${newTracking}`
          : "Radicado de respuesta creado correctamente.",
      );
      await loadDocuments();
      if (drawerOpen) closeDrawer();
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo crear el radicado de respuesta.");
    } finally {
      actionLoading = false;
    }
  }

  /**
   * Registra la decisión del usuario sobre un Visto Bueno pendiente.
   * POST /bandeja/api/[id]/vobo → /api/v1/workflows/{id}/vistos-buenos/decidir
   *
   * Nota: la creación de una cadena de VoBo (POST .../vistos-buenos con revisores[])
   * requiere un selector de usuarios que aún no existe en el frontend.
   * TODO T-08c: implementar cuando esté disponible el componente UserPicker.
   */
  async function voboDecidir() {
    if (!selectedDoc || actionLoading) return;
    if (!voboAprobarStr) {
      notifyError("Seleccione una decisión antes de confirmar.");
      return;
    }

    actionLoading = true;
    try {
      const aprobar = voboAprobarStr === "true";
      const res = await fetch(`/bandeja/api/${selectedDoc.id}/vobo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          aprobar,
          ...(voboComentario.trim() ? { comentario: voboComentario.trim() } : {}),
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;

      modalVoBo = false;
      notifySuccess(aprobar ? "Visto bueno aprobado." : "Visto bueno rechazado.");
      await loadDocuments();
      if (drawerOpen) closeDrawer();
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo registrar la decisión del visto bueno.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Acciones inline (T-08 scope — sin T-08b)
  // ---------------------------------------------------------------------------

  /**
   * Avanza el estado de un radicado (solo transiciones válidas).
   * Solo disponible si el usuario tiene permisos de operador.
   * Llama al endpoint local /bandeja/api/[id] — el backend re-valida RBAC.
   * @param {string} docId
   * @param {'distributed'|'in_progress'} newStatus
   */
  async function advanceStatus(docId, newStatus) {
    if (actionLoading) return;
    actionLoading = true;
    try {
      const res = await fetch(`/bandeja/api/${docId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      const updated = await res.json();
      if (!res.ok) throw updated;
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
  // Autorización (la UI filtra; el backend re-valida — D-05, ADR-013/020)
  // Los permisos en $permissions vienen de /auth/me (layout.server.js).
  // D-07: 'GESTIONAR_RADICADO' y 'RADICAR_ENTRADA' no existen en el catálogo
  // RBAC real (migración 004/005/007). Mapeados a los permisos reales:
  //   PERM_RADI            — permiso base de radicación (acceso general a radicados)
  //   PERM_RADI_ENTRADA    — radicar documentos de entrada
  //   PERM_SOL_ANULAR      — solicitar anulación de radicado (migración 007)
  //   PERM_RESPUESTA_VINCULADA — crear respuesta vinculada Salida RE: (migración 007)
  //   PERM_RADI_SALIDA     — radicar documentos de salida (cubre crear respuesta)
  // El backend re-valida RBAC en cada endpoint; la UI no es fuente de verdad.
  // ---------------------------------------------------------------------------
  $: canManage = $permissions.includes("PERM_RADI") || $permissions.includes("PERM_RADI_ENTRADA");

  // T-08b: permisos para acciones de operador
  $: canAnular = $permissions.includes("PERM_SOL_ANULAR");
  $: canDevolver = $permissions.includes("PERM_RADI");
  $: canResponder =
    $permissions.includes("PERM_RESPUESTA_VINCULADA") || $permissions.includes("PERM_RADI_SALIDA");
  $: canVoBo = $permissions.includes("PERM_RADI");
  // Pre-computed para evitar condiciones largas en template (printWidth)
  $: canDevolverInDrawer =
    canDevolver && (selectedDoc?.status === "in_progress" || selectedDoc?.status === "distributed");
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
                  <!-- T-08b: Anular inline; otras acciones en el drawer de detalle -->
                  {#if canAnular && doc.status !== "closed" && doc.status !== "cancelled"}
                    <button
                      class="action-btn action-btn--danger"
                      type="button"
                      aria-label="Solicitar anulación del radicado {doc.tracking_number}"
                      disabled={actionLoading}
                      on:click={() => openAnulacionFromRow(doc)}
                    >
                      <i class="fas fa-ban" aria-hidden="true"></i>
                      Anular
                    </button>
                  {/if}
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
      <!-- T-08b: acciones de operador -->
      {#if canResponder && selectedDoc.doc_type === "E"}
        <Button
          variant="secondary"
          size="sm"
          disabled={actionLoading}
          on:click={openModalResponder}
        >
          <i class="fas fa-reply" aria-hidden="true"></i>
          Responder
        </Button>
      {/if}
      {#if canDevolverInDrawer}
        <Button variant="secondary" size="sm" disabled={actionLoading} on:click={openModalDevolver}>
          <i class="fas fa-undo" aria-hidden="true"></i>
          Devolver
        </Button>
      {/if}
      {#if canVoBo && selectedDoc.status === "in_progress"}
        <Button variant="secondary" size="sm" disabled={actionLoading} on:click={openModalVoBo}>
          <i class="fas fa-check-double" aria-hidden="true"></i>
          VoBo
        </Button>
      {/if}
      {#if canAnular && selectedDoc.status !== "closed" && selectedDoc.status !== "cancelled"}
        <Button variant="danger" size="sm" disabled={actionLoading} on:click={openModalAnulacion}>
          <i class="fas fa-ban" aria-hidden="true"></i>
          Anular
        </Button>
      {/if}
    {/if}
    <Button variant="ghost" size="sm" on:click={closeDrawer}>Cerrar</Button>
  </svelte:fragment>
</Drawer>

<!-- =========================================================================
  T-08b: Modales de acciones de operador
  Cada modal pide confirmación explícita antes de la acción (ADR-020 §3).
  Los endpoints proxy leen el token de la cookie httpOnly (nunca el cliente).
========================================================================= -->

<!-- Modal: Solicitar anulación -->
<Modal
  open={modalAnulacion}
  title="Solicitar anulación"
  size="sm"
  on:close={() => (modalAnulacion = false)}
>
  <p class="modal-desc">
    Solicita la anulación del radicado
    <strong>{selectedDoc?.tracking_number ?? ""}</strong>. La anulación requiere aprobación
    posterior por un supervisor.
  </p>
  <div class="modal-form">
    <FormField
      id="anul-causal"
      label="Causal"
      type="text"
      bind:value={anulCausal}
      required={true}
      placeholder="Ej. DUPLICADO"
      hint="Código de causal, máx. 15 caracteres."
      error={anulCausalError}
    />
    <FormField
      id="anul-motivo"
      label="Motivo (opcional)"
      type="textarea"
      bind:value={anulMotivo}
      placeholder="Descripción detallada de la razón de anulación…"
      rows={3}
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="danger" size="sm" loading={actionLoading} on:click={requestAnulacion}>
      Solicitar anulación
    </Button>
    <Button
      variant="ghost"
      size="sm"
      disabled={actionLoading}
      on:click={() => (modalAnulacion = false)}
    >
      Cancelar
    </Button>
  </svelte:fragment>
</Modal>

<!-- Modal: Devolver en flujo -->
<Modal
  open={modalDevolver}
  title="Devolver radicado"
  size="sm"
  on:close={() => (modalDevolver = false)}
>
  <p class="modal-desc">
    Devuelve el radicado
    <strong>{selectedDoc?.tracking_number ?? ""}</strong> a otra dependencia dentro del flujo.
  </p>
  <div class="modal-form">
    <FormField
      id="dev-to-dept"
      label="Dependencia destino"
      type="text"
      bind:value={devToDept}
      required={true}
      placeholder="Nombre o código de la dependencia"
      hint="Máx. 100 caracteres."
      error={devToDeptError}
    />
    <FormField
      id="dev-causal"
      label="Causal"
      type="text"
      bind:value={devCausal}
      required={true}
      placeholder="Ej. INCOMPLETO"
      hint="Código de causal, máx. 15 caracteres."
      error={devCausalError}
    />
    <FormField
      id="dev-comentario"
      label="Comentario (opcional)"
      type="textarea"
      bind:value={devComentario}
      placeholder="Información adicional para la dependencia destino…"
      rows={3}
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="primary" size="sm" loading={actionLoading} on:click={devolver}>
      <i class="fas fa-undo" aria-hidden="true"></i>
      Devolver
    </Button>
    <Button
      variant="ghost"
      size="sm"
      disabled={actionLoading}
      on:click={() => (modalDevolver = false)}
    >
      Cancelar
    </Button>
  </svelte:fragment>
</Modal>

<!-- Modal: Responder (crear Salida vinculada) -->
<Modal
  open={modalResponder}
  title="Responder radicado"
  size="md"
  on:close={() => (modalResponder = false)}
>
  <p class="modal-desc">
    Crea un radicado de Salida vinculado a
    <strong>{selectedDoc?.tracking_number ?? ""}</strong>. Todos los campos son opcionales; el
    backend genera el número de radicado.
  </p>
  <div class="modal-form">
    <FormField
      id="resp-subject"
      label="Asunto de la respuesta"
      type="text"
      bind:value={respSubject}
      placeholder="Asunto del radicado de salida"
    />
    <FormField
      id="resp-dest-dept"
      label="Dependencia destino"
      type="text"
      bind:value={respDestDept}
      placeholder="Dependencia que recibirá la respuesta"
    />
    <FormField
      id="resp-observations"
      label="Observaciones"
      type="textarea"
      bind:value={respObservations}
      placeholder="Observaciones para el radicado de respuesta…"
      rows={3}
    />
    <FormField
      id="resp-days"
      label="Días de respuesta"
      type="text"
      bind:value={respResponseDays}
      placeholder="Ej. 15"
      hint="Número de días hábiles de plazo para la respuesta."
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="primary" size="sm" loading={actionLoading} on:click={responder}>
      <i class="fas fa-reply" aria-hidden="true"></i>
      Crear respuesta
    </Button>
    <Button
      variant="ghost"
      size="sm"
      disabled={actionLoading}
      on:click={() => (modalResponder = false)}
    >
      Cancelar
    </Button>
  </svelte:fragment>
</Modal>

<!-- Modal: Decisión de Visto Bueno -->
<Modal open={modalVoBo} title="Visto bueno" size="sm" on:close={() => (modalVoBo = false)}>
  <p class="modal-desc">
    Registra tu decisión sobre el radicado
    <strong>{selectedDoc?.tracking_number ?? ""}</strong>.
  </p>
  <div class="modal-form">
    <FormField
      id="vobo-decision"
      label="Decisión"
      type="select"
      bind:value={voboAprobarStr}
      required={true}
      placeholder="Seleccione una decisión…"
      options={[
        { value: "true", label: "Aprobar" },
        { value: "false", label: "Rechazar" },
      ]}
    />
    <FormField
      id="vobo-comentario"
      label="Comentario (opcional)"
      type="textarea"
      bind:value={voboComentario}
      placeholder="Justificación de la decisión…"
      rows={3}
    />
  </div>
  <!--
    TODO T-08c: Crear cadena de VoBo (POST /workflows/{id}/vistos-buenos con revisores[])
    requiere un componente UserPicker que aún no existe en el frontend.
    Implementar cuando esté disponible.
  -->
  <svelte:fragment slot="footer">
    <Button
      variant="primary"
      size="sm"
      loading={actionLoading}
      disabled={actionLoading || !voboAprobarStr}
      on:click={voboDecidir}
    >
      <i class="fas fa-check-double" aria-hidden="true"></i>
      Confirmar decisión
    </Button>
    <Button variant="ghost" size="sm" disabled={actionLoading} on:click={() => (modalVoBo = false)}>
      Cancelar
    </Button>
  </svelte:fragment>
</Modal>

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

    &--danger {
      background: transparent;
      color: var(--op-error);
      border-color: var(--op-error);

      &:hover:not(:disabled) {
        background: var(--op-error);
        color: var(--op-surface);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Modales de operador (T-08b)
  // ---------------------------------------------------------------------------

  .modal-desc {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    margin: 0 0 var(--op-space-4);
    line-height: var(--op-line-height);

    strong {
      font-family: monospace;
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
    }
  }

  .modal-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
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
