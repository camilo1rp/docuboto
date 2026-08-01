import { c as create_ssr_component, s as subscribe, v as validate_component, b as add_attribute, d as each, e as escape } from "../../../../chunks/ssr.js";
import { p as permissions } from "../../../../chunks/auth.js";
import { M as Modal } from "../../../../chunks/Modal.js";
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
/* empty css                                                      */
import { B as Button } from "../../../../chunks/Button.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
import { F as FormField } from "../../../../chunks/FormField.js";
import { C as Card } from "../../../../chunks/Card.js";
const css = {
  code: ".tr-workspace.svelte-9jxy65.svelte-9jxy65{display:flex;flex-direction:column;gap:var(--op-space-4);padding:var(--op-space-5)}.tr-workspace__header.svelte-9jxy65.svelte-9jxy65{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:var(--op-space-3)}.tr-workspace__title.svelte-9jxy65.svelte-9jxy65{font-size:var(--op-font-xl);font-weight:var(--op-weight-bold);color:var(--op-text);display:flex;align-items:center;gap:var(--op-space-2)}.tr-workspace__title.svelte-9jxy65 i.svelte-9jxy65{color:var(--op-primary)}.tr-filters.svelte-9jxy65.svelte-9jxy65{display:flex;align-items:flex-end;gap:var(--op-space-3);flex-wrap:wrap}.tr-filters__field.svelte-9jxy65.svelte-9jxy65{display:flex;flex-direction:column;gap:var(--op-space-1);min-width:160px}.tr-table-wrap.svelte-9jxy65.svelte-9jxy65{overflow-x:auto}.tr-table-wrap.svelte-9jxy65 .table.svelte-9jxy65{border-collapse:collapse;width:100%}.tr-table-wrap.svelte-9jxy65 .table th.svelte-9jxy65{background:var(--op-bg);color:var(--op-text-secondary);font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);text-transform:uppercase;letter-spacing:0.04em;padding:var(--op-space-2) var(--op-space-3);border-bottom:2px solid var(--op-border)}.tr-table-wrap.svelte-9jxy65 .table td.svelte-9jxy65{padding:var(--op-space-2) var(--op-space-3);border-bottom:1px solid var(--op-border);color:var(--op-text);font-size:var(--op-font-sm);vertical-align:middle}.tr-actions-cell.svelte-9jxy65.svelte-9jxy65{display:flex;gap:var(--op-space-1);justify-content:flex-end;align-items:center;flex-wrap:wrap;white-space:nowrap}.tr-cell-truncate.svelte-9jxy65.svelte-9jxy65{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tr-expte-code.svelte-9jxy65.svelte-9jxy65{font-family:monospace;font-size:var(--op-font-xs);background:var(--op-bg);padding:1px var(--op-space-1);border-radius:var(--op-radius-sm)}.tr-form.svelte-9jxy65.svelte-9jxy65{display:flex;flex-direction:column;gap:var(--op-space-3);padding:var(--op-space-2) 0}.tr-modal-footer.svelte-9jxy65.svelte-9jxy65{display:flex;justify-content:flex-end;gap:var(--op-space-2)}",
  map: '{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  Módulo de Transferencias documentales (T-12, E12).\\n\\n  Vista consolidada:\\n    - Tabla de transferencias con filtros por estado y tipo\\n    - Acciones guiadas: Nueva, Enviar, Recibir, Rechazar (con confirmación)\\n    - Ver FUID por transferencia en Modal\\n\\n  Accesibilidad:\\n    - Labels y roles ARIA en todos los elementos interactivos\\n    - aria-busy durante cargas, <caption> y scope en tablas\\n    - Modales con focus trap (componente Modal)\\n    - Confirmación antes de toda acción de escritura (ADR-013)\\n\\n  RBAC espejo del backend:\\n    - Acciones solo visibles si USUA_PERM_EXPEDIENTE\\n    - El backend re-valida en cada operación\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { permissions } from \\"$lib/stores/auth.js\\";\\n  import { notifySuccess, notifyError } from \\"$lib/stores/notifications.js\\";\\n\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Loader from \\"$lib/components/ui/Loader.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import Card from \\"$lib/components/ui/Card.svelte\\";\\n\\n  /** @type {import(\'./$types\').PageData} */\\n  export let data;\\n\\n  // ---------------------------------------------------------------------------\\n  // Breadcrumbs\\n  // ---------------------------------------------------------------------------\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Panel principal\\", href: \\"/dashboard\\" }, { label: \\"Transferencias\\" }]);\\n  });\\n\\n  // ---------------------------------------------------------------------------\\n  // Autorización (espejo del RBAC; backend re-valida siempre)\\n  // ---------------------------------------------------------------------------\\n  $: canManage = $permissions.includes(\\"USUA_PERM_EXPEDIENTE\\");\\n\\n  // ---------------------------------------------------------------------------\\n  // Opciones de dominio (FormField options)\\n  // ---------------------------------------------------------------------------\\n\\n  /** Tipos de transferencia documental (E12) */\\n  const TIPOS_TRANSFERENCIA_OPTIONS = [\\n    { value: \\"primaria\\", label: \\"Primaria (gestión → central)\\" },\\n    { value: \\"secundaria\\", label: \\"Secundaria (central → histórico)\\" },\\n  ];\\n\\n  // ---------------------------------------------------------------------------\\n  // Datos iniciales del servidor\\n  // ---------------------------------------------------------------------------\\n  /** @type {any[]} */\\n  let transferencias = data.transferencias?.data ?? [];\\n  let cargaError = data.transferencias?.error ?? null;\\n  let loading = false;\\n\\n  // ---------------------------------------------------------------------------\\n  // Filtros dinámicos\\n  // ---------------------------------------------------------------------------\\n  let filtroEstado = \\"\\";\\n  let filtroTipo = \\"\\";\\n\\n  async function cargarTransferencias() {\\n    loading = true;\\n    cargaError = null;\\n    try {\\n      const params = new URLSearchParams();\\n      if (filtroEstado) params.set(\\"estado\\", filtroEstado);\\n      if (filtroTipo) params.set(\\"tipo\\", filtroTipo);\\n      const res = await fetch(`/transferencias/api${params.toString() ? `?${params}` : \\"\\"}`);\\n      const responseData = await res.json();\\n      if (!res.ok) throw responseData;\\n      transferencias = Array.isArray(responseData) ? responseData : (responseData.items ?? []);\\n    } catch (err) {\\n      cargaError = { detail: err?.detail ?? \\"No se pudieron cargar las transferencias.\\" };\\n      transferencias = [];\\n    } finally {\\n      loading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal: nueva transferencia\\n  // ---------------------------------------------------------------------------\\n  let showNuevaModal = false;\\n  let nuevaForm = {\\n    tipo: \\"\\",\\n    expediente_id: \\"\\",\\n    ubicacion_origen_id: \\"\\",\\n    ubicacion_destino_id: \\"\\",\\n    observacion: \\"\\",\\n  };\\n  let nuevaLoading = false;\\n\\n  async function handleNuevaTransferencia() {\\n    if (!nuevaForm.tipo || !nuevaForm.expediente_id.trim()) {\\n      notifyError(\\"Tipo y expediente_id son obligatorios.\\");\\n      return;\\n    }\\n    nuevaLoading = true;\\n    try {\\n      const res = await fetch(\\"/transferencias/api\\", {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          tipo: nuevaForm.tipo,\\n          expediente_id: nuevaForm.expediente_id.trim(),\\n          ubicacion_origen_id: nuevaForm.ubicacion_origen_id.trim() || undefined,\\n          ubicacion_destino_id: nuevaForm.ubicacion_destino_id.trim() || undefined,\\n          observacion: nuevaForm.observacion.trim() || undefined,\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      notifySuccess(\\"Transferencia creada correctamente.\\");\\n      showNuevaModal = false;\\n      nuevaForm = {\\n        tipo: \\"\\",\\n        expediente_id: \\"\\",\\n        ubicacion_origen_id: \\"\\",\\n        ubicacion_destino_id: \\"\\",\\n        observacion: \\"\\",\\n      };\\n      await cargarTransferencias();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo crear la transferencia.\\");\\n    } finally {\\n      nuevaLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Acciones de transición de estado (con confirmación)\\n  // ---------------------------------------------------------------------------\\n\\n  // Modal de confirmación genérico para acciones de estado\\n  let showConfirmModal = false;\\n  let confirmTitle = \\"\\";\\n  let confirmMessage = \\"\\";\\n  let confirmAction = /** @type {(() => Promise<void>)|null} */ (null);\\n  let confirmLoading = false;\\n\\n  function openConfirm(title, message, action) {\\n    confirmTitle = title;\\n    confirmMessage = message;\\n    confirmAction = action;\\n    showConfirmModal = true;\\n  }\\n\\n  async function executeConfirm() {\\n    if (!confirmAction) return;\\n    confirmLoading = true;\\n    try {\\n      await confirmAction();\\n    } finally {\\n      confirmLoading = false;\\n      showConfirmModal = false;\\n    }\\n  }\\n\\n  // Enviar\\n  function handleEnviar(id) {\\n    openConfirm(\\n      \\"Confirmar envío\\",\\n      \\"¿Enviar esta transferencia? El estado cambiará a \'Enviada\'.\\",\\n      async () => {\\n        const res = await fetch(`/transferencias/api/${id}/enviar`, { method: \\"POST\\" });\\n        const result = await res.json().catch(() => ({ ok: true }));\\n        if (!res.ok) {\\n          notifyError(result?.detail ?? \\"No se pudo enviar la transferencia.\\");\\n          return;\\n        }\\n        notifySuccess(\\"Transferencia enviada correctamente.\\");\\n        await cargarTransferencias();\\n      },\\n    );\\n  }\\n\\n  // Recibir\\n  function handleRecibir(id) {\\n    openConfirm(\\"Confirmar recepción\\", \\"¿Marcar esta transferencia como recibida?\\", async () => {\\n      const res = await fetch(`/transferencias/api/${id}/recibir`, { method: \\"POST\\" });\\n      const result = await res.json().catch(() => ({ ok: true }));\\n      if (!res.ok) {\\n        notifyError(result?.detail ?? \\"No se pudo recibir la transferencia.\\");\\n        return;\\n      }\\n      notifySuccess(\\"Transferencia recibida correctamente.\\");\\n      await cargarTransferencias();\\n    });\\n  }\\n\\n  // Rechazar (con observacion)\\n  let showRechazarModal = false;\\n  /** @type {string|null} */\\n  let rechazarId = null;\\n  let rechazarObservacion = \\"\\";\\n  let rechazarLoading = false;\\n\\n  function openRechazarModal(id) {\\n    rechazarId = id;\\n    rechazarObservacion = \\"\\";\\n    showRechazarModal = true;\\n  }\\n\\n  async function handleRechazar() {\\n    rechazarLoading = true;\\n    try {\\n      const res = await fetch(`/transferencias/api/${rechazarId}/rechazar`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({ observacion: rechazarObservacion.trim() || undefined }),\\n      });\\n      const result = await res.json().catch(() => ({ ok: true }));\\n      if (!res.ok) throw result;\\n      notifySuccess(\\"Transferencia rechazada.\\");\\n      showRechazarModal = false;\\n      await cargarTransferencias();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo rechazar la transferencia.\\");\\n    } finally {\\n      rechazarLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal: FUID de transferencia\\n  // ---------------------------------------------------------------------------\\n  let showFuidModal = false;\\n  /** @type {any[]|null} */\\n  let fuidData = null;\\n  let fuidLoading = false;\\n\\n  async function verFuid(id) {\\n    fuidData = null;\\n    showFuidModal = true;\\n    fuidLoading = true;\\n    try {\\n      const res = await fetch(`/transferencias/api/${id}/fuid`);\\n      const data = await res.json();\\n      if (!res.ok) throw data;\\n      fuidData = Array.isArray(data) ? data : (data.items ?? data.rows ?? [data]);\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo obtener el FUID.\\");\\n      showFuidModal = false;\\n    } finally {\\n      fuidLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de formato\\n  // ---------------------------------------------------------------------------\\n  /** @param {string|null|undefined} iso */\\n  function fmtDate(iso) {\\n    if (!iso) return \\"—\\";\\n    return new Date(iso).toLocaleDateString(\\"es-CO\\", {\\n      day: \\"2-digit\\",\\n      month: \\"2-digit\\",\\n      year: \\"numeric\\",\\n    });\\n  }\\n\\n  const ESTADO_MAP = {\\n    pendiente: { label: \\"Pendiente\\", status: \\"warning\\" },\\n    enviada: { label: \\"Enviada\\", status: \\"info\\" },\\n    recibida: { label: \\"Recibida\\", status: \\"success\\" },\\n    rechazada: { label: \\"Rechazada\\", status: \\"danger\\" },\\n  };\\n\\n  const TIPO_MAP = {\\n    primaria: \\"Primaria\\",\\n    secundaria: \\"Secundaria\\",\\n  };\\n<\/script>\\n\\n<svelte:head>\\n  <title>Transferencias — OrpycaMCP</title>\\n</svelte:head>\\n\\n<!-- =========================================================================\\n     Workspace de transferencias\\n     ========================================================================= -->\\n<div class=\\"tr-workspace\\">\\n  <!-- Cabecera -->\\n  <div class=\\"tr-workspace__header\\">\\n    <h1 class=\\"tr-workspace__title\\">\\n      <i class=\\"fas fa-exchange-alt\\" aria-hidden=\\"true\\"></i>\\n      Transferencias documentales\\n    </h1>\\n    {#if canManage}\\n      <Button variant=\\"primary\\" on:click={() => (showNuevaModal = true)}>\\n        <i class=\\"fas fa-plus\\" aria-hidden=\\"true\\"></i>\\n        Nueva transferencia\\n      </Button>\\n    {/if}\\n  </div>\\n\\n  <!-- Filtros -->\\n  <Card>\\n    <div class=\\"tr-filters\\" role=\\"search\\" aria-label=\\"Filtros de transferencias\\">\\n      <div class=\\"tr-filters__field\\">\\n        <label for=\\"tr-filtro-estado\\" class=\\"label\\">Estado</label>\\n        <div class=\\"select\\">\\n          <select id=\\"tr-filtro-estado\\" bind:value={filtroEstado} aria-label=\\"Filtrar por estado\\">\\n            <option value=\\"\\">Todos los estados</option>\\n            <option value=\\"pendiente\\">Pendiente</option>\\n            <option value=\\"enviada\\">Enviada</option>\\n            <option value=\\"recibida\\">Recibida</option>\\n            <option value=\\"rechazada\\">Rechazada</option>\\n          </select>\\n        </div>\\n      </div>\\n      <div class=\\"tr-filters__field\\">\\n        <label for=\\"tr-filtro-tipo\\" class=\\"label\\">Tipo</label>\\n        <div class=\\"select\\">\\n          <select id=\\"tr-filtro-tipo\\" bind:value={filtroTipo} aria-label=\\"Filtrar por tipo\\">\\n            <option value=\\"\\">Todos los tipos</option>\\n            <option value=\\"primaria\\">Primaria</option>\\n            <option value=\\"secundaria\\">Secundaria</option>\\n          </select>\\n        </div>\\n      </div>\\n      <Button variant=\\"primary\\" on:click={cargarTransferencias} disabled={loading}>\\n        {#if loading}\\n          <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n        {:else}\\n          <i class=\\"fas fa-filter\\" aria-hidden=\\"true\\"></i>\\n        {/if}\\n        Aplicar filtros\\n      </Button>\\n    </div>\\n  </Card>\\n\\n  <!-- Tabla principal -->\\n  <Card>\\n    <div aria-live=\\"polite\\" aria-atomic=\\"false\\">\\n      {#if loading}\\n        <Loader label=\\"Cargando transferencias…\\" />\\n      {:else if cargaError}\\n        <EmptyState\\n          icon=\\"fas fa-exclamation-triangle\\"\\n          title=\\"Error al cargar\\"\\n          description={cargaError.detail ?? \\"Intenta recargar la página.\\"}\\n        />\\n      {:else if transferencias.length === 0}\\n        <EmptyState\\n          icon=\\"fas fa-exchange-alt\\"\\n          title=\\"Sin transferencias\\"\\n          description=\\"No hay transferencias que coincidan con los filtros aplicados.\\"\\n        />\\n      {:else}\\n        <div class=\\"tr-table-wrap\\" aria-busy={loading}>\\n          <table class=\\"table is-fullwidth is-hoverable\\">\\n            <caption class=\\"is-sr-only\\">Listado de transferencias documentales</caption>\\n            <thead>\\n              <tr>\\n                <th scope=\\"col\\">Tipo</th>\\n                <th scope=\\"col\\">Expediente</th>\\n                <th scope=\\"col\\">Origen</th>\\n                <th scope=\\"col\\">Destino</th>\\n                <th scope=\\"col\\">Estado</th>\\n                <th scope=\\"col\\">Fecha</th>\\n                <th scope=\\"col\\"><span class=\\"is-sr-only\\">Acciones</span></th>\\n              </tr>\\n            </thead>\\n            <tbody>\\n              {#each transferencias as t (t.id)}\\n                {@const estadoInfo = ESTADO_MAP[t.estado] ?? { label: t.estado, status: \\"neutral\\" }}\\n                <tr>\\n                  <td>\\n                    <span class=\\"tag is-light\\">\\n                      {TIPO_MAP[t.tipo] ?? t.tipo ?? \\"—\\"}\\n                    </span>\\n                  </td>\\n                  <td>\\n                    <code class=\\"tr-expte-code\\">{t.expediente_id ?? t.expediente?.code ?? \\"—\\"}</code\\n                    >\\n                  </td>\\n                  <td class=\\"tr-cell-truncate\\">\\n                    {t.ubicacion_origen?.nombre ?? t.ubicacion_origen_id ?? \\"—\\"}\\n                  </td>\\n                  <td class=\\"tr-cell-truncate\\">\\n                    {t.ubicacion_destino?.nombre ?? t.ubicacion_destino_id ?? \\"—\\"}\\n                  </td>\\n                  <td>\\n                    <StatusChip status={estadoInfo.status} label={estadoInfo.label} dot />\\n                  </td>\\n                  <td>{fmtDate(t.created_at ?? t.fecha)}</td>\\n                  <td class=\\"tr-actions-cell\\">\\n                    <!-- Ver FUID siempre disponible -->\\n                    <button\\n                      class=\\"button is-small is-light\\"\\n                      on:click={() => verFuid(t.id)}\\n                      aria-label=\\"Ver FUID de la transferencia\\"\\n                    >\\n                      <i class=\\"fas fa-file-alt\\" aria-hidden=\\"true\\"></i>\\n                      <span>FUID</span>\\n                    </button>\\n\\n                    {#if canManage}\\n                      <!-- Enviar: solo si pendiente -->\\n                      {#if t.estado === \\"pendiente\\"}\\n                        <button\\n                          class=\\"button is-small is-info is-light\\"\\n                          on:click={() => handleEnviar(t.id)}\\n                          aria-label=\\"Enviar transferencia\\"\\n                        >\\n                          <i class=\\"fas fa-paper-plane\\" aria-hidden=\\"true\\"></i>\\n                          <span>Enviar</span>\\n                        </button>\\n                      {/if}\\n\\n                      <!-- Recibir: solo si enviada -->\\n                      {#if t.estado === \\"enviada\\"}\\n                        <button\\n                          class=\\"button is-small is-success is-light\\"\\n                          on:click={() => handleRecibir(t.id)}\\n                          aria-label=\\"Marcar transferencia como recibida\\"\\n                        >\\n                          <i class=\\"fas fa-check-circle\\" aria-hidden=\\"true\\"></i>\\n                          <span>Recibir</span>\\n                        </button>\\n                      {/if}\\n\\n                      <!-- Rechazar: si pendiente o enviada -->\\n                      {#if t.estado === \\"pendiente\\" || t.estado === \\"enviada\\"}\\n                        <button\\n                          class=\\"button is-small is-danger is-light\\"\\n                          on:click={() => openRechazarModal(t.id)}\\n                          aria-label=\\"Rechazar transferencia\\"\\n                        >\\n                          <i class=\\"fas fa-times-circle\\" aria-hidden=\\"true\\"></i>\\n                          <span>Rechazar</span>\\n                        </button>\\n                      {/if}\\n                    {/if}\\n                  </td>\\n                </tr>\\n              {/each}\\n            </tbody>\\n          </table>\\n        </div>\\n      {/if}\\n    </div>\\n  </Card>\\n</div>\\n\\n<!-- =========================================================================\\n     Modales\\n     ========================================================================= -->\\n\\n<!-- Modal: nueva transferencia -->\\n<Modal\\n  open={showNuevaModal}\\n  title=\\"Nueva transferencia documental\\"\\n  on:close={() => (showNuevaModal = false)}\\n>\\n  <div class=\\"tr-form\\">\\n    <FormField\\n      id=\\"tr-tipo\\"\\n      label=\\"Tipo *\\"\\n      type=\\"select\\"\\n      required\\n      placeholder=\\"— Selecciona tipo —\\"\\n      bind:value={nuevaForm.tipo}\\n      options={TIPOS_TRANSFERENCIA_OPTIONS}\\n    />\\n    <FormField\\n      id=\\"tr-expte-id\\"\\n      label=\\"ID del expediente *\\"\\n      type=\\"text\\"\\n      required\\n      placeholder=\\"UUID del expediente\\"\\n      bind:value={nuevaForm.expediente_id}\\n    />\\n    <FormField\\n      id=\\"tr-origen-id\\"\\n      label=\\"Ubicación origen\\"\\n      type=\\"text\\"\\n      placeholder=\\"UUID de la ubicación origen (opcional)\\"\\n      bind:value={nuevaForm.ubicacion_origen_id}\\n    />\\n    <FormField\\n      id=\\"tr-destino-id\\"\\n      label=\\"Ubicación destino\\"\\n      type=\\"text\\"\\n      placeholder=\\"UUID de la ubicación destino (opcional)\\"\\n      bind:value={nuevaForm.ubicacion_destino_id}\\n    />\\n    <FormField\\n      id=\\"tr-observacion\\"\\n      label=\\"Observación\\"\\n      type=\\"textarea\\"\\n      placeholder=\\"Observaciones sobre la transferencia (opcional)\\"\\n      rows={3}\\n      bind:value={nuevaForm.observacion}\\n    />\\n  </div>\\n  <div slot=\\"footer\\" class=\\"tr-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showNuevaModal = false)}>Cancelar</Button>\\n    <Button variant=\\"primary\\" on:click={handleNuevaTransferencia} disabled={nuevaLoading}>\\n      {#if nuevaLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Crear transferencia\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: confirmación de acción -->\\n<Modal\\n  open={showConfirmModal}\\n  title={confirmTitle}\\n  size=\\"sm\\"\\n  on:close={() => (showConfirmModal = false)}\\n>\\n  <p>{confirmMessage}</p>\\n  <div slot=\\"footer\\" class=\\"tr-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showConfirmModal = false)}>Cancelar</Button>\\n    <Button variant=\\"primary\\" on:click={executeConfirm} disabled={confirmLoading}>\\n      {#if confirmLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Confirmar\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: rechazar con observación -->\\n<Modal\\n  open={showRechazarModal}\\n  title=\\"Rechazar transferencia\\"\\n  size=\\"sm\\"\\n  on:close={() => (showRechazarModal = false)}\\n>\\n  <p>Esta acción rechazará la transferencia y quedará registrada en la auditoría del sistema.</p>\\n  <div class=\\"tr-form\\" style=\\"margin-top: var(--op-space-3)\\">\\n    <FormField\\n      id=\\"rechazo-obs\\"\\n      label=\\"Motivo del rechazo\\"\\n      type=\\"textarea\\"\\n      placeholder=\\"Observación sobre el rechazo (opcional)\\"\\n      rows={3}\\n      bind:value={rechazarObservacion}\\n    />\\n  </div>\\n  <div slot=\\"footer\\" class=\\"tr-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showRechazarModal = false)}>Cancelar</Button>\\n    <Button variant=\\"danger\\" on:click={handleRechazar} disabled={rechazarLoading}>\\n      {#if rechazarLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Rechazar transferencia\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: FUID de la transferencia -->\\n<Modal\\n  open={showFuidModal}\\n  title=\\"FUID de la transferencia\\"\\n  size=\\"lg\\"\\n  on:close={() => (showFuidModal = false)}\\n>\\n  {#if fuidLoading}\\n    <Loader label=\\"Cargando FUID…\\" />\\n  {:else if !fuidData || fuidData.length === 0}\\n    <EmptyState\\n      icon=\\"fas fa-file-alt\\"\\n      title=\\"Sin registros FUID\\"\\n      description=\\"Esta transferencia no tiene datos FUID disponibles.\\"\\n    />\\n  {:else}\\n    <div class=\\"tr-table-wrap\\">\\n      <table class=\\"table is-fullwidth is-hoverable\\">\\n        <caption class=\\"is-sr-only\\"\\n          >Formato Único de Inventario Documental de la transferencia</caption\\n        >\\n        <thead>\\n          <tr>\\n            <th scope=\\"col\\">Orden</th>\\n            <th scope=\\"col\\">Código expediente</th>\\n            <th scope=\\"col\\">Asunto</th>\\n            <th scope=\\"col\\">Serie</th>\\n            <th scope=\\"col\\">Signatura</th>\\n            <th scope=\\"col\\">Soporte</th>\\n            <th scope=\\"col\\">Folios</th>\\n          </tr>\\n        </thead>\\n        <tbody>\\n          {#each fuidData as row, i (row.id ?? row.expediente_id ?? i)}\\n            <tr>\\n              <td>{i + 1}</td>\\n              <td><code>{row.codigo_expediente ?? row.expediente?.code ?? \\"—\\"}</code></td>\\n              <td>{row.asunto ?? row.expediente?.name ?? \\"—\\"}</td>\\n              <td>{row.serie ?? row.trd_serie?.nombre ?? \\"—\\"}</td>\\n              <td>{row.signatura ?? \\"—\\"}</td>\\n              <td>{row.soporte ?? \\"—\\"}</td>\\n              <td>{row.num_folios ?? row.folios ?? \\"—\\"}</td>\\n            </tr>\\n          {/each}\\n        </tbody>\\n      </table>\\n    </div>\\n  {/if}\\n  <div slot=\\"footer\\" class=\\"tr-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showFuidModal = false)}>Cerrar</Button>\\n  </div>\\n</Modal>\\n\\n<style lang=\\"scss\\">.tr-workspace {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n  padding: var(--op-space-5);\\n}\\n.tr-workspace__header {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-3);\\n}\\n.tr-workspace__title {\\n  font-size: var(--op-font-xl);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n}\\n.tr-workspace__title i {\\n  color: var(--op-primary);\\n}\\n\\n.tr-filters {\\n  display: flex;\\n  align-items: flex-end;\\n  gap: var(--op-space-3);\\n  flex-wrap: wrap;\\n}\\n.tr-filters__field {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-1);\\n  min-width: 160px;\\n}\\n\\n.tr-table-wrap {\\n  overflow-x: auto;\\n}\\n.tr-table-wrap .table {\\n  border-collapse: collapse;\\n  width: 100%;\\n}\\n.tr-table-wrap .table th {\\n  background: var(--op-bg);\\n  color: var(--op-text-secondary);\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  text-transform: uppercase;\\n  letter-spacing: 0.04em;\\n  padding: var(--op-space-2) var(--op-space-3);\\n  border-bottom: 2px solid var(--op-border);\\n}\\n.tr-table-wrap .table td {\\n  padding: var(--op-space-2) var(--op-space-3);\\n  border-bottom: 1px solid var(--op-border);\\n  color: var(--op-text);\\n  font-size: var(--op-font-sm);\\n  vertical-align: middle;\\n}\\n\\n.tr-actions-cell {\\n  display: flex;\\n  gap: var(--op-space-1);\\n  justify-content: flex-end;\\n  align-items: center;\\n  flex-wrap: wrap;\\n  white-space: nowrap;\\n}\\n\\n.tr-cell-truncate {\\n  max-width: 180px;\\n  overflow: hidden;\\n  text-overflow: ellipsis;\\n  white-space: nowrap;\\n}\\n\\n.tr-expte-code {\\n  font-family: monospace;\\n  font-size: var(--op-font-xs);\\n  background: var(--op-bg);\\n  padding: 1px var(--op-space-1);\\n  border-radius: var(--op-radius-sm);\\n}\\n\\n.tr-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n  padding: var(--op-space-2) 0;\\n}\\n\\n.tr-modal-footer {\\n  display: flex;\\n  justify-content: flex-end;\\n  gap: var(--op-space-2);\\n}</style>\\n"],"names":[],"mappings":"AAunBmB,yCAAc,CAC/B,OAAO,CAAE,IAAI,CACb,cAAc,CAAE,MAAM,CACtB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,OAAO,CAAE,IAAI,YAAY,CAC3B,CACA,iDAAsB,CACpB,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,MAAM,CACnB,eAAe,CAAE,aAAa,CAC9B,SAAS,CAAE,IAAI,CACf,GAAG,CAAE,IAAI,YAAY,CACvB,CACA,gDAAqB,CACnB,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,MAAM,CACnB,GAAG,CAAE,IAAI,YAAY,CACvB,CACA,kCAAoB,CAAC,eAAE,CACrB,KAAK,CAAE,IAAI,YAAY,CACzB,CAEA,uCAAY,CACV,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,QAAQ,CACrB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,SAAS,CAAE,IACb,CACA,8CAAmB,CACjB,OAAO,CAAE,IAAI,CACb,cAAc,CAAE,MAAM,CACtB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,SAAS,CAAE,KACb,CAEA,0CAAe,CACb,UAAU,CAAE,IACd,CACA,4BAAc,CAAC,oBAAO,CACpB,eAAe,CAAE,QAAQ,CACzB,KAAK,CAAE,IACT,CACA,4BAAc,CAAC,MAAM,CAAC,gBAAG,CACvB,UAAU,CAAE,IAAI,OAAO,CAAC,CACxB,KAAK,CAAE,IAAI,mBAAmB,CAAC,CAC/B,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,cAAc,CAAE,SAAS,CACzB,cAAc,CAAE,MAAM,CACtB,OAAO,CAAE,IAAI,YAAY,CAAC,CAAC,IAAI,YAAY,CAAC,CAC5C,aAAa,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,WAAW,CAC1C,CACA,4BAAc,CAAC,MAAM,CAAC,gBAAG,CACvB,OAAO,CAAE,IAAI,YAAY,CAAC,CAAC,IAAI,YAAY,CAAC,CAC5C,aAAa,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,WAAW,CAAC,CACzC,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,cAAc,CAAE,MAClB,CAEA,4CAAiB,CACf,OAAO,CAAE,IAAI,CACb,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,eAAe,CAAE,QAAQ,CACzB,WAAW,CAAE,MAAM,CACnB,SAAS,CAAE,IAAI,CACf,WAAW,CAAE,MACf,CAEA,6CAAkB,CAChB,SAAS,CAAE,KAAK,CAChB,QAAQ,CAAE,MAAM,CAChB,aAAa,CAAE,QAAQ,CACvB,WAAW,CAAE,MACf,CAEA,0CAAe,CACb,WAAW,CAAE,SAAS,CACtB,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,UAAU,CAAE,IAAI,OAAO,CAAC,CACxB,OAAO,CAAE,GAAG,CAAC,IAAI,YAAY,CAAC,CAC9B,aAAa,CAAE,IAAI,cAAc,CACnC,CAEA,oCAAS,CACP,OAAO,CAAE,IAAI,CACb,cAAc,CAAE,MAAM,CACtB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,OAAO,CAAE,IAAI,YAAY,CAAC,CAAC,CAC7B,CAEA,4CAAiB,CACf,OAAO,CAAE,IAAI,CACb,eAAe,CAAE,QAAQ,CACzB,GAAG,CAAE,IAAI,YAAY,CACvB"}'
};
function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("es-CO", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric"
  });
}
const Page = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let canManage;
  let $permissions, $$unsubscribe_permissions;
  $$unsubscribe_permissions = subscribe(permissions, (value) => $permissions = value);
  let { data } = $$props;
  const TIPOS_TRANSFERENCIA_OPTIONS = [
    {
      value: "primaria",
      label: "Primaria (gestión → central)"
    },
    {
      value: "secundaria",
      label: "Secundaria (central → histórico)"
    }
  ];
  let transferencias = data.transferencias?.data ?? [];
  let cargaError = data.transferencias?.error ?? null;
  let loading = false;
  let showNuevaModal = false;
  let nuevaForm = {
    tipo: "",
    expediente_id: "",
    ubicacion_origen_id: "",
    ubicacion_destino_id: "",
    observacion: ""
  };
  let nuevaLoading = false;
  let showConfirmModal = false;
  let confirmTitle = "";
  let confirmMessage = "";
  let confirmLoading = false;
  let showRechazarModal = false;
  let rechazarObservacion = "";
  let rechazarLoading = false;
  let showFuidModal = false;
  const ESTADO_MAP = {
    pendiente: { label: "Pendiente", status: "warning" },
    enviada: { label: "Enviada", status: "info" },
    recibida: { label: "Recibida", status: "success" },
    rechazada: { label: "Rechazada", status: "danger" }
  };
  const TIPO_MAP = {
    primaria: "Primaria",
    secundaria: "Secundaria"
  };
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    canManage = $permissions.includes("USUA_PERM_EXPEDIENTE");
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-15d0og3_START -->${$$result.title = `<title>Transferencias — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-15d0og3_END -->`, ""}  <div class="tr-workspace svelte-9jxy65"> <div class="tr-workspace__header svelte-9jxy65"><h1 class="tr-workspace__title svelte-9jxy65" data-svelte-h="svelte-14mt3y7"><i class="fas fa-exchange-alt svelte-9jxy65" aria-hidden="true"></i>
      Transferencias documentales</h1> ${canManage ? `${validate_component(Button, "Button").$$render($$result, { variant: "primary" }, {}, {
      default: () => {
        return `<i class="fas fa-plus" aria-hidden="true"></i>
        Nueva transferencia`;
      }
    })}` : ``}</div>  ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `<div class="tr-filters svelte-9jxy65" role="search" aria-label="Filtros de transferencias"><div class="tr-filters__field svelte-9jxy65"><label for="tr-filtro-estado" class="label" data-svelte-h="svelte-tbetzp">Estado</label> <div class="select"><select id="tr-filtro-estado" aria-label="Filtrar por estado"><option value="" data-svelte-h="svelte-3662q4">Todos los estados</option><option value="pendiente" data-svelte-h="svelte-122sejy">Pendiente</option><option value="enviada" data-svelte-h="svelte-kldgv2">Enviada</option><option value="recibida" data-svelte-h="svelte-1g0dv5e">Recibida</option><option value="rechazada" data-svelte-h="svelte-r55tj0">Rechazada</option></select></div></div> <div class="tr-filters__field svelte-9jxy65"><label for="tr-filtro-tipo" class="label" data-svelte-h="svelte-e5jifd">Tipo</label> <div class="select"><select id="tr-filtro-tipo" aria-label="Filtrar por tipo"><option value="" data-svelte-h="svelte-109wric">Todos los tipos</option><option value="primaria" data-svelte-h="svelte-muz2sa">Primaria</option><option value="secundaria" data-svelte-h="svelte-1duk2l2">Secundaria</option></select></div></div> ${validate_component(Button, "Button").$$render($$result, { variant: "primary", disabled: loading }, {}, {
          default: () => {
            return `${`<i class="fas fa-filter" aria-hidden="true"></i>`}
        Aplicar filtros`;
          }
        })}</div>`;
      }
    })}  ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `<div aria-live="polite" aria-atomic="false">${`${cargaError ? `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-exclamation-triangle",
            title: "Error al cargar",
            description: cargaError.detail ?? "Intenta recargar la página."
          },
          {},
          {}
        )}` : `${transferencias.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-exchange-alt",
            title: "Sin transferencias",
            description: "No hay transferencias que coincidan con los filtros aplicados."
          },
          {},
          {}
        )}` : `<div class="tr-table-wrap svelte-9jxy65"${add_attribute("aria-busy", loading, 0)}><table class="table is-fullwidth is-hoverable svelte-9jxy65"><caption class="is-sr-only" data-svelte-h="svelte-12ok7sj">Listado de transferencias documentales</caption> <thead data-svelte-h="svelte-ew1o1z"><tr><th scope="col" class="svelte-9jxy65">Tipo</th> <th scope="col" class="svelte-9jxy65">Expediente</th> <th scope="col" class="svelte-9jxy65">Origen</th> <th scope="col" class="svelte-9jxy65">Destino</th> <th scope="col" class="svelte-9jxy65">Estado</th> <th scope="col" class="svelte-9jxy65">Fecha</th> <th scope="col" class="svelte-9jxy65"><span class="is-sr-only">Acciones</span></th></tr></thead> <tbody>${each(transferencias, (t) => {
          let estadoInfo = ESTADO_MAP[t.estado] ?? { label: t.estado, status: "neutral" };
          return ` <tr><td class="svelte-9jxy65"><span class="tag is-light">${escape(TIPO_MAP[t.tipo] ?? t.tipo ?? "—")} </span></td> <td class="svelte-9jxy65"><code class="tr-expte-code svelte-9jxy65">${escape(t.expediente_id ?? t.expediente?.code ?? "—")}</code></td> <td class="tr-cell-truncate svelte-9jxy65">${escape(t.ubicacion_origen?.nombre ?? t.ubicacion_origen_id ?? "—")}</td> <td class="tr-cell-truncate svelte-9jxy65">${escape(t.ubicacion_destino?.nombre ?? t.ubicacion_destino_id ?? "—")}</td> <td class="svelte-9jxy65">${validate_component(StatusChip, "StatusChip").$$render(
            $$result,
            {
              status: estadoInfo.status,
              label: estadoInfo.label,
              dot: true
            },
            {},
            {}
          )}</td> <td class="svelte-9jxy65">${escape(fmtDate(t.created_at ?? t.fecha))}</td> <td class="tr-actions-cell svelte-9jxy65"> <button class="button is-small is-light" aria-label="Ver FUID de la transferencia" data-svelte-h="svelte-rlze84"><i class="fas fa-file-alt" aria-hidden="true"></i> <span>FUID</span></button> ${canManage ? ` ${t.estado === "pendiente" ? `<button class="button is-small is-info is-light" aria-label="Enviar transferencia" data-svelte-h="svelte-1646e2v"><i class="fas fa-paper-plane" aria-hidden="true"></i> <span>Enviar</span> </button>` : ``}  ${t.estado === "enviada" ? `<button class="button is-small is-success is-light" aria-label="Marcar transferencia como recibida" data-svelte-h="svelte-v33hiy"><i class="fas fa-check-circle" aria-hidden="true"></i> <span>Recibir</span> </button>` : ``}  ${t.estado === "pendiente" || t.estado === "enviada" ? `<button class="button is-small is-danger is-light" aria-label="Rechazar transferencia" data-svelte-h="svelte-s8tmg"><i class="fas fa-times-circle" aria-hidden="true"></i> <span>Rechazar</span> </button>` : ``}` : ``}</td> </tr>`;
        })}</tbody></table></div>`}`}`}</div>`;
      }
    })}</div>   ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: showNuevaModal,
        title: "Nueva transferencia documental"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="tr-modal-footer svelte-9jxy65">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              disabled: nuevaLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Crear transferencia`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<div class="tr-form svelte-9jxy65">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "tr-tipo",
              label: "Tipo *",
              type: "select",
              required: true,
              placeholder: "— Selecciona tipo —",
              options: TIPOS_TRANSFERENCIA_OPTIONS,
              value: nuevaForm.tipo
            },
            {
              value: ($$value) => {
                nuevaForm.tipo = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "tr-expte-id",
              label: "ID del expediente *",
              type: "text",
              required: true,
              placeholder: "UUID del expediente",
              value: nuevaForm.expediente_id
            },
            {
              value: ($$value) => {
                nuevaForm.expediente_id = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "tr-origen-id",
              label: "Ubicación origen",
              type: "text",
              placeholder: "UUID de la ubicación origen (opcional)",
              value: nuevaForm.ubicacion_origen_id
            },
            {
              value: ($$value) => {
                nuevaForm.ubicacion_origen_id = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "tr-destino-id",
              label: "Ubicación destino",
              type: "text",
              placeholder: "UUID de la ubicación destino (opcional)",
              value: nuevaForm.ubicacion_destino_id
            },
            {
              value: ($$value) => {
                nuevaForm.ubicacion_destino_id = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "tr-observacion",
              label: "Observación",
              type: "textarea",
              placeholder: "Observaciones sobre la transferencia (opcional)",
              rows: 3,
              value: nuevaForm.observacion
            },
            {
              value: ($$value) => {
                nuevaForm.observacion = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: showConfirmModal,
        title: confirmTitle,
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="tr-modal-footer svelte-9jxy65">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              disabled: confirmLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Confirmar`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<p>${escape(confirmMessage)}</p>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: showRechazarModal,
        title: "Rechazar transferencia",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="tr-modal-footer svelte-9jxy65">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "danger",
              disabled: rechazarLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Rechazar transferencia`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<p data-svelte-h="svelte-k40xkc">Esta acción rechazará la transferencia y quedará registrada en la auditoría del sistema.</p> <div class="tr-form svelte-9jxy65" style="margin-top: var(--op-space-3)">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "rechazo-obs",
              label: "Motivo del rechazo",
              type: "textarea",
              placeholder: "Observación sobre el rechazo (opcional)",
              rows: 3,
              value: rechazarObservacion
            },
            {
              value: ($$value) => {
                rechazarObservacion = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: showFuidModal,
        title: "FUID de la transferencia",
        size: "lg"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="tr-modal-footer svelte-9jxy65">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cerrar`;
            }
          })}</div>`;
        },
        default: () => {
          return `${`${`${validate_component(EmptyState, "EmptyState").$$render(
            $$result,
            {
              icon: "fas fa-file-alt",
              title: "Sin registros FUID",
              description: "Esta transferencia no tiene datos FUID disponibles."
            },
            {},
            {}
          )}`}`}`;
        }
      }
    )}`;
  } while (!$$settled);
  $$unsubscribe_permissions();
  return $$rendered;
});
export {
  Page as default
};
