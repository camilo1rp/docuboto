import { c as create_ssr_component, s as subscribe, v as validate_component, d as each, e as escape, b as add_attribute } from "../../../../chunks/ssr.js";
import { p as permissions } from "../../../../chunks/auth.js";
import { M as Modal } from "../../../../chunks/Modal.js";
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
/* empty css                                                      */
import { B as Button } from "../../../../chunks/Button.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
import { F as FormField } from "../../../../chunks/FormField.js";
import { C as Card } from "../../../../chunks/Card.js";
const css = {
  code: '@charset "UTF-8";.af-workspace.svelte-sai9mo.svelte-sai9mo{display:flex;flex-direction:column;gap:var(--op-space-4);padding:var(--op-space-5)}.af-workspace__header.svelte-sai9mo.svelte-sai9mo{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:var(--op-space-3)}.af-workspace__title.svelte-sai9mo.svelte-sai9mo{font-size:var(--op-font-xl);font-weight:var(--op-weight-bold);color:var(--op-text);display:flex;align-items:center;gap:var(--op-space-2)}.af-workspace__title.svelte-sai9mo i.svelte-sai9mo{color:var(--op-primary)}.af-workspace__header-actions.svelte-sai9mo.svelte-sai9mo{display:flex;gap:var(--op-space-2);flex-wrap:wrap}.af-workspace__panels.svelte-sai9mo.svelte-sai9mo{background:var(--op-surface);border:1px solid var(--op-border);border-radius:0 0 var(--op-radius) var(--op-radius);border-top:none}.af-tabs.svelte-sai9mo.svelte-sai9mo{display:flex;border-bottom:2px solid var(--op-border);background:var(--op-bg);overflow-x:auto}.af-tabs__tab.svelte-sai9mo.svelte-sai9mo{display:flex;align-items:center;gap:var(--op-space-2);padding:var(--op-space-3) var(--op-space-4);border:none;background:transparent;color:var(--op-text-secondary);font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);cursor:pointer;white-space:nowrap;border-bottom:2px solid transparent;margin-bottom:-2px;transition:color var(--op-transition), border-color var(--op-transition)}.af-tabs__tab.svelte-sai9mo.svelte-sai9mo:hover{color:var(--op-primary)}.af-tabs__tab--active.svelte-sai9mo.svelte-sai9mo{color:var(--op-primary-dark);font-weight:var(--op-weight-bold);border-bottom-color:var(--op-primary)}.af-tabs__tab.svelte-sai9mo.svelte-sai9mo:focus-visible{outline:2px solid var(--op-primary);outline-offset:-2px}.af-panel.svelte-sai9mo.svelte-sai9mo{padding:var(--op-space-5)}.af-panel-header.svelte-sai9mo.svelte-sai9mo{display:flex;align-items:center;justify-content:space-between;gap:var(--op-space-2)}.af-filter-row.svelte-sai9mo.svelte-sai9mo{display:flex;align-items:flex-end;gap:var(--op-space-3);flex-wrap:wrap;margin-bottom:var(--op-space-4)}.af-filter-row__field.svelte-sai9mo.svelte-sai9mo{display:flex;flex-direction:column;gap:var(--op-space-1);min-width:200px;flex:1}.af-filter-row__actions.svelte-sai9mo.svelte-sai9mo{display:flex;gap:var(--op-space-2)}.af-table-wrap.svelte-sai9mo.svelte-sai9mo{overflow-x:auto}.af-table-wrap.svelte-sai9mo .table.svelte-sai9mo{border-collapse:collapse;width:100%}.af-table-wrap.svelte-sai9mo .table th.svelte-sai9mo{background:var(--op-bg);color:var(--op-text-secondary);font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);text-transform:uppercase;letter-spacing:0.04em;padding:var(--op-space-2) var(--op-space-3);border-bottom:2px solid var(--op-border)}.af-table-wrap.svelte-sai9mo .table td.svelte-sai9mo{padding:var(--op-space-2) var(--op-space-3);border-bottom:1px solid var(--op-border);color:var(--op-text);font-size:var(--op-font-sm);vertical-align:middle}.af-table-wrap.svelte-sai9mo .table caption.svelte-sai9mo{font-size:var(--op-font-sm);color:var(--op-text-secondary);margin-bottom:var(--op-space-2);text-align:left}.af-actions-cell.svelte-sai9mo.svelte-sai9mo{display:flex;gap:var(--op-space-1);justify-content:flex-end;align-items:center;white-space:nowrap}.af-child-row.svelte-sai9mo td.svelte-sai9mo:first-child{padding-left:var(--op-space-6);color:var(--op-text-secondary)}.af-child-indent.svelte-sai9mo.svelte-sai9mo{position:relative}.af-child-indent.svelte-sai9mo.svelte-sai9mo::before{content:"└ ";color:var(--op-border)}.af-form.svelte-sai9mo.svelte-sai9mo{display:flex;flex-direction:column;gap:var(--op-space-3);padding:var(--op-space-2) 0}.af-modal-footer.svelte-sai9mo.svelte-sai9mo{display:flex;justify-content:flex-end;gap:var(--op-space-2)}.op-card-title{font-size:var(--op-font-md);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}',
  map: '{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  Módulo de Archivo Físico (T-12, E17).\\n\\n  5 pestañas internas (patrón tablist ARIA idéntico al workspace de expedientes):\\n    0 — Ubicaciones      → árbol de ubicaciones de archivo físico\\n    1 — Unidades         → unidades de conservación (cajas, carpetas…)\\n    2 — Localizar expte. → localizar un expediente en archivo físico\\n    3 — Préstamos        → préstamos y devoluciones de unidades\\n    4 — FUID             → Formato Único de Inventario Documental\\n\\n  Accesibilidad:\\n    - Patrón ARIA tablist/tab/tabpanel (WCAG 2.1 §4.1.2)\\n    - Navegación por teclado: flechas ← → entre tabs\\n    - Labels y roles ARIA en todos los elementos interactivos\\n    - aria-busy durante cargas, <caption> y scope en tablas\\n    - Modales con focus trap (componente Modal)\\n\\n  Acciones de escritura (crear ubicación, nueva unidad, préstamo, vincular expediente):\\n    - Solo visibles si el usuario tiene USUA_PERM_EXPEDIENTE\\n    - El backend re-valida RBAC en cada operación (ADR-013)\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { permissions } from \\"$lib/stores/auth.js\\";\\n  import { notifySuccess, notifyError } from \\"$lib/stores/notifications.js\\";\\n\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Loader from \\"$lib/components/ui/Loader.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import Card from \\"$lib/components/ui/Card.svelte\\";\\n\\n  /** @type {import(\'./$types\').PageData} */\\n  export let data;\\n\\n  // ---------------------------------------------------------------------------\\n  // Breadcrumbs\\n  // ---------------------------------------------------------------------------\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Panel principal\\", href: \\"/dashboard\\" }, { label: \\"Archivo físico\\" }]);\\n  });\\n\\n  // ---------------------------------------------------------------------------\\n  // Autorización (espejo del RBAC; backend re-valida siempre)\\n  // ---------------------------------------------------------------------------\\n  $: canManage = $permissions.includes(\\"USUA_PERM_EXPEDIENTE\\");\\n\\n  // ---------------------------------------------------------------------------\\n  // Opciones de dominio (FormField options)\\n  // ---------------------------------------------------------------------------\\n\\n  /** Niveles de ubicación física según la jerarquía de archivo (E17) */\\n  const NIVELES_UBICACION_OPTIONS = [\\n    { value: \\"sede\\", label: \\"Sede\\" },\\n    { value: \\"edificio\\", label: \\"Edificio\\" },\\n    { value: \\"piso\\", label: \\"Piso\\" },\\n    { value: \\"deposito\\", label: \\"Depósito\\" },\\n    { value: \\"modulo\\", label: \\"Módulo\\" },\\n    { value: \\"estante\\", label: \\"Estante\\" },\\n    { value: \\"entrepano\\", label: \\"Entrepaño\\" },\\n    { value: \\"gaveta\\", label: \\"Gaveta\\" },\\n    { value: \\"custodia_externa\\", label: \\"Custodia externa\\" },\\n  ];\\n\\n  /** Tipos de archivo según Acuerdo AGN */\\n  const TIPOS_ARCHIVO_OPTIONS = [\\n    { value: \\"gestion\\", label: \\"Gestión\\" },\\n    { value: \\"central\\", label: \\"Central\\" },\\n    { value: \\"historico\\", label: \\"Histórico\\" },\\n  ];\\n\\n  /** Tipos de unidad de conservación */\\n  const TIPOS_UNIDAD_OPTIONS = [\\n    { value: \\"caja\\", label: \\"Caja\\" },\\n    { value: \\"carpeta\\", label: \\"Carpeta\\" },\\n    { value: \\"legajo\\", label: \\"Legajo\\" },\\n    { value: \\"libro\\", label: \\"Libro\\" },\\n    { value: \\"tomo\\", label: \\"Tomo\\" },\\n    { value: \\"az\\", label: \\"AZ\\" },\\n    { value: \\"folder\\", label: \\"Folder\\" },\\n  ];\\n\\n  // ---------------------------------------------------------------------------\\n  // Pestañas (tablist ARIA)\\n  // ---------------------------------------------------------------------------\\n  const TABS = [\\n    { id: \\"ubicaciones\\", label: \\"Ubicaciones\\", icon: \\"fa-building\\" },\\n    { id: \\"unidades\\", label: \\"Unidades\\", icon: \\"fa-box\\" },\\n    { id: \\"localizar\\", label: \\"Localizar expediente\\", icon: \\"fa-map-marker-alt\\" },\\n    { id: \\"prestamos\\", label: \\"Préstamos\\", icon: \\"fa-hand-holding\\" },\\n    { id: \\"fuid\\", label: \\"FUID\\", icon: \\"fa-file-alt\\" },\\n  ];\\n  let activeTab = \\"ubicaciones\\";\\n\\n  function handleTabKeydown(e, tabId) {\\n    const idx = TABS.findIndex((t) => t.id === tabId);\\n    if (e.key === \\"ArrowRight\\") {\\n      e.preventDefault();\\n      const next = TABS[(idx + 1) % TABS.length];\\n      activeTab = next.id;\\n      document.getElementById(`tab-${next.id}`)?.focus();\\n    } else if (e.key === \\"ArrowLeft\\") {\\n      e.preventDefault();\\n      const prev = TABS[(idx - 1 + TABS.length) % TABS.length];\\n      activeTab = prev.id;\\n      document.getElementById(`tab-${prev.id}`)?.focus();\\n    } else if (e.key === \\"Home\\") {\\n      e.preventDefault();\\n      activeTab = TABS[0].id;\\n      document.getElementById(`tab-${TABS[0].id}`)?.focus();\\n    } else if (e.key === \\"End\\") {\\n      e.preventDefault();\\n      const last = TABS[TABS.length - 1];\\n      activeTab = last.id;\\n      document.getElementById(`tab-${last.id}`)?.focus();\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Tab 0: Ubicaciones\\n  // ---------------------------------------------------------------------------\\n  /** @type {any[]} */\\n  let ubicaciones = data.facetas?.ubicaciones?.data ?? [];\\n  let ubicacionesError = data.facetas?.ubicaciones?.error ?? null;\\n  let ubicacionesLoading = false;\\n\\n  // Árbol en memoria: mapa id → children cargados\\n  /** @type {Record<string, any[]>} */\\n  let ubicacionChildren = {};\\n  /** @type {Record<string, boolean>} */\\n  let ubicacionExpanded = {};\\n  /** @type {Record<string, boolean>} */\\n  let ubicacionChildrenLoading = {};\\n\\n  async function loadChildren(parentId) {\\n    ubicacionChildrenLoading[parentId] = true;\\n    try {\\n      const res = await fetch(`/archivo-fisico/api/ubicaciones?parent_id=${parentId}`);\\n      const data = await res.json();\\n      if (!res.ok) throw data;\\n      ubicacionChildren[parentId] = Array.isArray(data) ? data : (data.items ?? []);\\n      ubicacionExpanded[parentId] = true;\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudieron cargar los hijos de la ubicación.\\");\\n    } finally {\\n      ubicacionChildrenLoading[parentId] = false;\\n    }\\n  }\\n\\n  function toggleUbicacion(id) {\\n    if (ubicacionExpanded[id]) {\\n      ubicacionExpanded[id] = false;\\n    } else {\\n      loadChildren(id);\\n    }\\n  }\\n\\n  // Modal crear ubicación\\n  let showUbicacionModal = false;\\n  let ubicacionForm = { nivel: \\"\\", codigo: \\"\\", nombre: \\"\\", parent_id: \\"\\", tipo_archivo: \\"\\" };\\n  let ubicacionFormLoading = false;\\n\\n  async function handleCrearUbicacion() {\\n    if (\\n      !ubicacionForm.nivel.trim() ||\\n      !ubicacionForm.codigo.trim() ||\\n      !ubicacionForm.nombre.trim()\\n    ) {\\n      notifyError(\\"Los campos Nivel, Código y Nombre son obligatorios.\\");\\n      return;\\n    }\\n    ubicacionFormLoading = true;\\n    try {\\n      const res = await fetch(\\"/archivo-fisico/api/ubicaciones\\", {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          nivel: ubicacionForm.nivel.trim(),\\n          codigo: ubicacionForm.codigo.trim(),\\n          nombre: ubicacionForm.nombre.trim(),\\n          parent_id: ubicacionForm.parent_id.trim() || undefined,\\n          tipo_archivo: ubicacionForm.tipo_archivo || undefined,\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      notifySuccess(\\"Ubicación creada correctamente.\\");\\n      showUbicacionModal = false;\\n      ubicacionForm = { nivel: \\"\\", codigo: \\"\\", nombre: \\"\\", parent_id: \\"\\", tipo_archivo: \\"\\" };\\n      // Recargar raíz\\n      ubicacionesLoading = true;\\n      const reloadRes = await fetch(\\"/archivo-fisico/api/ubicaciones?activo=true\\");\\n      const reloadData = await reloadRes.json();\\n      ubicaciones = Array.isArray(reloadData) ? reloadData : (reloadData.items ?? []);\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo crear la ubicación.\\");\\n    } finally {\\n      ubicacionFormLoading = false;\\n      ubicacionesLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Tab 1: Unidades\\n  // ---------------------------------------------------------------------------\\n  let unidadFiltroUbicacionId = \\"\\";\\n  let unidadFiltroTipo = \\"\\";\\n  /** @type {any[]} */\\n  let unidades = [];\\n  let unidadesLoading = false;\\n  let unidadesError = null;\\n\\n  async function buscarUnidades() {\\n    unidadesLoading = true;\\n    unidadesError = null;\\n    try {\\n      const params = new URLSearchParams();\\n      if (unidadFiltroUbicacionId.trim())\\n        params.set(\\"ubicacion_id\\", unidadFiltroUbicacionId.trim());\\n      if (unidadFiltroTipo) params.set(\\"tipo\\", unidadFiltroTipo);\\n      const res = await fetch(\\n        `/archivo-fisico/api/unidades${params.toString() ? `?${params}` : \\"\\"}`,\\n      );\\n      const data = await res.json();\\n      if (!res.ok) throw data;\\n      unidades = Array.isArray(data) ? data : (data.items ?? []);\\n    } catch (err) {\\n      unidadesError = err?.detail ?? \\"No se pudieron cargar las unidades.\\";\\n      unidades = [];\\n    } finally {\\n      unidadesLoading = false;\\n    }\\n  }\\n\\n  // Modal nueva unidad\\n  let showNuevaUnidadModal = false;\\n  let nuevaUnidadForm = { tipo: \\"\\", codigo: \\"\\", ubicacion_id: \\"\\", soporte: \\"\\", num_folios: \\"\\" };\\n  let nuevaUnidadLoading = false;\\n\\n  async function handleCrearUnidad() {\\n    if (!nuevaUnidadForm.tipo || !nuevaUnidadForm.codigo.trim()) {\\n      notifyError(\\"Los campos Tipo y Código son obligatorios.\\");\\n      return;\\n    }\\n    nuevaUnidadLoading = true;\\n    try {\\n      const res = await fetch(\\"/archivo-fisico/api/unidades\\", {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          tipo: nuevaUnidadForm.tipo,\\n          codigo: nuevaUnidadForm.codigo.trim(),\\n          ubicacion_id: nuevaUnidadForm.ubicacion_id.trim() || undefined,\\n          soporte: nuevaUnidadForm.soporte.trim() || undefined,\\n          num_folios: nuevaUnidadForm.num_folios ? Number(nuevaUnidadForm.num_folios) : undefined,\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      notifySuccess(\\"Unidad de conservación creada correctamente.\\");\\n      showNuevaUnidadModal = false;\\n      nuevaUnidadForm = { tipo: \\"\\", codigo: \\"\\", ubicacion_id: \\"\\", soporte: \\"\\", num_folios: \\"\\" };\\n      // Refrescar lista si hay filtros activos\\n      if (unidadFiltroUbicacionId || unidadFiltroTipo) {\\n        await buscarUnidades();\\n      }\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo crear la unidad.\\");\\n    } finally {\\n      nuevaUnidadLoading = false;\\n    }\\n  }\\n\\n  // Modal vincular expediente a unidad\\n  let showVincularModal = false;\\n  /** @type {string|null} */\\n  let vincularUnidadId = null;\\n  let vincularForm = { expediente_id: \\"\\", folio_inicio: \\"\\", folio_fin: \\"\\" };\\n  let vincularLoading = false;\\n\\n  function openVincularModal(unidadId) {\\n    vincularUnidadId = unidadId;\\n    vincularForm = { expediente_id: \\"\\", folio_inicio: \\"\\", folio_fin: \\"\\" };\\n    showVincularModal = true;\\n  }\\n\\n  async function handleVincularExpediente() {\\n    if (!vincularForm.expediente_id.trim()) {\\n      notifyError(\\"El ID del expediente es obligatorio.\\");\\n      return;\\n    }\\n    vincularLoading = true;\\n    try {\\n      const res = await fetch(`/archivo-fisico/api/unidades/${vincularUnidadId}/expedientes`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          expediente_id: vincularForm.expediente_id.trim(),\\n          folio_inicio: vincularForm.folio_inicio ? Number(vincularForm.folio_inicio) : undefined,\\n          folio_fin: vincularForm.folio_fin ? Number(vincularForm.folio_fin) : undefined,\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      notifySuccess(\\"Expediente vinculado a la unidad correctamente.\\");\\n      showVincularModal = false;\\n      // Refrescar lista de unidades si hay filtros activos\\n      if (unidadFiltroUbicacionId || unidadFiltroTipo) {\\n        await buscarUnidades();\\n      }\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo vincular el expediente.\\");\\n    } finally {\\n      vincularLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Tab 2: Localizar expediente\\n  // ---------------------------------------------------------------------------\\n  let localizarExpedienteId = \\"\\";\\n  /** @type {any[]} */\\n  let localizarResultados = [];\\n  let localizarLoading = false;\\n  let localizarError = null;\\n  let localizarBuscado = false;\\n\\n  async function handleLocalizar() {\\n    if (!localizarExpedienteId.trim()) {\\n      notifyError(\\"Introduce el ID del expediente.\\");\\n      return;\\n    }\\n    localizarLoading = true;\\n    localizarError = null;\\n    localizarBuscado = true;\\n    try {\\n      const res = await fetch(\\n        `/archivo-fisico/api/expedientes/${encodeURIComponent(localizarExpedienteId.trim())}/unidades`,\\n      );\\n      const data = await res.json();\\n      if (!res.ok) throw data;\\n      localizarResultados = Array.isArray(data) ? data : (data.items ?? []);\\n    } catch (err) {\\n      localizarError = err?.detail ?? \\"No se pudo localizar el expediente.\\";\\n      localizarResultados = [];\\n    } finally {\\n      localizarLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Tab 3: Préstamos\\n  // ---------------------------------------------------------------------------\\n  let prestamosEstado = \\"prestado\\";\\n\\n  // Datos iniciales del servidor\\n  /** @type {any[]} */\\n  let prestamosActivos = data.facetas?.prestamosActivos?.data ?? [];\\n  /** @type {any[]} */\\n  let prestamosVencidos = data.facetas?.prestamosVencidos?.data ?? [];\\n  let prestamosDevueltos = /** @type {any[]} */ ([]);\\n\\n  $: prestamosVisibles =\\n    prestamosEstado === \\"prestado\\"\\n      ? prestamosActivos\\n      : prestamosEstado === \\"vencido\\"\\n        ? prestamosVencidos\\n        : prestamosDevueltos;\\n\\n  let prestamosLoading = false;\\n\\n  async function cargarPrestamos() {\\n    if (![\\"prestado\\", \\"devuelto\\", \\"vencido\\"].includes(prestamosEstado)) return;\\n    prestamosLoading = true;\\n    try {\\n      const res = await fetch(`/archivo-fisico/api/prestamos?estado=${prestamosEstado}`);\\n      const data = await res.json();\\n      if (!res.ok) throw data;\\n      const lista = Array.isArray(data) ? data : (data.items ?? []);\\n      if (prestamosEstado === \\"prestado\\") prestamosActivos = lista;\\n      else if (prestamosEstado === \\"vencido\\") prestamosVencidos = lista;\\n      else prestamosDevueltos = lista;\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudieron cargar los préstamos.\\");\\n    } finally {\\n      prestamosLoading = false;\\n    }\\n  }\\n\\n  // Modal nuevo préstamo (declarado al final del bloque script para evitar hoisting issues)\\n  let prestamoForm = { unidad_id: \\"\\", prestado_a: \\"\\", fecha_devolucion_esperada: \\"\\" };\\n  let prestamoLoading = false;\\n\\n  async function handleRegistrarPrestamo() {\\n    if (\\n      !prestamoForm.unidad_id.trim() ||\\n      !prestamoForm.prestado_a.trim() ||\\n      !prestamoForm.fecha_devolucion_esperada\\n    ) {\\n      notifyError(\\"Los campos Unidad ID, Prestado a y Fecha de devolución son obligatorios.\\");\\n      return;\\n    }\\n    prestamoLoading = true;\\n    try {\\n      const res = await fetch(\\n        `/archivo-fisico/api/unidades/${encodeURIComponent(prestamoForm.unidad_id.trim())}/prestamos`,\\n        {\\n          method: \\"POST\\",\\n          headers: { \\"Content-Type\\": \\"application/json\\" },\\n          body: JSON.stringify({\\n            prestado_a: prestamoForm.prestado_a.trim(),\\n            fecha_devolucion_esperada: prestamoForm.fecha_devolucion_esperada,\\n          }),\\n        },\\n      );\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      notifySuccess(\\"Préstamo registrado correctamente.\\");\\n      showPrestamoModal = false;\\n      prestamoForm = { unidad_id: \\"\\", prestado_a: \\"\\", fecha_devolucion_esperada: \\"\\" };\\n      await cargarPrestamos();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo registrar el préstamo.\\");\\n    } finally {\\n      prestamoLoading = false;\\n    }\\n  }\\n\\n  // Devolución de préstamo\\n  let showDevolverModal = false;\\n  /** @type {string|null} */\\n  let devolverPrestamoId = null;\\n  let devolverPrestamoDesc = \\"\\";\\n  let devolverLoading = false;\\n\\n  function openDevolverModal(id, desc) {\\n    devolverPrestamoId = id;\\n    devolverPrestamoDesc = desc;\\n    showDevolverModal = true;\\n  }\\n\\n  async function handleDevolver() {\\n    devolverLoading = true;\\n    try {\\n      const res = await fetch(`/archivo-fisico/api/prestamos/${devolverPrestamoId}/devolver`, {\\n        method: \\"POST\\",\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      notifySuccess(\\"Devolución registrada correctamente.\\");\\n      showDevolverModal = false;\\n      await cargarPrestamos();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo registrar la devolución.\\");\\n    } finally {\\n      devolverLoading = false;\\n    }\\n  }\\n\\n  // Modal movimientos de unidad\\n  let showMovimientosModal = false;\\n  /** @type {any[]} */\\n  let movimientos = [];\\n  let movimientosLoading = false;\\n  let movimientosUnidadDesc = \\"\\";\\n\\n  async function verMovimientos(unidadId, desc) {\\n    movimientosUnidadDesc = desc;\\n    movimientos = [];\\n    showMovimientosModal = true;\\n    movimientosLoading = true;\\n    try {\\n      const res = await fetch(\\n        `/archivo-fisico/api/unidades/${encodeURIComponent(unidadId)}/movimientos`,\\n      );\\n      const data = await res.json();\\n      if (!res.ok) throw data;\\n      movimientos = Array.isArray(data) ? data : (data.items ?? []);\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo obtener el historial de movimientos.\\");\\n      showMovimientosModal = false;\\n    } finally {\\n      movimientosLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Tab 4: FUID\\n  // ---------------------------------------------------------------------------\\n  let fuidFiltroUbicacionId = \\"\\";\\n  let fuidFiltroTrdSerieId = \\"\\";\\n  /** @type {any[]} */\\n  let fuidRows = [];\\n  let fuidLoading = false;\\n  let fuidError = null;\\n  let fuidBuscado = false;\\n  let fuidXmlLoading = false;\\n\\n  async function consultarFuid() {\\n    fuidLoading = true;\\n    fuidError = null;\\n    fuidBuscado = true;\\n    try {\\n      const params = new URLSearchParams();\\n      if (fuidFiltroUbicacionId.trim()) params.set(\\"ubicacion_id\\", fuidFiltroUbicacionId.trim());\\n      if (fuidFiltroTrdSerieId.trim()) params.set(\\"trd_serie_id\\", fuidFiltroTrdSerieId.trim());\\n      const res = await fetch(`/archivo-fisico/api/fuid${params.toString() ? `?${params}` : \\"\\"}`);\\n      const data = await res.json();\\n      if (!res.ok) throw data;\\n      fuidRows = Array.isArray(data) ? data : (data.items ?? data.rows ?? []);\\n    } catch (err) {\\n      fuidError = err?.detail ?? \\"No se pudo consultar el FUID.\\";\\n      fuidRows = [];\\n    } finally {\\n      fuidLoading = false;\\n    }\\n  }\\n\\n  async function handleDescargarFuidXml() {\\n    fuidXmlLoading = true;\\n    try {\\n      const params = new URLSearchParams();\\n      if (fuidFiltroUbicacionId.trim()) params.set(\\"ubicacion_id\\", fuidFiltroUbicacionId.trim());\\n      if (fuidFiltroTrdSerieId.trim()) params.set(\\"trd_serie_id\\", fuidFiltroTrdSerieId.trim());\\n      const res = await fetch(\\n        `/archivo-fisico/api/fuid.xml${params.toString() ? `?${params}` : \\"\\"}`,\\n      );\\n      if (!res.ok) {\\n        // NUNCA crear blob de error — parsear JSON y mostrar notificación\\n        const err = await res.json().catch(() => ({}));\\n        throw err;\\n      }\\n      const blob = await res.blob();\\n      const url = URL.createObjectURL(blob);\\n      const a = document.createElement(\\"a\\");\\n      a.href = url;\\n      a.download = \\"fuid.xml\\";\\n      a.click();\\n      URL.revokeObjectURL(url);\\n      notifySuccess(\\"FUID XML descargado correctamente.\\");\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo descargar el FUID XML.\\");\\n    } finally {\\n      fuidXmlLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de formato\\n  // ---------------------------------------------------------------------------\\n  /** @param {string|null|undefined} iso */\\n  function fmtDate(iso) {\\n    if (!iso) return \\"—\\";\\n    return new Date(iso).toLocaleDateString(\\"es-CO\\", {\\n      day: \\"2-digit\\",\\n      month: \\"2-digit\\",\\n      year: \\"numeric\\",\\n    });\\n  }\\n\\n  const ESTADO_PRESTAMO_MAP = {\\n    prestado: { label: \\"Prestado\\", status: \\"warning\\" },\\n    devuelto: { label: \\"Devuelto\\", status: \\"success\\" },\\n    vencido: { label: \\"Vencido\\", status: \\"danger\\" },\\n  };\\n\\n  // Variable para el modal del préstamo (corregida sintaxis)\\n  let showPrestamoModal = false;\\n<\/script>\\n\\n<svelte:head>\\n  <title>Archivo físico — OrpycaMCP</title>\\n</svelte:head>\\n\\n<!-- =========================================================================\\n     Workspace de archivo físico\\n     ========================================================================= -->\\n<div class=\\"af-workspace\\">\\n  <!-- Cabecera -->\\n  <div class=\\"af-workspace__header\\">\\n    <h1 class=\\"af-workspace__title\\">\\n      <i class=\\"fas fa-box-archive\\" aria-hidden=\\"true\\"></i>\\n      Archivo físico\\n    </h1>\\n    {#if canManage}\\n      <div class=\\"af-workspace__header-actions\\" role=\\"group\\" aria-label=\\"Acciones de archivo\\">\\n        <Button variant=\\"primary\\" on:click={() => (showUbicacionModal = true)}>\\n          <i class=\\"fas fa-plus\\" aria-hidden=\\"true\\"></i>\\n          Nueva ubicación\\n        </Button>\\n        <Button variant=\\"secondary\\" on:click={() => (showNuevaUnidadModal = true)}>\\n          <i class=\\"fas fa-box\\" aria-hidden=\\"true\\"></i>\\n          Nueva unidad\\n        </Button>\\n      </div>\\n    {/if}\\n  </div>\\n\\n  <!-- Tablist -->\\n  <div class=\\"af-tabs\\" role=\\"tablist\\" aria-label=\\"Módulos de archivo físico\\">\\n    {#each TABS as tab (tab.id)}\\n      <button\\n        id=\\"tab-{tab.id}\\"\\n        class=\\"af-tabs__tab\\"\\n        class:af-tabs__tab--active={activeTab === tab.id}\\n        role=\\"tab\\"\\n        aria-selected={activeTab === tab.id}\\n        aria-controls=\\"panel-{tab.id}\\"\\n        tabindex={activeTab === tab.id ? 0 : -1}\\n        on:click={() => (activeTab = tab.id)}\\n        on:keydown={(e) => handleTabKeydown(e, tab.id)}\\n      >\\n        <i class=\\"fas {tab.icon}\\" aria-hidden=\\"true\\"></i>\\n        <span>{tab.label}</span>\\n      </button>\\n    {/each}\\n  </div>\\n\\n  <!-- Panels -->\\n  <div class=\\"af-workspace__panels\\">\\n    <!-- -----------------------------------------------------------------------\\n         Panel 0 — Ubicaciones\\n         ----------------------------------------------------------------------- -->\\n    <div\\n      id=\\"panel-ubicaciones\\"\\n      role=\\"tabpanel\\"\\n      aria-labelledby=\\"tab-ubicaciones\\"\\n      hidden={activeTab !== \\"ubicaciones\\"}\\n      tabindex=\\"0\\"\\n      class=\\"af-panel\\"\\n    >\\n      <Card>\\n        <h2 slot=\\"header\\" class=\\"op-card-title\\">Ubicaciones de archivo</h2>\\n\\n        {#if ubicacionesLoading}\\n          <Loader label=\\"Cargando ubicaciones…\\" />\\n        {:else if ubicacionesError}\\n          <EmptyState\\n            icon=\\"fas fa-exclamation-triangle\\"\\n            title=\\"Error al cargar ubicaciones\\"\\n            description={ubicacionesError.detail ?? \\"Intenta recargar la página.\\"}\\n          />\\n        {:else if ubicaciones.length === 0}\\n          <EmptyState\\n            icon=\\"fas fa-building\\"\\n            title=\\"Sin ubicaciones\\"\\n            description=\\"No se han registrado ubicaciones de archivo.\\"\\n          />\\n        {:else}\\n          <div class=\\"af-table-wrap\\">\\n            <table class=\\"table is-fullwidth is-hoverable\\">\\n              <caption class=\\"is-sr-only\\">Ubicaciones de archivo físico</caption>\\n              <thead>\\n                <tr>\\n                  <th scope=\\"col\\">Nivel</th>\\n                  <th scope=\\"col\\">Código</th>\\n                  <th scope=\\"col\\">Nombre</th>\\n                  <th scope=\\"col\\">Tipo</th>\\n                  <th scope=\\"col\\">Cap. (ml)</th>\\n                  <th scope=\\"col\\"><span class=\\"is-sr-only\\">Acciones</span></th>\\n                </tr>\\n              </thead>\\n              <tbody>\\n                {#each ubicaciones as ub (ub.id)}\\n                  <tr>\\n                    <td>{ub.nivel ?? \\"—\\"}</td>\\n                    <td><code>{ub.codigo ?? \\"—\\"}</code></td>\\n                    <td>{ub.nombre ?? \\"—\\"}</td>\\n                    <td>{ub.tipo_archivo ?? \\"—\\"}</td>\\n                    <td>{ub.capacidad_ml ?? \\"—\\"}</td>\\n                    <td class=\\"af-actions-cell\\">\\n                      <button\\n                        class=\\"button is-small is-light\\"\\n                        aria-label=\\"Ver hijos de {ub.nombre}\\"\\n                        aria-expanded={ubicacionExpanded[ub.id] ?? false}\\n                        aria-busy={ubicacionChildrenLoading[ub.id] ?? false}\\n                        on:click={() => toggleUbicacion(ub.id)}\\n                      >\\n                        {#if ubicacionChildrenLoading[ub.id]}\\n                          <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n                        {:else if ubicacionExpanded[ub.id]}\\n                          <i class=\\"fas fa-chevron-up\\" aria-hidden=\\"true\\"></i>\\n                        {:else}\\n                          <i class=\\"fas fa-chevron-down\\" aria-hidden=\\"true\\"></i>\\n                        {/if}\\n                        <span class=\\"is-sr-only\\">Ver hijos</span>\\n                      </button>\\n                    </td>\\n                  </tr>\\n                  {#if ubicacionExpanded[ub.id] && ubicacionChildren[ub.id]}\\n                    {#each ubicacionChildren[ub.id] as child (child.id)}\\n                      <tr class=\\"af-child-row\\">\\n                        <td class=\\"af-child-indent\\">{child.nivel ?? \\"—\\"}</td>\\n                        <td><code>{child.codigo ?? \\"—\\"}</code></td>\\n                        <td>{child.nombre ?? \\"—\\"}</td>\\n                        <td>{child.tipo_archivo ?? \\"—\\"}</td>\\n                        <td>{child.capacidad_ml ?? \\"—\\"}</td>\\n                        <td></td>\\n                      </tr>\\n                    {/each}\\n                  {/if}\\n                {/each}\\n              </tbody>\\n            </table>\\n          </div>\\n        {/if}\\n      </Card>\\n    </div>\\n\\n    <!-- -----------------------------------------------------------------------\\n         Panel 1 — Unidades\\n         ----------------------------------------------------------------------- -->\\n    <div\\n      id=\\"panel-unidades\\"\\n      role=\\"tabpanel\\"\\n      aria-labelledby=\\"tab-unidades\\"\\n      hidden={activeTab !== \\"unidades\\"}\\n      tabindex=\\"0\\"\\n      class=\\"af-panel\\"\\n    >\\n      <Card>\\n        <h2 slot=\\"header\\" class=\\"op-card-title\\">Unidades de conservación</h2>\\n\\n        <!-- Filtros de búsqueda -->\\n        <div class=\\"af-filter-row\\" role=\\"search\\" aria-label=\\"Filtros de unidades\\">\\n          <div class=\\"af-filter-row__field\\">\\n            <label for=\\"unidad-filtro-ubicacion\\" class=\\"label\\">Ubicación ID</label>\\n            <input\\n              id=\\"unidad-filtro-ubicacion\\"\\n              class=\\"input\\"\\n              type=\\"text\\"\\n              placeholder=\\"UUID de la ubicación\\"\\n              bind:value={unidadFiltroUbicacionId}\\n              aria-label=\\"ID de ubicación\\"\\n            />\\n          </div>\\n          <div class=\\"af-filter-row__field\\">\\n            <label for=\\"unidad-filtro-tipo\\" class=\\"label\\">Tipo</label>\\n            <div class=\\"select\\">\\n              <select\\n                id=\\"unidad-filtro-tipo\\"\\n                bind:value={unidadFiltroTipo}\\n                aria-label=\\"Tipo de unidad\\"\\n              >\\n                <option value=\\"\\">Todos</option>\\n                <option value=\\"caja\\">Caja</option>\\n                <option value=\\"carpeta\\">Carpeta</option>\\n                <option value=\\"legajo\\">Legajo</option>\\n                <option value=\\"libro\\">Libro</option>\\n                <option value=\\"tomo\\">Tomo</option>\\n                <option value=\\"az\\">AZ</option>\\n                <option value=\\"folder\\">Folder</option>\\n              </select>\\n            </div>\\n          </div>\\n          <Button variant=\\"primary\\" on:click={buscarUnidades} disabled={unidadesLoading}>\\n            {#if unidadesLoading}\\n              <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n            {:else}\\n              <i class=\\"fas fa-search\\" aria-hidden=\\"true\\"></i>\\n            {/if}\\n            Buscar\\n          </Button>\\n        </div>\\n\\n        <div aria-live=\\"polite\\" aria-atomic=\\"false\\">\\n          {#if unidadesLoading}\\n            <Loader label=\\"Buscando unidades…\\" />\\n          {:else if unidadesError}\\n            <EmptyState\\n              icon=\\"fas fa-exclamation-triangle\\"\\n              title=\\"Error al buscar\\"\\n              description={unidadesError}\\n            />\\n          {:else if unidades.length === 0}\\n            <EmptyState\\n              icon=\\"fas fa-box-open\\"\\n              title=\\"Sin resultados\\"\\n              description=\\"Aplica filtros y pulsa Buscar para ver unidades de conservación.\\"\\n            />\\n          {:else}\\n            <div class=\\"af-table-wrap\\" aria-busy={unidadesLoading}>\\n              <table class=\\"table is-fullwidth is-hoverable\\">\\n                <caption class=\\"is-sr-only\\">Unidades de conservación encontradas</caption>\\n                <thead>\\n                  <tr>\\n                    <th scope=\\"col\\">Tipo</th>\\n                    <th scope=\\"col\\">Código</th>\\n                    <th scope=\\"col\\">Signatura</th>\\n                    <th scope=\\"col\\">Soporte</th>\\n                    <th scope=\\"col\\">Folios</th>\\n                    <th scope=\\"col\\"><span class=\\"is-sr-only\\">Acciones</span></th>\\n                  </tr>\\n                </thead>\\n                <tbody>\\n                  {#each unidades as u (u.id)}\\n                    <tr>\\n                      <td><span class=\\"tag is-light\\">{u.tipo ?? \\"—\\"}</span></td>\\n                      <td><code>{u.codigo ?? \\"—\\"}</code></td>\\n                      <td>{u.signatura ?? \\"—\\"}</td>\\n                      <td>{u.soporte ?? \\"—\\"}</td>\\n                      <td>{u.num_folios ?? \\"—\\"}</td>\\n                      <td class=\\"af-actions-cell\\">\\n                        {#if canManage}\\n                          <button\\n                            class=\\"button is-small is-light\\"\\n                            on:click={() => openVincularModal(u.id)}\\n                            aria-label=\\"Vincular expediente a {u.codigo}\\"\\n                          >\\n                            <i class=\\"fas fa-link\\" aria-hidden=\\"true\\"></i>\\n                            <span>Vincular expte.</span>\\n                          </button>\\n                        {/if}\\n                        <button\\n                          class=\\"button is-small is-light\\"\\n                          on:click={() => verMovimientos(u.id, u.codigo)}\\n                          aria-label=\\"Ver movimientos de {u.codigo}\\"\\n                        >\\n                          <i class=\\"fas fa-history\\" aria-hidden=\\"true\\"></i>\\n                          <span>Movimientos</span>\\n                        </button>\\n                      </td>\\n                    </tr>\\n                  {/each}\\n                </tbody>\\n              </table>\\n            </div>\\n          {/if}\\n        </div>\\n      </Card>\\n    </div>\\n\\n    <!-- -----------------------------------------------------------------------\\n         Panel 2 — Localizar expediente\\n         ----------------------------------------------------------------------- -->\\n    <div\\n      id=\\"panel-localizar\\"\\n      role=\\"tabpanel\\"\\n      aria-labelledby=\\"tab-localizar\\"\\n      hidden={activeTab !== \\"localizar\\"}\\n      tabindex=\\"0\\"\\n      class=\\"af-panel\\"\\n    >\\n      <Card>\\n        <h2 slot=\\"header\\" class=\\"op-card-title\\">Localizar expediente en archivo</h2>\\n\\n        <div class=\\"af-filter-row\\" role=\\"search\\" aria-label=\\"Localizar expediente\\">\\n          <div class=\\"af-filter-row__field\\">\\n            <label for=\\"localizar-id\\" class=\\"label\\">ID de expediente</label>\\n            <input\\n              id=\\"localizar-id\\"\\n              class=\\"input\\"\\n              type=\\"text\\"\\n              placeholder=\\"UUID del expediente\\"\\n              bind:value={localizarExpedienteId}\\n              on:keydown={(e) => e.key === \\"Enter\\" && handleLocalizar()}\\n              aria-label=\\"ID del expediente a localizar\\"\\n            />\\n          </div>\\n          <Button variant=\\"primary\\" on:click={handleLocalizar} disabled={localizarLoading}>\\n            {#if localizarLoading}\\n              <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n            {:else}\\n              <i class=\\"fas fa-map-marker-alt\\" aria-hidden=\\"true\\"></i>\\n            {/if}\\n            Localizar\\n          </Button>\\n        </div>\\n\\n        <div aria-live=\\"polite\\" aria-atomic=\\"false\\">\\n          {#if localizarLoading}\\n            <Loader label=\\"Localizando expediente…\\" />\\n          {:else if localizarError}\\n            <EmptyState\\n              icon=\\"fas fa-exclamation-triangle\\"\\n              title=\\"Error\\"\\n              description={localizarError}\\n            />\\n          {:else if localizarBuscado && localizarResultados.length === 0}\\n            <EmptyState\\n              icon=\\"fas fa-search\\"\\n              title=\\"No encontrado\\"\\n              description=\\"El expediente no está vinculado a ninguna unidad de conservación.\\"\\n            />\\n          {:else if localizarResultados.length > 0}\\n            <div class=\\"af-table-wrap\\">\\n              <table class=\\"table is-fullwidth\\">\\n                <caption>Unidades donde se encuentra el expediente</caption>\\n                <thead>\\n                  <tr>\\n                    <th scope=\\"col\\">Signatura</th>\\n                    <th scope=\\"col\\">Tipo unidad</th>\\n                    <th scope=\\"col\\">Código unidad</th>\\n                    <th scope=\\"col\\">Folio inicio</th>\\n                    <th scope=\\"col\\">Folio fin</th>\\n                  </tr>\\n                </thead>\\n                <tbody>\\n                  {#each localizarResultados as r (r.id ?? r.unidad_id)}\\n                    <tr>\\n                      <td>{r.signatura ?? r.unidad?.signatura ?? \\"—\\"}</td>\\n                      <td>{r.tipo ?? r.unidad?.tipo ?? \\"—\\"}</td>\\n                      <td><code>{r.codigo ?? r.unidad?.codigo ?? \\"—\\"}</code></td>\\n                      <td>{r.folio_inicio ?? \\"—\\"}</td>\\n                      <td>{r.folio_fin ?? \\"—\\"}</td>\\n                    </tr>\\n                  {/each}\\n                </tbody>\\n              </table>\\n            </div>\\n          {:else}\\n            <EmptyState\\n              icon=\\"fas fa-map-marker-alt\\"\\n              title=\\"Busca un expediente\\"\\n              description=\\"Introduce el UUID del expediente y pulsa Localizar.\\"\\n            />\\n          {/if}\\n        </div>\\n      </Card>\\n    </div>\\n\\n    <!-- -----------------------------------------------------------------------\\n         Panel 3 — Préstamos\\n         ----------------------------------------------------------------------- -->\\n    <div\\n      id=\\"panel-prestamos\\"\\n      role=\\"tabpanel\\"\\n      aria-labelledby=\\"tab-prestamos\\"\\n      hidden={activeTab !== \\"prestamos\\"}\\n      tabindex=\\"0\\"\\n      class=\\"af-panel\\"\\n    >\\n      <Card>\\n        <div slot=\\"header\\" class=\\"af-panel-header\\">\\n          <h2 class=\\"op-card-title\\">Préstamos de unidades</h2>\\n          {#if canManage}\\n            <Button variant=\\"primary\\" size=\\"sm\\" on:click={() => (showPrestamoModal = true)}>\\n              <i class=\\"fas fa-plus\\" aria-hidden=\\"true\\"></i>\\n              Registrar préstamo\\n            </Button>\\n          {/if}\\n        </div>\\n\\n        <!-- Selector de estado -->\\n        <div class=\\"af-filter-row\\">\\n          <div class=\\"af-filter-row__field\\">\\n            <label for=\\"prestamos-estado\\" class=\\"label\\">Estado</label>\\n            <div class=\\"select\\">\\n              <select\\n                id=\\"prestamos-estado\\"\\n                bind:value={prestamosEstado}\\n                on:change={cargarPrestamos}\\n                aria-label=\\"Filtrar préstamos por estado\\"\\n              >\\n                <option value=\\"prestado\\">Prestado</option>\\n                <option value=\\"vencido\\">Vencido</option>\\n                <option value=\\"devuelto\\">Devuelto</option>\\n              </select>\\n            </div>\\n          </div>\\n        </div>\\n\\n        {#if prestamosLoading}\\n          <Loader label=\\"Cargando préstamos…\\" />\\n        {:else if prestamosVisibles.length === 0}\\n          <EmptyState\\n            icon=\\"fas fa-hand-holding\\"\\n            title=\\"Sin préstamos\\"\\n            description=\\"No hay préstamos con el estado seleccionado.\\"\\n          />\\n        {:else}\\n          <div class=\\"af-table-wrap\\" aria-busy={prestamosLoading}>\\n            <table class=\\"table is-fullwidth is-hoverable\\">\\n              <caption class=\\"is-sr-only\\">Lista de préstamos</caption>\\n              <thead>\\n                <tr>\\n                  <th scope=\\"col\\">Unidad</th>\\n                  <th scope=\\"col\\">Prestado a</th>\\n                  <th scope=\\"col\\">Fecha préstamo</th>\\n                  <th scope=\\"col\\">Fecha devolución esp.</th>\\n                  <th scope=\\"col\\">Estado</th>\\n                  <th scope=\\"col\\"><span class=\\"is-sr-only\\">Acciones</span></th>\\n                </tr>\\n              </thead>\\n              <tbody>\\n                {#each prestamosVisibles as p (p.id)}\\n                  {@const estadoInfo = ESTADO_PRESTAMO_MAP[p.estado] ?? {\\n                    label: p.estado,\\n                    status: \\"neutral\\",\\n                  }}\\n                  <tr>\\n                    <td><code>{p.unidad_codigo ?? p.unidad?.codigo ?? \\"—\\"}</code></td>\\n                    <td>{p.prestado_a ?? \\"—\\"}</td>\\n                    <td>{fmtDate(p.fecha_prestamo ?? p.created_at)}</td>\\n                    <td>{fmtDate(p.fecha_devolucion_esperada)}</td>\\n                    <td>\\n                      <StatusChip status={estadoInfo.status} label={estadoInfo.label} dot />\\n                    </td>\\n                    <td class=\\"af-actions-cell\\">\\n                      {#if canManage && p.estado === \\"prestado\\"}\\n                        <button\\n                          class=\\"button is-small is-warning is-light\\"\\n                          on:click={() => openDevolverModal(p.id, p.prestado_a)}\\n                          aria-label=\\"Registrar devolución del préstamo a {p.prestado_a}\\"\\n                        >\\n                          <i class=\\"fas fa-undo\\" aria-hidden=\\"true\\"></i>\\n                          <span>Devolver</span>\\n                        </button>\\n                      {/if}\\n                      <button\\n                        class=\\"button is-small is-light\\"\\n                        on:click={() =>\\n                          verMovimientos(\\n                            p.unidad_id ?? p.unidad?.id,\\n                            p.unidad_codigo ?? p.unidad?.codigo,\\n                          )}\\n                        aria-label=\\"Ver movimientos\\"\\n                      >\\n                        <i class=\\"fas fa-history\\" aria-hidden=\\"true\\"></i>\\n                        <span class=\\"is-sr-only\\">Movimientos</span>\\n                      </button>\\n                    </td>\\n                  </tr>\\n                {/each}\\n              </tbody>\\n            </table>\\n          </div>\\n        {/if}\\n      </Card>\\n    </div>\\n\\n    <!-- -----------------------------------------------------------------------\\n         Panel 4 — FUID\\n         ----------------------------------------------------------------------- -->\\n    <div\\n      id=\\"panel-fuid\\"\\n      role=\\"tabpanel\\"\\n      aria-labelledby=\\"tab-fuid\\"\\n      hidden={activeTab !== \\"fuid\\"}\\n      tabindex=\\"0\\"\\n      class=\\"af-panel\\"\\n    >\\n      <Card>\\n        <h2 slot=\\"header\\" class=\\"op-card-title\\">FUID — Formato Único de Inventario Documental</h2>\\n\\n        <!-- Filtros -->\\n        <div class=\\"af-filter-row\\" role=\\"search\\" aria-label=\\"Filtros FUID\\">\\n          <div class=\\"af-filter-row__field\\">\\n            <label for=\\"fuid-ubicacion-id\\" class=\\"label\\">Ubicación ID</label>\\n            <input\\n              id=\\"fuid-ubicacion-id\\"\\n              class=\\"input\\"\\n              type=\\"text\\"\\n              placeholder=\\"UUID de ubicación (opcional)\\"\\n              bind:value={fuidFiltroUbicacionId}\\n              aria-label=\\"UUID de ubicación para filtrar FUID\\"\\n            />\\n          </div>\\n          <div class=\\"af-filter-row__field\\">\\n            <label for=\\"fuid-trd-serie-id\\" class=\\"label\\">Serie TRD ID</label>\\n            <input\\n              id=\\"fuid-trd-serie-id\\"\\n              class=\\"input\\"\\n              type=\\"text\\"\\n              placeholder=\\"UUID de serie TRD (opcional)\\"\\n              bind:value={fuidFiltroTrdSerieId}\\n              aria-label=\\"UUID de serie TRD para filtrar FUID\\"\\n            />\\n          </div>\\n          <div class=\\"af-filter-row__actions\\">\\n            <Button variant=\\"primary\\" on:click={consultarFuid} disabled={fuidLoading}>\\n              {#if fuidLoading}\\n                <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n              {:else}\\n                <i class=\\"fas fa-search\\" aria-hidden=\\"true\\"></i>\\n              {/if}\\n              Consultar\\n            </Button>\\n            <Button variant=\\"secondary\\" on:click={handleDescargarFuidXml} disabled={fuidXmlLoading}>\\n              {#if fuidXmlLoading}\\n                <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n              {:else}\\n                <i class=\\"fas fa-file-download\\" aria-hidden=\\"true\\"></i>\\n              {/if}\\n              Descargar XML\\n            </Button>\\n          </div>\\n        </div>\\n\\n        <div aria-live=\\"polite\\" aria-atomic=\\"false\\">\\n          {#if fuidLoading}\\n            <Loader label=\\"Consultando FUID…\\" />\\n          {:else if fuidError}\\n            <EmptyState\\n              icon=\\"fas fa-exclamation-triangle\\"\\n              title=\\"Error al consultar FUID\\"\\n              description={fuidError}\\n            />\\n          {:else if fuidBuscado && fuidRows.length === 0}\\n            <EmptyState\\n              icon=\\"fas fa-file-alt\\"\\n              title=\\"Sin registros\\"\\n              description=\\"No se encontraron registros FUID con los filtros indicados.\\"\\n            />\\n          {:else if fuidRows.length > 0}\\n            <div class=\\"af-table-wrap\\" aria-busy={fuidLoading}>\\n              <table class=\\"table is-fullwidth is-hoverable\\">\\n                <caption>Formato Único de Inventario Documental</caption>\\n                <thead>\\n                  <tr>\\n                    <th scope=\\"col\\">Orden</th>\\n                    <th scope=\\"col\\">Código expediente</th>\\n                    <th scope=\\"col\\">Asunto</th>\\n                    <th scope=\\"col\\">Serie</th>\\n                    <th scope=\\"col\\">Signatura</th>\\n                    <th scope=\\"col\\">Unidad</th>\\n                    <th scope=\\"col\\">Soporte</th>\\n                    <th scope=\\"col\\">Folios</th>\\n                  </tr>\\n                </thead>\\n                <tbody>\\n                  {#each fuidRows as row, i (row.id ?? row.expediente_id ?? i)}\\n                    <tr>\\n                      <td>{i + 1}</td>\\n                      <td><code>{row.codigo_expediente ?? row.expediente?.code ?? \\"—\\"}</code></td>\\n                      <td>{row.asunto ?? row.expediente?.name ?? \\"—\\"}</td>\\n                      <td>{row.serie ?? row.trd_serie?.nombre ?? \\"—\\"}</td>\\n                      <td>{row.signatura ?? \\"—\\"}</td>\\n                      <td>{row.unidad ?? row.unidad_codigo ?? \\"—\\"}</td>\\n                      <td>{row.soporte ?? \\"—\\"}</td>\\n                      <td>{row.num_folios ?? row.folios ?? \\"—\\"}</td>\\n                    </tr>\\n                  {/each}\\n                </tbody>\\n              </table>\\n            </div>\\n          {:else}\\n            <EmptyState\\n              icon=\\"fas fa-file-alt\\"\\n              title=\\"Consulta el FUID\\"\\n              description=\\"Aplica filtros opcionales y pulsa Consultar para ver el inventario documental.\\"\\n            />\\n          {/if}\\n        </div>\\n      </Card>\\n    </div>\\n  </div>\\n</div>\\n\\n<!-- =========================================================================\\n     Modales\\n     ========================================================================= -->\\n\\n<!-- Modal: crear ubicación -->\\n<Modal\\n  open={showUbicacionModal}\\n  title=\\"Nueva ubicación de archivo\\"\\n  on:close={() => (showUbicacionModal = false)}\\n>\\n  <div class=\\"af-form\\">\\n    <FormField\\n      id=\\"ub-nivel\\"\\n      label=\\"Nivel *\\"\\n      type=\\"select\\"\\n      required\\n      placeholder=\\"— Selecciona nivel —\\"\\n      bind:value={ubicacionForm.nivel}\\n      options={NIVELES_UBICACION_OPTIONS}\\n    />\\n    <FormField\\n      id=\\"ub-codigo\\"\\n      label=\\"Código *\\"\\n      type=\\"text\\"\\n      required\\n      placeholder=\\"Ej. EST-001\\"\\n      bind:value={ubicacionForm.codigo}\\n    />\\n    <FormField\\n      id=\\"ub-nombre\\"\\n      label=\\"Nombre *\\"\\n      type=\\"text\\"\\n      required\\n      placeholder=\\"Nombre descriptivo\\"\\n      bind:value={ubicacionForm.nombre}\\n    />\\n    <FormField\\n      id=\\"ub-parent\\"\\n      label=\\"ID padre (vacío = raíz)\\"\\n      type=\\"text\\"\\n      placeholder=\\"UUID de la ubicación padre (opcional)\\"\\n      bind:value={ubicacionForm.parent_id}\\n    />\\n    <FormField\\n      id=\\"ub-tipo\\"\\n      label=\\"Tipo de archivo\\"\\n      type=\\"select\\"\\n      placeholder=\\"— Sin tipo —\\"\\n      bind:value={ubicacionForm.tipo_archivo}\\n      options={TIPOS_ARCHIVO_OPTIONS}\\n    />\\n  </div>\\n  <div slot=\\"footer\\" class=\\"af-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showUbicacionModal = false)}>Cancelar</Button>\\n    <Button variant=\\"primary\\" on:click={handleCrearUbicacion} disabled={ubicacionFormLoading}>\\n      {#if ubicacionFormLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Crear ubicación\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: nueva unidad de conservación -->\\n<Modal\\n  open={showNuevaUnidadModal}\\n  title=\\"Nueva unidad de conservación\\"\\n  on:close={() => (showNuevaUnidadModal = false)}\\n>\\n  <div class=\\"af-form\\">\\n    <FormField\\n      id=\\"nu-tipo\\"\\n      label=\\"Tipo *\\"\\n      type=\\"select\\"\\n      required\\n      placeholder=\\"— Selecciona tipo —\\"\\n      bind:value={nuevaUnidadForm.tipo}\\n      options={TIPOS_UNIDAD_OPTIONS}\\n    />\\n    <FormField\\n      id=\\"nu-codigo\\"\\n      label=\\"Código *\\"\\n      type=\\"text\\"\\n      required\\n      placeholder=\\"Ej. CAJ-001\\"\\n      bind:value={nuevaUnidadForm.codigo}\\n    />\\n    <FormField\\n      id=\\"nu-ubicacion\\"\\n      label=\\"ID de ubicación\\"\\n      type=\\"text\\"\\n      placeholder=\\"UUID de la ubicación (opcional)\\"\\n      bind:value={nuevaUnidadForm.ubicacion_id}\\n    />\\n    <FormField\\n      id=\\"nu-soporte\\"\\n      label=\\"Soporte\\"\\n      type=\\"text\\"\\n      placeholder=\\"Ej. papel, digital\\"\\n      bind:value={nuevaUnidadForm.soporte}\\n    />\\n    <FormField\\n      id=\\"nu-folios\\"\\n      label=\\"N.° de folios\\"\\n      type=\\"number\\"\\n      placeholder=\\"0\\"\\n      bind:value={nuevaUnidadForm.num_folios}\\n    />\\n  </div>\\n  <div slot=\\"footer\\" class=\\"af-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showNuevaUnidadModal = false)}>Cancelar</Button>\\n    <Button variant=\\"primary\\" on:click={handleCrearUnidad} disabled={nuevaUnidadLoading}>\\n      {#if nuevaUnidadLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Crear unidad\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: vincular expediente a unidad -->\\n<Modal\\n  open={showVincularModal}\\n  title=\\"Vincular expediente a unidad\\"\\n  on:close={() => (showVincularModal = false)}\\n>\\n  <div class=\\"af-form\\">\\n    <FormField\\n      id=\\"vin-expte\\"\\n      label=\\"ID del expediente *\\"\\n      type=\\"text\\"\\n      required\\n      placeholder=\\"UUID del expediente\\"\\n      bind:value={vincularForm.expediente_id}\\n    />\\n    <FormField\\n      id=\\"vin-folio-ini\\"\\n      label=\\"Folio inicio\\"\\n      type=\\"number\\"\\n      placeholder=\\"Número de folio inicial\\"\\n      bind:value={vincularForm.folio_inicio}\\n    />\\n    <FormField\\n      id=\\"vin-folio-fin\\"\\n      label=\\"Folio fin\\"\\n      type=\\"number\\"\\n      placeholder=\\"Número de folio final\\"\\n      bind:value={vincularForm.folio_fin}\\n    />\\n  </div>\\n  <div slot=\\"footer\\" class=\\"af-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showVincularModal = false)}>Cancelar</Button>\\n    <Button variant=\\"primary\\" on:click={handleVincularExpediente} disabled={vincularLoading}>\\n      {#if vincularLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Vincular\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: registrar préstamo -->\\n<Modal\\n  open={showPrestamoModal}\\n  title=\\"Registrar préstamo\\"\\n  on:close={() => (showPrestamoModal = false)}\\n>\\n  <div class=\\"af-form\\">\\n    <FormField\\n      id=\\"pr-unidad\\"\\n      label=\\"ID de la unidad *\\"\\n      type=\\"text\\"\\n      required\\n      placeholder=\\"UUID de la unidad a prestar\\"\\n      bind:value={prestamoForm.unidad_id}\\n    />\\n    <FormField\\n      id=\\"pr-prestado-a\\"\\n      label=\\"Prestado a *\\"\\n      type=\\"text\\"\\n      required\\n      placeholder=\\"Nombre del solicitante\\"\\n      bind:value={prestamoForm.prestado_a}\\n    />\\n    <FormField\\n      id=\\"pr-fecha\\"\\n      label=\\"Fecha de devolución esperada *\\"\\n      type=\\"date\\"\\n      required\\n      bind:value={prestamoForm.fecha_devolucion_esperada}\\n    />\\n  </div>\\n  <div slot=\\"footer\\" class=\\"af-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showPrestamoModal = false)}>Cancelar</Button>\\n    <Button variant=\\"primary\\" on:click={handleRegistrarPrestamo} disabled={prestamoLoading}>\\n      {#if prestamoLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Registrar préstamo\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: confirmar devolución -->\\n<Modal\\n  open={showDevolverModal}\\n  title=\\"Confirmar devolución\\"\\n  size=\\"sm\\"\\n  on:close={() => (showDevolverModal = false)}\\n>\\n  <p>\\n    ¿Confirmas la devolución del préstamo a <strong>{devolverPrestamoDesc}</strong>? Esta acción\\n    quedará registrada en la auditoría del sistema.\\n  </p>\\n  <div slot=\\"footer\\" class=\\"af-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showDevolverModal = false)}>Cancelar</Button>\\n    <Button variant=\\"primary\\" on:click={handleDevolver} disabled={devolverLoading}>\\n      {#if devolverLoading}\\n        <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n      {/if}\\n      Confirmar devolución\\n    </Button>\\n  </div>\\n</Modal>\\n\\n<!-- Modal: historial de movimientos -->\\n<Modal\\n  open={showMovimientosModal}\\n  title=\\"Movimientos de la unidad {movimientosUnidadDesc}\\"\\n  size=\\"lg\\"\\n  on:close={() => (showMovimientosModal = false)}\\n>\\n  {#if movimientosLoading}\\n    <Loader label=\\"Cargando movimientos…\\" />\\n  {:else if movimientos.length === 0}\\n    <EmptyState\\n      icon=\\"fas fa-history\\"\\n      title=\\"Sin movimientos\\"\\n      description=\\"Esta unidad no tiene movimientos registrados.\\"\\n    />\\n  {:else}\\n    <div class=\\"af-table-wrap\\">\\n      <table class=\\"table is-fullwidth\\">\\n        <caption class=\\"is-sr-only\\">Historial de movimientos</caption>\\n        <thead>\\n          <tr>\\n            <th scope=\\"col\\">Fecha</th>\\n            <th scope=\\"col\\">Tipo</th>\\n            <th scope=\\"col\\">Usuario</th>\\n            <th scope=\\"col\\">Detalle</th>\\n          </tr>\\n        </thead>\\n        <tbody>\\n          {#each movimientos as m (m.id)}\\n            <tr>\\n              <td>{fmtDate(m.created_at ?? m.fecha)}</td>\\n              <td>{m.tipo ?? m.tipo_movimiento ?? \\"—\\"}</td>\\n              <td>{m.usuario ?? m.user_id ?? \\"—\\"}</td>\\n              <td>{m.detalle ?? m.observacion ?? \\"—\\"}</td>\\n            </tr>\\n          {/each}\\n        </tbody>\\n      </table>\\n    </div>\\n  {/if}\\n  <div slot=\\"footer\\" class=\\"af-modal-footer\\">\\n    <Button variant=\\"secondary\\" on:click={() => (showMovimientosModal = false)}>Cerrar</Button>\\n  </div>\\n</Modal>\\n\\n<style lang=\\"scss\\">@charset \\"UTF-8\\";\\n.af-workspace {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n  padding: var(--op-space-5);\\n}\\n.af-workspace__header {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-3);\\n}\\n.af-workspace__title {\\n  font-size: var(--op-font-xl);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n}\\n.af-workspace__title i {\\n  color: var(--op-primary);\\n}\\n.af-workspace__header-actions {\\n  display: flex;\\n  gap: var(--op-space-2);\\n  flex-wrap: wrap;\\n}\\n.af-workspace__panels {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: 0 0 var(--op-radius) var(--op-radius);\\n  border-top: none;\\n}\\n\\n.af-tabs {\\n  display: flex;\\n  border-bottom: 2px solid var(--op-border);\\n  background: var(--op-bg);\\n  overflow-x: auto;\\n}\\n.af-tabs__tab {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  padding: var(--op-space-3) var(--op-space-4);\\n  border: none;\\n  background: transparent;\\n  color: var(--op-text-secondary);\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  cursor: pointer;\\n  white-space: nowrap;\\n  border-bottom: 2px solid transparent;\\n  margin-bottom: -2px;\\n  transition: color var(--op-transition), border-color var(--op-transition);\\n}\\n.af-tabs__tab:hover {\\n  color: var(--op-primary);\\n}\\n.af-tabs__tab--active {\\n  color: var(--op-primary-dark);\\n  font-weight: var(--op-weight-bold);\\n  border-bottom-color: var(--op-primary);\\n}\\n.af-tabs__tab:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: -2px;\\n}\\n\\n.af-panel {\\n  padding: var(--op-space-5);\\n}\\n\\n.af-panel-header {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  gap: var(--op-space-2);\\n}\\n\\n.af-filter-row {\\n  display: flex;\\n  align-items: flex-end;\\n  gap: var(--op-space-3);\\n  flex-wrap: wrap;\\n  margin-bottom: var(--op-space-4);\\n}\\n.af-filter-row__field {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-1);\\n  min-width: 200px;\\n  flex: 1;\\n}\\n.af-filter-row__actions {\\n  display: flex;\\n  gap: var(--op-space-2);\\n}\\n\\n.af-table-wrap {\\n  overflow-x: auto;\\n}\\n.af-table-wrap .table {\\n  border-collapse: collapse;\\n  width: 100%;\\n}\\n.af-table-wrap .table th {\\n  background: var(--op-bg);\\n  color: var(--op-text-secondary);\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  text-transform: uppercase;\\n  letter-spacing: 0.04em;\\n  padding: var(--op-space-2) var(--op-space-3);\\n  border-bottom: 2px solid var(--op-border);\\n}\\n.af-table-wrap .table td {\\n  padding: var(--op-space-2) var(--op-space-3);\\n  border-bottom: 1px solid var(--op-border);\\n  color: var(--op-text);\\n  font-size: var(--op-font-sm);\\n  vertical-align: middle;\\n}\\n.af-table-wrap .table caption {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n  margin-bottom: var(--op-space-2);\\n  text-align: left;\\n}\\n\\n.af-actions-cell {\\n  display: flex;\\n  gap: var(--op-space-1);\\n  justify-content: flex-end;\\n  align-items: center;\\n  white-space: nowrap;\\n}\\n\\n.af-child-row td:first-child {\\n  padding-left: var(--op-space-6);\\n  color: var(--op-text-secondary);\\n}\\n\\n.af-child-indent {\\n  position: relative;\\n}\\n.af-child-indent::before {\\n  content: \\"└ \\";\\n  color: var(--op-border);\\n}\\n\\n.af-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n  padding: var(--op-space-2) 0;\\n}\\n\\n.af-modal-footer {\\n  display: flex;\\n  justify-content: flex-end;\\n  gap: var(--op-space-2);\\n}\\n\\n:global(.op-card-title) {\\n  font-size: var(--op-font-md);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}</style>\\n"],"names":[],"mappings":"AAy5CmB,SAAS,OAAO,CACnC,yCAAc,CACZ,OAAO,CAAE,IAAI,CACb,cAAc,CAAE,MAAM,CACtB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,OAAO,CAAE,IAAI,YAAY,CAC3B,CACA,iDAAsB,CACpB,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,MAAM,CACnB,eAAe,CAAE,aAAa,CAC9B,SAAS,CAAE,IAAI,CACf,GAAG,CAAE,IAAI,YAAY,CACvB,CACA,gDAAqB,CACnB,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,MAAM,CACnB,GAAG,CAAE,IAAI,YAAY,CACvB,CACA,kCAAoB,CAAC,eAAE,CACrB,KAAK,CAAE,IAAI,YAAY,CACzB,CACA,yDAA8B,CAC5B,OAAO,CAAE,IAAI,CACb,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,SAAS,CAAE,IACb,CACA,iDAAsB,CACpB,UAAU,CAAE,IAAI,YAAY,CAAC,CAC7B,MAAM,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,WAAW,CAAC,CAClC,aAAa,CAAE,CAAC,CAAC,CAAC,CAAC,IAAI,WAAW,CAAC,CAAC,IAAI,WAAW,CAAC,CACpD,UAAU,CAAE,IACd,CAEA,oCAAS,CACP,OAAO,CAAE,IAAI,CACb,aAAa,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,WAAW,CAAC,CACzC,UAAU,CAAE,IAAI,OAAO,CAAC,CACxB,UAAU,CAAE,IACd,CACA,yCAAc,CACZ,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,MAAM,CACnB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,OAAO,CAAE,IAAI,YAAY,CAAC,CAAC,IAAI,YAAY,CAAC,CAC5C,MAAM,CAAE,IAAI,CACZ,UAAU,CAAE,WAAW,CACvB,KAAK,CAAE,IAAI,mBAAmB,CAAC,CAC/B,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,kBAAkB,CAAC,CACpC,MAAM,CAAE,OAAO,CACf,WAAW,CAAE,MAAM,CACnB,aAAa,CAAE,GAAG,CAAC,KAAK,CAAC,WAAW,CACpC,aAAa,CAAE,IAAI,CACnB,UAAU,CAAE,KAAK,CAAC,IAAI,eAAe,CAAC,CAAC,CAAC,YAAY,CAAC,IAAI,eAAe,CAC1E,CACA,yCAAa,MAAO,CAClB,KAAK,CAAE,IAAI,YAAY,CACzB,CACA,iDAAsB,CACpB,KAAK,CAAE,IAAI,iBAAiB,CAAC,CAC7B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,mBAAmB,CAAE,IAAI,YAAY,CACvC,CACA,yCAAa,cAAe,CAC1B,OAAO,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,YAAY,CAAC,CACpC,cAAc,CAAE,IAClB,CAEA,qCAAU,CACR,OAAO,CAAE,IAAI,YAAY,CAC3B,CAEA,4CAAiB,CACf,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,MAAM,CACnB,eAAe,CAAE,aAAa,CAC9B,GAAG,CAAE,IAAI,YAAY,CACvB,CAEA,0CAAe,CACb,OAAO,CAAE,IAAI,CACb,WAAW,CAAE,QAAQ,CACrB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,SAAS,CAAE,IAAI,CACf,aAAa,CAAE,IAAI,YAAY,CACjC,CACA,iDAAsB,CACpB,OAAO,CAAE,IAAI,CACb,cAAc,CAAE,MAAM,CACtB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,SAAS,CAAE,KAAK,CAChB,IAAI,CAAE,CACR,CACA,mDAAwB,CACtB,OAAO,CAAE,IAAI,CACb,GAAG,CAAE,IAAI,YAAY,CACvB,CAEA,0CAAe,CACb,UAAU,CAAE,IACd,CACA,4BAAc,CAAC,oBAAO,CACpB,eAAe,CAAE,QAAQ,CACzB,KAAK,CAAE,IACT,CACA,4BAAc,CAAC,MAAM,CAAC,gBAAG,CACvB,UAAU,CAAE,IAAI,OAAO,CAAC,CACxB,KAAK,CAAE,IAAI,mBAAmB,CAAC,CAC/B,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,cAAc,CAAE,SAAS,CACzB,cAAc,CAAE,MAAM,CACtB,OAAO,CAAE,IAAI,YAAY,CAAC,CAAC,IAAI,YAAY,CAAC,CAC5C,aAAa,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,WAAW,CAC1C,CACA,4BAAc,CAAC,MAAM,CAAC,gBAAG,CACvB,OAAO,CAAE,IAAI,YAAY,CAAC,CAAC,IAAI,YAAY,CAAC,CAC5C,aAAa,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,WAAW,CAAC,CACzC,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,cAAc,CAAE,MAClB,CACA,4BAAc,CAAC,MAAM,CAAC,qBAAQ,CAC5B,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,KAAK,CAAE,IAAI,mBAAmB,CAAC,CAC/B,aAAa,CAAE,IAAI,YAAY,CAAC,CAChC,UAAU,CAAE,IACd,CAEA,4CAAiB,CACf,OAAO,CAAE,IAAI,CACb,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,eAAe,CAAE,QAAQ,CACzB,WAAW,CAAE,MAAM,CACnB,WAAW,CAAE,MACf,CAEA,2BAAa,CAAC,gBAAE,YAAa,CAC3B,YAAY,CAAE,IAAI,YAAY,CAAC,CAC/B,KAAK,CAAE,IAAI,mBAAmB,CAChC,CAEA,4CAAiB,CACf,QAAQ,CAAE,QACZ,CACA,4CAAgB,QAAS,CACvB,OAAO,CAAE,IAAI,CACb,KAAK,CAAE,IAAI,WAAW,CACxB,CAEA,oCAAS,CACP,OAAO,CAAE,IAAI,CACb,cAAc,CAAE,MAAM,CACtB,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,OAAO,CAAE,IAAI,YAAY,CAAC,CAAC,CAC7B,CAEA,4CAAiB,CACf,OAAO,CAAE,IAAI,CACb,eAAe,CAAE,QAAQ,CACzB,GAAG,CAAE,IAAI,YAAY,CACvB,CAEQ,cAAgB,CACtB,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,MAAM,CAAE,CACV"}'
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
  let prestamosVisibles;
  let $permissions, $$unsubscribe_permissions;
  $$unsubscribe_permissions = subscribe(permissions, (value) => $permissions = value);
  let { data } = $$props;
  const NIVELES_UBICACION_OPTIONS = [
    { value: "sede", label: "Sede" },
    { value: "edificio", label: "Edificio" },
    { value: "piso", label: "Piso" },
    { value: "deposito", label: "Depósito" },
    { value: "modulo", label: "Módulo" },
    { value: "estante", label: "Estante" },
    { value: "entrepano", label: "Entrepaño" },
    { value: "gaveta", label: "Gaveta" },
    {
      value: "custodia_externa",
      label: "Custodia externa"
    }
  ];
  const TIPOS_ARCHIVO_OPTIONS = [
    { value: "gestion", label: "Gestión" },
    { value: "central", label: "Central" },
    { value: "historico", label: "Histórico" }
  ];
  const TIPOS_UNIDAD_OPTIONS = [
    { value: "caja", label: "Caja" },
    { value: "carpeta", label: "Carpeta" },
    { value: "legajo", label: "Legajo" },
    { value: "libro", label: "Libro" },
    { value: "tomo", label: "Tomo" },
    { value: "az", label: "AZ" },
    { value: "folder", label: "Folder" }
  ];
  const TABS = [
    {
      id: "ubicaciones",
      label: "Ubicaciones",
      icon: "fa-building"
    },
    {
      id: "unidades",
      label: "Unidades",
      icon: "fa-box"
    },
    {
      id: "localizar",
      label: "Localizar expediente",
      icon: "fa-map-marker-alt"
    },
    {
      id: "prestamos",
      label: "Préstamos",
      icon: "fa-hand-holding"
    },
    {
      id: "fuid",
      label: "FUID",
      icon: "fa-file-alt"
    }
  ];
  let activeTab = "ubicaciones";
  let ubicaciones = data.facetas?.ubicaciones?.data ?? [];
  let ubicacionesError = data.facetas?.ubicaciones?.error ?? null;
  let ubicacionChildren = {};
  let ubicacionExpanded = {};
  let ubicacionChildrenLoading = {};
  let showUbicacionModal = false;
  let ubicacionForm = {
    nivel: "",
    codigo: "",
    nombre: "",
    parent_id: "",
    tipo_archivo: ""
  };
  let ubicacionFormLoading = false;
  let unidadFiltroUbicacionId = "";
  let unidades = [];
  let unidadesLoading = false;
  let showNuevaUnidadModal = false;
  let nuevaUnidadForm = {
    tipo: "",
    codigo: "",
    ubicacion_id: "",
    soporte: "",
    num_folios: ""
  };
  let nuevaUnidadLoading = false;
  let showVincularModal = false;
  let vincularForm = {
    expediente_id: "",
    folio_inicio: "",
    folio_fin: ""
  };
  let vincularLoading = false;
  let localizarExpedienteId = "";
  let localizarResultados = [];
  let localizarLoading = false;
  let prestamosActivos = data.facetas?.prestamosActivos?.data ?? [];
  data.facetas?.prestamosVencidos?.data ?? [];
  let prestamosLoading = false;
  let prestamoForm = {
    unidad_id: "",
    prestado_a: "",
    fecha_devolucion_esperada: ""
  };
  let prestamoLoading = false;
  let showDevolverModal = false;
  let devolverPrestamoDesc = "";
  let devolverLoading = false;
  let showMovimientosModal = false;
  let movimientos = [];
  let movimientosUnidadDesc = "";
  let fuidFiltroUbicacionId = "";
  let fuidFiltroTrdSerieId = "";
  let fuidRows = [];
  let fuidLoading = false;
  let fuidXmlLoading = false;
  const ESTADO_PRESTAMO_MAP = {
    prestado: { label: "Prestado", status: "warning" },
    devuelto: { label: "Devuelto", status: "success" },
    vencido: { label: "Vencido", status: "danger" }
  };
  let showPrestamoModal = false;
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    canManage = $permissions.includes("USUA_PERM_EXPEDIENTE");
    prestamosVisibles = prestamosActivos;
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-18v5ppa_START -->${$$result.title = `<title>Archivo físico — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-18v5ppa_END -->`, ""}  <div class="af-workspace svelte-sai9mo"> <div class="af-workspace__header svelte-sai9mo"><h1 class="af-workspace__title svelte-sai9mo" data-svelte-h="svelte-16ax41i"><i class="fas fa-box-archive svelte-sai9mo" aria-hidden="true"></i>
      Archivo físico</h1> ${canManage ? `<div class="af-workspace__header-actions svelte-sai9mo" role="group" aria-label="Acciones de archivo">${validate_component(Button, "Button").$$render($$result, { variant: "primary" }, {}, {
      default: () => {
        return `<i class="fas fa-plus" aria-hidden="true"></i>
          Nueva ubicación`;
      }
    })} ${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
      default: () => {
        return `<i class="fas fa-box" aria-hidden="true"></i>
          Nueva unidad`;
      }
    })}</div>` : ``}</div>  <div class="af-tabs svelte-sai9mo" role="tablist" aria-label="Módulos de archivo físico">${each(TABS, (tab) => {
      return `<button id="${"tab-" + escape(tab.id, true)}" class="${[
        "af-tabs__tab svelte-sai9mo",
        activeTab === tab.id ? "af-tabs__tab--active" : ""
      ].join(" ").trim()}" role="tab"${add_attribute("aria-selected", activeTab === tab.id, 0)} aria-controls="${"panel-" + escape(tab.id, true)}"${add_attribute("tabindex", activeTab === tab.id ? 0 : -1, 0)}><i class="${"fas " + escape(tab.icon, true) + " svelte-sai9mo"}" aria-hidden="true"></i> <span>${escape(tab.label)}</span> </button>`;
    })}</div>  <div class="af-workspace__panels svelte-sai9mo"> <div id="panel-ubicaciones" role="tabpanel" aria-labelledby="tab-ubicaciones" ${""} tabindex="0" class="af-panel svelte-sai9mo">${validate_component(Card, "Card").$$render($$result, {}, {}, {
      header: () => {
        return `<h2 slot="header" class="op-card-title" data-svelte-h="svelte-1rupkxk">Ubicaciones de archivo</h2>`;
      },
      default: () => {
        return `${`${ubicacionesError ? `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-exclamation-triangle",
            title: "Error al cargar ubicaciones",
            description: ubicacionesError.detail ?? "Intenta recargar la página."
          },
          {},
          {}
        )}` : `${ubicaciones.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-building",
            title: "Sin ubicaciones",
            description: "No se han registrado ubicaciones de archivo."
          },
          {},
          {}
        )}` : `<div class="af-table-wrap svelte-sai9mo"><table class="table is-fullwidth is-hoverable svelte-sai9mo"><caption class="is-sr-only svelte-sai9mo" data-svelte-h="svelte-ivznb5">Ubicaciones de archivo físico</caption> <thead data-svelte-h="svelte-3nut2x"><tr><th scope="col" class="svelte-sai9mo">Nivel</th> <th scope="col" class="svelte-sai9mo">Código</th> <th scope="col" class="svelte-sai9mo">Nombre</th> <th scope="col" class="svelte-sai9mo">Tipo</th> <th scope="col" class="svelte-sai9mo">Cap. (ml)</th> <th scope="col" class="svelte-sai9mo"><span class="is-sr-only">Acciones</span></th></tr></thead> <tbody>${each(ubicaciones, (ub) => {
          return `<tr><td class="svelte-sai9mo">${escape(ub.nivel ?? "—")}</td> <td class="svelte-sai9mo"><code>${escape(ub.codigo ?? "—")}</code></td> <td class="svelte-sai9mo">${escape(ub.nombre ?? "—")}</td> <td class="svelte-sai9mo">${escape(ub.tipo_archivo ?? "—")}</td> <td class="svelte-sai9mo">${escape(ub.capacidad_ml ?? "—")}</td> <td class="af-actions-cell svelte-sai9mo"><button class="button is-small is-light" aria-label="${"Ver hijos de " + escape(ub.nombre, true)}"${add_attribute("aria-expanded", ubicacionExpanded[ub.id] ?? false, 0)}${add_attribute("aria-busy", ubicacionChildrenLoading[ub.id] ?? false, 0)}>${ubicacionChildrenLoading[ub.id] ? `<i class="fas fa-spinner fa-spin" aria-hidden="true"></i>` : `${ubicacionExpanded[ub.id] ? `<i class="fas fa-chevron-up" aria-hidden="true"></i>` : `<i class="fas fa-chevron-down" aria-hidden="true"></i>`}`} <span class="is-sr-only" data-svelte-h="svelte-a3qtdc">Ver hijos</span></button> </td></tr> ${ubicacionExpanded[ub.id] && ubicacionChildren[ub.id] ? `${each(ubicacionChildren[ub.id], (child) => {
            return `<tr class="af-child-row svelte-sai9mo"><td class="af-child-indent svelte-sai9mo">${escape(child.nivel ?? "—")}</td> <td class="svelte-sai9mo"><code>${escape(child.codigo ?? "—")}</code></td> <td class="svelte-sai9mo">${escape(child.nombre ?? "—")}</td> <td class="svelte-sai9mo">${escape(child.tipo_archivo ?? "—")}</td> <td class="svelte-sai9mo">${escape(child.capacidad_ml ?? "—")}</td> <td class="svelte-sai9mo"></td> </tr>`;
          })}` : ``}`;
        })}</tbody></table></div>`}`}`}`;
      }
    })}</div>  <div id="panel-unidades" role="tabpanel" aria-labelledby="tab-unidades" ${"hidden"} tabindex="0" class="af-panel svelte-sai9mo">${validate_component(Card, "Card").$$render($$result, {}, {}, {
      header: () => {
        return `<h2 slot="header" class="op-card-title" data-svelte-h="svelte-19yx9mu">Unidades de conservación</h2>`;
      },
      default: () => {
        return ` <div class="af-filter-row svelte-sai9mo" role="search" aria-label="Filtros de unidades"><div class="af-filter-row__field svelte-sai9mo"><label for="unidad-filtro-ubicacion" class="label" data-svelte-h="svelte-lbbxmf">Ubicación ID</label> <input id="unidad-filtro-ubicacion" class="input" type="text" placeholder="UUID de la ubicación" aria-label="ID de ubicación"${add_attribute("value", unidadFiltroUbicacionId, 0)}></div> <div class="af-filter-row__field svelte-sai9mo"><label for="unidad-filtro-tipo" class="label" data-svelte-h="svelte-36ij9o">Tipo</label> <div class="select"><select id="unidad-filtro-tipo" aria-label="Tipo de unidad"><option value="" data-svelte-h="svelte-uh9n4x">Todos</option><option value="caja" data-svelte-h="svelte-cbdk16">Caja</option><option value="carpeta" data-svelte-h="svelte-vn75a">Carpeta</option><option value="legajo" data-svelte-h="svelte-zxd3la">Legajo</option><option value="libro" data-svelte-h="svelte-jmpspm">Libro</option><option value="tomo" data-svelte-h="svelte-nf1wpm">Tomo</option><option value="az" data-svelte-h="svelte-1550xdi">AZ</option><option value="folder" data-svelte-h="svelte-3axzam">Folder</option></select></div></div> ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "primary",
            disabled: unidadesLoading
          },
          {},
          {
            default: () => {
              return `${`<i class="fas fa-search" aria-hidden="true"></i>`}
            Buscar`;
            }
          }
        )}</div> <div aria-live="polite" aria-atomic="false">${`${`${unidades.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-box-open",
            title: "Sin resultados",
            description: "Aplica filtros y pulsa Buscar para ver unidades de conservación."
          },
          {},
          {}
        )}` : `<div class="af-table-wrap svelte-sai9mo"${add_attribute("aria-busy", unidadesLoading, 0)}><table class="table is-fullwidth is-hoverable svelte-sai9mo"><caption class="is-sr-only svelte-sai9mo" data-svelte-h="svelte-15ea960">Unidades de conservación encontradas</caption> <thead data-svelte-h="svelte-wiyikk"><tr><th scope="col" class="svelte-sai9mo">Tipo</th> <th scope="col" class="svelte-sai9mo">Código</th> <th scope="col" class="svelte-sai9mo">Signatura</th> <th scope="col" class="svelte-sai9mo">Soporte</th> <th scope="col" class="svelte-sai9mo">Folios</th> <th scope="col" class="svelte-sai9mo"><span class="is-sr-only">Acciones</span></th></tr></thead> <tbody>${each(unidades, (u) => {
          return `<tr><td class="svelte-sai9mo"><span class="tag is-light">${escape(u.tipo ?? "—")}</span></td> <td class="svelte-sai9mo"><code>${escape(u.codigo ?? "—")}</code></td> <td class="svelte-sai9mo">${escape(u.signatura ?? "—")}</td> <td class="svelte-sai9mo">${escape(u.soporte ?? "—")}</td> <td class="svelte-sai9mo">${escape(u.num_folios ?? "—")}</td> <td class="af-actions-cell svelte-sai9mo">${canManage ? `<button class="button is-small is-light" aria-label="${"Vincular expediente a " + escape(u.codigo, true)}"><i class="fas fa-link" aria-hidden="true"></i> <span data-svelte-h="svelte-jn37mk">Vincular expte.</span> </button>` : ``} <button class="button is-small is-light" aria-label="${"Ver movimientos de " + escape(u.codigo, true)}"><i class="fas fa-history" aria-hidden="true"></i> <span data-svelte-h="svelte-1rxdfcg">Movimientos</span> </button></td> </tr>`;
        })}</tbody></table></div>`}`}`}</div>`;
      }
    })}</div>  <div id="panel-localizar" role="tabpanel" aria-labelledby="tab-localizar" ${"hidden"} tabindex="0" class="af-panel svelte-sai9mo">${validate_component(Card, "Card").$$render($$result, {}, {}, {
      header: () => {
        return `<h2 slot="header" class="op-card-title" data-svelte-h="svelte-1h9tgub">Localizar expediente en archivo</h2>`;
      },
      default: () => {
        return `<div class="af-filter-row svelte-sai9mo" role="search" aria-label="Localizar expediente"><div class="af-filter-row__field svelte-sai9mo"><label for="localizar-id" class="label" data-svelte-h="svelte-rx0g1p">ID de expediente</label> <input id="localizar-id" class="input" type="text" placeholder="UUID del expediente" aria-label="ID del expediente a localizar"${add_attribute("value", localizarExpedienteId, 0)}></div> ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "primary",
            disabled: localizarLoading
          },
          {},
          {
            default: () => {
              return `${`<i class="fas fa-map-marker-alt" aria-hidden="true"></i>`}
            Localizar`;
            }
          }
        )}</div> <div aria-live="polite" aria-atomic="false">${`${`${`${localizarResultados.length > 0 ? `<div class="af-table-wrap svelte-sai9mo"><table class="table is-fullwidth svelte-sai9mo"><caption class="svelte-sai9mo" data-svelte-h="svelte-v0ojq0">Unidades donde se encuentra el expediente</caption> <thead data-svelte-h="svelte-2p7ylo"><tr><th scope="col" class="svelte-sai9mo">Signatura</th> <th scope="col" class="svelte-sai9mo">Tipo unidad</th> <th scope="col" class="svelte-sai9mo">Código unidad</th> <th scope="col" class="svelte-sai9mo">Folio inicio</th> <th scope="col" class="svelte-sai9mo">Folio fin</th></tr></thead> <tbody>${each(localizarResultados, (r) => {
          return `<tr><td class="svelte-sai9mo">${escape(r.signatura ?? r.unidad?.signatura ?? "—")}</td> <td class="svelte-sai9mo">${escape(r.tipo ?? r.unidad?.tipo ?? "—")}</td> <td class="svelte-sai9mo"><code>${escape(r.codigo ?? r.unidad?.codigo ?? "—")}</code></td> <td class="svelte-sai9mo">${escape(r.folio_inicio ?? "—")}</td> <td class="svelte-sai9mo">${escape(r.folio_fin ?? "—")}</td> </tr>`;
        })}</tbody></table></div>` : `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-map-marker-alt",
            title: "Busca un expediente",
            description: "Introduce el UUID del expediente y pulsa Localizar."
          },
          {},
          {}
        )}`}`}`}`}</div>`;
      }
    })}</div>  <div id="panel-prestamos" role="tabpanel" aria-labelledby="tab-prestamos" ${"hidden"} tabindex="0" class="af-panel svelte-sai9mo">${validate_component(Card, "Card").$$render($$result, {}, {}, {
      header: () => {
        return `<div slot="header" class="af-panel-header svelte-sai9mo"><h2 class="op-card-title" data-svelte-h="svelte-1rvxski">Préstamos de unidades</h2> ${canManage ? `${validate_component(Button, "Button").$$render($$result, { variant: "primary", size: "sm" }, {}, {
          default: () => {
            return `<i class="fas fa-plus" aria-hidden="true"></i>
              Registrar préstamo`;
          }
        })}` : ``}</div>`;
      },
      default: () => {
        return ` <div class="af-filter-row svelte-sai9mo"><div class="af-filter-row__field svelte-sai9mo"><label for="prestamos-estado" class="label" data-svelte-h="svelte-180al3u">Estado</label> <div class="select"><select id="prestamos-estado" aria-label="Filtrar préstamos por estado"><option value="prestado" data-svelte-h="svelte-7rv826">Prestado</option><option value="vencido" data-svelte-h="svelte-1vkradi">Vencido</option><option value="devuelto" data-svelte-h="svelte-183hhg2">Devuelto</option></select></div></div></div> ${`${prestamosVisibles.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-hand-holding",
            title: "Sin préstamos",
            description: "No hay préstamos con el estado seleccionado."
          },
          {},
          {}
        )}` : `<div class="af-table-wrap svelte-sai9mo"${add_attribute("aria-busy", prestamosLoading, 0)}><table class="table is-fullwidth is-hoverable svelte-sai9mo"><caption class="is-sr-only svelte-sai9mo" data-svelte-h="svelte-1vixsu2">Lista de préstamos</caption> <thead data-svelte-h="svelte-8igk3e"><tr><th scope="col" class="svelte-sai9mo">Unidad</th> <th scope="col" class="svelte-sai9mo">Prestado a</th> <th scope="col" class="svelte-sai9mo">Fecha préstamo</th> <th scope="col" class="svelte-sai9mo">Fecha devolución esp.</th> <th scope="col" class="svelte-sai9mo">Estado</th> <th scope="col" class="svelte-sai9mo"><span class="is-sr-only">Acciones</span></th></tr></thead> <tbody>${each(prestamosVisibles, (p) => {
          let estadoInfo = ESTADO_PRESTAMO_MAP[p.estado] ?? { label: p.estado, status: "neutral" };
          return ` <tr><td class="svelte-sai9mo"><code>${escape(p.unidad_codigo ?? p.unidad?.codigo ?? "—")}</code></td> <td class="svelte-sai9mo">${escape(p.prestado_a ?? "—")}</td> <td class="svelte-sai9mo">${escape(fmtDate(p.fecha_prestamo ?? p.created_at))}</td> <td class="svelte-sai9mo">${escape(fmtDate(p.fecha_devolucion_esperada))}</td> <td class="svelte-sai9mo">${validate_component(StatusChip, "StatusChip").$$render(
            $$result,
            {
              status: estadoInfo.status,
              label: estadoInfo.label,
              dot: true
            },
            {},
            {}
          )}</td> <td class="af-actions-cell svelte-sai9mo">${canManage && p.estado === "prestado" ? `<button class="button is-small is-warning is-light" aria-label="${"Registrar devolución del préstamo a " + escape(p.prestado_a, true)}"><i class="fas fa-undo" aria-hidden="true"></i> <span data-svelte-h="svelte-1ndgp7z">Devolver</span> </button>` : ``} <button class="button is-small is-light" aria-label="Ver movimientos" data-svelte-h="svelte-jx4qwc"><i class="fas fa-history" aria-hidden="true"></i> <span class="is-sr-only">Movimientos</span> </button></td> </tr>`;
        })}</tbody></table></div>`}`}`;
      }
    })}</div>  <div id="panel-fuid" role="tabpanel" aria-labelledby="tab-fuid" ${"hidden"} tabindex="0" class="af-panel svelte-sai9mo">${validate_component(Card, "Card").$$render($$result, {}, {}, {
      header: () => {
        return `<h2 slot="header" class="op-card-title" data-svelte-h="svelte-elwfwd">FUID — Formato Único de Inventario Documental</h2>`;
      },
      default: () => {
        return ` <div class="af-filter-row svelte-sai9mo" role="search" aria-label="Filtros FUID"><div class="af-filter-row__field svelte-sai9mo"><label for="fuid-ubicacion-id" class="label" data-svelte-h="svelte-1v6cxqh">Ubicación ID</label> <input id="fuid-ubicacion-id" class="input" type="text" placeholder="UUID de ubicación (opcional)" aria-label="UUID de ubicación para filtrar FUID"${add_attribute("value", fuidFiltroUbicacionId, 0)}></div> <div class="af-filter-row__field svelte-sai9mo"><label for="fuid-trd-serie-id" class="label" data-svelte-h="svelte-5pgc74">Serie TRD ID</label> <input id="fuid-trd-serie-id" class="input" type="text" placeholder="UUID de serie TRD (opcional)" aria-label="UUID de serie TRD para filtrar FUID"${add_attribute("value", fuidFiltroTrdSerieId, 0)}></div> <div class="af-filter-row__actions svelte-sai9mo">${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "primary",
            disabled: fuidLoading
          },
          {},
          {
            default: () => {
              return `${`<i class="fas fa-search" aria-hidden="true"></i>`}
              Consultar`;
            }
          }
        )} ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "secondary",
            disabled: fuidXmlLoading
          },
          {},
          {
            default: () => {
              return `${`<i class="fas fa-file-download" aria-hidden="true"></i>`}
              Descargar XML`;
            }
          }
        )}</div></div> <div aria-live="polite" aria-atomic="false">${`${`${`${fuidRows.length > 0 ? `<div class="af-table-wrap svelte-sai9mo"${add_attribute("aria-busy", fuidLoading, 0)}><table class="table is-fullwidth is-hoverable svelte-sai9mo"><caption class="svelte-sai9mo" data-svelte-h="svelte-1ps7klb">Formato Único de Inventario Documental</caption> <thead data-svelte-h="svelte-rl9ku2"><tr><th scope="col" class="svelte-sai9mo">Orden</th> <th scope="col" class="svelte-sai9mo">Código expediente</th> <th scope="col" class="svelte-sai9mo">Asunto</th> <th scope="col" class="svelte-sai9mo">Serie</th> <th scope="col" class="svelte-sai9mo">Signatura</th> <th scope="col" class="svelte-sai9mo">Unidad</th> <th scope="col" class="svelte-sai9mo">Soporte</th> <th scope="col" class="svelte-sai9mo">Folios</th></tr></thead> <tbody>${each(fuidRows, (row, i) => {
          return `<tr><td class="svelte-sai9mo">${escape(i + 1)}</td> <td class="svelte-sai9mo"><code>${escape(row.codigo_expediente ?? row.expediente?.code ?? "—")}</code></td> <td class="svelte-sai9mo">${escape(row.asunto ?? row.expediente?.name ?? "—")}</td> <td class="svelte-sai9mo">${escape(row.serie ?? row.trd_serie?.nombre ?? "—")}</td> <td class="svelte-sai9mo">${escape(row.signatura ?? "—")}</td> <td class="svelte-sai9mo">${escape(row.unidad ?? row.unidad_codigo ?? "—")}</td> <td class="svelte-sai9mo">${escape(row.soporte ?? "—")}</td> <td class="svelte-sai9mo">${escape(row.num_folios ?? row.folios ?? "—")}</td> </tr>`;
        })}</tbody></table></div>` : `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-file-alt",
            title: "Consulta el FUID",
            description: "Aplica filtros opcionales y pulsa Consultar para ver el inventario documental."
          },
          {},
          {}
        )}`}`}`}`}</div>`;
      }
    })}</div></div></div>   ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: showUbicacionModal,
        title: "Nueva ubicación de archivo"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="af-modal-footer svelte-sai9mo">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              disabled: ubicacionFormLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Crear ubicación`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<div class="af-form svelte-sai9mo">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "ub-nivel",
              label: "Nivel *",
              type: "select",
              required: true,
              placeholder: "— Selecciona nivel —",
              options: NIVELES_UBICACION_OPTIONS,
              value: ubicacionForm.nivel
            },
            {
              value: ($$value) => {
                ubicacionForm.nivel = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "ub-codigo",
              label: "Código *",
              type: "text",
              required: true,
              placeholder: "Ej. EST-001",
              value: ubicacionForm.codigo
            },
            {
              value: ($$value) => {
                ubicacionForm.codigo = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "ub-nombre",
              label: "Nombre *",
              type: "text",
              required: true,
              placeholder: "Nombre descriptivo",
              value: ubicacionForm.nombre
            },
            {
              value: ($$value) => {
                ubicacionForm.nombre = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "ub-parent",
              label: "ID padre (vacío = raíz)",
              type: "text",
              placeholder: "UUID de la ubicación padre (opcional)",
              value: ubicacionForm.parent_id
            },
            {
              value: ($$value) => {
                ubicacionForm.parent_id = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "ub-tipo",
              label: "Tipo de archivo",
              type: "select",
              placeholder: "— Sin tipo —",
              options: TIPOS_ARCHIVO_OPTIONS,
              value: ubicacionForm.tipo_archivo
            },
            {
              value: ($$value) => {
                ubicacionForm.tipo_archivo = $$value;
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
        open: showNuevaUnidadModal,
        title: "Nueva unidad de conservación"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="af-modal-footer svelte-sai9mo">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              disabled: nuevaUnidadLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Crear unidad`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<div class="af-form svelte-sai9mo">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "nu-tipo",
              label: "Tipo *",
              type: "select",
              required: true,
              placeholder: "— Selecciona tipo —",
              options: TIPOS_UNIDAD_OPTIONS,
              value: nuevaUnidadForm.tipo
            },
            {
              value: ($$value) => {
                nuevaUnidadForm.tipo = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "nu-codigo",
              label: "Código *",
              type: "text",
              required: true,
              placeholder: "Ej. CAJ-001",
              value: nuevaUnidadForm.codigo
            },
            {
              value: ($$value) => {
                nuevaUnidadForm.codigo = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "nu-ubicacion",
              label: "ID de ubicación",
              type: "text",
              placeholder: "UUID de la ubicación (opcional)",
              value: nuevaUnidadForm.ubicacion_id
            },
            {
              value: ($$value) => {
                nuevaUnidadForm.ubicacion_id = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "nu-soporte",
              label: "Soporte",
              type: "text",
              placeholder: "Ej. papel, digital",
              value: nuevaUnidadForm.soporte
            },
            {
              value: ($$value) => {
                nuevaUnidadForm.soporte = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "nu-folios",
              label: "N.° de folios",
              type: "number",
              placeholder: "0",
              value: nuevaUnidadForm.num_folios
            },
            {
              value: ($$value) => {
                nuevaUnidadForm.num_folios = $$value;
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
        open: showVincularModal,
        title: "Vincular expediente a unidad"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="af-modal-footer svelte-sai9mo">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              disabled: vincularLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Vincular`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<div class="af-form svelte-sai9mo">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "vin-expte",
              label: "ID del expediente *",
              type: "text",
              required: true,
              placeholder: "UUID del expediente",
              value: vincularForm.expediente_id
            },
            {
              value: ($$value) => {
                vincularForm.expediente_id = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "vin-folio-ini",
              label: "Folio inicio",
              type: "number",
              placeholder: "Número de folio inicial",
              value: vincularForm.folio_inicio
            },
            {
              value: ($$value) => {
                vincularForm.folio_inicio = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "vin-folio-fin",
              label: "Folio fin",
              type: "number",
              placeholder: "Número de folio final",
              value: vincularForm.folio_fin
            },
            {
              value: ($$value) => {
                vincularForm.folio_fin = $$value;
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
        open: showPrestamoModal,
        title: "Registrar préstamo"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="af-modal-footer svelte-sai9mo">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              disabled: prestamoLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Registrar préstamo`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<div class="af-form svelte-sai9mo">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "pr-unidad",
              label: "ID de la unidad *",
              type: "text",
              required: true,
              placeholder: "UUID de la unidad a prestar",
              value: prestamoForm.unidad_id
            },
            {
              value: ($$value) => {
                prestamoForm.unidad_id = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "pr-prestado-a",
              label: "Prestado a *",
              type: "text",
              required: true,
              placeholder: "Nombre del solicitante",
              value: prestamoForm.prestado_a
            },
            {
              value: ($$value) => {
                prestamoForm.prestado_a = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "pr-fecha",
              label: "Fecha de devolución esperada *",
              type: "date",
              required: true,
              value: prestamoForm.fecha_devolucion_esperada
            },
            {
              value: ($$value) => {
                prestamoForm.fecha_devolucion_esperada = $$value;
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
        open: showDevolverModal,
        title: "Confirmar devolución",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="af-modal-footer svelte-sai9mo">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              disabled: devolverLoading
            },
            {},
            {
              default: () => {
                return `${``}
      Confirmar devolución`;
              }
            }
          )}</div>`;
        },
        default: () => {
          return `<p>¿Confirmas la devolución del préstamo a <strong>${escape(devolverPrestamoDesc)}</strong>? Esta acción
    quedará registrada en la auditoría del sistema.</p>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: showMovimientosModal,
        title: "Movimientos de la unidad " + movimientosUnidadDesc,
        size: "lg"
      },
      {},
      {
        footer: () => {
          return `<div slot="footer" class="af-modal-footer svelte-sai9mo">${validate_component(Button, "Button").$$render($$result, { variant: "secondary" }, {}, {
            default: () => {
              return `Cerrar`;
            }
          })}</div>`;
        },
        default: () => {
          return `${`${movimientos.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
            $$result,
            {
              icon: "fas fa-history",
              title: "Sin movimientos",
              description: "Esta unidad no tiene movimientos registrados."
            },
            {},
            {}
          )}` : `<div class="af-table-wrap svelte-sai9mo"><table class="table is-fullwidth svelte-sai9mo"><caption class="is-sr-only svelte-sai9mo" data-svelte-h="svelte-1upc1yk">Historial de movimientos</caption> <thead data-svelte-h="svelte-1kanj41"><tr><th scope="col" class="svelte-sai9mo">Fecha</th> <th scope="col" class="svelte-sai9mo">Tipo</th> <th scope="col" class="svelte-sai9mo">Usuario</th> <th scope="col" class="svelte-sai9mo">Detalle</th></tr></thead> <tbody>${each(movimientos, (m) => {
            return `<tr><td class="svelte-sai9mo">${escape(fmtDate(m.created_at ?? m.fecha))}</td> <td class="svelte-sai9mo">${escape(m.tipo ?? m.tipo_movimiento ?? "—")}</td> <td class="svelte-sai9mo">${escape(m.usuario ?? m.user_id ?? "—")}</td> <td class="svelte-sai9mo">${escape(m.detalle ?? m.observacion ?? "—")}</td> </tr>`;
          })}</tbody></table></div>`}`}`;
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
