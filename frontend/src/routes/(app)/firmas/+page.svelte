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
  /firmas — Bandeja del firmante (T-12d, RF-UI-17).

  Criterios de hecho:
    - Listar solicitudes de firma pendientes del usuario autenticado (paginado).
    - Selección múltiple + firma en lote vía /firmas/api/batch.
    - Ver detalle de la cadena en un Drawer (turnos con estado, turno activo resaltado).
    - Firmar turno activo desde el Drawer.
    - Rechazar (con motivo obligatorio) desde Modal (drawer se cierra primero, no apilar).
    - Resumen de resultados de lote con notifySuccess / notifyWarning.
    - EmptyState cuando la bandeja está vacía.

  RBAC (espejo del backend, D-05):
    - PERM_FIRMA → ver lista, firmar, rechazar, batch.
    El backend re-valida; la UI solo filtra visibilidad.

  D-05 — access_token NUNCA en cliente:
    Todas las llamadas van a /firmas/api[/id/accion] que leen locals.accessToken
    en el servidor. El token nunca llega al JS del cliente.
-->
<script>
  import { onMount, tick } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions, sessionUser } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess, notifyWarning } from "$lib/stores/notifications.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import SignAssistant from "$lib/components/features/firmas/SignAssistant.svelte";
  import CertificadoDrawer from "$lib/components/features/firmas/CertificadoDrawer.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Permisos (espejo del RBAC del backend)
  // ---------------------------------------------------------------------------
  $: hasFirma = $permissions.includes("PERM_FIRMA");

  // ---------------------------------------------------------------------------
  // Estado de la lista
  // ---------------------------------------------------------------------------

  /** @type {import('$lib/types/api.js').SignaturePendingItem[]} */
  let pendientes = data.pendientes ?? [];
  let total = data.total ?? 0;
  let loading = false;
  let currentPage = data.initialFilters?.page ?? 1;
  const pageSize = 20;

  // ---------------------------------------------------------------------------
  // Selección múltiple (batch)
  // ---------------------------------------------------------------------------

  /** @type {Set<string>} */
  let selectedIds = new Set();

  $: selectedCount = selectedIds.size;
  $: allPageSelected =
    pendientes.length > 0 && pendientes.every((p) => selectedIds.has(p.solicitud_id));
  $: allIndeterminate = selectedCount > 0 && !allPageSelected;

  function toggleRow(/** @type {string} */ id) {
    if (selectedIds.has(id)) {
      selectedIds.delete(id);
    } else {
      selectedIds.add(id);
    }
    selectedIds = selectedIds; // trigger reactivity
  }

  function toggleAll() {
    if (allPageSelected) {
      pendientes.forEach((p) => selectedIds.delete(p.solicitud_id));
    } else {
      pendientes.forEach((p) => selectedIds.add(p.solicitud_id));
    }
    selectedIds = selectedIds;
  }

  // ---------------------------------------------------------------------------
  // Firma en lote
  // ---------------------------------------------------------------------------
  let batchLoading = false;
  // Confirmación explícita antes de toda escritura irreversible (frontend/CLAUDE.md):
  // "Firmar seleccionados" dispara firma electrónica de N solicitudes de una sola vez.
  let modalConfirmBatch = false;

  function abrirConfirmBatch() {
    if (selectedIds.size === 0) {
      notifyError("Seleccione al menos una solicitud para firmar.");
      return;
    }
    modalConfirmBatch = true;
  }

  function cerrarConfirmBatch() {
    modalConfirmBatch = false;
  }

  async function firmarSeleccionados() {
    if (selectedIds.size === 0) {
      notifyError("Seleccione al menos una solicitud para firmar.");
      return;
    }

    modalConfirmBatch = false;
    batchLoading = true;
    try {
      const res = await fetch("/firmas/api/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ solicitud_ids: Array.from(selectedIds) }),
      });
      const result = await res.json();

      if (!res.ok) {
        notifyError(result.detail ?? "Error al procesar la firma en lote.");
        return;
      }

      /** @type {import('$lib/types/api.js').SignatureBatchResult[]} */
      const resultados = result.resultados ?? [];
      const firmados = resultados.filter((r) => r.resultado === "firmado");
      const omitidos = resultados.filter((r) => r.resultado === "omitido");

      if (firmados.length > 0 && omitidos.length === 0) {
        notifySuccess(
          `Firmado${firmados.length === 1 ? "" : "s"}: ${firmados.length} de ${resultados.length} solicitude${resultados.length === 1 ? "" : "s"}.`,
        );
      } else if (firmados.length > 0 && omitidos.length > 0) {
        notifySuccess(`Firmados: ${firmados.length}`);
        const detalleOmitidos = omitidos
          .map((r) => r.detalle)
          .filter(Boolean)
          .join("; ");
        notifyWarning(
          `Omitidos: ${omitidos.length}${detalleOmitidos ? ` — ${detalleOmitidos}` : "."}`,
          6000,
        );
      } else {
        const detalleOmitidos = omitidos
          .map((r) => r.detalle)
          .filter(Boolean)
          .join("; ");
        notifyWarning(
          `Ninguna solicitud pudo firmarse.${detalleOmitidos ? ` ${detalleOmitidos}` : ""}`,
          6000,
        );
      }

      selectedIds = new Set();
      await loadPendientes();
    } catch {
      notifyError("No se pudo conectar al servidor de firmas.");
    } finally {
      batchLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Drawer: detalle de la cadena de firma
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  /** @type {import('$lib/types/api.js').SignatureRequestResponse|null} */
  let selectedDetail = null;
  let detailLoading = false;

  async function openDetail(/** @type {any} */ item) {
    drawerOpen = true;
    selectedDetail = null;
    detailLoading = true;
    try {
      const res = await fetch(`/firmas/api/${encodeURIComponent(item.solicitud_id)}`);
      const detail = await res.json();
      if (res.ok) {
        selectedDetail = detail;
      } else {
        notifyError(detail?.detail ?? "No se pudo cargar el detalle de la solicitud.");
        drawerOpen = false;
      }
    } catch {
      notifyError("No se pudo conectar al servidor de firmas.");
      drawerOpen = false;
    } finally {
      detailLoading = false;
    }
  }

  function closeDrawer() {
    drawerOpen = false;
    selectedDetail = null;
  }

  // ---------------------------------------------------------------------------
  // Acción: Firmar turno activo — asistente Stepper (Confirmar → OTP → Comprobante)
  // ---------------------------------------------------------------------------
  // Confirmación explícita antes de toda escritura irreversible (frontend/CLAUDE.md):
  // firma electrónica con efectos jurídicos (E06). Cierra el Drawer antes de abrir
  // el asistente para no apilar focus traps (mismo patrón que abrirModalRechazar).
  let signAssistantOpen = false;

  function abrirConfirmSign() {
    if (!selectedDetail) return;
    drawerOpen = false;
    signAssistantOpen = true;
  }

  function cerrarSignAssistant() {
    signAssistantOpen = false;
    tick().then(() => tituloEl?.focus());
  }

  /** @param {CustomEvent<import('$lib/types/api.js').SignatureRequestResponse>} e */
  function onDocumentoFirmado(e) {
    const firmado = e.detail;
    notifySuccess("Documento firmado correctamente.");
    signAssistantOpen = false;
    closeDrawer();
    // Eliminar de la lista local; el item ya no está en el turno del usuario
    pendientes = pendientes.filter((p) => p.solicitud_id !== firmado?.solicitud_id);
    total = Math.max(0, total - 1);
    selectedIds.delete(firmado?.solicitud_id ?? "");
    selectedIds = selectedIds;
  }

  // ---------------------------------------------------------------------------
  // Certificado de firma (Drawer con apariencia de certificado)
  // ---------------------------------------------------------------------------
  let certDrawerOpen = false;
  /** @type {string|null} */
  let certFirmaId = null;

  function verCertificado(/** @type {string} */ firmaId) {
    certFirmaId = firmaId;
    certDrawerOpen = true;
  }

  function cerrarCertDrawer() {
    certDrawerOpen = false;
  }

  // ---------------------------------------------------------------------------
  // Acción: Rechazar (Modal con motivo)
  // ---------------------------------------------------------------------------
  let modalRechazar = false;
  let fMotivo = "";
  let fMotivoError = "";
  let rejectLoading = false;
  /** @type {HTMLElement | undefined} Título de la página: destino de foco estable. */
  let tituloEl;

  function abrirModalRechazar() {
    fMotivo = "";
    fMotivoError = "";
    // Cerrar drawer ANTES de abrir modal para no apilar focus traps (A11y)
    drawerOpen = false;
    modalRechazar = true;
  }

  // Cierra el Modal de rechazo devolviendo el foco a un destino estable: el
  // disparador "Rechazar" vivía dentro del Drawer ya desmontado, así que el
  // retorno de foco nativo del Modal caería en <body>. Movemos el foco al
  // título de la página para no perder la posición de navegación por teclado.
  function cerrarModalRechazar() {
    modalRechazar = false;
    tick().then(() => tituloEl?.focus());
  }

  async function rechazarTurno() {
    if (!selectedDetail) return;
    fMotivoError = "";

    if (!fMotivo.trim()) {
      fMotivoError = "El motivo de rechazo es obligatorio.";
      return;
    }

    rejectLoading = true;
    try {
      const res = await fetch(
        `/firmas/api/${encodeURIComponent(selectedDetail.solicitud_id)}/reject`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ motivo: fMotivo.trim() }),
        },
      );
      const result = await res.json();

      if (res.ok) {
        notifySuccess("Solicitud de firma rechazada.");
        cerrarModalRechazar();
        // Eliminar de la lista: una vez rechazada no aparece más en la bandeja
        pendientes = pendientes.filter((p) => p.solicitud_id !== selectedDetail?.solicitud_id);
        total = Math.max(0, total - 1);
        selectedIds.delete(selectedDetail?.solicitud_id ?? "");
        selectedIds = selectedIds;
        selectedDetail = null;
      } else {
        handleSignError(res.status, result);
        cerrarModalRechazar();
      }
    } catch {
      notifyError("No se pudo conectar al servidor de firmas.");
    } finally {
      rejectLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Mapeo de errores del gateway (403 no_es_su_turno, 409 solicitud_no_pendiente)
  // ---------------------------------------------------------------------------

  /**
   * Traduce errores del gateway de firma a mensajes de usuario claros y refresca.
   *
   * @param {number} status
   * @param {{ error?: string; detail?: string }} result
   */
  async function handleSignError(status, result) {
    const code = result?.error ?? "";
    const isTurnError = status === 403 && (code === "no_es_su_turno" || code === "no_es_firmante");
    const isPendingError = status === 409 && code === "solicitud_no_pendiente";

    if (isTurnError || isPendingError) {
      notifyWarning(
        isTurnError
          ? "Este documento ya no está en tu turno; se actualizó la bandeja."
          : "Esta solicitud ya no está pendiente; se actualizó la bandeja.",
      );
      // Refrescar bandeja para reflejar el estado real
      await loadPendientes();
    } else {
      notifyError(result?.detail ?? `Error ${status} al procesar la firma.`);
    }
  }

  // ---------------------------------------------------------------------------
  // Carga paginada (client-side)
  // ---------------------------------------------------------------------------

  async function loadPendientes() {
    loading = true;
    try {
      const params = new URLSearchParams({
        page: String(currentPage),
        size: String(pageSize),
      });

      const res = await fetch(`/firmas/api?${params}`);
      const result = await res.json();
      if (!res.ok) throw result;
      pendientes = result.pendientes ?? [];
      total = result.total ?? 0;
      // Limpiar selección al cambiar de página o refrescar
      selectedIds = new Set();
    } catch (/** @type {any} */ err) {
      notifyError(err?.detail ?? "Error al cargar la bandeja de firmas.");
    } finally {
      loading = false;
    }
  }

  function onPageChange(/** @type {CustomEvent<{page: number}>} */ e) {
    currentPage = e.detail.page;
    loadPendientes();
  }

  // ---------------------------------------------------------------------------
  // Helpers de presentación
  // ---------------------------------------------------------------------------

  const TIPO_LABEL = /** @type {Record<string,string>} */ ({
    radicado: "Radicado",
    borrador: "Borrador",
    documento: "Documento",
    expediente: "Expediente",
  });

  const TURNO_ESTADO_CHIP = /** @type {Record<string,string>} */ ({
    pendiente: "warning",
    firmado: "success",
    rechazado: "error",
  });

  const TURNO_ESTADO_LABEL = /** @type {Record<string,string>} */ ({
    pendiente: "Pendiente",
    firmado: "Firmado",
    rechazado: "Rechazado",
  });

  const SOLICITUD_ESTADO_CHIP = /** @type {Record<string,string>} */ ({
    pendiente: "warning",
    firmado: "success",
    rechazado: "error",
  });

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

  /**
   * Determina si un turno de la cadena es el turno activo del usuario actual.
   * El turno activo es el único con estado 'pendiente' en la solicitud.
   *
   * @param {import('$lib/types/api.js').SignatureTurn} turno
   * @returns {boolean}
   */
  function esTurnoActivo(turno) {
    if (turno.estado !== "pendiente") return false;
    // Si el firmante_id coincide con el sub del JWT, es definitivamente el turno del usuario.
    // Si no hay sub disponible (edge case), mostrar como activo igualmente (el backend re-valida).
    const sub = $sessionUser?.sub;
    return !sub || turno.firmante_id === sub;
  }

  const TABLE_COLUMNS = [
    { key: "_check", label: "Selección", width: "40px" },
    { key: "titulo", label: "Título / documento" },
    { key: "objeto_tipo", label: "Tipo" },
    { key: "orden", label: "Turno" },
    { key: "created_at", label: "Recibido" },
    { key: "_actions", label: "Acciones", width: "110px" },
  ];

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Firmas" }]);
  });
