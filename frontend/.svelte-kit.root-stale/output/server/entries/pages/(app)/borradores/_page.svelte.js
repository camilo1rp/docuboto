import { c as create_ssr_component, s as subscribe, v as validate_component, d as each, b as add_attribute, e as escape } from "../../../../chunks/ssr.js";
import { p as permissions } from "../../../../chunks/auth.js";
import { D as Drawer } from "../../../../chunks/Drawer.js";
import { M as Modal } from "../../../../chunks/Modal.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
/* empty css                                                      */
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
import { B as Button } from "../../../../chunks/Button.js";
import { F as FormField } from "../../../../chunks/FormField.js";
import { R as RichEditor } from "../../../../chunks/RichEditor.js";
const css = {
  code: ":root{--op-primary:#2a8c3a;--op-primary-dark:#1c6b33;--op-primary-light:#dceed7;--op-on-primary:#ffffff;--op-accent:#8dbf3c;--op-brand-700:#173a22;--op-brand-400:#4fa84b;--op-text:#232624;--op-text-secondary:#6e7370;--op-border:#e7eae2;--op-bg:#fbfcf8;--op-surface:#ffffff;--op-success:#2a8c3a;--op-warning:#c8881c;--op-error:#c4452c;--op-info:#2b6cb0;--op-success-bg:#dceed7;--op-success-fg:#1c6b33;--op-warning-bg:#fdf3dc;--op-warning-fg:#7a5700;--op-error-bg:#fce8e8;--op-error-fg:#8b1a1a;--op-info-bg:#e0eef7;--op-info-fg:#1e4e6b;--op-error-strong-bg:#3b0000;--op-error-strong-fg:#ffdddd;--op-focus-ring:rgba(42, 140, 58, 0.15);--op-error-ring:rgba(196, 69, 44, 0.15);--op-font-family:Public Sans, Segoe UI, system-ui, -apple-system, sans-serif;--op-font-display:Space Grotesk, Segoe UI, system-ui, -apple-system, sans-serif;--op-font-size-base:15px;--op-line-height:1.6;--op-font-xs:12px;--op-font-sm:13px;--op-font-base:15px;--op-font-md:16px;--op-font-lg:18px;--op-font-xl:22px;--op-font-2xl:28px;--op-font-size-display:46px;--op-font-mono:12px;--op-weight-regular:400;--op-weight-medium:500;--op-weight-bold:600;--op-weight-display:700;--op-space-1:4px;--op-space-2:8px;--op-space-3:12px;--op-space-4:16px;--op-space-5:24px;--op-space-6:32px;--op-space-7:48px;--op-space-8:64px;--op-radius-sm:6px;--op-radius:10px;--op-radius-lg:16px;--op-radius-full:999px;--op-shadow-xs:0 1px 2px rgba(0, 0, 0, 0.04);--op-shadow-sm:0 2px 8px rgba(0, 0, 0, 0.05);--op-shadow:0 8px 24px rgba(0, 0, 0, 0.06);--op-shadow-lg:0 8px 24px rgba(0, 0, 0, 0.06);--op-transition:200ms cubic-bezier(0.2, 0.8, 0.2, 1);--op-motion-fast:120ms;--op-motion-base:200ms;--op-motion-slow:300ms;--op-motion-easing:cubic-bezier(0.2, 0.8, 0.2, 1);--op-sidebar-width:264px;--op-sidebar-collapsed-width:64px;--op-topbar-height:52px;--op-breadcrumb-height:36px}@media(prefers-reduced-motion: reduce){:root{--op-motion-fast:0ms;--op-motion-base:0ms;--op-motion-slow:0ms;--op-transition:0ms}.svelte-1xju26n.svelte-1xju26n,.svelte-1xju26n.svelte-1xju26n::before,.svelte-1xju26n.svelte-1xju26n::after{animation-duration:0.01ms !important;animation-iteration-count:1 !important;transition-duration:0.01ms !important;scroll-behavior:auto !important}}.borradores-page.svelte-1xju26n.svelte-1xju26n{padding:var(--op-space-5);max-width:1100px}.borradores-page__header.svelte-1xju26n.svelte-1xju26n{display:flex;flex-direction:column;gap:var(--op-space-3);margin-bottom:var(--op-space-5)}.borradores-page__title-row.svelte-1xju26n.svelte-1xju26n{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:var(--op-space-3)}.borradores-page__title.svelte-1xju26n.svelte-1xju26n{font-size:var(--op-font-lg);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}.borradores-page__filters.svelte-1xju26n.svelte-1xju26n{display:flex;flex-wrap:wrap;gap:var(--op-space-2)}.borradores-page__filter-btn.svelte-1xju26n.svelte-1xju26n{padding:var(--op-space-1) var(--op-space-3);font-size:var(--op-font-sm);font-family:var(--op-font-family);border:1px solid var(--op-border);border-radius:var(--op-radius-full);background:var(--op-surface);color:var(--op-text-secondary);cursor:pointer;transition:background var(--op-transition), color var(--op-transition), border-color var(--op-transition)}.borradores-page__filter-btn.svelte-1xju26n.svelte-1xju26n:hover{background:var(--op-primary-light);color:var(--op-primary-dark);border-color:var(--op-primary-light)}.borradores-page__filter-btn--active.svelte-1xju26n.svelte-1xju26n{background:var(--op-primary);color:var(--op-surface);border-color:var(--op-primary)}.borradores-page__filter-btn--active.svelte-1xju26n.svelte-1xju26n:hover{background:var(--op-primary-dark);border-color:var(--op-primary-dark)}.borradores-page__filter-btn.svelte-1xju26n.svelte-1xju26n:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}.borradores-page__error.svelte-1xju26n.svelte-1xju26n{display:flex;align-items:center;gap:var(--op-space-2);padding:var(--op-space-3) var(--op-space-4);background:var(--op-error-bg);color:var(--op-error-fg);border-radius:var(--op-radius);font-size:var(--op-font-sm);margin-bottom:var(--op-space-4)}.borradores-page__loader.svelte-1xju26n.svelte-1xju26n{display:flex;justify-content:center;padding:var(--op-space-7)}.borradores-table-wrap.svelte-1xju26n.svelte-1xju26n{overflow-x:auto;border:1px solid var(--op-border);border-radius:var(--op-radius)}.borradores-table.svelte-1xju26n.svelte-1xju26n{width:100%;border-collapse:collapse;font-size:var(--op-font-sm)}.borradores-table.svelte-1xju26n th.svelte-1xju26n,.borradores-table.svelte-1xju26n td.svelte-1xju26n{padding:var(--op-space-3) var(--op-space-4);text-align:left;border-bottom:1px solid var(--op-border)}.borradores-table.svelte-1xju26n th.svelte-1xju26n{background:var(--op-bg);font-weight:var(--op-weight-medium);color:var(--op-text-secondary);font-size:var(--op-font-xs);text-transform:uppercase;letter-spacing:0.04em}.borradores-table.svelte-1xju26n tbody tr:last-child td.svelte-1xju26n{border-bottom:none}.borradores-table__row.svelte-1xju26n:hover td.svelte-1xju26n{background:var(--op-primary-light)}.borradores-table__subject.svelte-1xju26n.svelte-1xju26n{font-weight:var(--op-weight-medium);color:var(--op-text);max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.borradores-table__date.svelte-1xju26n.svelte-1xju26n{color:var(--op-text-secondary);white-space:nowrap}.borradores-table__tracking.svelte-1xju26n.svelte-1xju26n{white-space:nowrap}.borradores-table__tracking-num.svelte-1xju26n.svelte-1xju26n{font-weight:var(--op-weight-medium);color:var(--op-primary-dark);font-size:var(--op-font-xs);background:var(--op-primary-light);padding:2px var(--op-space-2);border-radius:var(--op-radius-sm)}.borradores-table__tracking-none.svelte-1xju26n.svelte-1xju26n{color:var(--op-text-secondary)}.borradores-table__actions.svelte-1xju26n.svelte-1xju26n{width:80px}.borrador-form.svelte-1xju26n.svelte-1xju26n{display:flex;flex-direction:column;gap:var(--op-space-4)}.borrador-form__estado.svelte-1xju26n.svelte-1xju26n{display:flex;align-items:center;gap:var(--op-space-2);padding:var(--op-space-2) var(--op-space-3);background:var(--op-bg);border-radius:var(--op-radius);font-size:var(--op-font-sm)}.borrador-form__estado-label.svelte-1xju26n.svelte-1xju26n{color:var(--op-text-secondary)}.borrador-form__save-row.svelte-1xju26n.svelte-1xju26n{display:flex;justify-content:flex-end}.borrador-form__flow-actions.svelte-1xju26n.svelte-1xju26n{display:flex;flex-direction:column;gap:var(--op-space-3);padding-top:var(--op-space-2)}.borrador-form__divider.svelte-1xju26n.svelte-1xju26n{border:none;border-top:1px solid var(--op-border);margin:0}.borrador-form__flow-title.svelte-1xju26n.svelte-1xju26n{font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);color:var(--op-text-secondary);margin:0}.borrador-form__flow-done.svelte-1xju26n.svelte-1xju26n{display:flex;align-items:center;flex-wrap:wrap;gap:var(--op-space-2);font-size:var(--op-font-sm);color:var(--op-success-fg);background:var(--op-success-bg);padding:var(--op-space-2) var(--op-space-3);border-radius:var(--op-radius);margin:0}.borrador-form__flow-done.svelte-1xju26n strong.svelte-1xju26n{font-weight:var(--op-weight-bold)}.modal-confirm__text.svelte-1xju26n.svelte-1xju26n{font-size:var(--op-font-sm);color:var(--op-text);line-height:var(--op-line-height);margin:0}.modal-success.svelte-1xju26n.svelte-1xju26n{display:flex;flex-direction:column;align-items:center;gap:var(--op-space-3);text-align:center;padding:var(--op-space-3) 0}.modal-success__icon.svelte-1xju26n.svelte-1xju26n{font-size:var(--op-font-2xl);color:var(--op-success)}.modal-success__text.svelte-1xju26n.svelte-1xju26n{font-size:var(--op-font-sm);color:var(--op-text);margin:0}.modal-success__tracking.svelte-1xju26n.svelte-1xju26n{font-size:var(--op-font-md);color:var(--op-text);background:var(--op-primary-light);padding:var(--op-space-2) var(--op-space-4);border-radius:var(--op-radius);margin:0}.modal-success__tracking.svelte-1xju26n strong.svelte-1xju26n{color:var(--op-primary-dark);font-weight:var(--op-weight-bold)}",
  map: `{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /borradores — Gestor de borradores de Salida/Interno (T-12c).\\n\\n  Criterio de hecho: crear un borrador desde una plantilla, aprobarlo y radicarlo\\n  genera el radicado real — su tracking_number se muestra en pantalla.\\n\\n  Acciones del flujo:\\n    borrador → [editar] → [aprobar] → aprobado → [radicar] → radicado (DocumentResponse)\\n\\n  RBAC (espejo del backend, D-11):\\n    - Ver lista, crear, editar, aprobar, radicar → PERM_RADI\\n    - Acciones de escritura deshabilitadas si el usuario no tiene PERM_RADI.\\n    El backend re-valida en cada llamada; la UI solo filtra la visibilidad.\\n\\n  D-05 — access_token NUNCA en cliente:\\n    Todas las llamadas van a /borradores/api[/id/accion] que leen locals.accessToken\\n    en el servidor. El token nunca llega al JS del cliente.\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { permissions } from \\"$lib/stores/auth.js\\";\\n  import { notifyError, notifySuccess } from \\"$lib/stores/notifications.js\\";\\n\\n  import Drawer from \\"$lib/components/ui/Drawer.svelte\\";\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import Loader from \\"$lib/components/ui/Loader.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import RichEditor from \\"$lib/components/editor/RichEditor.svelte\\";\\n\\n  /** @type {import('./$types').PageData} */\\n  export let data;\\n\\n  // ---------------------------------------------------------------------------\\n  // Estado de la lista\\n  // ---------------------------------------------------------------------------\\n\\n  /** @type {import('$lib/types/api.js').BorradorResponse[]} */\\n  let borradores = data.borradores ?? [];\\n  let plantillas = data.plantillas ?? [];\\n  let loading = false;\\n  let estadoFilter = data.initialEstado ?? \\"\\";\\n\\n  const ESTADOS = [\\n    { value: \\"\\", label: \\"Todos\\" },\\n    { value: \\"borrador\\", label: \\"Borrador\\" },\\n    { value: \\"aprobado\\", label: \\"Aprobado\\" },\\n    { value: \\"radicado\\", label: \\"Radicado\\" },\\n  ];\\n\\n  // ---------------------------------------------------------------------------\\n  // Drawer de creación / edición\\n  // ---------------------------------------------------------------------------\\n  let drawerOpen = false;\\n  let drawerMode = /** @type {'create'|'edit'} */ (\\"create\\");\\n  /** @type {import('$lib/types/api.js').BorradorResponse|null} */\\n  let editingBorrador = null;\\n  let actionLoading = false;\\n\\n  // Campos del formulario\\n  let fDocType = \\"S\\";\\n  let fSubject = \\"\\";\\n  let fDestDept = \\"\\";\\n  let fCuerpo = \\"\\";\\n  let fPlantillaId = \\"\\";\\n  let fSubjectError = \\"\\";\\n\\n  /**\\n   * Precarga el cuerpo del editor cuando el usuario selecciona una plantilla.\\n   * Solo activo en modo creación; en edición el cambio de plantilla NO pisa el\\n   * cuerpo existente del borrador (ALTO #1 — pérdida de datos).\\n   */\\n  function handlePlantillaChange() {\\n    if (drawerMode !== \\"create\\" || !fPlantillaId) return;\\n    const found = plantillas.find((p) => p.id === fPlantillaId);\\n    // BAJO #6: sembrar el editor desde el cuerpo YA saneado (cuerpo_html).\\n    if (found) fCuerpo = found.cuerpo_html ?? found.cuerpo ?? \\"\\";\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal de confirmación de radicación\\n  // ---------------------------------------------------------------------------\\n  let modalRadicar = false;\\n  /** @type {string|null} — tracking_number tras radicación exitosa */\\n  let trackingNumberResult = null;\\n  let modalRadicadoOpen = false;\\n\\n  // ---------------------------------------------------------------------------\\n  // Breadcrumbs\\n  // ---------------------------------------------------------------------------\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Borradores\\" }]);\\n  });\\n\\n  // ---------------------------------------------------------------------------\\n  // Filtrar lista\\n  // ---------------------------------------------------------------------------\\n  async function applyFilter(estado) {\\n    estadoFilter = estado;\\n    loading = true;\\n    try {\\n      const params = new URLSearchParams();\\n      if (estado) params.set(\\"estado\\", estado);\\n      const res = await fetch(\`/borradores/api?\${params}\`);\\n      if (res.ok) {\\n        borradores = await res.json();\\n      } else {\\n        const err = await res.json().catch(() => ({}));\\n        notifyError(err.detail ?? \\"Error al cargar borradores.\\");\\n      }\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      loading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Abrir drawer para crear\\n  // ---------------------------------------------------------------------------\\n  function openCreate() {\\n    drawerMode = \\"create\\";\\n    editingBorrador = null;\\n    fDocType = \\"S\\";\\n    fSubject = \\"\\";\\n    fDestDept = \\"\\";\\n    fCuerpo = \\"\\";\\n    fPlantillaId = \\"\\";\\n    fSubjectError = \\"\\";\\n    drawerOpen = true;\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Abrir drawer para editar\\n  // ---------------------------------------------------------------------------\\n  function openEdit(borrador) {\\n    drawerMode = \\"edit\\";\\n    editingBorrador = borrador;\\n    fDocType = borrador.doc_type ?? \\"S\\";\\n    fSubject = borrador.subject ?? \\"\\";\\n    fDestDept = borrador.dest_dept ?? \\"\\";\\n    // BAJO #6: sembrar el editor desde el cuerpo YA saneado (cuerpo_html).\\n    fCuerpo = borrador.cuerpo_html ?? borrador.cuerpo ?? \\"\\";\\n    fPlantillaId = borrador.plantilla_id ?? \\"\\";\\n    fSubjectError = \\"\\";\\n    drawerOpen = true;\\n  }\\n\\n  function closeDrawer() {\\n    drawerOpen = false;\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Guardar (crear o editar)\\n  // ---------------------------------------------------------------------------\\n  async function guardar() {\\n    fSubjectError = \\"\\";\\n    if (!fSubject.trim()) {\\n      fSubjectError = \\"El asunto es obligatorio.\\";\\n      return;\\n    }\\n\\n    actionLoading = true;\\n    try {\\n      let res;\\n      if (drawerMode === \\"create\\") {\\n        res = await fetch(\\"/borradores/api\\", {\\n          method: \\"POST\\",\\n          headers: { \\"Content-Type\\": \\"application/json\\" },\\n          body: JSON.stringify({\\n            doc_type: fDocType,\\n            subject: fSubject.trim(),\\n            dest_dept: fDestDept.trim() || null,\\n            cuerpo: fCuerpo || null,\\n            plantilla_id: fPlantillaId || null,\\n          }),\\n        });\\n      } else {\\n        res = await fetch(\`/borradores/api/\${editingBorrador.id}\`, {\\n          method: \\"PATCH\\",\\n          headers: { \\"Content-Type\\": \\"application/json\\" },\\n          body: JSON.stringify({\\n            subject: fSubject.trim(),\\n            dest_dept: fDestDept.trim() || null,\\n            cuerpo: fCuerpo || null,\\n          }),\\n        });\\n      }\\n\\n      const data = await res.json();\\n      if (!res.ok) {\\n        notifyError(data.detail ?? \\"Error al guardar el borrador.\\");\\n        return;\\n      }\\n\\n      if (drawerMode === \\"create\\") {\\n        borradores = [data, ...borradores];\\n        notifySuccess(\\"Borrador creado.\\");\\n      } else {\\n        borradores = borradores.map((b) => (b.id === data.id ? data : b));\\n        editingBorrador = data;\\n        notifySuccess(\\"Borrador actualizado.\\");\\n      }\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Aprobar\\n  // ---------------------------------------------------------------------------\\n  async function aprobar() {\\n    if (!editingBorrador) return;\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(\`/borradores/api/\${editingBorrador.id}/aprobar\`, {\\n        method: \\"POST\\",\\n      });\\n      const data = await res.json();\\n      if (!res.ok) {\\n        notifyError(data.detail ?? \\"No se pudo aprobar el borrador.\\");\\n        return;\\n      }\\n      borradores = borradores.map((b) => (b.id === data.id ? data : b));\\n      editingBorrador = data;\\n      notifySuccess(\\"Borrador aprobado.\\");\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Radicar (con confirmación)\\n  // ---------------------------------------------------------------------------\\n  function pedirConfirmacionRadicar() {\\n    modalRadicar = true;\\n  }\\n\\n  async function confirmarRadicar() {\\n    if (!editingBorrador) return;\\n    modalRadicar = false;\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(\`/borradores/api/\${editingBorrador.id}/radicar\`, {\\n        method: \\"POST\\",\\n      });\\n      const data = await res.json();\\n      if (!res.ok) {\\n        notifyError(data.detail ?? \\"No se pudo radicar el borrador.\\");\\n        return;\\n      }\\n      // data es DocumentResponse con tracking_number\\n      trackingNumberResult = data.tracking_number ?? null;\\n      // MEDIO #4: persistir tracking_number y radicado_id en la fila de la lista\\n      // para que sigan visibles tras cerrar el modal.\\n      // Cast explícito para que \`estado\` mantenga la unión literal, no \`string\`.\\n      /** @type {import('$lib/types/api.js').BorradorResponse} */\\n      const updatedBorrador = {\\n        ...editingBorrador,\\n        estado: /** @type {'borrador'|'aprobado'|'radicado'} */ (\\"radicado\\"),\\n        radicado_id: data.id ?? editingBorrador.radicado_id,\\n        _tracking_number: data.tracking_number ?? null,\\n      };\\n      borradores = borradores.map((b) =>\\n        b.id === editingBorrador.id ? updatedBorrador : b,\\n      );\\n      editingBorrador = updatedBorrador;\\n      modalRadicadoOpen = true;\\n      drawerOpen = false;\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de presentación\\n  // ---------------------------------------------------------------------------\\n  const ESTADO_CHIP = {\\n    borrador: \\"neutral\\",\\n    aprobado: \\"warning\\",\\n    radicado: \\"success\\",\\n  };\\n\\n  const ESTADO_LABEL = {\\n    borrador: \\"Borrador\\",\\n    aprobado: \\"Aprobado\\",\\n    radicado: \\"Radicado\\",\\n  };\\n\\n  const DOCTYPE_LABEL = { S: \\"Salida\\", I: \\"Interno\\" };\\n\\n  function formatDate(iso) {\\n    if (!iso) return \\"—\\";\\n    try {\\n      return new Intl.DateTimeFormat(\\"es-CO\\", {\\n        day: \\"2-digit\\",\\n        month: \\"2-digit\\",\\n        year: \\"numeric\\",\\n      }).format(new Date(iso));\\n    } catch {\\n      return iso.slice(0, 10);\\n    }\\n  }\\n\\n  // ¿El borrador activo se puede editar? Solo si estado === 'borrador'\\n  $: canEdit = editingBorrador?.estado === \\"borrador\\";\\n  $: canAprobar = editingBorrador?.estado === \\"borrador\\";\\n  $: canRadicar = editingBorrador?.estado === \\"aprobado\\";\\n  $: hasPerm = $permissions.includes(\\"PERM_RADI\\");\\n\\n  // Opciones de plantillas para el select\\n  $: plantillaOptions = [\\n    { value: \\"\\", label: \\"Sin plantilla\\" },\\n    ...plantillas.map((p) => ({ value: p.id, label: p.nombre })),\\n  ];\\n<\/script>\\n\\n<svelte:head>\\n  <title>Borradores — OrpycaMCP</title>\\n</svelte:head>\\n\\n<!-- =========================================================================\\n     Página principal\\n     ========================================================================= -->\\n<div class=\\"borradores-page\\">\\n  <!-- Cabecera -->\\n  <div class=\\"borradores-page__header\\">\\n    <div class=\\"borradores-page__title-row\\">\\n      <h1 class=\\"borradores-page__title\\">Borradores</h1>\\n      {#if hasPerm}\\n        <Button icon=\\"fas fa-plus\\" on:click={openCreate}>Nuevo borrador</Button>\\n      {/if}\\n    </div>\\n\\n    <!-- Filtros de estado -->\\n    <div class=\\"borradores-page__filters\\" role=\\"group\\" aria-label=\\"Filtrar por estado\\">\\n      {#each ESTADOS as e (e.value)}\\n        <button\\n          type=\\"button\\"\\n          class=\\"borradores-page__filter-btn\\"\\n          class:borradores-page__filter-btn--active={estadoFilter === e.value}\\n          aria-pressed={estadoFilter === e.value}\\n          on:click={() => applyFilter(e.value)}\\n        >\\n          {e.label}\\n        </button>\\n      {/each}\\n    </div>\\n  </div>\\n\\n  <!-- Error de carga -->\\n  {#if data.loadError}\\n    <div class=\\"borradores-page__error\\" role=\\"alert\\">\\n      <i class=\\"fas fa-exclamation-triangle\\" aria-hidden=\\"true\\"></i>\\n      {data.loadError}\\n    </div>\\n  {/if}\\n\\n  <!-- Estado de carga -->\\n  {#if loading}\\n    <div class=\\"borradores-page__loader\\">\\n      <Loader />\\n    </div>\\n  {:else if borradores.length === 0}\\n    <EmptyState\\n      icon=\\"fa-file-alt\\"\\n      title=\\"No hay borradores\\"\\n      description={estadoFilter\\n        ? \`No hay borradores en estado \\"\${ESTADO_LABEL[estadoFilter] ?? estadoFilter}\\".\`\\n        : \\"Crea el primer borrador con el botón «Nuevo borrador».\\"}\\n    />\\n  {:else}\\n    <!-- Tabla de borradores -->\\n    <div class=\\"borradores-table-wrap\\" role=\\"region\\" aria-label=\\"Lista de borradores\\">\\n      <table class=\\"borradores-table\\" aria-label=\\"Borradores\\">\\n        <thead>\\n          <tr>\\n            <th scope=\\"col\\">Asunto</th>\\n            <th scope=\\"col\\">Tipo</th>\\n            <th scope=\\"col\\">Destino</th>\\n            <th scope=\\"col\\">Estado</th>\\n            <th scope=\\"col\\">N.° Radicado</th>\\n            <th scope=\\"col\\">Creado</th>\\n            <th scope=\\"col\\"><span class=\\"sr-only\\">Acciones</span></th>\\n          </tr>\\n        </thead>\\n        <tbody>\\n          {#each borradores as borrador (borrador.id)}\\n            <tr class=\\"borradores-table__row\\">\\n              <td class=\\"borradores-table__subject\\">{borrador.subject}</td>\\n              <td>{DOCTYPE_LABEL[borrador.doc_type] ?? borrador.doc_type}</td>\\n              <td>{borrador.dest_dept ?? \\"—\\"}</td>\\n              <td>\\n                <StatusChip\\n                  status={ESTADO_CHIP[borrador.estado] ?? \\"neutral\\"}\\n                  label={ESTADO_LABEL[borrador.estado] ?? borrador.estado}\\n                  dot\\n                />\\n              </td>\\n              <td class=\\"borradores-table__tracking\\">\\n                {#if borrador._tracking_number}\\n                  <span class=\\"borradores-table__tracking-num\\">{borrador._tracking_number}</span>\\n                {:else}\\n                  <span class=\\"borradores-table__tracking-none\\">—</span>\\n                {/if}\\n              </td>\\n              <td class=\\"borradores-table__date\\">{formatDate(borrador.created_at)}</td>\\n              <td class=\\"borradores-table__actions\\">\\n                <Button\\n                  variant=\\"ghost\\"\\n                  size=\\"sm\\"\\n                  icon=\\"fas fa-pencil-alt\\"\\n                  aria-label=\\"Editar borrador {borrador.subject}\\"\\n                  on:click={() => openEdit(borrador)}\\n                >\\n                  Abrir\\n                </Button>\\n              </td>\\n            </tr>\\n          {/each}\\n        </tbody>\\n      </table>\\n    </div>\\n  {/if}\\n</div>\\n\\n<!-- =========================================================================\\n     Drawer: crear / editar borrador\\n     ========================================================================= -->\\n<Drawer\\n  open={drawerOpen}\\n  title={drawerMode === \\"create\\" ? \\"Nuevo borrador\\" : \\"Editar borrador\\"}\\n  width=\\"600px\\"\\n  on:close={closeDrawer}\\n>\\n  <form\\n    class=\\"borrador-form\\"\\n    on:submit|preventDefault={guardar}\\n    aria-label={drawerMode === \\"create\\" ? \\"Formulario de nuevo borrador\\" : \\"Formulario de edición\\"}\\n    novalidate\\n  >\\n    <!-- Estado visible (solo en edición) -->\\n    {#if editingBorrador}\\n      <div class=\\"borrador-form__estado\\">\\n        <span class=\\"borrador-form__estado-label\\">Estado:</span>\\n        <StatusChip\\n          status={ESTADO_CHIP[editingBorrador.estado] ?? \\"neutral\\"}\\n          label={ESTADO_LABEL[editingBorrador.estado] ?? editingBorrador.estado}\\n          dot\\n        />\\n      </div>\\n    {/if}\\n\\n    <!-- Tipo de documento -->\\n    <FormField\\n      id=\\"borrador-doctype\\"\\n      label=\\"Tipo de documento\\"\\n      type=\\"select\\"\\n      bind:value={fDocType}\\n      required\\n      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}\\n      options={[\\n        { value: \\"S\\", label: \\"Salida\\" },\\n        { value: \\"I\\", label: \\"Interno\\" },\\n      ]}\\n    />\\n\\n    <!-- Asunto -->\\n    <FormField\\n      id=\\"borrador-subject\\"\\n      label=\\"Asunto\\"\\n      type=\\"text\\"\\n      bind:value={fSubject}\\n      placeholder=\\"Descripción breve del documento\\"\\n      required\\n      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}\\n      error={fSubjectError}\\n    />\\n\\n    <!-- Destino -->\\n    <FormField\\n      id=\\"borrador-destdept\\"\\n      label=\\"Dependencia destino\\"\\n      type=\\"text\\"\\n      bind:value={fDestDept}\\n      placeholder=\\"Nombre o código de la dependencia receptora\\"\\n      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}\\n    />\\n\\n    <!-- Selector de plantilla (solo en creación) -->\\n    {#if drawerMode === \\"create\\" && plantillas.length > 0}\\n      <FormField\\n        id=\\"borrador-plantilla\\"\\n        label=\\"Plantilla de contenido\\"\\n        type=\\"select\\"\\n        bind:value={fPlantillaId}\\n        disabled={!hasPerm}\\n        options={plantillaOptions}\\n        hint=\\"Al elegir una plantilla, su contenido se carga en el editor.\\"\\n        on:change={handlePlantillaChange}\\n      />\\n    {/if}\\n\\n    <!-- Editor de cuerpo -->\\n    <RichEditor\\n      id=\\"borrador-cuerpo\\"\\n      label=\\"Contenido del documento\\"\\n      bind:value={fCuerpo}\\n      placeholder=\\"Redacte el cuerpo del documento aquí…\\"\\n      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}\\n    />\\n\\n    <!-- Botón guardar -->\\n    {#if hasPerm && (editingBorrador === null || canEdit)}\\n      <div class=\\"borrador-form__save-row\\">\\n        <Button type=\\"submit\\" icon=\\"fas fa-save\\" loading={actionLoading}>\\n          {drawerMode === \\"create\\" ? \\"Crear borrador\\" : \\"Guardar cambios\\"}\\n        </Button>\\n      </div>\\n    {/if}\\n  </form>\\n\\n  <!-- Acciones de flujo (solo en edición) -->\\n  {#if editingBorrador && hasPerm}\\n    <div class=\\"borrador-form__flow-actions\\">\\n      <hr class=\\"borrador-form__divider\\" aria-hidden=\\"true\\" />\\n      <p class=\\"borrador-form__flow-title\\">Acciones de flujo</p>\\n\\n      <!-- Aprobar -->\\n      {#if canAprobar}\\n        <Button\\n          variant=\\"secondary\\"\\n          icon=\\"fas fa-check-circle\\"\\n          loading={actionLoading}\\n          on:click={aprobar}\\n        >\\n          Aprobar borrador\\n        </Button>\\n      {/if}\\n\\n      <!-- Radicar (solo si aprobado) -->\\n      {#if canRadicar}\\n        <Button\\n          icon=\\"fas fa-paper-plane\\"\\n          loading={actionLoading}\\n          on:click={pedirConfirmacionRadicar}\\n        >\\n          Radicar\\n        </Button>\\n      {/if}\\n\\n      <!-- Radicado — estado final, tracking_number visible (MEDIO #4) -->\\n      {#if editingBorrador.estado === \\"radicado\\"}\\n        <div class=\\"borrador-form__flow-done\\" role=\\"status\\">\\n          <i class=\\"fas fa-check-double\\" aria-hidden=\\"true\\"></i>\\n          <span>Este borrador ya fue radicado.</span>\\n          {#if editingBorrador._tracking_number}\\n            <span>\\n              N.° <strong>{editingBorrador._tracking_number}</strong>\\n            </span>\\n          {:else if editingBorrador.radicado_id}\\n            <span class=\\"sr-only\\">ID de radicado: {editingBorrador.radicado_id}</span>\\n          {/if}\\n        </div>\\n      {/if}\\n    </div>\\n  {/if}\\n</Drawer>\\n\\n<!-- =========================================================================\\n     Modal: confirmación de radicación\\n     ========================================================================= -->\\n<Modal\\n  open={modalRadicar}\\n  title=\\"Confirmar radicación\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalRadicar = false)}\\n>\\n  <p class=\\"modal-confirm__text\\">\\n    ¿Confirma la radicación de este borrador? Esta acción generará un radicado real con número de\\n    control inmutable y no puede deshacerse.\\n  </p>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (modalRadicar = false)}>Cancelar</Button>\\n    <Button icon=\\"fas fa-paper-plane\\" loading={actionLoading} on:click={confirmarRadicar}>\\n      Sí, radicar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- =========================================================================\\n     Modal: resultado de radicación exitosa\\n     ========================================================================= -->\\n<Modal\\n  open={modalRadicadoOpen}\\n  title=\\"Radicado generado\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalRadicadoOpen = false)}\\n>\\n  <div class=\\"modal-success\\">\\n    <i class=\\"fas fa-check-circle modal-success__icon\\" aria-hidden=\\"true\\"></i>\\n    <p class=\\"modal-success__text\\">El borrador fue radicado exitosamente.</p>\\n    {#if trackingNumberResult}\\n      <p class=\\"modal-success__tracking\\" aria-live=\\"polite\\">\\n        Número de radicado:\\n        <strong>{trackingNumberResult}</strong>\\n      </p>\\n    {/if}\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button on:click={() => (modalRadicadoOpen = false)}>Cerrar</Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<style lang=\\"scss\\">:root {\\n  --op-primary: #2a8c3a;\\n  --op-primary-dark: #1c6b33;\\n  --op-primary-light: #dceed7;\\n  --op-on-primary: #ffffff;\\n  --op-accent: #8dbf3c;\\n  --op-brand-700: #173a22;\\n  --op-brand-400: #4fa84b;\\n  --op-text: #232624;\\n  --op-text-secondary: #6e7370;\\n  --op-border: #e7eae2;\\n  --op-bg: #fbfcf8;\\n  --op-surface: #ffffff;\\n  --op-success: #2a8c3a;\\n  --op-warning: #c8881c;\\n  --op-error: #c4452c;\\n  --op-info: #2b6cb0;\\n  --op-success-bg: #dceed7;\\n  --op-success-fg: #1c6b33;\\n  --op-warning-bg: #fdf3dc;\\n  --op-warning-fg: #7a5700;\\n  --op-error-bg: #fce8e8;\\n  --op-error-fg: #8b1a1a;\\n  --op-info-bg: #e0eef7;\\n  --op-info-fg: #1e4e6b;\\n  --op-error-strong-bg: #3b0000;\\n  --op-error-strong-fg: #ffdddd;\\n  --op-focus-ring: rgba(42, 140, 58, 0.15);\\n  --op-error-ring: rgba(196, 69, 44, 0.15);\\n  --op-font-family: Public Sans, Segoe UI, system-ui, -apple-system, sans-serif;\\n  --op-font-display: Space Grotesk, Segoe UI, system-ui, -apple-system, sans-serif;\\n  --op-font-size-base: 15px;\\n  --op-line-height: 1.6;\\n  --op-font-xs: 12px;\\n  --op-font-sm: 13px;\\n  --op-font-base: 15px;\\n  --op-font-md: 16px;\\n  --op-font-lg: 18px;\\n  --op-font-xl: 22px;\\n  --op-font-2xl: 28px;\\n  --op-font-size-display: 46px;\\n  --op-font-mono: 12px;\\n  --op-weight-regular: 400;\\n  --op-weight-medium: 500;\\n  --op-weight-bold: 600;\\n  --op-weight-display: 700;\\n  --op-space-1: 4px;\\n  --op-space-2: 8px;\\n  --op-space-3: 12px;\\n  --op-space-4: 16px;\\n  --op-space-5: 24px;\\n  --op-space-6: 32px;\\n  --op-space-7: 48px;\\n  --op-space-8: 64px;\\n  --op-radius-sm: 6px;\\n  --op-radius: 10px;\\n  --op-radius-lg: 16px;\\n  --op-radius-full: 999px;\\n  --op-shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);\\n  --op-shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.05);\\n  --op-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);\\n  --op-shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.06);\\n  --op-transition: 200ms cubic-bezier(0.2, 0.8, 0.2, 1);\\n  --op-motion-fast: 120ms;\\n  --op-motion-base: 200ms;\\n  --op-motion-slow: 300ms;\\n  --op-motion-easing: cubic-bezier(0.2, 0.8, 0.2, 1);\\n  --op-sidebar-width: 264px;\\n  --op-sidebar-collapsed-width: 64px;\\n  --op-topbar-height: 52px;\\n  --op-breadcrumb-height: 36px;\\n}\\n\\n@media (prefers-reduced-motion: reduce) {\\n  :root {\\n    --op-motion-fast: 0ms;\\n    --op-motion-base: 0ms;\\n    --op-motion-slow: 0ms;\\n    --op-transition: 0ms;\\n  }\\n  *,\\n  *::before,\\n  *::after {\\n    animation-duration: 0.01ms !important;\\n    animation-iteration-count: 1 !important;\\n    transition-duration: 0.01ms !important;\\n    scroll-behavior: auto !important;\\n  }\\n}\\n.borradores-page {\\n  padding: var(--op-space-5);\\n  max-width: 1100px;\\n}\\n.borradores-page__header {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n  margin-bottom: var(--op-space-5);\\n}\\n.borradores-page__title-row {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-3);\\n}\\n.borradores-page__title {\\n  font-size: var(--op-font-lg);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n.borradores-page__filters {\\n  display: flex;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-2);\\n}\\n.borradores-page__filter-btn {\\n  padding: var(--op-space-1) var(--op-space-3);\\n  font-size: var(--op-font-sm);\\n  font-family: var(--op-font-family);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-full);\\n  background: var(--op-surface);\\n  color: var(--op-text-secondary);\\n  cursor: pointer;\\n  transition: background var(--op-transition), color var(--op-transition), border-color var(--op-transition);\\n}\\n.borradores-page__filter-btn:hover {\\n  background: var(--op-primary-light);\\n  color: var(--op-primary-dark);\\n  border-color: var(--op-primary-light);\\n}\\n.borradores-page__filter-btn--active {\\n  background: var(--op-primary);\\n  color: var(--op-surface);\\n  border-color: var(--op-primary);\\n}\\n.borradores-page__filter-btn--active:hover {\\n  background: var(--op-primary-dark);\\n  border-color: var(--op-primary-dark);\\n}\\n.borradores-page__filter-btn:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n.borradores-page__error {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  padding: var(--op-space-3) var(--op-space-4);\\n  background: var(--op-error-bg);\\n  color: var(--op-error-fg);\\n  border-radius: var(--op-radius);\\n  font-size: var(--op-font-sm);\\n  margin-bottom: var(--op-space-4);\\n}\\n.borradores-page__loader {\\n  display: flex;\\n  justify-content: center;\\n  padding: var(--op-space-7);\\n}\\n\\n.borradores-table-wrap {\\n  overflow-x: auto;\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n}\\n\\n.borradores-table {\\n  width: 100%;\\n  border-collapse: collapse;\\n  font-size: var(--op-font-sm);\\n}\\n.borradores-table th,\\n.borradores-table td {\\n  padding: var(--op-space-3) var(--op-space-4);\\n  text-align: left;\\n  border-bottom: 1px solid var(--op-border);\\n}\\n.borradores-table th {\\n  background: var(--op-bg);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text-secondary);\\n  font-size: var(--op-font-xs);\\n  text-transform: uppercase;\\n  letter-spacing: 0.04em;\\n}\\n.borradores-table tbody tr:last-child td {\\n  border-bottom: none;\\n}\\n.borradores-table__row:hover td {\\n  background: var(--op-primary-light);\\n}\\n.borradores-table__subject {\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text);\\n  max-width: 280px;\\n  white-space: nowrap;\\n  overflow: hidden;\\n  text-overflow: ellipsis;\\n}\\n.borradores-table__date {\\n  color: var(--op-text-secondary);\\n  white-space: nowrap;\\n}\\n.borradores-table__tracking {\\n  white-space: nowrap;\\n}\\n.borradores-table__tracking-num {\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n  font-size: var(--op-font-xs);\\n  background: var(--op-primary-light);\\n  padding: 2px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n}\\n.borradores-table__tracking-none {\\n  color: var(--op-text-secondary);\\n}\\n.borradores-table__actions {\\n  width: 80px;\\n}\\n\\n.borrador-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}\\n.borrador-form__estado {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  padding: var(--op-space-2) var(--op-space-3);\\n  background: var(--op-bg);\\n  border-radius: var(--op-radius);\\n  font-size: var(--op-font-sm);\\n}\\n.borrador-form__estado-label {\\n  color: var(--op-text-secondary);\\n}\\n.borrador-form__save-row {\\n  display: flex;\\n  justify-content: flex-end;\\n}\\n\\n.borrador-form__flow-actions {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n  padding-top: var(--op-space-2);\\n}\\n\\n.borrador-form__divider {\\n  border: none;\\n  border-top: 1px solid var(--op-border);\\n  margin: 0;\\n}\\n\\n.borrador-form__flow-title {\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text-secondary);\\n  margin: 0;\\n}\\n\\n.borrador-form__flow-done {\\n  display: flex;\\n  align-items: center;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-2);\\n  font-size: var(--op-font-sm);\\n  color: var(--op-success-fg);\\n  background: var(--op-success-bg);\\n  padding: var(--op-space-2) var(--op-space-3);\\n  border-radius: var(--op-radius);\\n  margin: 0;\\n}\\n.borrador-form__flow-done strong {\\n  font-weight: var(--op-weight-bold);\\n}\\n\\n.modal-confirm__text {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  line-height: var(--op-line-height);\\n  margin: 0;\\n}\\n\\n.modal-success {\\n  display: flex;\\n  flex-direction: column;\\n  align-items: center;\\n  gap: var(--op-space-3);\\n  text-align: center;\\n  padding: var(--op-space-3) 0;\\n}\\n.modal-success__icon {\\n  font-size: var(--op-font-2xl);\\n  color: var(--op-success);\\n}\\n.modal-success__text {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n.modal-success__tracking {\\n  font-size: var(--op-font-md);\\n  color: var(--op-text);\\n  background: var(--op-primary-light);\\n  padding: var(--op-space-2) var(--op-space-4);\\n  border-radius: var(--op-radius);\\n  margin: 0;\\n}\\n.modal-success__tracking strong {\\n  color: var(--op-primary-dark);\\n  font-weight: var(--op-weight-bold);\\n}</style>\\n"],"names":[],"mappings":"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"}`
};
function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(
      "es-CO",
      {
        day: "2-digit",
        month: "2-digit",
        year: "numeric"
      }
    ).format(new Date(iso));
  } catch {
    return iso.slice(0, 10);
  }
}
const Page = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let hasPerm;
  let plantillaOptions;
  let $permissions, $$unsubscribe_permissions;
  $$unsubscribe_permissions = subscribe(permissions, (value) => $permissions = value);
  let { data } = $$props;
  let borradores = data.borradores ?? [];
  let plantillas = data.plantillas ?? [];
  let estadoFilter = data.initialEstado ?? "";
  const ESTADOS = [
    { value: "", label: "Todos" },
    { value: "borrador", label: "Borrador" },
    { value: "aprobado", label: "Aprobado" },
    { value: "radicado", label: "Radicado" }
  ];
  let drawerOpen = false;
  let editingBorrador = null;
  let actionLoading = false;
  let fDocType = "S";
  let fSubject = "";
  let fDestDept = "";
  let fCuerpo = "";
  let fPlantillaId = "";
  let fSubjectError = "";
  let modalRadicar = false;
  let modalRadicadoOpen = false;
  const ESTADO_CHIP = {
    borrador: "neutral",
    aprobado: "warning",
    radicado: "success"
  };
  const ESTADO_LABEL = {
    borrador: "Borrador",
    aprobado: "Aprobado",
    radicado: "Radicado"
  };
  const DOCTYPE_LABEL = { S: "Salida", I: "Interno" };
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    hasPerm = $permissions.includes("PERM_RADI");
    plantillaOptions = [
      { value: "", label: "Sin plantilla" },
      ...plantillas.map((p) => ({ value: p.id, label: p.nombre }))
    ];
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-1dwum0y_START -->${$$result.title = `<title>Borradores — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-1dwum0y_END -->`, ""}  <div class="borradores-page svelte-1xju26n"> <div class="borradores-page__header svelte-1xju26n"><div class="borradores-page__title-row svelte-1xju26n"><h1 class="borradores-page__title svelte-1xju26n" data-svelte-h="svelte-1u95d95">Borradores</h1> ${hasPerm ? `${validate_component(Button, "Button").$$render($$result, { icon: "fas fa-plus" }, {}, {
      default: () => {
        return `Nuevo borrador`;
      }
    })}` : ``}</div>  <div class="borradores-page__filters svelte-1xju26n" role="group" aria-label="Filtrar por estado">${each(ESTADOS, (e) => {
      return `<button type="button" class="${[
        "borradores-page__filter-btn svelte-1xju26n",
        estadoFilter === e.value ? "borradores-page__filter-btn--active" : ""
      ].join(" ").trim()}"${add_attribute("aria-pressed", estadoFilter === e.value, 0)}>${escape(e.label)} </button>`;
    })}</div></div>  ${data.loadError ? `<div class="borradores-page__error svelte-1xju26n" role="alert"><i class="fas fa-exclamation-triangle svelte-1xju26n" aria-hidden="true"></i> ${escape(data.loadError)}</div>` : ``}  ${`${borradores.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fa-file-alt",
        title: "No hay borradores",
        description: estadoFilter ? `No hay borradores en estado "${ESTADO_LABEL[estadoFilter] ?? estadoFilter}".` : "Crea el primer borrador con el botón «Nuevo borrador»."
      },
      {},
      {}
    )}` : ` <div class="borradores-table-wrap svelte-1xju26n" role="region" aria-label="Lista de borradores"><table class="borradores-table svelte-1xju26n" aria-label="Borradores"><thead class="svelte-1xju26n" data-svelte-h="svelte-18c6dd"><tr class="svelte-1xju26n"><th scope="col" class="svelte-1xju26n">Asunto</th> <th scope="col" class="svelte-1xju26n">Tipo</th> <th scope="col" class="svelte-1xju26n">Destino</th> <th scope="col" class="svelte-1xju26n">Estado</th> <th scope="col" class="svelte-1xju26n">N.° Radicado</th> <th scope="col" class="svelte-1xju26n">Creado</th> <th scope="col" class="svelte-1xju26n"><span class="sr-only svelte-1xju26n">Acciones</span></th></tr></thead> <tbody class="svelte-1xju26n">${each(borradores, (borrador) => {
      return `<tr class="borradores-table__row svelte-1xju26n"><td class="borradores-table__subject svelte-1xju26n">${escape(borrador.subject)}</td> <td class="svelte-1xju26n">${escape(DOCTYPE_LABEL[borrador.doc_type] ?? borrador.doc_type)}</td> <td class="svelte-1xju26n">${escape(borrador.dest_dept ?? "—")}</td> <td class="svelte-1xju26n">${validate_component(StatusChip, "StatusChip").$$render(
        $$result,
        {
          status: ESTADO_CHIP[borrador.estado] ?? "neutral",
          label: ESTADO_LABEL[borrador.estado] ?? borrador.estado,
          dot: true
        },
        {},
        {}
      )}</td> <td class="borradores-table__tracking svelte-1xju26n">${borrador._tracking_number ? `<span class="borradores-table__tracking-num svelte-1xju26n">${escape(borrador._tracking_number)}</span>` : `<span class="borradores-table__tracking-none svelte-1xju26n" data-svelte-h="svelte-jc1r77">—</span>`}</td> <td class="borradores-table__date svelte-1xju26n">${escape(formatDate(borrador.created_at))}</td> <td class="borradores-table__actions svelte-1xju26n">${validate_component(Button, "Button").$$render(
        $$result,
        {
          variant: "ghost",
          size: "sm",
          icon: "fas fa-pencil-alt",
          "aria-label": "Editar borrador " + borrador.subject
        },
        {},
        {
          default: () => {
            return `Abrir
                `;
          }
        }
      )}</td> </tr>`;
    })}</tbody></table></div>`}`}</div>  ${validate_component(Drawer, "Drawer").$$render(
      $$result,
      {
        open: drawerOpen,
        title: "Nuevo borrador",
        width: "600px"
      },
      {},
      {
        default: () => {
          return `<form class="borrador-form svelte-1xju26n"${add_attribute(
            "aria-label",
            "Formulario de nuevo borrador",
            0
          )} novalidate> ${``}  ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "borrador-doctype",
              label: "Tipo de documento",
              type: "select",
              required: true,
              disabled: !hasPerm || editingBorrador !== null,
              options: [{ value: "S", label: "Salida" }, { value: "I", label: "Interno" }],
              value: fDocType
            },
            {
              value: ($$value) => {
                fDocType = $$value;
                $$settled = false;
              }
            },
            {}
          )}  ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "borrador-subject",
              label: "Asunto",
              type: "text",
              placeholder: "Descripción breve del documento",
              required: true,
              disabled: !hasPerm || editingBorrador !== null,
              error: fSubjectError,
              value: fSubject
            },
            {
              value: ($$value) => {
                fSubject = $$value;
                $$settled = false;
              }
            },
            {}
          )}  ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "borrador-destdept",
              label: "Dependencia destino",
              type: "text",
              placeholder: "Nombre o código de la dependencia receptora",
              disabled: !hasPerm || editingBorrador !== null,
              value: fDestDept
            },
            {
              value: ($$value) => {
                fDestDept = $$value;
                $$settled = false;
              }
            },
            {}
          )}  ${plantillas.length > 0 ? `${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "borrador-plantilla",
              label: "Plantilla de contenido",
              type: "select",
              disabled: !hasPerm,
              options: plantillaOptions,
              hint: "Al elegir una plantilla, su contenido se carga en el editor.",
              value: fPlantillaId
            },
            {
              value: ($$value) => {
                fPlantillaId = $$value;
                $$settled = false;
              }
            },
            {}
          )}` : ``}  ${validate_component(RichEditor, "RichEditor").$$render(
            $$result,
            {
              id: "borrador-cuerpo",
              label: "Contenido del documento",
              placeholder: "Redacte el cuerpo del documento aquí…",
              disabled: !hasPerm || editingBorrador !== null,
              value: fCuerpo
            },
            {
              value: ($$value) => {
                fCuerpo = $$value;
                $$settled = false;
              }
            },
            {}
          )}  ${hasPerm && editingBorrador === null ? `<div class="borrador-form__save-row svelte-1xju26n">${validate_component(Button, "Button").$$render(
            $$result,
            {
              type: "submit",
              icon: "fas fa-save",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `${escape(
                  "Crear borrador"
                )}`;
              }
            }
          )}</div>` : ``}</form>  ${``}`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalRadicar,
        title: "Confirmar radicación",
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
              icon: "fas fa-paper-plane",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `Sí, radicar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-confirm__text svelte-1xju26n" data-svelte-h="svelte-udcuhz">¿Confirma la radicación de este borrador? Esta acción generará un radicado real con número de
    control inmutable y no puede deshacerse.</p>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalRadicadoOpen,
        title: "Radicado generado",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render($$result, {}, {}, {
            default: () => {
              return `Cerrar`;
            }
          })} `;
        },
        default: () => {
          return `<div class="modal-success svelte-1xju26n"><i class="fas fa-check-circle modal-success__icon svelte-1xju26n" aria-hidden="true"></i> <p class="modal-success__text svelte-1xju26n" data-svelte-h="svelte-1jvhz7g">El borrador fue radicado exitosamente.</p> ${``}</div>`;
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
