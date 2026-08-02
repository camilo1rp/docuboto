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
  /envios — Bandeja de envíos postales + planilla de despacho (T-12e).

  Criterios de hecho:
    - Listar envíos transversales paginados, filtrar por estado.
    - Ver detalle (drawer) con historial de eventos del envío.
    - Crear nuevo envío: radicado_id + operador + destinatario + dirección.
    - Cambiar estado de un envío (registrado → en_transito → entregado…).
    - Registrar devolución con causal de lista fija (T-12e).
    - Seleccionar envíos (checkbox) y exportar planilla de despacho en CSV (cliente).

  RBAC (espejo del backend, D-14):
    - Ver lista, detalle → PERM_RADI_SALIDA
    - Crear, cambiar estado, registrar devolución → PERM_RADI_SALIDA
    El backend re-valida; la UI solo filtra visibilidad.

  D-05 — access_token NUNCA en cliente:
    Todas las llamadas van a /envios/api[/id/accion] que leen locals.accessToken
    en el servidor. El token nunca llega al JS del cliente.

  Planilla de despacho (CSV):
    Generada completamente en el cliente vía Blob/URL.createObjectURL.
    Sin llamada al servidor. Campos: N.°, Radicado, Operador, Guía, Estado,
    Destinatario, Dirección, Fecha.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Permisos (espejo del RBAC del backend, D-14)
  // ---------------------------------------------------------------------------
  $: hasPerm = $permissions.includes("PERM_RADI_SALIDA");

  // ---------------------------------------------------------------------------
  // Estado de la lista
  // ---------------------------------------------------------------------------

  /** @type {import('$lib/types/api.js').ShipmentResponse[]} */
  let shipments = data.shipments ?? [];
  let total = data.total ?? 0;
  let loading = false;
  let filterEstado = data.initialFilters?.estado ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  const pageSize = 20;

  // ---------------------------------------------------------------------------
  // Planilla de despacho (multi-select)
  // ---------------------------------------------------------------------------

  /** @type {Set<string>} */
  let selectedIds = new Set();

  $: selectedCount = selectedIds.size;
  $: selectedShipments = shipments.filter((s) => selectedIds.has(s.id));
  $: allPageSelected = shipments.length > 0 && shipments.every((s) => selectedIds.has(s.id));
  // Estado indeterminado del checkbox "seleccionar todos": algunos pero no todos.
  $: allIndeterminate = selectedCount > 0 && !allPageSelected;

  function toggleRow(id) {
    if (selectedIds.has(id)) {
      selectedIds.delete(id);
    } else {
      selectedIds.add(id);
    }
    selectedIds = selectedIds; // trigger reactivity
  }

  function toggleAll() {
    if (allPageSelected) {
      shipments.forEach((s) => selectedIds.delete(s.id));
    } else {
      shipments.forEach((s) => selectedIds.add(s.id));
    }
    selectedIds = selectedIds;
  }

  /**
   * Genera y descarga la planilla de despacho en CSV.
   * Generación completamente cliente-side vía Blob + URL.createObjectURL.
   * No requiere llamada al servidor (solo se usa data ya cargada en memoria).
   */
  function exportarCSV() {
    if (selectedShipments.length === 0) {
      notifyError("Seleccione al menos un envío para exportar.");
      return;
    }

    const HEADERS = [
      "N.°",
      "Radicado ID",
      "Operador",
      "Guía",
      "Estado",
      "Destinatario",
      "Dirección",
      "Fecha de registro",
    ];

    const escapeCell = (/** @type {string|null|undefined} */ v) => {
      let s = v ?? "";
      // Neutralizar prefijos de fórmula (=, +, -, @, tab, CR) antes de envolver
      // en comillas, para prevenir inyección DDE/CSV en Excel y LibreOffice.
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
      // Envolver en comillas dobles si contiene comas, comillas o saltos de línea.
      if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
      return s;
    };

    const rows = selectedShipments.map((s, i) => [
      String(i + 1),
      escapeCell(s.radicado_id),
      escapeCell(OPERADOR_LABELS[s.operador] ?? s.operador),
      escapeCell(s.guia),
      escapeCell(ESTADO_LABEL[s.estado] ?? s.estado),
      escapeCell(s.destinatario),
      escapeCell(s.direccion),
      escapeCell(formatDate(s.created_at)),
    ]);

    const csv =
      HEADERS.map(escapeCell).join(",") + "\r\n" + rows.map((r) => r.join(",")).join("\r\n");

    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `planilla-despacho-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);

    notifySuccess(`Planilla exportada (${selectedShipments.length} envíos).`);
  }

  // ---------------------------------------------------------------------------
  // Drawer de detalle
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  /** @type {import('$lib/types/api.js').ShipmentResponse|null} */
  let selectedShipment = null;
  let detailLoading = false;

  async function openDetail(shipment) {
    selectedShipment = shipment;
    drawerOpen = true;
    trackingResult = null;
    trackingError = "";
    // Refrescar el detalle para obtener eventos actualizados
    detailLoading = true;
    try {
      const res = await fetch(`/envios/api/${shipment.id}`);
      const data = await res.json();
      if (res.ok) selectedShipment = data;
    } catch {
      // silencioso; mostramos el dato de la lista
    } finally {
      detailLoading = false;
    }
  }

  function closeDrawer() {
    drawerOpen = false;
    selectedShipment = null;
  }

  // ---------------------------------------------------------------------------
  // Rastreo contra el operador postal (F5, Fase 8) — consulta PULL bajo demanda
  // ---------------------------------------------------------------------------
  let trackingLoading = false;
  /** @type {import('$lib/types/api.js').TrackingResponse|null} */
  let trackingResult = null;
  let trackingError = "";

  async function actualizarRastreo() {
    if (!selectedShipment) return;
    trackingLoading = true;
    trackingError = "";
    try {
      const res = await fetch(`/envios/api/${selectedShipment.id}/tracking`);
      const result = await res.json();
      if (!res.ok) {
        // Distinguir "no se pudo consultar" de "sin novedades": nunca se
        // pinta un resultado sintético cuando la consulta falló.
        trackingError = result?.detail ?? "No se pudo consultar el rastreo.";
        trackingResult = null;
        return;
      }
      trackingResult = result;
    } catch {
      trackingError = "No se pudo conectar al servidor de rastreo.";
      trackingResult = null;
    } finally {
      trackingLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal: cambiar estado
  // ---------------------------------------------------------------------------
  let modalEstado = false;
  let fEstado = "";
  let fEstadoDescripcion = "";
  let fEstadoError = "";
  let actionLoading = false;

  function abrirModalEstado() {
    if (!selectedShipment) return;
    fEstado = selectedShipment.estado;
    fEstadoDescripcion = "";
    fEstadoError = "";
    // IMPORTANTE #3: cerrar el drawer antes de abrir el modal para evitar dos
    // trampas de foco y que Escape cierre ambos diálogos a la vez.
    // selectedShipment se mantiene en memoria para usarlo en cambiarEstado().
    drawerOpen = false;
    modalEstado = true;
  }

  async function cambiarEstado() {
    if (!selectedShipment) return;
    fEstadoError = "";
    if (!fEstado) {
      fEstadoError = "Seleccione el nuevo estado.";
      return;
    }

    actionLoading = true;
    try {
      const res = await fetch(`/envios/api/${selectedShipment.id}/estado`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ estado: fEstado, descripcion: fEstadoDescripcion.trim() || null }),
      });
      const responseData = await res.json();
      if (!res.ok) {
        notifyError(responseData.detail ?? "No se pudo cambiar el estado.");
        return;
      }
      // Actualizar en la lista y el drawer
      shipments = shipments.map((s) => (s.id === selectedShipment.id ? responseData : s));
      selectedShipment = responseData;
      modalEstado = false;
      notifySuccess("Estado actualizado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal: devolución con causal
  // ---------------------------------------------------------------------------
  const CAUSALES_DEVOLUCION = [
    "Dirección incorrecta",
    "Destinatario desconocido",
    "Rehusado",
    "No reclamado",
    "Otro",
  ];
  // Nota: la causal estructurada con catálogo propio es roadmap del backend.
  // Por ahora la causal se envía en el campo `descripcion` con prefijo "Causal: <x> — <nota>".

  let modalDevolucion = false;
  let fDevCausal = "";
  let fDevNota = "";
  let fDevCausalError = "";

  function abrirModalDevolucion() {
    fDevCausal = "";
    fDevNota = "";
    fDevCausalError = "";
    // IMPORTANTE #3: cerrar el drawer antes de abrir el modal (mismo razonamiento
    // que abrirModalEstado: evitar dos trampas de foco activas simultáneamente).
    drawerOpen = false;
    modalDevolucion = true;
  }

  async function registrarDevolucion() {
    if (!selectedShipment) return;
    fDevCausalError = "";
    if (!fDevCausal) {
      fDevCausalError = "Seleccione una causal de devolución.";
      return;
    }

    const descripcion = fDevNota.trim()
      ? `Causal: ${fDevCausal} — ${fDevNota.trim()}`
      : `Causal: ${fDevCausal}`;

    actionLoading = true;
    try {
      const res = await fetch(`/envios/api/${selectedShipment.id}/estado`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ estado: "devuelto", descripcion }),
      });
      const responseData = await res.json();
      if (!res.ok) {
        notifyError(responseData.detail ?? "No se pudo registrar la devolución.");
        return;
      }
      shipments = shipments.map((s) => (s.id === selectedShipment.id ? responseData : s));
      selectedShipment = responseData;
      modalDevolucion = false;
      notifySuccess("Devolución registrada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal: nuevo envío
  // ---------------------------------------------------------------------------
  let modalCrear = false;
  let fRadicadoId = "";
  let fOperador = "";
  let fDestinatario = "";
  let fDireccion = "";
  let fRadicadoIdError = "";
  let fOperadorError = "";
  let crearLoading = false;

  function abrirModalCrear() {
    fRadicadoId = "";
    fOperador = "";
    fDestinatario = "";
    fDireccion = "";
    fRadicadoIdError = "";
    fOperadorError = "";
    modalCrear = true;
  }

  async function crearEnvio() {
    fRadicadoIdError = "";
    fOperadorError = "";
    let valid = true;

    if (!fRadicadoId.trim()) {
      fRadicadoIdError = "El ID del radicado es obligatorio.";
      valid = false;
    }
    if (!fOperador) {
      fOperadorError = "Seleccione un operador postal.";
      valid = false;
    }
    if (!valid) return;

    crearLoading = true;
    try {
      const res = await fetch("/envios/api/crear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          radicado_id: fRadicadoId.trim(),
          operador: fOperador,
          destinatario: fDestinatario.trim() || null,
          direccion: fDireccion.trim() || null,
        }),
      });
      const responseData = await res.json();
      if (!res.ok) {
        notifyError(responseData.detail ?? "No se pudo crear el envío.");
        return;
      }
      // Insertar al inicio de la lista (el más reciente primero)
      shipments = [responseData, ...shipments];
      total += 1;
      modalCrear = false;
      notifySuccess("Envío registrado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      crearLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Carga paginada
  // ---------------------------------------------------------------------------

  async function loadShipments() {
    loading = true;
    try {
      const params = new URLSearchParams({
        page: String(currentPage),
        size: String(pageSize),
      });
      if (filterEstado) params.set("estado", filterEstado);

      const res = await fetch(`/envios/api?${params}`);
      const result = await res.json();
      if (!res.ok) throw result;
      shipments = result.shipments ?? [];
      total = result.total ?? 0;
      // Limpiar selección al cambiar de página/filtro
      selectedIds = new Set();
    } catch (/** @type {any} */ err) {
      notifyError(err?.detail ?? "Error al cargar los envíos.");
    } finally {
      loading = false;
    }
  }

  function applyFilter(estado) {
    filterEstado = estado;
    currentPage = 1;
    loadShipments();
  }

  function onPageChange(/** @type {CustomEvent<{page: number}>} */ e) {
    currentPage = e.detail.page;
    loadShipments();
  }

  // ---------------------------------------------------------------------------
  // Helpers de presentación
  // ---------------------------------------------------------------------------

  const ESTADO_LABEL = {
    registrado: "Registrado",
    en_transito: "En tránsito",
    entregado: "Entregado",
    devuelto: "Devuelto",
    fallido: "Fallido",
  };

  // StatusChip status mapping
  const ESTADO_CHIP = {
    registrado: "info",
    en_transito: "pending",
    entregado: "success",
    devuelto: "warning",
    fallido: "error",
  };

  const OPERADOR_LABELS = {
    "4-72": "4-72",
    servientrega: "Servientrega",
    otro: "Otro",
  };

  const ESTADOS_FILTER = [
    { value: "", label: "Todos" },
    { value: "registrado", label: "Registrado" },
    { value: "en_transito", label: "En tránsito" },
    { value: "entregado", label: "Entregado" },
    { value: "devuelto", label: "Devuelto" },
    { value: "fallido", label: "Fallido" },
  ];

  const ESTADOS_OPTIONS = [
    { value: "registrado", label: "Registrado" },
    { value: "en_transito", label: "En tránsito" },
    { value: "entregado", label: "Entregado" },
    { value: "devuelto", label: "Devuelto" },
    { value: "fallido", label: "Fallido" },
  ];

  const OPERADOR_OPTIONS = [
    { value: "4-72", label: "4-72" },
    { value: "servientrega", label: "Servientrega" },
    { value: "otro", label: "Otro" },
  ];

  const CAUSALES_OPTIONS = CAUSALES_DEVOLUCION.map((c) => ({ value: c, label: c }));

  function formatDate(/** @type {string} */ iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }

  function formatDateTime(/** @type {string} */ iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("es-CO", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // SUGERENCIA #5: las columnas sin texto visible llevan label sr-only para que los
  // lectores de pantalla las anuncien correctamente al navegar por teclado.
  const TABLE_COLUMNS = [
    { key: "_check", label: "Selección", width: "40px" },
    { key: "operador", label: "Operador" },
    { key: "guia", label: "Guía" },
    { key: "estado", label: "Estado" },
    { key: "destinatario", label: "Destinatario" },
    { key: "created_at", label: "Registrado" },
    { key: "_actions", label: "Acciones", width: "80px" },
  ];

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Envíos postales" }]);
  });
</script>

<!-- =========================================================================
     Cabecera de la página
     ========================================================================= -->
<div class="envios-page">
  <header class="envios-page__header">
    <div class="envios-page__title-row">
      <h1 class="envios-page__title">Envíos postales</h1>
      {#if hasPerm}
        <Button icon="fas fa-plus" on:click={abrirModalCrear}>Nuevo envío</Button>
      {/if}
    </div>

    <!-- Filtros de estado -->
    <div class="envios-page__filters" role="group" aria-label="Filtrar por estado">
      {#each ESTADOS_FILTER as f (f.value)}
        <button
          type="button"
          class="envios-page__filter-btn"
          class:envios-page__filter-btn--active={filterEstado === f.value}
          aria-pressed={filterEstado === f.value}
          on:click={() => applyFilter(f.value)}
        >
          {f.label}
        </button>
      {/each}
    </div>
  </header>

  <!-- Barra de planilla de despacho (visible cuando hay selección) -->
  {#if selectedCount > 0}
    <div class="envios-planilla-bar" role="status" aria-live="polite">
      <span class="envios-planilla-bar__count">
        <i class="fas fa-check-square" aria-hidden="true"></i>
        {selectedCount}
        {selectedCount === 1 ? "envío seleccionado" : "envíos seleccionados"}
      </span>
      <Button variant="secondary" icon="fas fa-file-csv" on:click={exportarCSV}>
        Exportar planilla CSV
      </Button>
    </div>
  {/if}

  <!-- Tabla paginada -->
  <div class="envios-table-wrap">
    <!-- "Seleccionar todos en página" — cableado a toggleAll/allPageSelected (IMPORTANTE #2).
         La selección se resetea al paginar: decisión deliberada para evitar exportar filas
         no visibles; la acumulación cross-page queda como roadmap (#6). -->
    {#if !loading && shipments.length > 0}
      <div class="envios-selectall">
        <label class="envios-selectall__label">
          <input
            type="checkbox"
            class="envios-selectall__input"
            checked={allPageSelected}
            bind:indeterminate={allIndeterminate}
            on:change={toggleAll}
            aria-label="Seleccionar todos los envíos de la página"
          />
          <span class="envios-selectall__text">Seleccionar todos en esta página</span>
        </label>
      </div>
    {/if}

    <DataTable
      columns={TABLE_COLUMNS}
      rows={shipments}
      totalCount={total}
      bind:page={currentPage}
      {pageSize}
      {loading}
      caption="Bandeja de envíos postales"
      on:pageChange={onPageChange}
    >
      <svelte:fragment slot="row" let:row>
        <!-- Checkbox de selección (planilla) -->
        <td class="op-datatable__td envios-table__check">
          <label class="envios-table__check-label">
            <input
              type="checkbox"
              class="envios-table__check-input"
              checked={selectedIds.has(row.id)}
              on:change={() => toggleRow(row.id)}
              aria-label="Seleccionar envío {row.guia ?? row.id}"
            />
          </label>
        </td>

        <!-- Operador -->
        <td class="op-datatable__td">
          <span class="envios-table__operador">
            {OPERADOR_LABELS[row.operador] ?? row.operador}
          </span>
        </td>

        <!-- Guía -->
        <td class="op-datatable__td envios-table__guia">
          {#if row.guia}
            {row.guia}
          {:else}
            <span class="envios-table__na">Sin guía</span>
          {/if}
        </td>

        <!-- Estado -->
        <td class="op-datatable__td">
          <StatusChip
            status={ESTADO_CHIP[row.estado] ?? "neutral"}
            label={ESTADO_LABEL[row.estado] ?? row.estado}
            dot
          />
        </td>

        <!-- Destinatario -->
        <td class="op-datatable__td envios-table__destinatario">
          {row.destinatario ?? "—"}
        </td>

        <!-- Fecha -->
        <td class="op-datatable__td envios-table__date">
          {formatDate(row.created_at)}
        </td>

        <!-- Acciones -->
        <td class="op-datatable__td envios-table__actions">
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-eye"
            on:click={() => openDetail(row)}
            aria-label="Ver detalle del envío {row.guia ?? row.id}"
          >
            Ver
          </Button>
        </td>
      </svelte:fragment>
    </DataTable>
  </div>
</div>

<!-- =========================================================================
     Drawer: detalle del envío + historial de eventos
     ========================================================================= -->
<Drawer open={drawerOpen} title="Detalle del envío" width="520px" on:close={closeDrawer}>
  {#if detailLoading}
    <div class="envio-detail__loader">
      <Loader />
    </div>
  {:else if selectedShipment}
    <!-- Datos del envío -->
    <dl class="envio-detail__data">
      <div class="envio-detail__row">
        <dt>Estado</dt>
        <dd>
          <StatusChip
            status={ESTADO_CHIP[selectedShipment.estado] ?? "neutral"}
            label={ESTADO_LABEL[selectedShipment.estado] ?? selectedShipment.estado}
            dot
          />
        </dd>
      </div>
      <div class="envio-detail__row">
        <dt>Operador</dt>
        <dd>{OPERADOR_LABELS[selectedShipment.operador] ?? selectedShipment.operador}</dd>
      </div>
      <div class="envio-detail__row">
        <dt>Guía</dt>
        <dd>{selectedShipment.guia ?? "—"}</dd>
      </div>
      <div class="envio-detail__row">
        <dt>Radicado</dt>
        <dd class="envio-detail__mono">{selectedShipment.radicado_id}</dd>
      </div>
      <div class="envio-detail__row">
        <dt>Destinatario</dt>
        <dd>{selectedShipment.destinatario ?? "—"}</dd>
      </div>
      <div class="envio-detail__row">
        <dt>Dirección</dt>
        <dd>{selectedShipment.direccion ?? "—"}</dd>
      </div>
      <div class="envio-detail__row">
        <dt>Registrado</dt>
        <dd>{formatDateTime(selectedShipment.created_at)}</dd>
      </div>
      <div class="envio-detail__row">
        <dt>Actualizado</dt>
        <dd>{formatDateTime(selectedShipment.updated_at)}</dd>
      </div>
    </dl>

    <!-- Historial de eventos -->
    {#if selectedShipment.eventos?.length > 0}
      <section class="envio-timeline" aria-label="Historial de eventos del envío">
        <h3 class="envio-timeline__title">Historial</h3>
        <ol class="envio-timeline__list" role="list">
          {#each selectedShipment.eventos as ev, idx (idx)}
            <li class="envio-timeline__item">
              <span
                class="envio-timeline__dot"
                aria-hidden="true"
                class:envio-timeline__dot--last={idx === selectedShipment.eventos.length - 1}
              ></span>
              <div class="envio-timeline__content">
                <div class="envio-timeline__row">
                  <StatusChip
                    status={ESTADO_CHIP[ev.estado] ?? "neutral"}
                    label={ESTADO_LABEL[ev.estado] ?? ev.estado}
                  />
                  <time class="envio-timeline__ts" datetime={ev.ts} title={ev.ts}>
                    {formatDateTime(ev.ts)}
                  </time>
                </div>
                {#if ev.descripcion}
                  <p class="envio-timeline__desc">{ev.descripcion}</p>
                {/if}
              </div>
            </li>
          {/each}
        </ol>
      </section>
    {:else}
      <p class="envio-detail__no-eventos">Sin eventos registrados aún.</p>
    {/if}

    <!-- Rastreo contra el operador (consulta PULL bajo demanda, F5/Fase 8) -->
    {#if hasPerm}
      <section class="envio-tracking" aria-label="Rastreo contra el operador postal">
        <hr class="envio-detail__divider" aria-hidden="true" />
        <div class="envio-tracking__header">
          <p class="envio-detail__actions-title">Rastreo con el operador</p>
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-satellite-dish"
            loading={trackingLoading}
            on:click={actualizarRastreo}
          >
            Actualizar rastreo
          </Button>
        </div>

        {#if trackingError}
          <!-- "No se pudo consultar" — nunca se confunde con "sin novedades". -->
          <p class="envio-tracking__error" role="alert">
            <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            {trackingError}
          </p>
        {:else if trackingResult}
          <dl class="envio-detail__data" aria-live="polite">
            <div class="envio-detail__row">
              <dt>Estado interno</dt>
              <dd>
                {ESTADO_LABEL[trackingResult.estado_interno_actual] ??
                  trackingResult.estado_interno_actual}
              </dd>
            </div>
            <div class="envio-detail__row">
              <dt>Estado del operador</dt>
              <dd>{trackingResult.estado_operador ?? "—"}</dd>
            </div>
            <div class="envio-detail__row">
              <dt>Mapeo interno</dt>
              <dd>{trackingResult.estado_interno_mapeado ?? "—"}</dd>
            </div>
            <div class="envio-detail__row">
              <dt>Conexión con operador</dt>
              <dd>
                {#if trackingResult.operador_conectado}
                  <StatusChip status="success" label="Consulta en vivo" dot />
                {:else}
                  <!-- Sin integración real con el operador: se comunica la limitación
                       en vez de aparentar una consulta en vivo que no ocurrió. -->
                  <StatusChip
                    status="neutral"
                    label="Último estado conocido (sin integración en vivo)"
                  />
                {/if}
              </dd>
            </div>
          </dl>
        {/if}
      </section>
    {/if}

    <!-- Acciones (solo si tiene permiso) -->
    {#if hasPerm && selectedShipment.estado !== "entregado"}
      <div class="envio-detail__actions">
        <hr class="envio-detail__divider" aria-hidden="true" />
        <p class="envio-detail__actions-title">Acciones</p>

        <Button
          variant="secondary"
          icon="fas fa-exchange-alt"
          loading={actionLoading}
          on:click={abrirModalEstado}
        >
          Cambiar estado
        </Button>

        {#if selectedShipment.estado !== "devuelto" && selectedShipment.estado !== "fallido"}
          <Button
            variant="ghost"
            icon="fas fa-undo"
            loading={actionLoading}
            on:click={abrirModalDevolucion}
          >
            Registrar devolución
          </Button>
        {/if}
      </div>
    {/if}
  {/if}
</Drawer>

<!-- =========================================================================
     Modal: cambiar estado
     ========================================================================= -->
<Modal
  open={modalEstado}
  title="Cambiar estado del envío"
  size="sm"
  on:close={() => (modalEstado = false)}
>
  <div class="envio-modal-form">
    <FormField
      id="envio-nuevo-estado"
      label="Nuevo estado"
      type="select"
      bind:value={fEstado}
      required
      error={fEstadoError}
      options={ESTADOS_OPTIONS}
    />
    <FormField
      id="envio-estado-desc"
      label="Nota descriptiva (opcional)"
      type="textarea"
      bind:value={fEstadoDescripcion}
      placeholder="Observaciones sobre el cambio de estado…"
      rows={3}
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalEstado = false)}>Cancelar</Button>
    <Button icon="fas fa-check" loading={actionLoading} on:click={cambiarEstado}>Aplicar</Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: registrar devolución con causal
     ========================================================================= -->
<Modal
  open={modalDevolucion}
  title="Registrar devolución"
  size="sm"
  on:close={() => (modalDevolucion = false)}
>
  <div class="envio-modal-form">
    <!--
      La causal se envía en `descripcion` con el prefijo "Causal: <x> — <nota>".
      La causal estructurada como catálogo independiente es roadmap del backend.
    -->
    <FormField
      id="envio-dev-causal"
      label="Causal de devolución"
      type="select"
      bind:value={fDevCausal}
      required
      error={fDevCausalError}
      placeholder="Seleccione una causal"
      options={CAUSALES_OPTIONS}
    />
    <FormField
      id="envio-dev-nota"
      label="Nota adicional (opcional)"
      type="textarea"
      bind:value={fDevNota}
      placeholder="Detalle adicional de la devolución…"
      rows={3}
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalDevolucion = false)}>Cancelar</Button>
    <Button icon="fas fa-undo" loading={actionLoading} on:click={registrarDevolucion}>
      Registrar
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: nuevo envío
     ========================================================================= -->
<Modal open={modalCrear} title="Nuevo envío postal" size="sm" on:close={() => (modalCrear = false)}>
  <div class="envio-modal-form">
    <!--
      UX: se acepta el ID (UUID) del radicado de salida.
      El usuario puede obtenerlo desde la bandeja de radicados.
      Mejora futura: radicado picker con autocompletado (roadmap).
    -->
    <FormField
      id="envio-radicado-id"
      label="ID del radicado de salida"
      type="text"
      bind:value={fRadicadoId}
      placeholder="UUID del radicado de salida (p.ej. a1b2c3d4-…)"
      required
      error={fRadicadoIdError}
      hint="Disponible en el detalle del radicado en la Bandeja."
    />
    <FormField
      id="envio-operador"
      label="Operador postal"
      type="select"
      bind:value={fOperador}
      required
      error={fOperadorError}
      placeholder="Seleccione el operador"
      options={OPERADOR_OPTIONS}
    />
    <FormField
      id="envio-destinatario"
      label="Destinatario (opcional)"
      type="text"
      bind:value={fDestinatario}
      placeholder="Nombre del destinatario"
    />
    <FormField
      id="envio-direccion"
      label="Dirección (opcional)"
      type="text"
      bind:value={fDireccion}
      placeholder="Calle, ciudad, depto."
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalCrear = false)}>Cancelar</Button>
    <Button icon="fas fa-truck" loading={crearLoading} on:click={crearEnvio}>
      Registrar envío
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  @use "$styles/_tokens" as t;

  // ---------------------------------------------------------------------------
  // Página
  // ---------------------------------------------------------------------------
  .envios-page {
    padding: var(--op-space-5);
    max-width: 1100px;

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-4);
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
  }

  // ---------------------------------------------------------------------------
  // Barra de planilla (multi-select activo)
  // ---------------------------------------------------------------------------
  .envios-planilla-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--op-space-3);
    background: var(--op-primary-light);
    border: 1px solid var(--op-primary);
    border-radius: var(--op-radius);
    padding: var(--op-space-2) var(--op-space-4);
    margin-bottom: var(--op-space-3);

    &__count {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-primary-dark);
    }
  }

  // ---------------------------------------------------------------------------
  // Select-all de página (IMPORTANTE #2)
  // ---------------------------------------------------------------------------
  .envios-selectall {
    display: flex;
    align-items: center;
    padding: var(--op-space-2) var(--op-space-4);
    border-bottom: 1px solid var(--op-border);
    background: var(--op-bg);

    &__label {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      cursor: pointer;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      user-select: none;
    }

    &__input {
      width: 16px;
      height: 16px;
      accent-color: var(--op-primary);
      cursor: pointer;
      flex-shrink: 0;

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__text {
      font-weight: var(--op-weight-medium);
    }
  }

  // ---------------------------------------------------------------------------
  // Tabla wrapper
  // ---------------------------------------------------------------------------
  .envios-table-wrap {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    overflow: hidden;
  }

  // ---------------------------------------------------------------------------
  // Columnas específicas de la tabla
  // ---------------------------------------------------------------------------
  .envios-table {
    &__check {
      padding-left: var(--op-space-3);
      padding-right: var(--op-space-2);
    }

    &__check-label {
      display: flex;
      align-items: center;
      cursor: pointer;
    }

    &__check-input {
      width: 16px;
      height: 16px;
      accent-color: var(--op-primary);
      cursor: pointer;

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__operador {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__guia {
      font-family: monospace;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__destinatario {
      max-width: 200px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--op-text-secondary);
    }

    &__date {
      white-space: nowrap;
      color: var(--op-text-secondary);
    }

    &__actions {
      width: 80px;
    }

    &__na {
      color: var(--op-text-secondary);
      font-style: italic;
    }
  }

  // ---------------------------------------------------------------------------
  // Drawer: detalle del envío
  // ---------------------------------------------------------------------------
  .envio-detail {
    &__loader {
      display: flex;
      justify-content: center;
      padding: var(--op-space-6);
    }

    &__data {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      margin: 0 0 var(--op-space-4);
    }

    &__row {
      display: grid;
      grid-template-columns: 120px 1fr;
      gap: var(--op-space-2);
      padding: var(--op-space-1) 0;
      border-bottom: 1px solid var(--op-border);
      font-size: var(--op-font-sm);

      &:last-child {
        border-bottom: none;
      }

      dt {
        color: var(--op-text-secondary);
        font-weight: var(--op-weight-medium);
      }

      dd {
        color: var(--op-text);
        margin: 0;
        word-break: break-all;
      }
    }

    &__mono {
      font-family: monospace;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__no-eventos {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      padding: var(--op-space-3) 0;
      margin: 0;
    }

    &__divider {
      border: none;
      border-top: 1px solid var(--op-border);
      margin: var(--op-space-2) 0;
    }

    &__actions {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      padding-top: var(--op-space-2);
    }

    &__actions-title {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      margin: 0;
    }
  }

  // ---------------------------------------------------------------------------
  // Rastreo con el operador (Fase 8)
  // ---------------------------------------------------------------------------
  .envio-tracking {
    margin-bottom: var(--op-space-2);

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-2);
      margin-bottom: var(--op-space-2);
    }

    &__error {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-error);
      margin: 0 0 var(--op-space-3);
    }
  }

  // ---------------------------------------------------------------------------
  // Timeline de eventos
  // ---------------------------------------------------------------------------
  .envio-timeline {
    margin-bottom: var(--op-space-4);

    &__title {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-3);
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
      position: relative;

      // Línea vertical
      &::before {
        content: "";
        position: absolute;
        left: 7px;
        top: 0;
        bottom: 0;
        width: 2px;
        background: var(--op-border);
      }
    }

    &__item {
      display: flex;
      gap: var(--op-space-3);
      padding-bottom: var(--op-space-3);
      position: relative;
    }

    &__dot {
      flex-shrink: 0;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: var(--op-border);
      border: 2px solid var(--op-surface);
      margin-top: 2px;
      z-index: 1;

      &--last {
        background: var(--op-primary);
      }
    }

    &__content {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__row {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__ts {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__desc {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
      font-style: italic;
    }
  }

  // ---------------------------------------------------------------------------
  // Formularios de modales
  // ---------------------------------------------------------------------------
  .envio-modal-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }
</style>