</script>

<!-- =========================================================================
     Cabecera de la página
     ========================================================================= -->
<div class="firmas-page">
  <header class="firmas-page__header">
    <div class="firmas-page__title-row">
      <h1 class="firmas-page__title" bind:this={tituloEl} tabindex="-1">Bandeja de firmas</h1>
    </div>

    {#if data.loadError}
      <div class="firmas-page__load-error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {data.loadError}
      </div>
    {/if}
  </header>

  <!-- Barra de firma en lote (visible cuando hay selección) -->
  {#if selectedCount > 0 && hasFirma}
    <div class="firmas-batch-bar" role="status" aria-live="polite">
      <span class="firmas-batch-bar__count">
        <i class="fas fa-check-square" aria-hidden="true"></i>
        {selectedCount}
        {selectedCount === 1 ? "solicitud seleccionada" : "solicitudes seleccionadas"}
      </span>
      <Button
        variant="primary"
        icon="fas fa-signature"
        loading={batchLoading}
        on:click={abrirConfirmBatch}
      >
        Firmar seleccionados ({selectedCount})
      </Button>
    </div>
  {/if}

  <!-- Select-all de página -->
  {#if !loading && pendientes.length > 0 && hasFirma}
    <div class="firmas-selectall">
      <label class="firmas-selectall__label">
        <input
          type="checkbox"
          class="firmas-selectall__input"
          checked={allPageSelected}
          bind:indeterminate={allIndeterminate}
          on:change={toggleAll}
          aria-label="Seleccionar todas las solicitudes de esta página"
        />
        <span class="firmas-selectall__text">Seleccionar todas en esta página</span>
      </label>
    </div>
  {/if}

  <!-- Tabla paginada.
       Cuando la bandeja está vacía mostramos SOLO el EmptyState propio (más claro que
       el genérico interno del DataTable, que menciona "criterios" inexistentes aquí). -->
  {#if loading || pendientes.length > 0}
    <div class="firmas-table-wrap">
      <DataTable
        columns={TABLE_COLUMNS}
        rows={pendientes}
        totalCount={total}
        bind:page={currentPage}
        {pageSize}
        {loading}
        caption="Bandeja de solicitudes de firma pendientes"
        on:pageChange={onPageChange}
      >
        <svelte:fragment slot="row" let:row>
          <!-- Checkbox de selección (batch) -->
          <td class="op-datatable__td firmas-table__check">
            {#if hasFirma}
              <label class="firmas-table__check-label">
                <input
                  type="checkbox"
                  class="firmas-table__check-input"
                  checked={selectedIds.has(row.solicitud_id)}
                  on:change={() => toggleRow(row.solicitud_id)}
                  aria-label="Seleccionar solicitud {row.titulo ?? row.solicitud_id}"
                />
              </label>
            {:else}
              <span aria-hidden="true"></span>
            {/if}
          </td>

          <!-- Título / objeto -->
          <td class="op-datatable__td firmas-table__titulo">
            <span class="firmas-table__titulo-text">{row.titulo ?? "—"}</span>
            <span class="firmas-table__objeto-id">{row.objeto_id}</span>
          </td>

          <!-- Tipo de objeto -->
          <td class="op-datatable__td">
            <span class="firmas-table__tipo">
              {TIPO_LABEL[row.objeto_tipo] ?? row.objeto_tipo}
            </span>
          </td>

          <!-- Turno (orden) -->
          <td class="op-datatable__td firmas-table__orden">
            <span class="firmas-table__turno" aria-label="Turno {row.orden}">
              <i class="fas fa-user-check" aria-hidden="true"></i>
              Turno {row.orden}
            </span>
          </td>

          <!-- Fecha recibida -->
          <td class="op-datatable__td firmas-table__date">
            <time datetime={row.created_at} title={row.created_at}>
              {formatDate(row.created_at)}
            </time>
          </td>

          <!-- Acciones -->
          <td class="op-datatable__td firmas-table__actions">
            <Button
              variant="ghost"
              size="sm"
              icon="fas fa-eye"
              on:click={() => openDetail(row)}
              aria-label="Ver y firmar solicitud {row.titulo ?? row.solicitud_id}"
            >
              Ver / Firmar
            </Button>
          </td>
        </svelte:fragment>
      </DataTable>
    </div>
  {:else if !data.loadError}
    <EmptyState
      icon="fas fa-signature"
      title="Sin solicitudes pendientes"
      description="No tienes documentos pendientes de firma en este momento."
    />
  {/if}
</div>

<!-- =========================================================================
     Drawer: detalle de la cadena de firma
     ========================================================================= -->
<Drawer open={drawerOpen} title="Cadena de firma" width="540px" on:close={closeDrawer}>
  {#if detailLoading}
    <div class="firma-detail__loader">
      <Loader />
    </div>
  {:else if selectedDetail}
    <!-- Datos generales de la solicitud -->
    <dl class="firma-detail__data">
      <div class="firma-detail__row">
        <dt>Estado</dt>
        <dd>
          <StatusChip
            status={SOLICITUD_ESTADO_CHIP[selectedDetail.estado] ?? "neutral"}
            label={TURNO_ESTADO_LABEL[selectedDetail.estado] ?? selectedDetail.estado}
            dot
          />
        </dd>
      </div>
      <div class="firma-detail__row">
        <dt>Documento</dt>
        <dd class="firma-detail__titulo">{selectedDetail.titulo ?? "—"}</dd>
      </div>
      <div class="firma-detail__row">
        <dt>Tipo</dt>
        <dd>{TIPO_LABEL[selectedDetail.objeto_tipo] ?? selectedDetail.objeto_tipo}</dd>
      </div>
      <div class="firma-detail__row">
        <dt>ID objeto</dt>
        <dd class="firma-detail__mono">{selectedDetail.objeto_id}</dd>
      </div>
      <div class="firma-detail__row">
        <dt>Solicitado</dt>
        <dd>
          <time datetime={selectedDetail.created_at}>
            {formatDateTime(selectedDetail.created_at)}
          </time>
        </dd>
      </div>
    </dl>

    <!-- Cadena de turnos -->
    {#if selectedDetail.items?.length > 0}
      <section class="firma-chain" aria-label="Cadena de firmas">
        <h3 class="firma-chain__title">Turnos de firma</h3>
        <ol class="firma-chain__list" role="list">
          {#each selectedDetail.items as turno, idx (turno.orden)}
            {@const activo = esTurnoActivo(turno)}
            <li
              class="firma-chain__item"
              class:firma-chain__item--activo={activo}
              aria-current={activo ? "step" : undefined}
            >
              <span class="firma-chain__num" aria-hidden="true">
                {turno.orden}
              </span>
              <div class="firma-chain__content">
                <div class="firma-chain__row">
                  <StatusChip
                    status={TURNO_ESTADO_CHIP[turno.estado] ?? "neutral"}
                    label={TURNO_ESTADO_LABEL[turno.estado] ?? turno.estado}
                    dot
                  />
                  {#if activo}
                    <span class="firma-chain__activo-label">
                      <i class="fas fa-hand-point-right" aria-hidden="true"></i>
                      Tu turno
                    </span>
                  {/if}
                  {#if turno.decidido_at}
                    <time class="firma-chain__ts" datetime={turno.decidido_at}>
                      {formatDateTime(turno.decidido_at)}
                    </time>
                  {/if}
                </div>
                {#if turno.comentario}
                  <p class="firma-chain__comentario">{turno.comentario}</p>
                {/if}
                {#if turno.estado === "firmado" && turno.firma_id}
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="fas fa-certificate"
                    on:click={() => verCertificado(/** @type {string} */ (turno.firma_id))}
                  >
                    Ver certificado
                  </Button>
                {/if}
              </div>
            </li>
          {/each}
        </ol>
      </section>
    {/if}

    <!-- Acciones de firma (solo si la solicitud está pendiente y el usuario tiene permiso) -->
    {#if hasFirma && selectedDetail.estado === "pendiente"}
      <div class="firma-detail__actions">
        <hr class="firma-detail__divider" aria-hidden="true" />
        <p class="firma-detail__actions-title">Acciones sobre tu turno</p>
        <div class="firma-detail__actions-row">
          <Button variant="primary" icon="fas fa-signature" on:click={abrirConfirmSign}>
            Firmar
          </Button>
          <Button
            variant="ghost"
            icon="fas fa-times-circle"
            loading={rejectLoading}
            on:click={abrirModalRechazar}
          >
            Rechazar
          </Button>
        </div>
      </div>
    {/if}
  {/if}
</Drawer>

<!-- =========================================================================
     Modal: rechazar con motivo obligatorio
     ========================================================================= -->
<Modal
  open={modalRechazar}
  title="Rechazar solicitud de firma"
  size="sm"
  on:close={cerrarModalRechazar}
>
  <div class="firma-modal-form">
    <p class="firma-modal-form__info">
      <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
      El rechazo detiene la cadena de firma. Esta acción no se puede deshacer.
    </p>
    <FormField
      id="firma-motivo"
      label="Motivo del rechazo"
      type="textarea"
      bind:value={fMotivo}
      required
      error={fMotivoError}
      placeholder="Explique brevemente el motivo del rechazo…"
      rows={4}
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrarModalRechazar}>Cancelar</Button>
    <Button
      variant="primary"
      icon="fas fa-times-circle"
      loading={rejectLoading}
      on:click={rechazarTurno}
    >
      Confirmar rechazo
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Asistente de firma del turno activo — Confirmar → OTP → Comprobante
     (acción irreversible, E06 Inc.2/RF-FIR-13). Se remonta con {#key} por
     solicitud para partir siempre de un estado interno limpio.
     ========================================================================= -->
{#if selectedDetail}
  {#key selectedDetail.solicitud_id}
    <SignAssistant
      open={signAssistantOpen}
      solicitudId={selectedDetail.solicitud_id}
      titulo={selectedDetail.titulo ?? selectedDetail.objeto_id}
      on:close={cerrarSignAssistant}
      on:signed={onDocumentoFirmado}
    />
  {/key}
{/if}

<!-- =========================================================================
     Drawer: certificado de una firma individual (apariencia de certificado)
     ========================================================================= -->
<CertificadoDrawer open={certDrawerOpen} firmaId={certFirmaId} on:close={cerrarCertDrawer} />

<!-- =========================================================================
     Modal: confirmar firma en lote (acción irreversible, E06)
     ========================================================================= -->
<Modal
  open={modalConfirmBatch}
  title="Confirmar firma en lote"
  size="sm"
  on:close={cerrarConfirmBatch}
>
  <div class="firma-modal-form">
    <p class="firma-modal-form__info">
      <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
      Vas a firmar electrónicamente {selectedCount}
      {selectedCount === 1 ? "solicitud" : "solicitudes"} de una sola vez. La firma tiene efectos jurídicos
      y no se puede deshacer.
    </p>
    <p class="firma-modal-form__hint">
      Los documentos que requieran un código de verificación (reservados/clasificados) se omitirán
      del lote — fírmalos individualmente desde "Ver / Firmar" para completar la verificación.
    </p>
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrarConfirmBatch}>Cancelar</Button>
    <Button
      variant="primary"
      icon="fas fa-signature"
      loading={batchLoading}
      on:click={firmarSeleccionados}
    >
      Confirmar firma ({selectedCount})
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  @use "$styles/_tokens" as t;

  // ---------------------------------------------------------------------------
  // Página
  // ---------------------------------------------------------------------------
  .firmas-page {
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

    &__load-error {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      background: var(--op-error-bg);
      color: var(--op-error-fg);
      border: 1px solid var(--op-error);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
    }
  }

  // ---------------------------------------------------------------------------
  // Barra de firma en lote (multi-select activo)
  // ---------------------------------------------------------------------------
  .firmas-batch-bar {
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
  // Select-all de página
  // ---------------------------------------------------------------------------
  .firmas-selectall {
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
  .firmas-table-wrap {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    overflow: hidden;
  }

  // ---------------------------------------------------------------------------
  // Columnas de la tabla
  // ---------------------------------------------------------------------------
  .firmas-table {
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

    &__titulo {
      max-width: 280px;
    }

    &__titulo-text {
      display: block;
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__objeto-id {
      display: block;
      font-family: monospace;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-top: 2px;
    }

    &__tipo {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

    &__orden {
      white-space: nowrap;
    }

    &__turno {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-1);
      font-size: var(--op-font-sm);
      color: var(--op-primary-dark);
      font-weight: var(--op-weight-medium);
    }

    &__date {
      white-space: nowrap;
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }

    &__actions {
      width: 110px;
    }
  }

  // ---------------------------------------------------------------------------
  // Drawer: detalle de la cadena
  // ---------------------------------------------------------------------------
  .firma-detail {
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
      grid-template-columns: 100px 1fr;
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

    &__titulo {
      font-weight: var(--op-weight-medium);
    }

    &__mono {
      font-family: monospace;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__divider {
      border: none;
      border-top: 1px solid var(--op-border);
      margin: var(--op-space-3) 0;
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
      margin: 0 0 var(--op-space-2);
    }

    &__actions-row {
      display: flex;
      gap: var(--op-space-3);
      flex-wrap: wrap;
    }
  }

  // ---------------------------------------------------------------------------
  // Cadena de turnos
  // ---------------------------------------------------------------------------
  .firma-chain {
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
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__item {
      display: flex;
      gap: var(--op-space-3);
      align-items: flex-start;
      padding: var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      background: var(--op-surface);
      transition: border-color var(--op-transition);

      &--activo {
        border-color: var(--op-primary);
        background: var(--op-primary-light);
      }
    }

    &__num {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: var(--op-border);
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      flex-shrink: 0;

      .firma-chain__item--activo & {
        background: var(--op-primary-dark);
        color: var(--op-surface);
      }
    }

    &__content {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      min-width: 0;
    }

    &__row {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__activo-label {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-1);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
    }

    &__ts {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-left: auto;

      // Contraste AA (revisión UX lote 2): `--op-text-secondary` sobre el
      // fondo `--op-primary-light` del turno activo mide ~4.0:1, bajo AA.
      // `--op-text` sí cumple sobre ese mismo fondo.
      .firma-chain__item--activo & {
        color: var(--op-text);
      }
    }

    &__comentario {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
      font-style: italic;

      .firma-chain__item--activo & {
        color: var(--op-text);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Formulario del modal
  // ---------------------------------------------------------------------------
  .firma-modal-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__info {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-warning-fg);
      background: var(--op-warning-bg);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-3);
      margin: 0;
    }

    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }
  }
</style>
