import { c as create_ssr_component, s as subscribe, v as validate_component, d as each, b as add_attribute, e as escape } from "../../../../chunks/ssr.js";
import { p as permissions } from "../../../../chunks/auth.js";
import { D as DataTable } from "../../../../chunks/DataTable.js";
import { D as Drawer } from "../../../../chunks/Drawer.js";
import { M as Modal } from "../../../../chunks/Modal.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
/* empty css                                                      */
import { B as Button } from "../../../../chunks/Button.js";
import { F as FormField } from "../../../../chunks/FormField.js";
const css = {
  code: ':root{--op-primary:#2a8c3a;--op-primary-dark:#1c6b33;--op-primary-light:#dceed7;--op-on-primary:#ffffff;--op-accent:#8dbf3c;--op-brand-700:#173a22;--op-brand-400:#4fa84b;--op-text:#232624;--op-text-secondary:#6e7370;--op-border:#e7eae2;--op-bg:#fbfcf8;--op-surface:#ffffff;--op-success:#2a8c3a;--op-warning:#c8881c;--op-error:#c4452c;--op-info:#2b6cb0;--op-success-bg:#dceed7;--op-success-fg:#1c6b33;--op-warning-bg:#fdf3dc;--op-warning-fg:#7a5700;--op-error-bg:#fce8e8;--op-error-fg:#8b1a1a;--op-info-bg:#e0eef7;--op-info-fg:#1e4e6b;--op-error-strong-bg:#3b0000;--op-error-strong-fg:#ffdddd;--op-focus-ring:rgba(42, 140, 58, 0.15);--op-error-ring:rgba(196, 69, 44, 0.15);--op-font-family:Public Sans, Segoe UI, system-ui, -apple-system, sans-serif;--op-font-display:Space Grotesk, Segoe UI, system-ui, -apple-system, sans-serif;--op-font-size-base:15px;--op-line-height:1.6;--op-font-xs:12px;--op-font-sm:13px;--op-font-base:15px;--op-font-md:16px;--op-font-lg:18px;--op-font-xl:22px;--op-font-2xl:28px;--op-font-size-display:46px;--op-font-mono:12px;--op-weight-regular:400;--op-weight-medium:500;--op-weight-bold:600;--op-weight-display:700;--op-space-1:4px;--op-space-2:8px;--op-space-3:12px;--op-space-4:16px;--op-space-5:24px;--op-space-6:32px;--op-space-7:48px;--op-space-8:64px;--op-radius-sm:6px;--op-radius:10px;--op-radius-lg:16px;--op-radius-full:999px;--op-shadow-xs:0 1px 2px rgba(0, 0, 0, 0.04);--op-shadow-sm:0 2px 8px rgba(0, 0, 0, 0.05);--op-shadow:0 8px 24px rgba(0, 0, 0, 0.06);--op-shadow-lg:0 8px 24px rgba(0, 0, 0, 0.06);--op-transition:200ms cubic-bezier(0.2, 0.8, 0.2, 1);--op-motion-fast:120ms;--op-motion-base:200ms;--op-motion-slow:300ms;--op-motion-easing:cubic-bezier(0.2, 0.8, 0.2, 1);--op-sidebar-width:264px;--op-sidebar-collapsed-width:64px;--op-topbar-height:52px;--op-breadcrumb-height:36px}@media(prefers-reduced-motion: reduce){:root{--op-motion-fast:0ms;--op-motion-base:0ms;--op-motion-slow:0ms;--op-transition:0ms}.svelte-1kch2b0.svelte-1kch2b0,.svelte-1kch2b0.svelte-1kch2b0::before,.svelte-1kch2b0.svelte-1kch2b0::after{animation-duration:0.01ms !important;animation-iteration-count:1 !important;transition-duration:0.01ms !important;scroll-behavior:auto !important}}.envios-page.svelte-1kch2b0.svelte-1kch2b0{padding:var(--op-space-5);max-width:1100px}.envios-page__header.svelte-1kch2b0.svelte-1kch2b0{display:flex;flex-direction:column;gap:var(--op-space-3);margin-bottom:var(--op-space-4)}.envios-page__title-row.svelte-1kch2b0.svelte-1kch2b0{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:var(--op-space-3)}.envios-page__title.svelte-1kch2b0.svelte-1kch2b0{font-size:var(--op-font-lg);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}.envios-page__filters.svelte-1kch2b0.svelte-1kch2b0{display:flex;flex-wrap:wrap;gap:var(--op-space-2)}.envios-page__filter-btn.svelte-1kch2b0.svelte-1kch2b0{padding:var(--op-space-1) var(--op-space-3);font-size:var(--op-font-sm);font-family:var(--op-font-family);border:1px solid var(--op-border);border-radius:var(--op-radius-full);background:var(--op-surface);color:var(--op-text-secondary);cursor:pointer;transition:background var(--op-transition), color var(--op-transition), border-color var(--op-transition)}.envios-page__filter-btn.svelte-1kch2b0.svelte-1kch2b0:hover{background:var(--op-primary-light);color:var(--op-primary-dark);border-color:var(--op-primary-light)}.envios-page__filter-btn--active.svelte-1kch2b0.svelte-1kch2b0{background:var(--op-primary);color:var(--op-surface);border-color:var(--op-primary)}.envios-page__filter-btn--active.svelte-1kch2b0.svelte-1kch2b0:hover{background:var(--op-primary-dark);border-color:var(--op-primary-dark)}.envios-page__filter-btn.svelte-1kch2b0.svelte-1kch2b0:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}.envios-planilla-bar.svelte-1kch2b0.svelte-1kch2b0{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:var(--op-space-3);background:var(--op-primary-light);border:1px solid var(--op-primary);border-radius:var(--op-radius);padding:var(--op-space-2) var(--op-space-4);margin-bottom:var(--op-space-3)}.envios-planilla-bar__count.svelte-1kch2b0.svelte-1kch2b0{display:flex;align-items:center;gap:var(--op-space-2);font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);color:var(--op-primary-dark)}.envios-selectall.svelte-1kch2b0.svelte-1kch2b0{display:flex;align-items:center;padding:var(--op-space-2) var(--op-space-4);border-bottom:1px solid var(--op-border);background:var(--op-bg)}.envios-selectall__label.svelte-1kch2b0.svelte-1kch2b0{display:flex;align-items:center;gap:var(--op-space-2);cursor:pointer;font-size:var(--op-font-sm);color:var(--op-text-secondary);user-select:none}.envios-selectall__input.svelte-1kch2b0.svelte-1kch2b0{width:16px;height:16px;accent-color:var(--op-primary);cursor:pointer;flex-shrink:0}.envios-selectall__input.svelte-1kch2b0.svelte-1kch2b0:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}.envios-selectall__text.svelte-1kch2b0.svelte-1kch2b0{font-weight:var(--op-weight-medium)}.envios-table-wrap.svelte-1kch2b0.svelte-1kch2b0{border:1px solid var(--op-border);border-radius:var(--op-radius);overflow:hidden}.envios-table__check.svelte-1kch2b0.svelte-1kch2b0{padding-left:var(--op-space-3);padding-right:var(--op-space-2)}.envios-table__check-label.svelte-1kch2b0.svelte-1kch2b0{display:flex;align-items:center;cursor:pointer}.envios-table__check-input.svelte-1kch2b0.svelte-1kch2b0{width:16px;height:16px;accent-color:var(--op-primary);cursor:pointer}.envios-table__check-input.svelte-1kch2b0.svelte-1kch2b0:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}.envios-table__operador.svelte-1kch2b0.svelte-1kch2b0{font-weight:var(--op-weight-medium);color:var(--op-text)}.envios-table__guia.svelte-1kch2b0.svelte-1kch2b0{font-family:monospace;font-size:var(--op-font-xs);color:var(--op-text-secondary)}.envios-table__destinatario.svelte-1kch2b0.svelte-1kch2b0{max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--op-text-secondary)}.envios-table__date.svelte-1kch2b0.svelte-1kch2b0{white-space:nowrap;color:var(--op-text-secondary)}.envios-table__actions.svelte-1kch2b0.svelte-1kch2b0{width:80px}.envios-table__na.svelte-1kch2b0.svelte-1kch2b0{color:var(--op-text-secondary);font-style:italic}.envio-detail__loader.svelte-1kch2b0.svelte-1kch2b0{display:flex;justify-content:center;padding:var(--op-space-6)}.envio-detail__data.svelte-1kch2b0.svelte-1kch2b0{display:flex;flex-direction:column;gap:var(--op-space-1);margin:0 0 var(--op-space-4)}.envio-detail__row.svelte-1kch2b0.svelte-1kch2b0{display:grid;grid-template-columns:120px 1fr;gap:var(--op-space-2);padding:var(--op-space-1) 0;border-bottom:1px solid var(--op-border);font-size:var(--op-font-sm)}.envio-detail__row.svelte-1kch2b0.svelte-1kch2b0:last-child{border-bottom:none}.envio-detail__row.svelte-1kch2b0 dt.svelte-1kch2b0{color:var(--op-text-secondary);font-weight:var(--op-weight-medium)}.envio-detail__row.svelte-1kch2b0 dd.svelte-1kch2b0{color:var(--op-text);margin:0;word-break:break-all}.envio-detail__mono.svelte-1kch2b0.svelte-1kch2b0{font-family:monospace;font-size:var(--op-font-xs);color:var(--op-text-secondary)}.envio-detail__no-eventos.svelte-1kch2b0.svelte-1kch2b0{font-size:var(--op-font-sm);color:var(--op-text-secondary);padding:var(--op-space-3) 0;margin:0}.envio-detail__divider.svelte-1kch2b0.svelte-1kch2b0{border:none;border-top:1px solid var(--op-border);margin:var(--op-space-2) 0}.envio-detail__actions.svelte-1kch2b0.svelte-1kch2b0{display:flex;flex-direction:column;gap:var(--op-space-3);padding-top:var(--op-space-2)}.envio-detail__actions-title.svelte-1kch2b0.svelte-1kch2b0{font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);color:var(--op-text-secondary);margin:0}.envio-timeline.svelte-1kch2b0.svelte-1kch2b0{margin-bottom:var(--op-space-4)}.envio-timeline__title.svelte-1kch2b0.svelte-1kch2b0{font-size:var(--op-font-sm);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0 0 var(--op-space-3)}.envio-timeline__list.svelte-1kch2b0.svelte-1kch2b0{list-style:none;margin:0;padding:0;position:relative}.envio-timeline__list.svelte-1kch2b0.svelte-1kch2b0::before{content:"";position:absolute;left:7px;top:0;bottom:0;width:2px;background:var(--op-border)}.envio-timeline__item.svelte-1kch2b0.svelte-1kch2b0{display:flex;gap:var(--op-space-3);padding-bottom:var(--op-space-3);position:relative}.envio-timeline__dot.svelte-1kch2b0.svelte-1kch2b0{flex-shrink:0;width:16px;height:16px;border-radius:50%;background:var(--op-border);border:2px solid var(--op-surface);margin-top:2px;z-index:1}.envio-timeline__dot--last.svelte-1kch2b0.svelte-1kch2b0{background:var(--op-primary)}.envio-timeline__content.svelte-1kch2b0.svelte-1kch2b0{flex:1;display:flex;flex-direction:column;gap:var(--op-space-1)}.envio-timeline__row.svelte-1kch2b0.svelte-1kch2b0{display:flex;align-items:center;gap:var(--op-space-2);flex-wrap:wrap}.envio-timeline__ts.svelte-1kch2b0.svelte-1kch2b0{font-size:var(--op-font-xs);color:var(--op-text-secondary)}.envio-timeline__desc.svelte-1kch2b0.svelte-1kch2b0{font-size:var(--op-font-xs);color:var(--op-text-secondary);margin:0;font-style:italic}.envio-modal-form.svelte-1kch2b0.svelte-1kch2b0{display:flex;flex-direction:column;gap:var(--op-space-4)}',
  map: '{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /envios — Bandeja de envíos postales + planilla de despacho (T-12e).\\n\\n  Criterios de hecho:\\n    - Listar envíos transversales paginados, filtrar por estado.\\n    - Ver detalle (drawer) con historial de eventos del envío.\\n    - Crear nuevo envío: radicado_id + operador + destinatario + dirección.\\n    - Cambiar estado de un envío (registrado → en_transito → entregado…).\\n    - Registrar devolución con causal de lista fija (T-12e).\\n    - Seleccionar envíos (checkbox) y exportar planilla de despacho en CSV (cliente).\\n\\n  RBAC (espejo del backend, D-14):\\n    - Ver lista, detalle → PERM_RADI_SALIDA\\n    - Crear, cambiar estado, registrar devolución → PERM_RADI_SALIDA\\n    El backend re-valida; la UI solo filtra visibilidad.\\n\\n  D-05 — access_token NUNCA en cliente:\\n    Todas las llamadas van a /envios/api[/id/accion] que leen locals.accessToken\\n    en el servidor. El token nunca llega al JS del cliente.\\n\\n  Planilla de despacho (CSV):\\n    Generada completamente en el cliente vía Blob/URL.createObjectURL.\\n    Sin llamada al servidor. Campos: N.°, Radicado, Operador, Guía, Estado,\\n    Destinatario, Dirección, Fecha.\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { permissions } from \\"$lib/stores/auth.js\\";\\n  import { notifyError, notifySuccess } from \\"$lib/stores/notifications.js\\";\\n\\n  import DataTable from \\"$lib/components/ui/DataTable.svelte\\";\\n  import Drawer from \\"$lib/components/ui/Drawer.svelte\\";\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import Loader from \\"$lib/components/ui/Loader.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n\\n  /** @type {import(\'./$types\').PageData} */\\n  export let data;\\n\\n  // ---------------------------------------------------------------------------\\n  // Permisos (espejo del RBAC del backend, D-14)\\n  // ---------------------------------------------------------------------------\\n  $: hasPerm = $permissions.includes(\\"PERM_RADI_SALIDA\\");\\n\\n  // ---------------------------------------------------------------------------\\n  // Estado de la lista\\n  // ---------------------------------------------------------------------------\\n\\n  /** @type {import(\'$lib/types/api.js\').ShipmentResponse[]} */\\n  let shipments = data.shipments ?? [];\\n  let total = data.total ?? 0;\\n  let loading = false;\\n  let filterEstado = data.initialFilters?.estado ?? \\"\\";\\n  let currentPage = data.initialFilters?.page ?? 1;\\n  const pageSize = 20;\\n\\n  // ---------------------------------------------------------------------------\\n  // Planilla de despacho (multi-select)\\n  // ---------------------------------------------------------------------------\\n\\n  /** @type {Set<string>} */\\n  let selectedIds = new Set();\\n\\n  $: selectedCount = selectedIds.size;\\n  $: selectedShipments = shipments.filter((s) => selectedIds.has(s.id));\\n  $: allPageSelected =\\n    shipments.length > 0 && shipments.every((s) => selectedIds.has(s.id));\\n  // Estado indeterminado del checkbox \\"seleccionar todos\\": algunos pero no todos.\\n  $: allIndeterminate = selectedCount > 0 && !allPageSelected;\\n\\n  function toggleRow(id) {\\n    if (selectedIds.has(id)) {\\n      selectedIds.delete(id);\\n    } else {\\n      selectedIds.add(id);\\n    }\\n    selectedIds = selectedIds; // trigger reactivity\\n  }\\n\\n  function toggleAll() {\\n    if (allPageSelected) {\\n      shipments.forEach((s) => selectedIds.delete(s.id));\\n    } else {\\n      shipments.forEach((s) => selectedIds.add(s.id));\\n    }\\n    selectedIds = selectedIds;\\n  }\\n\\n  /**\\n   * Genera y descarga la planilla de despacho en CSV.\\n   * Generación completamente cliente-side vía Blob + URL.createObjectURL.\\n   * No requiere llamada al servidor (solo se usa data ya cargada en memoria).\\n   */\\n  function exportarCSV() {\\n    if (selectedShipments.length === 0) {\\n      notifyError(\\"Seleccione al menos un envío para exportar.\\");\\n      return;\\n    }\\n\\n    const HEADERS = [\\n      \\"N.°\\",\\n      \\"Radicado ID\\",\\n      \\"Operador\\",\\n      \\"Guía\\",\\n      \\"Estado\\",\\n      \\"Destinatario\\",\\n      \\"Dirección\\",\\n      \\"Fecha de registro\\",\\n    ];\\n\\n    const escapeCell = (/** @type {string|null|undefined} */ v) => {\\n      let s = v ?? \\"\\";\\n      // Neutralizar prefijos de fórmula (=, +, -, @, tab, CR) antes de envolver\\n      // en comillas, para prevenir inyección DDE/CSV en Excel y LibreOffice.\\n      if (/^[=+\\\\-@\\\\t\\\\r]/.test(s)) s = \\"\'\\" + s;\\n      // Envolver en comillas dobles si contiene comas, comillas o saltos de línea.\\n      if (/[\\",\\\\n\\\\r]/.test(s)) return `\\"${s.replace(/\\"/g, \'\\"\\"\')}\\"`;\\n      return s;\\n    };\\n\\n    const rows = selectedShipments.map((s, i) => [\\n      String(i + 1),\\n      escapeCell(s.radicado_id),\\n      escapeCell(OPERADOR_LABELS[s.operador] ?? s.operador),\\n      escapeCell(s.guia),\\n      escapeCell(ESTADO_LABEL[s.estado] ?? s.estado),\\n      escapeCell(s.destinatario),\\n      escapeCell(s.direccion),\\n      escapeCell(formatDate(s.created_at)),\\n    ]);\\n\\n    const csv =\\n      HEADERS.map(escapeCell).join(\\",\\") +\\n      \\"\\\\r\\\\n\\" +\\n      rows.map((r) => r.join(\\",\\")).join(\\"\\\\r\\\\n\\");\\n\\n    const blob = new Blob([\\"\uFEFF\\" + csv], { type: \\"text/csv;charset=utf-8\\" });\\n    const url = URL.createObjectURL(blob);\\n    const a = document.createElement(\\"a\\");\\n    a.href = url;\\n    a.download = `planilla-despacho-${new Date().toISOString().slice(0, 10)}.csv`;\\n    a.click();\\n    URL.revokeObjectURL(url);\\n\\n    notifySuccess(`Planilla exportada (${selectedShipments.length} envíos).`);\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Drawer de detalle\\n  // ---------------------------------------------------------------------------\\n  let drawerOpen = false;\\n  /** @type {import(\'$lib/types/api.js\').ShipmentResponse|null} */\\n  let selectedShipment = null;\\n  let detailLoading = false;\\n\\n  async function openDetail(shipment) {\\n    selectedShipment = shipment;\\n    drawerOpen = true;\\n    // Refrescar el detalle para obtener eventos actualizados\\n    detailLoading = true;\\n    try {\\n      const res = await fetch(`/envios/api/${shipment.id}`);\\n      const data = await res.json();\\n      if (res.ok) selectedShipment = data;\\n    } catch {\\n      // silencioso; mostramos el dato de la lista\\n    } finally {\\n      detailLoading = false;\\n    }\\n  }\\n\\n  function closeDrawer() {\\n    drawerOpen = false;\\n    selectedShipment = null;\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal: cambiar estado\\n  // ---------------------------------------------------------------------------\\n  let modalEstado = false;\\n  let fEstado = \\"\\";\\n  let fEstadoDescripcion = \\"\\";\\n  let fEstadoError = \\"\\";\\n  let actionLoading = false;\\n\\n  function abrirModalEstado() {\\n    if (!selectedShipment) return;\\n    fEstado = selectedShipment.estado;\\n    fEstadoDescripcion = \\"\\";\\n    fEstadoError = \\"\\";\\n    // IMPORTANTE #3: cerrar el drawer antes de abrir el modal para evitar dos\\n    // trampas de foco y que Escape cierre ambos diálogos a la vez.\\n    // selectedShipment se mantiene en memoria para usarlo en cambiarEstado().\\n    drawerOpen = false;\\n    modalEstado = true;\\n  }\\n\\n  async function cambiarEstado() {\\n    if (!selectedShipment) return;\\n    fEstadoError = \\"\\";\\n    if (!fEstado) {\\n      fEstadoError = \\"Seleccione el nuevo estado.\\";\\n      return;\\n    }\\n\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(`/envios/api/${selectedShipment.id}/estado`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({ estado: fEstado, descripcion: fEstadoDescripcion.trim() || null }),\\n      });\\n      const responseData = await res.json();\\n      if (!res.ok) {\\n        notifyError(responseData.detail ?? \\"No se pudo cambiar el estado.\\");\\n        return;\\n      }\\n      // Actualizar en la lista y el drawer\\n      shipments = shipments.map((s) => (s.id === selectedShipment.id ? responseData : s));\\n      selectedShipment = responseData;\\n      modalEstado = false;\\n      notifySuccess(\\"Estado actualizado.\\");\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal: devolución con causal\\n  // ---------------------------------------------------------------------------\\n  const CAUSALES_DEVOLUCION = [\\n    \\"Dirección incorrecta\\",\\n    \\"Destinatario desconocido\\",\\n    \\"Rehusado\\",\\n    \\"No reclamado\\",\\n    \\"Otro\\",\\n  ];\\n  // Nota: la causal estructurada con catálogo propio es roadmap del backend.\\n  // Por ahora la causal se envía en el campo `descripcion` con prefijo \\"Causal: <x> — <nota>\\".\\n\\n  let modalDevolucion = false;\\n  let fDevCausal = \\"\\";\\n  let fDevNota = \\"\\";\\n  let fDevCausalError = \\"\\";\\n\\n  function abrirModalDevolucion() {\\n    fDevCausal = \\"\\";\\n    fDevNota = \\"\\";\\n    fDevCausalError = \\"\\";\\n    // IMPORTANTE #3: cerrar el drawer antes de abrir el modal (mismo razonamiento\\n    // que abrirModalEstado: evitar dos trampas de foco activas simultáneamente).\\n    drawerOpen = false;\\n    modalDevolucion = true;\\n  }\\n\\n  async function registrarDevolucion() {\\n    if (!selectedShipment) return;\\n    fDevCausalError = \\"\\";\\n    if (!fDevCausal) {\\n      fDevCausalError = \\"Seleccione una causal de devolución.\\";\\n      return;\\n    }\\n\\n    const descripcion =\\n      fDevNota.trim()\\n        ? `Causal: ${fDevCausal} — ${fDevNota.trim()}`\\n        : `Causal: ${fDevCausal}`;\\n\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(`/envios/api/${selectedShipment.id}/estado`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({ estado: \\"devuelto\\", descripcion }),\\n      });\\n      const responseData = await res.json();\\n      if (!res.ok) {\\n        notifyError(responseData.detail ?? \\"No se pudo registrar la devolución.\\");\\n        return;\\n      }\\n      shipments = shipments.map((s) => (s.id === selectedShipment.id ? responseData : s));\\n      selectedShipment = responseData;\\n      modalDevolucion = false;\\n      notifySuccess(\\"Devolución registrada.\\");\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal: nuevo envío\\n  // ---------------------------------------------------------------------------\\n  let modalCrear = false;\\n  let fRadicadoId = \\"\\";\\n  let fOperador = \\"\\";\\n  let fDestinatario = \\"\\";\\n  let fDireccion = \\"\\";\\n  let fRadicadoIdError = \\"\\";\\n  let fOperadorError = \\"\\";\\n  let crearLoading = false;\\n\\n  function abrirModalCrear() {\\n    fRadicadoId = \\"\\";\\n    fOperador = \\"\\";\\n    fDestinatario = \\"\\";\\n    fDireccion = \\"\\";\\n    fRadicadoIdError = \\"\\";\\n    fOperadorError = \\"\\";\\n    modalCrear = true;\\n  }\\n\\n  async function crearEnvio() {\\n    fRadicadoIdError = \\"\\";\\n    fOperadorError = \\"\\";\\n    let valid = true;\\n\\n    if (!fRadicadoId.trim()) {\\n      fRadicadoIdError = \\"El ID del radicado es obligatorio.\\";\\n      valid = false;\\n    }\\n    if (!fOperador) {\\n      fOperadorError = \\"Seleccione un operador postal.\\";\\n      valid = false;\\n    }\\n    if (!valid) return;\\n\\n    crearLoading = true;\\n    try {\\n      const res = await fetch(\\"/envios/api/crear\\", {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          radicado_id: fRadicadoId.trim(),\\n          operador: fOperador,\\n          destinatario: fDestinatario.trim() || null,\\n          direccion: fDireccion.trim() || null,\\n        }),\\n      });\\n      const responseData = await res.json();\\n      if (!res.ok) {\\n        notifyError(responseData.detail ?? \\"No se pudo crear el envío.\\");\\n        return;\\n      }\\n      // Insertar al inicio de la lista (el más reciente primero)\\n      shipments = [responseData, ...shipments];\\n      total += 1;\\n      modalCrear = false;\\n      notifySuccess(\\"Envío registrado.\\");\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      crearLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Carga paginada\\n  // ---------------------------------------------------------------------------\\n\\n  async function loadShipments() {\\n    loading = true;\\n    try {\\n      const params = new URLSearchParams({\\n        page: String(currentPage),\\n        size: String(pageSize),\\n      });\\n      if (filterEstado) params.set(\\"estado\\", filterEstado);\\n\\n      const res = await fetch(`/envios/api?${params}`);\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      shipments = result.shipments ?? [];\\n      total = result.total ?? 0;\\n      // Limpiar selección al cambiar de página/filtro\\n      selectedIds = new Set();\\n    } catch (/** @type {any} */ err) {\\n      notifyError(err?.detail ?? \\"Error al cargar los envíos.\\");\\n    } finally {\\n      loading = false;\\n    }\\n  }\\n\\n  function applyFilter(estado) {\\n    filterEstado = estado;\\n    currentPage = 1;\\n    loadShipments();\\n  }\\n\\n  function onPageChange(/** @type {CustomEvent<{page: number}>} */ e) {\\n    currentPage = e.detail.page;\\n    loadShipments();\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de presentación\\n  // ---------------------------------------------------------------------------\\n\\n  const ESTADO_LABEL = {\\n    registrado: \\"Registrado\\",\\n    en_transito: \\"En tránsito\\",\\n    entregado: \\"Entregado\\",\\n    devuelto: \\"Devuelto\\",\\n    fallido: \\"Fallido\\",\\n  };\\n\\n  // StatusChip status mapping\\n  const ESTADO_CHIP = {\\n    registrado: \\"info\\",\\n    en_transito: \\"pending\\",\\n    entregado: \\"success\\",\\n    devuelto: \\"warning\\",\\n    fallido: \\"error\\",\\n  };\\n\\n  const OPERADOR_LABELS = {\\n    \\"4-72\\": \\"4-72\\",\\n    servientrega: \\"Servientrega\\",\\n    otro: \\"Otro\\",\\n  };\\n\\n  const ESTADOS_FILTER = [\\n    { value: \\"\\", label: \\"Todos\\" },\\n    { value: \\"registrado\\", label: \\"Registrado\\" },\\n    { value: \\"en_transito\\", label: \\"En tránsito\\" },\\n    { value: \\"entregado\\", label: \\"Entregado\\" },\\n    { value: \\"devuelto\\", label: \\"Devuelto\\" },\\n    { value: \\"fallido\\", label: \\"Fallido\\" },\\n  ];\\n\\n  const ESTADOS_OPTIONS = [\\n    { value: \\"registrado\\", label: \\"Registrado\\" },\\n    { value: \\"en_transito\\", label: \\"En tránsito\\" },\\n    { value: \\"entregado\\", label: \\"Entregado\\" },\\n    { value: \\"devuelto\\", label: \\"Devuelto\\" },\\n    { value: \\"fallido\\", label: \\"Fallido\\" },\\n  ];\\n\\n  const OPERADOR_OPTIONS = [\\n    { value: \\"4-72\\", label: \\"4-72\\" },\\n    { value: \\"servientrega\\", label: \\"Servientrega\\" },\\n    { value: \\"otro\\", label: \\"Otro\\" },\\n  ];\\n\\n  const CAUSALES_OPTIONS = CAUSALES_DEVOLUCION.map((c) => ({ value: c, label: c }));\\n\\n  function formatDate(/** @type {string} */ iso) {\\n    if (!iso) return \\"—\\";\\n    return new Date(iso).toLocaleDateString(\\"es-CO\\", {\\n      year: \\"numeric\\",\\n      month: \\"2-digit\\",\\n      day: \\"2-digit\\",\\n    });\\n  }\\n\\n  function formatDateTime(/** @type {string} */ iso) {\\n    if (!iso) return \\"—\\";\\n    return new Date(iso).toLocaleString(\\"es-CO\\", {\\n      year: \\"numeric\\",\\n      month: \\"2-digit\\",\\n      day: \\"2-digit\\",\\n      hour: \\"2-digit\\",\\n      minute: \\"2-digit\\",\\n    });\\n  }\\n\\n  // SUGERENCIA #5: las columnas sin texto visible llevan label sr-only para que los\\n  // lectores de pantalla las anuncien correctamente al navegar por teclado.\\n  const TABLE_COLUMNS = [\\n    { key: \\"_check\\", label: \\"Selección\\", width: \\"40px\\" },\\n    { key: \\"operador\\", label: \\"Operador\\" },\\n    { key: \\"guia\\", label: \\"Guía\\" },\\n    { key: \\"estado\\", label: \\"Estado\\" },\\n    { key: \\"destinatario\\", label: \\"Destinatario\\" },\\n    { key: \\"created_at\\", label: \\"Registrado\\" },\\n    { key: \\"_actions\\", label: \\"Acciones\\", width: \\"80px\\" },\\n  ];\\n\\n  // ---------------------------------------------------------------------------\\n  // Breadcrumbs\\n  // ---------------------------------------------------------------------------\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Envíos postales\\" }]);\\n  });\\n<\/script>\\n\\n<!-- =========================================================================\\n     Cabecera de la página\\n     ========================================================================= -->\\n<div class=\\"envios-page\\">\\n  <header class=\\"envios-page__header\\">\\n    <div class=\\"envios-page__title-row\\">\\n      <h1 class=\\"envios-page__title\\">Envíos postales</h1>\\n      {#if hasPerm}\\n        <Button icon=\\"fas fa-plus\\" on:click={abrirModalCrear}>Nuevo envío</Button>\\n      {/if}\\n    </div>\\n\\n    <!-- Filtros de estado -->\\n    <div class=\\"envios-page__filters\\" role=\\"group\\" aria-label=\\"Filtrar por estado\\">\\n      {#each ESTADOS_FILTER as f (f.value)}\\n        <button\\n          type=\\"button\\"\\n          class=\\"envios-page__filter-btn\\"\\n          class:envios-page__filter-btn--active={filterEstado === f.value}\\n          aria-pressed={filterEstado === f.value}\\n          on:click={() => applyFilter(f.value)}\\n        >\\n          {f.label}\\n        </button>\\n      {/each}\\n    </div>\\n  </header>\\n\\n  <!-- Barra de planilla de despacho (visible cuando hay selección) -->\\n  {#if selectedCount > 0}\\n    <div class=\\"envios-planilla-bar\\" role=\\"status\\" aria-live=\\"polite\\">\\n      <span class=\\"envios-planilla-bar__count\\">\\n        <i class=\\"fas fa-check-square\\" aria-hidden=\\"true\\"></i>\\n        {selectedCount}\\n        {selectedCount === 1 ? \\"envío seleccionado\\" : \\"envíos seleccionados\\"}\\n      </span>\\n      <Button\\n        variant=\\"secondary\\"\\n        icon=\\"fas fa-file-csv\\"\\n        on:click={exportarCSV}\\n      >\\n        Exportar planilla CSV\\n      </Button>\\n    </div>\\n  {/if}\\n\\n  <!-- Tabla paginada -->\\n  <div class=\\"envios-table-wrap\\">\\n    <!-- \\"Seleccionar todos en página\\" — cableado a toggleAll/allPageSelected (IMPORTANTE #2).\\n         La selección se resetea al paginar: decisión deliberada para evitar exportar filas\\n         no visibles; la acumulación cross-page queda como roadmap (#6). -->\\n    {#if !loading && shipments.length > 0}\\n      <div class=\\"envios-selectall\\">\\n        <label class=\\"envios-selectall__label\\">\\n          <input\\n            type=\\"checkbox\\"\\n            class=\\"envios-selectall__input\\"\\n            checked={allPageSelected}\\n            bind:indeterminate={allIndeterminate}\\n            on:change={toggleAll}\\n            aria-label=\\"Seleccionar todos los envíos de la página\\"\\n          />\\n          <span class=\\"envios-selectall__text\\">Seleccionar todos en esta página</span>\\n        </label>\\n      </div>\\n    {/if}\\n\\n    <DataTable\\n      columns={TABLE_COLUMNS}\\n      rows={shipments}\\n      totalCount={total}\\n      bind:page={currentPage}\\n      {pageSize}\\n      {loading}\\n      caption=\\"Bandeja de envíos postales\\"\\n      on:pageChange={onPageChange}\\n    >\\n      <svelte:fragment slot=\\"row\\" let:row>\\n        <!-- Checkbox de selección (planilla) -->\\n        <td class=\\"op-datatable__td envios-table__check\\">\\n          <label class=\\"envios-table__check-label\\">\\n            <input\\n              type=\\"checkbox\\"\\n              class=\\"envios-table__check-input\\"\\n              checked={selectedIds.has(row.id)}\\n              on:change={() => toggleRow(row.id)}\\n              aria-label=\\"Seleccionar envío {row.guia ?? row.id}\\"\\n            />\\n          </label>\\n        </td>\\n\\n        <!-- Operador -->\\n        <td class=\\"op-datatable__td\\">\\n          <span class=\\"envios-table__operador\\">\\n            {OPERADOR_LABELS[row.operador] ?? row.operador}\\n          </span>\\n        </td>\\n\\n        <!-- Guía -->\\n        <td class=\\"op-datatable__td envios-table__guia\\">\\n          {#if row.guia}\\n            {row.guia}\\n          {:else}\\n            <span class=\\"envios-table__na\\">Sin guía</span>\\n          {/if}\\n        </td>\\n\\n        <!-- Estado -->\\n        <td class=\\"op-datatable__td\\">\\n          <StatusChip\\n            status={ESTADO_CHIP[row.estado] ?? \\"neutral\\"}\\n            label={ESTADO_LABEL[row.estado] ?? row.estado}\\n            dot\\n          />\\n        </td>\\n\\n        <!-- Destinatario -->\\n        <td class=\\"op-datatable__td envios-table__destinatario\\">\\n          {row.destinatario ?? \\"—\\"}\\n        </td>\\n\\n        <!-- Fecha -->\\n        <td class=\\"op-datatable__td envios-table__date\\">\\n          {formatDate(row.created_at)}\\n        </td>\\n\\n        <!-- Acciones -->\\n        <td class=\\"op-datatable__td envios-table__actions\\">\\n          <Button\\n            variant=\\"ghost\\"\\n            size=\\"sm\\"\\n            icon=\\"fas fa-eye\\"\\n            on:click={() => openDetail(row)}\\n            aria-label=\\"Ver detalle del envío {row.guia ?? row.id}\\"\\n          >\\n            Ver\\n          </Button>\\n        </td>\\n      </svelte:fragment>\\n    </DataTable>\\n  </div>\\n</div>\\n\\n<!-- =========================================================================\\n     Drawer: detalle del envío + historial de eventos\\n     ========================================================================= -->\\n<Drawer\\n  open={drawerOpen}\\n  title=\\"Detalle del envío\\"\\n  width=\\"520px\\"\\n  on:close={closeDrawer}\\n>\\n  {#if detailLoading}\\n    <div class=\\"envio-detail__loader\\">\\n      <Loader />\\n    </div>\\n  {:else if selectedShipment}\\n    <!-- Datos del envío -->\\n    <dl class=\\"envio-detail__data\\">\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Estado</dt>\\n        <dd>\\n          <StatusChip\\n            status={ESTADO_CHIP[selectedShipment.estado] ?? \\"neutral\\"}\\n            label={ESTADO_LABEL[selectedShipment.estado] ?? selectedShipment.estado}\\n            dot\\n          />\\n        </dd>\\n      </div>\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Operador</dt>\\n        <dd>{OPERADOR_LABELS[selectedShipment.operador] ?? selectedShipment.operador}</dd>\\n      </div>\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Guía</dt>\\n        <dd>{selectedShipment.guia ?? \\"—\\"}</dd>\\n      </div>\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Radicado</dt>\\n        <dd class=\\"envio-detail__mono\\">{selectedShipment.radicado_id}</dd>\\n      </div>\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Destinatario</dt>\\n        <dd>{selectedShipment.destinatario ?? \\"—\\"}</dd>\\n      </div>\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Dirección</dt>\\n        <dd>{selectedShipment.direccion ?? \\"—\\"}</dd>\\n      </div>\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Registrado</dt>\\n        <dd>{formatDateTime(selectedShipment.created_at)}</dd>\\n      </div>\\n      <div class=\\"envio-detail__row\\">\\n        <dt>Actualizado</dt>\\n        <dd>{formatDateTime(selectedShipment.updated_at)}</dd>\\n      </div>\\n    </dl>\\n\\n    <!-- Historial de eventos -->\\n    {#if selectedShipment.eventos?.length > 0}\\n      <section class=\\"envio-timeline\\" aria-label=\\"Historial de eventos del envío\\">\\n        <h3 class=\\"envio-timeline__title\\">Historial</h3>\\n        <ol class=\\"envio-timeline__list\\" role=\\"list\\">\\n          {#each selectedShipment.eventos as ev, idx (idx)}\\n            <li class=\\"envio-timeline__item\\">\\n              <span\\n                class=\\"envio-timeline__dot\\"\\n                aria-hidden=\\"true\\"\\n                class:envio-timeline__dot--last={idx === selectedShipment.eventos.length - 1}\\n              ></span>\\n              <div class=\\"envio-timeline__content\\">\\n                <div class=\\"envio-timeline__row\\">\\n                  <StatusChip\\n                    status={ESTADO_CHIP[ev.estado] ?? \\"neutral\\"}\\n                    label={ESTADO_LABEL[ev.estado] ?? ev.estado}\\n                  />\\n                  <time\\n                    class=\\"envio-timeline__ts\\"\\n                    datetime={ev.ts}\\n                    title={ev.ts}\\n                  >\\n                    {formatDateTime(ev.ts)}\\n                  </time>\\n                </div>\\n                {#if ev.descripcion}\\n                  <p class=\\"envio-timeline__desc\\">{ev.descripcion}</p>\\n                {/if}\\n              </div>\\n            </li>\\n          {/each}\\n        </ol>\\n      </section>\\n    {:else}\\n      <p class=\\"envio-detail__no-eventos\\">Sin eventos registrados aún.</p>\\n    {/if}\\n\\n    <!-- Acciones (solo si tiene permiso) -->\\n    {#if hasPerm && selectedShipment.estado !== \\"entregado\\"}\\n      <div class=\\"envio-detail__actions\\">\\n        <hr class=\\"envio-detail__divider\\" aria-hidden=\\"true\\" />\\n        <p class=\\"envio-detail__actions-title\\">Acciones</p>\\n\\n        <Button\\n          variant=\\"secondary\\"\\n          icon=\\"fas fa-exchange-alt\\"\\n          loading={actionLoading}\\n          on:click={abrirModalEstado}\\n        >\\n          Cambiar estado\\n        </Button>\\n\\n        {#if selectedShipment.estado !== \\"devuelto\\" && selectedShipment.estado !== \\"fallido\\"}\\n          <Button\\n            variant=\\"ghost\\"\\n            icon=\\"fas fa-undo\\"\\n            loading={actionLoading}\\n            on:click={abrirModalDevolucion}\\n          >\\n            Registrar devolución\\n          </Button>\\n        {/if}\\n      </div>\\n    {/if}\\n  {/if}\\n</Drawer>\\n\\n<!-- =========================================================================\\n     Modal: cambiar estado\\n     ========================================================================= -->\\n<Modal\\n  open={modalEstado}\\n  title=\\"Cambiar estado del envío\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalEstado = false)}\\n>\\n  <div class=\\"envio-modal-form\\">\\n    <FormField\\n      id=\\"envio-nuevo-estado\\"\\n      label=\\"Nuevo estado\\"\\n      type=\\"select\\"\\n      bind:value={fEstado}\\n      required\\n      error={fEstadoError}\\n      options={ESTADOS_OPTIONS}\\n    />\\n    <FormField\\n      id=\\"envio-estado-desc\\"\\n      label=\\"Nota descriptiva (opcional)\\"\\n      type=\\"textarea\\"\\n      bind:value={fEstadoDescripcion}\\n      placeholder=\\"Observaciones sobre el cambio de estado…\\"\\n      rows={3}\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (modalEstado = false)}>Cancelar</Button>\\n    <Button icon=\\"fas fa-check\\" loading={actionLoading} on:click={cambiarEstado}>\\n      Aplicar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- =========================================================================\\n     Modal: registrar devolución con causal\\n     ========================================================================= -->\\n<Modal\\n  open={modalDevolucion}\\n  title=\\"Registrar devolución\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalDevolucion = false)}\\n>\\n  <div class=\\"envio-modal-form\\">\\n    <!--\\n      La causal se envía en `descripcion` con el prefijo \\"Causal: <x> — <nota>\\".\\n      La causal estructurada como catálogo independiente es roadmap del backend.\\n    -->\\n    <FormField\\n      id=\\"envio-dev-causal\\"\\n      label=\\"Causal de devolución\\"\\n      type=\\"select\\"\\n      bind:value={fDevCausal}\\n      required\\n      error={fDevCausalError}\\n      placeholder=\\"Seleccione una causal\\"\\n      options={CAUSALES_OPTIONS}\\n    />\\n    <FormField\\n      id=\\"envio-dev-nota\\"\\n      label=\\"Nota adicional (opcional)\\"\\n      type=\\"textarea\\"\\n      bind:value={fDevNota}\\n      placeholder=\\"Detalle adicional de la devolución…\\"\\n      rows={3}\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (modalDevolucion = false)}>Cancelar</Button>\\n    <Button icon=\\"fas fa-undo\\" loading={actionLoading} on:click={registrarDevolucion}>\\n      Registrar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- =========================================================================\\n     Modal: nuevo envío\\n     ========================================================================= -->\\n<Modal\\n  open={modalCrear}\\n  title=\\"Nuevo envío postal\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalCrear = false)}\\n>\\n  <div class=\\"envio-modal-form\\">\\n    <!--\\n      UX: se acepta el ID (UUID) del radicado de salida.\\n      El usuario puede obtenerlo desde la bandeja de radicados.\\n      Mejora futura: radicado picker con autocompletado (roadmap).\\n    -->\\n    <FormField\\n      id=\\"envio-radicado-id\\"\\n      label=\\"ID del radicado de salida\\"\\n      type=\\"text\\"\\n      bind:value={fRadicadoId}\\n      placeholder=\\"UUID del radicado de salida (p.ej. a1b2c3d4-…)\\"\\n      required\\n      error={fRadicadoIdError}\\n      hint=\\"Disponible en el detalle del radicado en la Bandeja.\\"\\n    />\\n    <FormField\\n      id=\\"envio-operador\\"\\n      label=\\"Operador postal\\"\\n      type=\\"select\\"\\n      bind:value={fOperador}\\n      required\\n      error={fOperadorError}\\n      placeholder=\\"Seleccione el operador\\"\\n      options={OPERADOR_OPTIONS}\\n    />\\n    <FormField\\n      id=\\"envio-destinatario\\"\\n      label=\\"Destinatario (opcional)\\"\\n      type=\\"text\\"\\n      bind:value={fDestinatario}\\n      placeholder=\\"Nombre del destinatario\\"\\n    />\\n    <FormField\\n      id=\\"envio-direccion\\"\\n      label=\\"Dirección (opcional)\\"\\n      type=\\"text\\"\\n      bind:value={fDireccion}\\n      placeholder=\\"Calle, ciudad, depto.\\"\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (modalCrear = false)}>Cancelar</Button>\\n    <Button icon=\\"fas fa-truck\\" loading={crearLoading} on:click={crearEnvio}>\\n      Registrar envío\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<style lang=\\"scss\\">:root {\\n  --op-primary: #2a8c3a;\\n  --op-primary-dark: #1c6b33;\\n  --op-primary-light: #dceed7;\\n  --op-on-primary: #ffffff;\\n  --op-accent: #8dbf3c;\\n  --op-brand-700: #173a22;\\n  --op-brand-400: #4fa84b;\\n  --op-text: #232624;\\n  --op-text-secondary: #6e7370;\\n  --op-border: #e7eae2;\\n  --op-bg: #fbfcf8;\\n  --op-surface: #ffffff;\\n  --op-success: #2a8c3a;\\n  --op-warning: #c8881c;\\n  --op-error: #c4452c;\\n  --op-info: #2b6cb0;\\n  --op-success-bg: #dceed7;\\n  --op-success-fg: #1c6b33;\\n  --op-warning-bg: #fdf3dc;\\n  --op-warning-fg: #7a5700;\\n  --op-error-bg: #fce8e8;\\n  --op-error-fg: #8b1a1a;\\n  --op-info-bg: #e0eef7;\\n  --op-info-fg: #1e4e6b;\\n  --op-error-strong-bg: #3b0000;\\n  --op-error-strong-fg: #ffdddd;\\n  --op-focus-ring: rgba(42, 140, 58, 0.15);\\n  --op-error-ring: rgba(196, 69, 44, 0.15);\\n  --op-font-family: Public Sans, Segoe UI, system-ui, -apple-system, sans-serif;\\n  --op-font-display: Space Grotesk, Segoe UI, system-ui, -apple-system, sans-serif;\\n  --op-font-size-base: 15px;\\n  --op-line-height: 1.6;\\n  --op-font-xs: 12px;\\n  --op-font-sm: 13px;\\n  --op-font-base: 15px;\\n  --op-font-md: 16px;\\n  --op-font-lg: 18px;\\n  --op-font-xl: 22px;\\n  --op-font-2xl: 28px;\\n  --op-font-size-display: 46px;\\n  --op-font-mono: 12px;\\n  --op-weight-regular: 400;\\n  --op-weight-medium: 500;\\n  --op-weight-bold: 600;\\n  --op-weight-display: 700;\\n  --op-space-1: 4px;\\n  --op-space-2: 8px;\\n  --op-space-3: 12px;\\n  --op-space-4: 16px;\\n  --op-space-5: 24px;\\n  --op-space-6: 32px;\\n  --op-space-7: 48px;\\n  --op-space-8: 64px;\\n  --op-radius-sm: 6px;\\n  --op-radius: 10px;\\n  --op-radius-lg: 16px;\\n  --op-radius-full: 999px;\\n  --op-shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);\\n  --op-shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.05);\\n  --op-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);\\n  --op-shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.06);\\n  --op-transition: 200ms cubic-bezier(0.2, 0.8, 0.2, 1);\\n  --op-motion-fast: 120ms;\\n  --op-motion-base: 200ms;\\n  --op-motion-slow: 300ms;\\n  --op-motion-easing: cubic-bezier(0.2, 0.8, 0.2, 1);\\n  --op-sidebar-width: 264px;\\n  --op-sidebar-collapsed-width: 64px;\\n  --op-topbar-height: 52px;\\n  --op-breadcrumb-height: 36px;\\n}\\n\\n@media (prefers-reduced-motion: reduce) {\\n  :root {\\n    --op-motion-fast: 0ms;\\n    --op-motion-base: 0ms;\\n    --op-motion-slow: 0ms;\\n    --op-transition: 0ms;\\n  }\\n  *,\\n  *::before,\\n  *::after {\\n    animation-duration: 0.01ms !important;\\n    animation-iteration-count: 1 !important;\\n    transition-duration: 0.01ms !important;\\n    scroll-behavior: auto !important;\\n  }\\n}\\n.envios-page {\\n  padding: var(--op-space-5);\\n  max-width: 1100px;\\n}\\n.envios-page__header {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n  margin-bottom: var(--op-space-4);\\n}\\n.envios-page__title-row {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-3);\\n}\\n.envios-page__title {\\n  font-size: var(--op-font-lg);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n.envios-page__filters {\\n  display: flex;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-2);\\n}\\n.envios-page__filter-btn {\\n  padding: var(--op-space-1) var(--op-space-3);\\n  font-size: var(--op-font-sm);\\n  font-family: var(--op-font-family);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-full);\\n  background: var(--op-surface);\\n  color: var(--op-text-secondary);\\n  cursor: pointer;\\n  transition: background var(--op-transition), color var(--op-transition), border-color var(--op-transition);\\n}\\n.envios-page__filter-btn:hover {\\n  background: var(--op-primary-light);\\n  color: var(--op-primary-dark);\\n  border-color: var(--op-primary-light);\\n}\\n.envios-page__filter-btn--active {\\n  background: var(--op-primary);\\n  color: var(--op-surface);\\n  border-color: var(--op-primary);\\n}\\n.envios-page__filter-btn--active:hover {\\n  background: var(--op-primary-dark);\\n  border-color: var(--op-primary-dark);\\n}\\n.envios-page__filter-btn:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n\\n.envios-planilla-bar {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-3);\\n  background: var(--op-primary-light);\\n  border: 1px solid var(--op-primary);\\n  border-radius: var(--op-radius);\\n  padding: var(--op-space-2) var(--op-space-4);\\n  margin-bottom: var(--op-space-3);\\n}\\n.envios-planilla-bar__count {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n}\\n\\n.envios-selectall {\\n  display: flex;\\n  align-items: center;\\n  padding: var(--op-space-2) var(--op-space-4);\\n  border-bottom: 1px solid var(--op-border);\\n  background: var(--op-bg);\\n}\\n.envios-selectall__label {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  cursor: pointer;\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n  user-select: none;\\n}\\n.envios-selectall__input {\\n  width: 16px;\\n  height: 16px;\\n  accent-color: var(--op-primary);\\n  cursor: pointer;\\n  flex-shrink: 0;\\n}\\n.envios-selectall__input:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n.envios-selectall__text {\\n  font-weight: var(--op-weight-medium);\\n}\\n\\n.envios-table-wrap {\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n  overflow: hidden;\\n}\\n\\n.envios-table__check {\\n  padding-left: var(--op-space-3);\\n  padding-right: var(--op-space-2);\\n}\\n.envios-table__check-label {\\n  display: flex;\\n  align-items: center;\\n  cursor: pointer;\\n}\\n.envios-table__check-input {\\n  width: 16px;\\n  height: 16px;\\n  accent-color: var(--op-primary);\\n  cursor: pointer;\\n}\\n.envios-table__check-input:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n.envios-table__operador {\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text);\\n}\\n.envios-table__guia {\\n  font-family: monospace;\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n}\\n.envios-table__destinatario {\\n  max-width: 200px;\\n  white-space: nowrap;\\n  overflow: hidden;\\n  text-overflow: ellipsis;\\n  color: var(--op-text-secondary);\\n}\\n.envios-table__date {\\n  white-space: nowrap;\\n  color: var(--op-text-secondary);\\n}\\n.envios-table__actions {\\n  width: 80px;\\n}\\n.envios-table__na {\\n  color: var(--op-text-secondary);\\n  font-style: italic;\\n}\\n\\n.envio-detail__loader {\\n  display: flex;\\n  justify-content: center;\\n  padding: var(--op-space-6);\\n}\\n.envio-detail__data {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-1);\\n  margin: 0 0 var(--op-space-4);\\n}\\n.envio-detail__row {\\n  display: grid;\\n  grid-template-columns: 120px 1fr;\\n  gap: var(--op-space-2);\\n  padding: var(--op-space-1) 0;\\n  border-bottom: 1px solid var(--op-border);\\n  font-size: var(--op-font-sm);\\n}\\n.envio-detail__row:last-child {\\n  border-bottom: none;\\n}\\n.envio-detail__row dt {\\n  color: var(--op-text-secondary);\\n  font-weight: var(--op-weight-medium);\\n}\\n.envio-detail__row dd {\\n  color: var(--op-text);\\n  margin: 0;\\n  word-break: break-all;\\n}\\n.envio-detail__mono {\\n  font-family: monospace;\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n}\\n.envio-detail__no-eventos {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n  padding: var(--op-space-3) 0;\\n  margin: 0;\\n}\\n.envio-detail__divider {\\n  border: none;\\n  border-top: 1px solid var(--op-border);\\n  margin: var(--op-space-2) 0;\\n}\\n.envio-detail__actions {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n  padding-top: var(--op-space-2);\\n}\\n.envio-detail__actions-title {\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text-secondary);\\n  margin: 0;\\n}\\n\\n.envio-timeline {\\n  margin-bottom: var(--op-space-4);\\n}\\n.envio-timeline__title {\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0 0 var(--op-space-3);\\n}\\n.envio-timeline__list {\\n  list-style: none;\\n  margin: 0;\\n  padding: 0;\\n  position: relative;\\n}\\n.envio-timeline__list::before {\\n  content: \\"\\";\\n  position: absolute;\\n  left: 7px;\\n  top: 0;\\n  bottom: 0;\\n  width: 2px;\\n  background: var(--op-border);\\n}\\n.envio-timeline__item {\\n  display: flex;\\n  gap: var(--op-space-3);\\n  padding-bottom: var(--op-space-3);\\n  position: relative;\\n}\\n.envio-timeline__dot {\\n  flex-shrink: 0;\\n  width: 16px;\\n  height: 16px;\\n  border-radius: 50%;\\n  background: var(--op-border);\\n  border: 2px solid var(--op-surface);\\n  margin-top: 2px;\\n  z-index: 1;\\n}\\n.envio-timeline__dot--last {\\n  background: var(--op-primary);\\n}\\n.envio-timeline__content {\\n  flex: 1;\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-1);\\n}\\n.envio-timeline__row {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  flex-wrap: wrap;\\n}\\n.envio-timeline__ts {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n}\\n.envio-timeline__desc {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  margin: 0;\\n  font-style: italic;\\n}\\n\\n.envio-modal-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}</style>\\n"],"names":[],"mappings":"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"}'
};
const pageSize = 20;
function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("es-CO", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  });
}
const Page = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let hasPerm;
  let selectedCount;
  let allPageSelected;
  let allIndeterminate;
  let $permissions, $$unsubscribe_permissions;
  $$unsubscribe_permissions = subscribe(permissions, (value) => $permissions = value);
  let { data } = $$props;
  let shipments = data.shipments ?? [];
  let total = data.total ?? 0;
  let loading = false;
  let filterEstado = data.initialFilters?.estado ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  let selectedIds = /* @__PURE__ */ new Set();
  let drawerOpen = false;
  let modalEstado = false;
  let fEstado = "";
  let fEstadoDescripcion = "";
  let fEstadoError = "";
  let actionLoading = false;
  const CAUSALES_DEVOLUCION = [
    "Dirección incorrecta",
    "Destinatario desconocido",
    "Rehusado",
    "No reclamado",
    "Otro"
  ];
  let modalDevolucion = false;
  let fDevCausal = "";
  let fDevNota = "";
  let fDevCausalError = "";
  let modalCrear = false;
  let fRadicadoId = "";
  let fOperador = "";
  let fDestinatario = "";
  let fDireccion = "";
  let fRadicadoIdError = "";
  let fOperadorError = "";
  let crearLoading = false;
  const ESTADO_LABEL = {
    registrado: "Registrado",
    en_transito: "En tránsito",
    entregado: "Entregado",
    devuelto: "Devuelto",
    fallido: "Fallido"
  };
  const ESTADO_CHIP = {
    registrado: "info",
    en_transito: "pending",
    entregado: "success",
    devuelto: "warning",
    fallido: "error"
  };
  const OPERADOR_LABELS = {
    "4-72": "4-72",
    servientrega: "Servientrega",
    otro: "Otro"
  };
  const ESTADOS_FILTER = [
    { value: "", label: "Todos" },
    { value: "registrado", label: "Registrado" },
    {
      value: "en_transito",
      label: "En tránsito"
    },
    { value: "entregado", label: "Entregado" },
    { value: "devuelto", label: "Devuelto" },
    { value: "fallido", label: "Fallido" }
  ];
  const ESTADOS_OPTIONS = [
    { value: "registrado", label: "Registrado" },
    {
      value: "en_transito",
      label: "En tránsito"
    },
    { value: "entregado", label: "Entregado" },
    { value: "devuelto", label: "Devuelto" },
    { value: "fallido", label: "Fallido" }
  ];
  const OPERADOR_OPTIONS = [
    { value: "4-72", label: "4-72" },
    {
      value: "servientrega",
      label: "Servientrega"
    },
    { value: "otro", label: "Otro" }
  ];
  const CAUSALES_OPTIONS = CAUSALES_DEVOLUCION.map((c) => ({ value: c, label: c }));
  const TABLE_COLUMNS = [
    {
      key: "_check",
      label: "Selección",
      width: "40px"
    },
    { key: "operador", label: "Operador" },
    { key: "guia", label: "Guía" },
    { key: "estado", label: "Estado" },
    {
      key: "destinatario",
      label: "Destinatario"
    },
    { key: "created_at", label: "Registrado" },
    {
      key: "_actions",
      label: "Acciones",
      width: "80px"
    }
  ];
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    hasPerm = $permissions.includes("PERM_RADI_SALIDA");
    selectedCount = selectedIds.size;
    shipments.filter((s) => selectedIds.has(s.id));
    allPageSelected = shipments.length > 0 && shipments.every((s) => selectedIds.has(s.id));
    allIndeterminate = selectedCount > 0 && !allPageSelected;
    $$rendered = `    <div class="envios-page svelte-1kch2b0"><header class="envios-page__header svelte-1kch2b0"><div class="envios-page__title-row svelte-1kch2b0"><h1 class="envios-page__title svelte-1kch2b0" data-svelte-h="svelte-1c2pvu8">Envíos postales</h1> ${hasPerm ? `${validate_component(Button, "Button").$$render($$result, { icon: "fas fa-plus" }, {}, {
      default: () => {
        return `Nuevo envío`;
      }
    })}` : ``}</div>  <div class="envios-page__filters svelte-1kch2b0" role="group" aria-label="Filtrar por estado">${each(ESTADOS_FILTER, (f) => {
      return `<button type="button" class="${[
        "envios-page__filter-btn svelte-1kch2b0",
        filterEstado === f.value ? "envios-page__filter-btn--active" : ""
      ].join(" ").trim()}"${add_attribute("aria-pressed", filterEstado === f.value, 0)}>${escape(f.label)} </button>`;
    })}</div></header>  ${selectedCount > 0 ? `<div class="envios-planilla-bar svelte-1kch2b0" role="status" aria-live="polite"><span class="envios-planilla-bar__count svelte-1kch2b0"><i class="fas fa-check-square svelte-1kch2b0" aria-hidden="true"></i> ${escape(selectedCount)} ${escape(selectedCount === 1 ? "envío seleccionado" : "envíos seleccionados")}</span> ${validate_component(Button, "Button").$$render(
      $$result,
      {
        variant: "secondary",
        icon: "fas fa-file-csv"
      },
      {},
      {
        default: () => {
          return `Exportar planilla CSV`;
        }
      }
    )}</div>` : ``}  <div class="envios-table-wrap svelte-1kch2b0"> ${shipments.length > 0 ? `<div class="envios-selectall svelte-1kch2b0"><label class="envios-selectall__label svelte-1kch2b0"><input type="checkbox" class="envios-selectall__input svelte-1kch2b0" ${allPageSelected ? "checked" : ""} aria-label="Seleccionar todos los envíos de la página"${add_attribute("indeterminate", allIndeterminate, 0)}> <span class="envios-selectall__text svelte-1kch2b0" data-svelte-h="svelte-8njjvn">Seleccionar todos en esta página</span></label></div>` : ``} ${validate_component(DataTable, "DataTable").$$render(
      $$result,
      {
        columns: TABLE_COLUMNS,
        rows: shipments,
        totalCount: total,
        pageSize,
        loading,
        caption: "Bandeja de envíos postales",
        page: currentPage
      },
      {
        page: ($$value) => {
          currentPage = $$value;
          $$settled = false;
        }
      },
      {
        row: ({ row }) => {
          return ` <td class="op-datatable__td envios-table__check svelte-1kch2b0"><label class="envios-table__check-label svelte-1kch2b0"><input type="checkbox" class="envios-table__check-input svelte-1kch2b0" ${selectedIds.has(row.id) ? "checked" : ""} aria-label="${"Seleccionar envío " + escape(row.guia ?? row.id, true)}"></label></td>  <td class="op-datatable__td svelte-1kch2b0"><span class="envios-table__operador svelte-1kch2b0">${escape(OPERADOR_LABELS[row.operador] ?? row.operador)}</span></td>  <td class="op-datatable__td envios-table__guia svelte-1kch2b0">${row.guia ? `${escape(row.guia)}` : `<span class="envios-table__na svelte-1kch2b0" data-svelte-h="svelte-18dg6bn">Sin guía</span>`}</td>  <td class="op-datatable__td svelte-1kch2b0">${validate_component(StatusChip, "StatusChip").$$render(
            $$result,
            {
              status: ESTADO_CHIP[row.estado] ?? "neutral",
              label: ESTADO_LABEL[row.estado] ?? row.estado,
              dot: true
            },
            {},
            {}
          )}</td>  <td class="op-datatable__td envios-table__destinatario svelte-1kch2b0">${escape(row.destinatario ?? "—")}</td>  <td class="op-datatable__td envios-table__date svelte-1kch2b0">${escape(formatDate(row.created_at))}</td>  <td class="op-datatable__td envios-table__actions svelte-1kch2b0">${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              icon: "fas fa-eye",
              "aria-label": "Ver detalle del envío " + (row.guia ?? row.id)
            },
            {},
            {
              default: () => {
                return `Ver`;
              }
            }
          )}</td>`;
        }
      }
    )}</div></div>  ${validate_component(Drawer, "Drawer").$$render(
      $$result,
      {
        open: drawerOpen,
        title: "Detalle del envío",
        width: "520px"
      },
      {},
      {
        default: () => {
          return `${`${``}`}`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalEstado,
        title: "Cambiar estado del envío",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              icon: "fas fa-check",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `Aplicar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<div class="envio-modal-form svelte-1kch2b0">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-nuevo-estado",
              label: "Nuevo estado",
              type: "select",
              required: true,
              error: fEstadoError,
              options: ESTADOS_OPTIONS,
              value: fEstado
            },
            {
              value: ($$value) => {
                fEstado = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-estado-desc",
              label: "Nota descriptiva (opcional)",
              type: "textarea",
              placeholder: "Observaciones sobre el cambio de estado…",
              rows: 3,
              value: fEstadoDescripcion
            },
            {
              value: ($$value) => {
                fEstadoDescripcion = $$value;
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
        open: modalDevolucion,
        title: "Registrar devolución",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              icon: "fas fa-undo",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `Registrar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<div class="envio-modal-form svelte-1kch2b0"> ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-dev-causal",
              label: "Causal de devolución",
              type: "select",
              required: true,
              error: fDevCausalError,
              placeholder: "Seleccione una causal",
              options: CAUSALES_OPTIONS,
              value: fDevCausal
            },
            {
              value: ($$value) => {
                fDevCausal = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-dev-nota",
              label: "Nota adicional (opcional)",
              type: "textarea",
              placeholder: "Detalle adicional de la devolución…",
              rows: 3,
              value: fDevNota
            },
            {
              value: ($$value) => {
                fDevNota = $$value;
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
        open: modalCrear,
        title: "Nuevo envío postal",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              icon: "fas fa-truck",
              loading: crearLoading
            },
            {},
            {
              default: () => {
                return `Registrar envío`;
              }
            }
          )} `;
        },
        default: () => {
          return `<div class="envio-modal-form svelte-1kch2b0"> ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-radicado-id",
              label: "ID del radicado de salida",
              type: "text",
              placeholder: "UUID del radicado de salida (p.ej. a1b2c3d4-…)",
              required: true,
              error: fRadicadoIdError,
              hint: "Disponible en el detalle del radicado en la Bandeja.",
              value: fRadicadoId
            },
            {
              value: ($$value) => {
                fRadicadoId = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-operador",
              label: "Operador postal",
              type: "select",
              required: true,
              error: fOperadorError,
              placeholder: "Seleccione el operador",
              options: OPERADOR_OPTIONS,
              value: fOperador
            },
            {
              value: ($$value) => {
                fOperador = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-destinatario",
              label: "Destinatario (opcional)",
              type: "text",
              placeholder: "Nombre del destinatario",
              value: fDestinatario
            },
            {
              value: ($$value) => {
                fDestinatario = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "envio-direccion",
              label: "Dirección (opcional)",
              type: "text",
              placeholder: "Calle, ciudad, depto.",
              value: fDireccion
            },
            {
              value: ($$value) => {
                fDireccion = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`;
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
