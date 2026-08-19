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
  Módulo de Transferencias documentales (T-12, E12).

  Vista consolidada:
    - Tabla de transferencias con filtros por estado y tipo
    - Acciones guiadas: Nueva, Enviar, Recibir, Rechazar (con confirmación)
    - Ver FUID por transferencia en Modal

  Accesibilidad:
    - Labels y roles ARIA en todos los elementos interactivos
    - aria-busy durante cargas, <caption> y scope en tablas
    - Modales con focus trap (componente Modal)
    - Confirmación antes de toda acción de escritura (ADR-013)

  RBAC espejo del backend:
    - Acciones solo visibles si USUA_PERM_EXPEDIENTE
    - El backend re-valida en cada operación
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFecha } from "$lib/utils/fecha.js";
  import { permissionLevels } from "$lib/stores/auth.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  import Modal from "$lib/components/ui/Modal.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Panel principal", href: "/dashboard" }, { label: "Transferencias" }]);
  });

  // ---------------------------------------------------------------------------
  // Autorización (espejo del RBAC; backend re-valida siempre)
  // ---------------------------------------------------------------------------
  // A6 (revisión UX): las 4 acciones que gatea (Nueva/Enviar/Recibir/Rechazar)
  // son todas mutaciones con `require_permission("USUA_PERM_EXPEDIENTE",
  // min_crud=3)` en el backend (transferencias.py líneas 54/95/113/127) —
  // antes solo miraba presencia del permiso (incluida lectura).
  $: canManage = ($permissionLevels["USUA_PERM_EXPEDIENTE"] ?? 0) >= 3;

  // ---------------------------------------------------------------------------
  // Opciones de dominio (FormField options)
  // ---------------------------------------------------------------------------

  /** Tipos de transferencia documental (E12) */
  const TIPOS_TRANSFERENCIA_OPTIONS = [
    { value: "primaria", label: "Primaria (gestión → central)" },
    { value: "secundaria", label: "Secundaria (central → histórico)" },
  ];

  // ---------------------------------------------------------------------------
  // Datos iniciales del servidor
  // ---------------------------------------------------------------------------
  /** @type {any[]} */
  let transferencias = data.transferencias?.data ?? [];
  let cargaError = data.transferencias?.error ?? null;
  let loading = false;

  // ---------------------------------------------------------------------------
  // Filtros dinámicos
  // ---------------------------------------------------------------------------
  let filtroEstado = "";
  let filtroTipo = "";

  async function cargarTransferencias() {
    loading = true;
    cargaError = null;
    try {
      const params = new URLSearchParams();
      if (filtroEstado) params.set("estado", filtroEstado);
      if (filtroTipo) params.set("tipo", filtroTipo);
      const res = await fetch(`/transferencias/api${params.toString() ? `?${params}` : ""}`);
      const responseData = await res.json();
      if (!res.ok) throw responseData;
      transferencias = Array.isArray(responseData) ? responseData : (responseData.items ?? []);
    } catch (err) {
      cargaError = { detail: mensajeError(err, "No se pudieron cargar las transferencias.") };
      transferencias = [];
    } finally {
      loading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal: nueva transferencia
  // ---------------------------------------------------------------------------
  let showNuevaModal = false;
  let nuevaForm = {
    tipo: "",
    expediente_id: "",
    ubicacion_origen_id: "",
    ubicacion_destino_id: "",
    observacion: "",
  };
  let nuevaLoading = false;

  async function handleNuevaTransferencia() {
    if (!nuevaForm.tipo || !nuevaForm.expediente_id.trim()) {
      notifyError("Tipo y expediente_id son obligatorios.");
      return;
    }
    nuevaLoading = true;
    try {
      const res = await fetch("/transferencias/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tipo: nuevaForm.tipo,
          expediente_id: nuevaForm.expediente_id.trim(),
          ubicacion_origen_id: nuevaForm.ubicacion_origen_id.trim() || undefined,
          ubicacion_destino_id: nuevaForm.ubicacion_destino_id.trim() || undefined,
          observacion: nuevaForm.observacion.trim() || undefined,
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess("Transferencia creada correctamente.");
      showNuevaModal = false;
      nuevaForm = {
        tipo: "",
        expediente_id: "",
        ubicacion_origen_id: "",
        ubicacion_destino_id: "",
        observacion: "",
      };
      await cargarTransferencias();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo crear la transferencia."));
    } finally {
      nuevaLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Acciones de transición de estado (con confirmación)
  // ---------------------------------------------------------------------------

  // Modal de confirmación genérico para acciones de estado
  let showConfirmModal = false;
  let confirmTitle = "";
  let confirmMessage = "";
  let confirmAction = /** @type {(() => Promise<void>)|null} */ (null);
  let confirmLoading = false;

  function openConfirm(title, message, action) {
    confirmTitle = title;
    confirmMessage = message;
    confirmAction = action;
    showConfirmModal = true;
  }

  async function executeConfirm() {
    if (!confirmAction) return;
    confirmLoading = true;
    try {
      await confirmAction();
    } finally {
      confirmLoading = false;
      showConfirmModal = false;
    }
  }

  // Enviar
  function handleEnviar(id) {
    openConfirm(
      "Confirmar envío",
      "¿Enviar esta transferencia? El estado cambiará a 'Enviada'.",
      async () => {
        const res = await fetch(`/transferencias/api/${id}/enviar`, { method: "POST" });
        const result = await res.json().catch(() => ({ ok: true }));
        if (!res.ok) {
          notifyError(mensajeError(result, "No se pudo enviar la transferencia."));
          return;
        }
        notifySuccess("Transferencia enviada correctamente.");
        await cargarTransferencias();
      },
    );
  }

  // Recibir
  function handleRecibir(id) {
    openConfirm("Confirmar recepción", "¿Marcar esta transferencia como recibida?", async () => {
      const res = await fetch(`/transferencias/api/${id}/recibir`, { method: "POST" });
      const result = await res.json().catch(() => ({ ok: true }));
      if (!res.ok) {
        notifyError(mensajeError(result, "No se pudo recibir la transferencia."));
        return;
      }
      notifySuccess("Transferencia recibida correctamente.");
      await cargarTransferencias();
    });
  }

  // Rechazar (con observacion)
  let showRechazarModal = false;
  /** @type {string|null} */
  let rechazarId = null;
  let rechazarObservacion = "";
  let rechazarLoading = false;

  function openRechazarModal(id) {
    rechazarId = id;
    rechazarObservacion = "";
    showRechazarModal = true;
  }

  async function handleRechazar() {
    rechazarLoading = true;
    try {
      const res = await fetch(`/transferencias/api/${rechazarId}/rechazar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ observacion: rechazarObservacion.trim() || undefined }),
      });
      const result = await res.json().catch(() => ({ ok: true }));
      if (!res.ok) throw result;
      notifySuccess("Transferencia rechazada.");
      showRechazarModal = false;
      await cargarTransferencias();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo rechazar la transferencia."));
    } finally {
      rechazarLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal: FUID de transferencia
  // ---------------------------------------------------------------------------
  let showFuidModal = false;
  /** @type {any[]|null} */
  let fuidData = null;
  let fuidLoading = false;

  async function verFuid(id) {
    fuidData = null;
    showFuidModal = true;
    fuidLoading = true;
    try {
      const res = await fetch(`/transferencias/api/${id}/fuid`);
      const data = await res.json();
      if (!res.ok) throw data;
      fuidData = Array.isArray(data) ? data : (data.items ?? data.rows ?? [data]);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo obtener el FUID."));
      showFuidModal = false;
    } finally {
      fuidLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers de formato
  // ---------------------------------------------------------------------------
  /** @param {string|null|undefined} iso */
  function fmtDate(iso) {
    return formatearFecha(iso);
  }

  const ESTADO_MAP = {
    pendiente: { label: "Pendiente", status: "warning" },
    enviada: { label: "Enviada", status: "info" },
    recibida: { label: "Recibida", status: "success" },
    rechazada: { label: "Rechazada", status: "danger" },
  };

  const TIPO_MAP = {
    primaria: "Primaria",
    secundaria: "Secundaria",
  };
</script>

<svelte:head>
  <title>Transferencias — OrpycaMCP</title>
</svelte:head>

<!-- =========================================================================
     Workspace de transferencias
     ========================================================================= -->
<div class="tr-workspace">
  <!-- Cabecera -->
  <div class="tr-workspace__header">
    <h1 class="tr-workspace__title">
      <i class="fas fa-exchange-alt" aria-hidden="true"></i>
      Transferencias documentales
    </h1>
    {#if canManage}
      <Button variant="primary" on:click={() => (showNuevaModal = true)}>
        <i class="fas fa-plus" aria-hidden="true"></i>
        Nueva transferencia
      </Button>
    {/if}
  </div>

  <!-- Filtros -->
  <Card>
    <div class="tr-filters" role="search" aria-label="Filtros de transferencias">
      <div class="tr-filters__field">
        <label for="tr-filtro-estado" class="label">Estado</label>
        <div class="select">
          <select id="tr-filtro-estado" bind:value={filtroEstado} aria-label="Filtrar por estado">
            <option value="">Todos los estados</option>
            <option value="pendiente">Pendiente</option>
            <option value="enviada">Enviada</option>
            <option value="recibida">Recibida</option>
            <option value="rechazada">Rechazada</option>
          </select>
        </div>
      </div>
      <div class="tr-filters__field">
        <label for="tr-filtro-tipo" class="label">Tipo</label>
        <div class="select">
          <select id="tr-filtro-tipo" bind:value={filtroTipo} aria-label="Filtrar por tipo">
            <option value="">Todos los tipos</option>
            <option value="primaria">Primaria</option>
            <option value="secundaria">Secundaria</option>
          </select>
        </div>
      </div>
      <Button variant="primary" on:click={cargarTransferencias} disabled={loading}>
        {#if loading}
          <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
        {:else}
          <i class="fas fa-filter" aria-hidden="true"></i>
        {/if}
        Aplicar filtros
      </Button>
    </div>
  </Card>

  <!-- Tabla principal -->
  <Card>
    <div aria-live="polite" aria-atomic="false">
      {#if loading}
        <Loader label="Cargando transferencias…" />
      {:else if cargaError}
        <EmptyState
          icon="fas fa-exclamation-triangle"
          title="Error al cargar"
          description={mensajeError(cargaError, "Intenta recargar la página.")}
        />
      {:else if transferencias.length === 0}
        <EmptyState
          icon="fas fa-exchange-alt"
          title="Sin transferencias"
          description="No hay transferencias que coincidan con los filtros aplicados."
        />
      {:else}
        <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
        <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
        <div
          class="tr-table-wrap"
          aria-busy={loading}
          role="region"
          aria-label="Listado de transferencias documentales"
          tabindex="0"
        >
          <table class="table is-fullwidth is-hoverable">
            <caption class="is-sr-only">Listado de transferencias documentales</caption>
            <thead>
              <tr>
                <th scope="col">Tipo</th>
                <th scope="col">Expediente</th>
                <th scope="col">Origen</th>
                <th scope="col">Destino</th>
                <th scope="col">Estado</th>
                <th scope="col">Fecha</th>
                <th scope="col"><span class="is-sr-only">Acciones</span></th>
              </tr>
            </thead>
            <tbody>
              {#each transferencias as t (t.id)}
                {@const estadoInfo = ESTADO_MAP[t.estado] ?? { label: t.estado, status: "neutral" }}
                <tr>
                  <td>
                    <span class="tag is-light">
                      {TIPO_MAP[t.tipo] ?? t.tipo ?? "—"}
                    </span>
                  </td>
                  <td>
                    <code class="tr-expte-code">{t.expediente_id ?? t.expediente?.code ?? "—"}</code
                    >
                  </td>
                  <td class="tr-cell-truncate">
                    {t.ubicacion_origen?.nombre ?? t.ubicacion_origen_id ?? "—"}
                  </td>
                  <td class="tr-cell-truncate">
                    {t.ubicacion_destino?.nombre ?? t.ubicacion_destino_id ?? "—"}
                  </td>
                  <td>
                    <StatusChip status={estadoInfo.status} label={estadoInfo.label} dot />
                  </td>
                  <td>{fmtDate(t.created_at ?? t.fecha)}</td>
                  <td class="tr-actions-cell">
                    <!-- Detalle: acta de entrega en apariencia de certificado -->
                    <Button variant="ghost" size="sm" href={`/transferencias/${t.id}`}>
                      <i class="fas fa-file-signature" aria-hidden="true"></i>
                      <span>Detalle / acta</span>
                    </Button>

                    <!-- Ver FUID siempre disponible -->
                    <button
                      class="button is-small is-light"
                      on:click={() => verFuid(t.id)}
                      aria-label="Ver FUID de la transferencia"
                    >
                      <i class="fas fa-file-alt" aria-hidden="true"></i>
                      <span>FUID</span>
                    </button>

                    {#if canManage}
                      <!-- Enviar: solo si pendiente -->
                      {#if t.estado === "pendiente"}
                        <button
                          class="button is-small is-info is-light"
                          on:click={() => handleEnviar(t.id)}
                          aria-label="Enviar transferencia"
                        >
                          <i class="fas fa-paper-plane" aria-hidden="true"></i>
                          <span>Enviar</span>
                        </button>
                      {/if}

                      <!-- Recibir: solo si enviada -->
                      {#if t.estado === "enviada"}
                        <button
                          class="button is-small is-success is-light"
                          on:click={() => handleRecibir(t.id)}
                          aria-label="Marcar transferencia como recibida"
                        >
                          <i class="fas fa-check-circle" aria-hidden="true"></i>
                          <span>Recibir</span>
                        </button>
                      {/if}

                      <!-- Rechazar: si pendiente o enviada -->
                      {#if t.estado === "pendiente" || t.estado === "enviada"}
                        <button
                          class="button is-small is-danger is-light"
                          on:click={() => openRechazarModal(t.id)}
                          aria-label="Rechazar transferencia"
                        >
                          <i class="fas fa-times-circle" aria-hidden="true"></i>
                          <span>Rechazar</span>
                        </button>
                      {/if}
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </div>
  </Card>
</div>

<!-- =========================================================================
     Modales
     ========================================================================= -->

<!-- Modal: nueva transferencia -->
<Modal
  open={showNuevaModal}
  title="Nueva transferencia documental"
  on:close={() => (showNuevaModal = false)}
>
  <div class="tr-form">
    <FormField
      id="tr-tipo"
      label="Tipo *"
      type="select"
      required
      placeholder="— Selecciona tipo —"
      bind:value={nuevaForm.tipo}
      options={TIPOS_TRANSFERENCIA_OPTIONS}
    />
    <FormField
      id="tr-expte-id"
      label="ID del expediente *"
      type="text"
      required
      placeholder="UUID del expediente"
      bind:value={nuevaForm.expediente_id}
    />
    <FormField
      id="tr-origen-id"
      label="Ubicación origen"
      type="text"
      placeholder="UUID de la ubicación origen (opcional)"
      bind:value={nuevaForm.ubicacion_origen_id}
    />
    <FormField
      id="tr-destino-id"
      label="Ubicación destino"
      type="text"
      placeholder="UUID de la ubicación destino (opcional)"
      bind:value={nuevaForm.ubicacion_destino_id}
    />
    <FormField
      id="tr-observacion"
      label="Observación"
      type="textarea"
      placeholder="Observaciones sobre la transferencia (opcional)"
      rows={3}
      bind:value={nuevaForm.observacion}
    />
  </div>
  <div slot="footer" class="tr-modal-footer">
    <Button variant="secondary" on:click={() => (showNuevaModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={handleNuevaTransferencia} disabled={nuevaLoading}>
      {#if nuevaLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Crear transferencia
    </Button>
  </div>
</Modal>

<!-- Modal: confirmación de acción -->
<Modal
  open={showConfirmModal}
  title={confirmTitle}
  size="sm"
  on:close={() => (showConfirmModal = false)}
>
  <p>{confirmMessage}</p>
  <div slot="footer" class="tr-modal-footer">
    <Button variant="secondary" on:click={() => (showConfirmModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={executeConfirm} disabled={confirmLoading}>
      {#if confirmLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Confirmar
    </Button>
  </div>
</Modal>

<!-- Modal: rechazar con observación -->
<Modal
  open={showRechazarModal}
  title="Rechazar transferencia"
  size="sm"
  on:close={() => (showRechazarModal = false)}
>
  <p>Esta acción rechazará la transferencia y quedará registrada en la auditoría del sistema.</p>
  <div class="tr-form" style="margin-top: var(--op-space-3)">
    <FormField
      id="rechazo-obs"
      label="Motivo del rechazo"
      type="textarea"
      placeholder="Observación sobre el rechazo (opcional)"
      rows={3}
      bind:value={rechazarObservacion}
    />
  </div>
  <div slot="footer" class="tr-modal-footer">
    <Button variant="secondary" on:click={() => (showRechazarModal = false)}>Cancelar</Button>
    <Button variant="danger" on:click={handleRechazar} disabled={rechazarLoading}>
      {#if rechazarLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Rechazar transferencia
    </Button>
  </div>
</Modal>

<!-- Modal: FUID de la transferencia -->
<Modal
  open={showFuidModal}
  title="FUID de la transferencia"
  size="lg"
  on:close={() => (showFuidModal = false)}
>
  {#if fuidLoading}
    <Loader label="Cargando FUID…" />
  {:else if !fuidData || fuidData.length === 0}
    <EmptyState
      icon="fas fa-file-alt"
      title="Sin registros FUID"
      description="Esta transferencia no tiene datos FUID disponibles."
    />
  {:else}
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      class="tr-table-wrap"
      role="region"
      aria-label="Formato Único de Inventario Documental de la transferencia"
      tabindex="0"
    >
      <table class="table is-fullwidth is-hoverable">
        <caption class="is-sr-only"
          >Formato Único de Inventario Documental de la transferencia</caption
        >
        <thead>
          <tr>
            <th scope="col">Orden</th>
            <th scope="col">Código expediente</th>
            <th scope="col">Asunto</th>
            <th scope="col">Serie</th>
            <th scope="col">Signatura</th>
            <th scope="col">Soporte</th>
            <th scope="col">Folios</th>
          </tr>
        </thead>
        <tbody>
          {#each fuidData as row, i (row.id ?? row.expediente_id ?? i)}
            <tr>
              <td>{i + 1}</td>
              <td><code>{row.codigo_expediente ?? row.expediente?.code ?? "—"}</code></td>
              <td>{row.asunto ?? row.expediente?.name ?? "—"}</td>
              <td>{row.serie ?? row.trd_serie?.nombre ?? "—"}</td>
              <td>{row.signatura ?? "—"}</td>
              <td>{row.soporte ?? "—"}</td>
              <td>{row.num_folios ?? row.folios ?? "—"}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
  <div slot="footer" class="tr-modal-footer">
    <Button variant="secondary" on:click={() => (showFuidModal = false)}>Cerrar</Button>
  </div>
</Modal>

<style lang="scss">
  .tr-workspace {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
    padding: var(--op-space-5);

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
    }

    &__title {
      font-size: var(--op-font-xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      display: flex;
      align-items: center;
      gap: var(--op-space-2);

      i {
        color: var(--op-primary);
      }
    }
  }

  // Filtros
  .tr-filters {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-3);
    flex-wrap: wrap;

    &__field {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      min-width: 160px;
    }
  }

  // Tabla
  .tr-table-wrap {
    overflow-x: auto;

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }

    .table {
      border-collapse: collapse;
      width: 100%;

      th {
        background: var(--op-bg);
        color: var(--op-text-secondary);
        font-size: var(--op-font-xs);
        font-weight: var(--op-weight-bold);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        padding: var(--op-space-2) var(--op-space-3);
        border-bottom: 2px solid var(--op-border);
      }

      td {
        padding: var(--op-space-2) var(--op-space-3);
        border-bottom: 1px solid var(--op-border);
        color: var(--op-text);
        font-size: var(--op-font-sm);
        vertical-align: middle;
      }
    }
  }

  .tr-actions-cell {
    display: flex;
    gap: var(--op-space-1);
    justify-content: flex-end;
    align-items: center;
    flex-wrap: wrap;
    white-space: nowrap;
  }

  .tr-cell-truncate {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tr-expte-code {
    font-family: monospace;
    font-size: var(--op-font-xs);
    background: var(--op-bg);
    padding: 1px var(--op-space-1);
    border-radius: var(--op-radius-sm);
  }

  // Formulario en modal
  .tr-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding: var(--op-space-2) 0;
  }

  .tr-modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--op-space-2);
  }
</style>
