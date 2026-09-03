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
  import { formatearFecha, formatearFechaHora } from "$lib/utils/fecha.js";
  import { permissions, userId } from "$lib/stores/auth.js";
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
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";

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
  //  La firma en lote solo alcanza los turnos de FIRMA.
  //
  //  La casilla se ofrecía en TODA fila, así que un «seleccionar todas»
  //  mandaba también los turnos de visto bueno a firmar: un revisor
  //  comprometía a la entidad en bloque con un acto que para él era «lo he
  //  leído y me parece bien». El backend ya lo rechaza (409
  //  `turno_es_de_visto_bueno`), pero ofrecer el gesto y luego negarlo es
  //  enseñar un camino que no existe.
  $: firmables = pendientes.filter((p) => p.rol === "firma");
  $: allPageSelected =
    firmables.length > 0 && firmables.every((p) => selectedIds.has(p.solicitud_id));
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
      firmables.forEach((p) => selectedIds.delete(p.solicitud_id));
    } else {
      firmables.forEach((p) => selectedIds.add(p.solicitud_id));
    }
    selectedIds = selectedIds;
  }

  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Ver el documento que se pide aprobar
  // ---------------------------------------------------------------------------
  let viendoDocumento = false;
  let verDocumentoError = "";

  /**
   * Abre la previsualización del borrador en una pestaña nueva.
   *
   * Reutiliza el proxy de `/borradores` en vez de abrir un camino propio: la
   * previsualización no persiste nada y ya está gateada por clearance en el
   * servidor, así que el revisor no ve nada que no pudiera ver de todos modos.
   *
   * Límite declarado: el endpoint exige `PERM_RADI`. Un revisor que solo tenga
   * `PERM_FIRMA` recibe un 403 y lo verá dicho, no en silencio — que su turno
   * le dé derecho a leer lo que se le pide aprobar es lo correcto y hoy no
   * está resuelto en el backend.
   */
  async function verDocumento() {
    if (!selectedDetail) return;
    viendoDocumento = true;
    verDocumentoError = "";
    try {
      const res = await fetch(`/borradores/api/${selectedDetail.objeto_id}/previsualizar`, {
        method: "POST",
      });
      if (res.status === 403) {
        throw new Error(
          "No tienes permiso para ver este documento. Pide acceso de radicación " +
            "a quien administre los permisos: tu turno no basta hoy para leerlo.",
        );
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(mensajeError(data, "No se pudo abrir el documento."));
      }
      const url = URL.createObjectURL(await res.blob());
      window.open(url, "_blank", "noopener");
      // Con retraso: revocarlo en el acto deja la pestaña recién abierta vacía.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      verDocumentoError = err instanceof Error ? err.message : String(err);
    } finally {
      viendoDocumento = false;
    }
  }

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
        notifyError(mensajeError(result, "Error al procesar la firma en lote."));
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
        notifyError(mensajeError(detail, "No se pudo cargar el detalle de la solicitud."));
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
  // Visto bueno (E23 Inc.4, RF-PRO-10)
  // ---------------------------------------------------------------------------

  let voboLoading = false;

  /**
   * Da el visto bueno del turno activo: aprueba SIN firmar.
   *
   * No lleva segundo factor, y su ausencia es la decisión D-6, no un olvido: lo
   * obligatorio siempre es el anclaje a la huella del texto —que el backend
   * comprueba— y exigir además credencial de firma y 2FA a cada revisor es
   * viable en una entidad de tres personas y no en una de cuarenta.
   */
  async function darVistoBueno() {
    if (!selectedDetail) return;
    voboLoading = true;
    try {
      const res = await fetch(
        `/firmas/api/${encodeURIComponent(selectedDetail.solicitud_id)}/vobo`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ comentario: fComentarioVobo.trim() || null }),
        },
      );
      const result = await res.json();
      if (res.ok) {
        notifySuccess("Visto bueno registrado. El trámite pasa al siguiente turno.");
        fComentarioVobo = "";
        pendientes = pendientes.filter((p) => p.solicitud_id !== selectedDetail?.solicitud_id);
        total = Math.max(0, total - 1);
        selectedDetail = null;
      } else {
        handleSignError(res.status, result);
      }
    } catch {
      notifyError("No se pudo conectar al servidor de firmas.");
    } finally {
      voboLoading = false;
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
    const code = codigoError(result) ?? "";
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
      notifyError(mensajeError(result, `Error ${status} al procesar la firma.`));
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
      notifyError(mensajeError(err, "Error al cargar la bandeja de firmas."));
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

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    return formatearFecha(iso);
  }

  function formatDateTime(/** @type {string} */ iso) {
    if (!iso) return "—";
    return formatearFechaHora(iso);
  }

  /**
   * Determina si un turno de la cadena es el turno activo del usuario actual.
   * El turno activo es el único con estado 'pendiente' en la solicitud.
   *
   * @param {import('$lib/types/api.js').SignatureTurn} turno
   * @returns {boolean}
   */
  /** Nota opcional del revisor al dar el visto bueno. */
  let fComentarioVobo = "";

  /** Etiqueta y matiz visual de cada rol de turno (E23, RF-PRO-10). */
  const ROL_LABEL = /** @type {Record<string,string>} */ ({
    visto_bueno: "Visto bueno",
    firma: "Firma",
  });

  /**
   * El turno activo del usuario, o `null`.
   *
   * De él depende QUÉ acto se ofrece: aprobar o firmar. Ofrecer los dos, o el
   * equivocado, atribuiría a un revisor un compromiso que no adquirió — que es
   * justo lo que el rol existe para separar.
   */
  $: turnoPropio = selectedDetail?.items?.find((t) => esTurnoActivo(t)) ?? null;
  $: esTurnoDeVistoBueno = turnoPropio?.rol === "visto_bueno";

  function esTurnoActivo(turno) {
    if (turno.estado !== "pendiente") return false;
    // `firmante_id` es la identidad INTERNA del tenant (`auth_users.id`), NO el
    // `sub` de Keycloak: son dos UUID distintos para la misma persona. Al
    // comparar contra `sub`, ningún turno se reconocía como propio y la cadena
    // de firmas se leía entera como «no te toca».
    // Sin identidad en sesión se muestra como activo y el backend re-valida:
    // es preferible ofrecer de más que ocultarle a alguien su propio turno.
    const yo = $userId;
    return !yo || turno.firmante_id === yo;
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
  {#if !loading && firmables.length > 0 && hasFirma}
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
            {#if hasFirma && row.rol === "firma"}
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

          <!-- Turno (orden) + QUÉ se pide (E23, RF-PRO-10).
               La bandeja decía en qué posición va el turno pero no si a esta
               persona le piden revisar o comprometerse, y eso solo se sabía
               abriendo el detalle. Dar el visto bueno y firmar no obligan
               igual: la diferencia va donde se decide si abrir la fila. -->
          <td class="op-datatable__td firmas-table__orden">
            <span
              class="firmas-table__turno"
              aria-label={`Turno ${row.orden}, ${ROL_LABEL[row.rol] ?? row.rol}`}
            >
              <i class="fas fa-{row.rol === 'firma' ? 'pen-nib' : 'user-check'}" aria-hidden="true"
              ></i>
              Turno {row.orden}
            </span>
            <span class="firmas-table__rol" class:firmas-table__rol--firma={row.rol === "firma"}>
              {ROL_LABEL[row.rol] ?? row.rol}
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
            <!-- El boton nombra el acto REAL del turno. «Firmar» sobre un
                 visto bueno describe mal lo que va a pasar, y describe de mas:
                 firmar compromete a la entidad, revisar no. -->
            <Button
              variant="ghost"
              size="sm"
              icon="fas fa-eye"
              on:click={() => openDetail(row)}
              aria-label={row.rol === "firma"
                ? `Ver y firmar solicitud ${row.titulo ?? row.solicitud_id}`
                : `Ver y dar visto bueno a ${row.titulo ?? row.solicitud_id}`}
            >
              {row.rol === "firma" ? "Ver / Firmar" : "Ver / Dar visto bueno"}
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
                  <!-- El rol se enseña SIEMPRE, también en los turnos ya
                       resueltos: quien lea la cadena dentro de diez años tiene
                       que poder distinguir quién aprobó de quién firmó. -->
                  <span
                    class="firma-chain__rol"
                    class:firma-chain__rol--firma={turno.rol === "firma"}
                  >
                    {ROL_LABEL[turno.rol] ?? turno.rol}
                  </span>
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

    <!-- Ver el documento que se pide aprobar o firmar.
         El detalle mostraba identificador, tipo y fecha, y ni el texto ni una
         forma de llegar a él: se aprobaba a ciegas. El circuito ancla la
         aprobación al hash del cuerpo —«aprobó ESTE texto»—, y esa garantía es
         hueca si quien aprueba nunca lo vio. La previsualización no persiste
         nada: es exactamente la lectura que hacía falta. -->
    {#if selectedDetail.objeto_tipo === "borrador"}
      <div class="firma-detail__ver">
        <Button
          variant="secondary"
          size="sm"
          icon="fas fa-file-pdf"
          loading={viendoDocumento}
          on:click={verDocumento}
        >
          Ver documento
        </Button>
        {#if verDocumentoError}
          <p class="firma-detail__ver-error" role="alert">{verDocumentoError}</p>
        {/if}
      </div>
    {/if}

    <!-- Acciones de firma (solo si la solicitud está pendiente y el usuario tiene permiso) -->
    {#if hasFirma && selectedDetail.estado === "pendiente"}
      <div class="firma-detail__actions">
        <hr class="firma-detail__divider" aria-hidden="true" />
        <p class="firma-detail__actions-title">Acciones sobre tu turno</p>
        {#if esTurnoDeVistoBueno}
          <p class="firma-detail__rol-nota">
            Tu turno es de <strong>visto bueno</strong>: apruebas el documento, no lo firmas. La
            firma que compromete a la entidad la pone el último turno de la cadena.
          </p>
          <FormField
            id="firma-comentario-vobo"
            label="Comentario (opcional)"
            type="textarea"
            bind:value={fComentarioVobo}
            placeholder="Observaciones sobre el documento…"
            rows={2}
          />
        {/if}
        <div class="firma-detail__actions-row">
          {#if esTurnoDeVistoBueno}
            <Button
              variant="primary"
              icon="fas fa-check"
              loading={voboLoading}
              on:click={darVistoBueno}
            >
              Dar visto bueno
            </Button>
          {:else}
            <Button variant="primary" icon="fas fa-signature" on:click={abrirConfirmSign}>
              Firmar
            </Button>
          {/if}
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
      font-family: var(--op-font-family-mono);
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
      font-family: var(--op-font-family-mono);
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

    // Rol del turno (E23, RF-PRO-10). Se distingue por FORMA (borde y peso),
    // no solo por color: quien no distingue los dos tonos tiene que poder
    // separar igualmente «aprobó» de «firmó», que es la diferencia entre una
    // opinión y un compromiso de la entidad.
    &__rol {
      font-size: var(--op-font-xs);
      padding: 0 var(--op-space-2);
      border-radius: var(--op-radius-full);
      border: 1px solid var(--op-border);
      color: var(--op-text-secondary);
      white-space: nowrap;

      &--firma {
        border-color: var(--op-primary);
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);
      }

      .firma-chain__item--activo & {
        color: var(--op-text);
      }
    }
  }

  // Acceso al documento que se pide aprobar. Bloque propio, NO dentro de un
  // contenedor flex: el mensaje de error es prosa y en flex se trocea en
  // columnas de una palabra.
  .firma-detail__ver {
    display: block;
    margin: var(--op-space-3) 0 0;

    &-error {
      display: block;
      margin: var(--op-space-2) 0 0;
      font-size: var(--op-font-sm);
      line-height: var(--op-line-height);
      color: var(--op-error-fg);
      background: var(--op-error-bg);
      padding: var(--op-space-2) var(--op-space-3);
      border-radius: var(--op-radius);
    }
  }

  // Nota que explica QUÉ acto va a realizar el usuario en su turno.
  .firma-detail__rol-nota {
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);
    color: var(--op-info-fg);
    background: var(--op-info-bg);
    padding: var(--op-space-2) var(--op-space-3);
    border-radius: var(--op-radius);
    margin: 0 0 var(--op-space-3);
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

  .firmas-table__rol {
    display: block;
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.03em;

    &--firma {
      color: var(--op-primary-dark);
      font-weight: var(--op-weight-bold);
    }
  }
</style>
