import { c as create_ssr_component, s as subscribe, v as validate_component, e as escape, d as each, b as add_attribute } from "../../../../../chunks/ssr.js";
import { p as permissions } from "../../../../../chunks/auth.js";
import { M as Modal } from "../../../../../chunks/Modal.js";
import { B as Button } from "../../../../../chunks/Button.js";
import { F as FormField } from "../../../../../chunks/FormField.js";
import { S as StatusChip } from "../../../../../chunks/StatusChip.js";
import { E as EmptyState } from "../../../../../chunks/EmptyState.js";
import { R as RichEditor } from "../../../../../chunks/RichEditor.js";
const css = {
  code: ":root{--op-primary:#2a8c3a;--op-primary-dark:#1c6b33;--op-primary-light:#dceed7;--op-on-primary:#ffffff;--op-accent:#8dbf3c;--op-brand-700:#173a22;--op-brand-400:#4fa84b;--op-text:#232624;--op-text-secondary:#6e7370;--op-border:#e7eae2;--op-bg:#fbfcf8;--op-surface:#ffffff;--op-success:#2a8c3a;--op-warning:#c8881c;--op-error:#c4452c;--op-info:#2b6cb0;--op-success-bg:#dceed7;--op-success-fg:#1c6b33;--op-warning-bg:#fdf3dc;--op-warning-fg:#7a5700;--op-error-bg:#fce8e8;--op-error-fg:#8b1a1a;--op-info-bg:#e0eef7;--op-info-fg:#1e4e6b;--op-error-strong-bg:#3b0000;--op-error-strong-fg:#ffdddd;--op-focus-ring:rgba(42, 140, 58, 0.15);--op-error-ring:rgba(196, 69, 44, 0.15);--op-font-family:Public Sans, Segoe UI, system-ui, -apple-system, sans-serif;--op-font-display:Space Grotesk, Segoe UI, system-ui, -apple-system, sans-serif;--op-font-size-base:15px;--op-line-height:1.6;--op-font-xs:12px;--op-font-sm:13px;--op-font-base:15px;--op-font-md:16px;--op-font-lg:18px;--op-font-xl:22px;--op-font-2xl:28px;--op-font-size-display:46px;--op-font-mono:12px;--op-weight-regular:400;--op-weight-medium:500;--op-weight-bold:600;--op-weight-display:700;--op-space-1:4px;--op-space-2:8px;--op-space-3:12px;--op-space-4:16px;--op-space-5:24px;--op-space-6:32px;--op-space-7:48px;--op-space-8:64px;--op-radius-sm:6px;--op-radius:10px;--op-radius-lg:16px;--op-radius-full:999px;--op-shadow-xs:0 1px 2px rgba(0, 0, 0, 0.04);--op-shadow-sm:0 2px 8px rgba(0, 0, 0, 0.05);--op-shadow:0 8px 24px rgba(0, 0, 0, 0.06);--op-shadow-lg:0 8px 24px rgba(0, 0, 0, 0.06);--op-transition:200ms cubic-bezier(0.2, 0.8, 0.2, 1);--op-motion-fast:120ms;--op-motion-base:200ms;--op-motion-slow:300ms;--op-motion-easing:cubic-bezier(0.2, 0.8, 0.2, 1);--op-sidebar-width:264px;--op-sidebar-collapsed-width:64px;--op-topbar-height:52px;--op-breadcrumb-height:36px}@media(prefers-reduced-motion: reduce){:root{--op-motion-fast:0ms;--op-motion-base:0ms;--op-motion-slow:0ms;--op-transition:0ms}.svelte-1ymhsf6.svelte-1ymhsf6,.svelte-1ymhsf6.svelte-1ymhsf6::before,.svelte-1ymhsf6.svelte-1ymhsf6::after{animation-duration:0.01ms !important;animation-iteration-count:1 !important;transition-duration:0.01ms !important;scroll-behavior:auto !important}}.plantillas-page.svelte-1ymhsf6.svelte-1ymhsf6{padding:var(--op-space-5);max-width:1000px}.plantillas-page__header.svelte-1ymhsf6.svelte-1ymhsf6{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:var(--op-space-3);margin-bottom:var(--op-space-5)}.plantillas-page__title.svelte-1ymhsf6.svelte-1ymhsf6{font-size:var(--op-font-lg);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}.plantillas-page__error.svelte-1ymhsf6.svelte-1ymhsf6{display:flex;align-items:center;gap:var(--op-space-2);padding:var(--op-space-3) var(--op-space-4);background:var(--op-error-bg);color:var(--op-error-fg);border-radius:var(--op-radius);font-size:var(--op-font-sm);margin-bottom:var(--op-space-4)}.plantillas-table-wrap.svelte-1ymhsf6.svelte-1ymhsf6{overflow-x:auto;border:1px solid var(--op-border);border-radius:var(--op-radius)}.plantillas-table.svelte-1ymhsf6.svelte-1ymhsf6{width:100%;border-collapse:collapse;font-size:var(--op-font-sm)}.plantillas-table.svelte-1ymhsf6 th.svelte-1ymhsf6,.plantillas-table.svelte-1ymhsf6 td.svelte-1ymhsf6{padding:var(--op-space-3) var(--op-space-4);text-align:left;border-bottom:1px solid var(--op-border)}.plantillas-table.svelte-1ymhsf6 th.svelte-1ymhsf6{background:var(--op-bg);font-weight:var(--op-weight-medium);color:var(--op-text-secondary);font-size:var(--op-font-xs);text-transform:uppercase;letter-spacing:0.04em}.plantillas-table.svelte-1ymhsf6 tbody tr:last-child td.svelte-1ymhsf6{border-bottom:none}.plantillas-table__row.svelte-1ymhsf6:hover td.svelte-1ymhsf6{background:var(--op-primary-light)}.plantillas-table__nombre.svelte-1ymhsf6.svelte-1ymhsf6{font-weight:var(--op-weight-medium);color:var(--op-text)}.plantillas-table__date.svelte-1ymhsf6.svelte-1ymhsf6{color:var(--op-text-secondary);white-space:nowrap}.plantillas-table__actions.svelte-1ymhsf6.svelte-1ymhsf6{width:100px}.plantilla-form.svelte-1ymhsf6.svelte-1ymhsf6{display:flex;flex-direction:column;gap:var(--op-space-4)}.plantilla-form__check-row.svelte-1ymhsf6.svelte-1ymhsf6{display:flex;align-items:center}.plantilla-form__check-label.svelte-1ymhsf6.svelte-1ymhsf6{display:flex;align-items:center;gap:var(--op-space-2);font-size:var(--op-font-sm);color:var(--op-text);cursor:pointer;user-select:none}.plantilla-form__check.svelte-1ymhsf6.svelte-1ymhsf6{width:16px;height:16px;accent-color:var(--op-primary);cursor:pointer}.plantilla-form__editor.svelte-1ymhsf6.svelte-1ymhsf6{display:flex;flex-direction:column;gap:var(--op-space-1)}.plantilla-form__editor-error.svelte-1ymhsf6.svelte-1ymhsf6{font-size:var(--op-font-xs);color:var(--op-error);margin:0}.modal-confirm__text.svelte-1ymhsf6.svelte-1ymhsf6{font-size:var(--op-font-sm);color:var(--op-text);line-height:var(--op-line-height);margin:0}",
  map: `{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /admin/plantillas — CRUD de plantillas de contenido reutilizables (T-12c).\\n\\n  RBAC (espejo del backend, D-11):\\n    - Crear / borrar plantillas → USUA_PERM_ADMIN\\n    - Ver la lista → USUA_PERM_ADMIN (esta vista es de administración)\\n    Para consumir plantillas al crear un borrador, el proxy /admin/plantillas/api\\n    admite GET con PERM_RADI (sin llegar a esta vista).\\n\\n  D-05 — access_token NUNCA en cliente:\\n    Todas las escrituras van a /admin/plantillas/api[/id] que leen locals.accessToken.\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { permissions } from \\"$lib/stores/auth.js\\";\\n  import { notifyError, notifySuccess } from \\"$lib/stores/notifications.js\\";\\n\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import RichEditor from \\"$lib/components/editor/RichEditor.svelte\\";\\n\\n  /** @type {import('./$types').PageData} */\\n  export let data;\\n\\n  /** @type {import('$lib/types/api.js').PlantillaResponse[]} */\\n  let plantillas = data.plantillas ?? [];\\n  let actionLoading = false;\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal de creación\\n  // ---------------------------------------------------------------------------\\n  let modalCreate = false;\\n  let fNombre = \\"\\";\\n  let fTipoDocumental = \\"\\";\\n  let fCuerpo = \\"\\";\\n  let fActivo = true;\\n  let fNombreError = \\"\\";\\n  let fCuerpoError = \\"\\";\\n\\n  function openCreate() {\\n    fNombre = \\"\\";\\n    fTipoDocumental = \\"\\";\\n    fCuerpo = \\"\\";\\n    fActivo = true;\\n    fNombreError = \\"\\";\\n    fCuerpoError = \\"\\";\\n    modalCreate = true;\\n  }\\n\\n  async function crearPlantilla() {\\n    fNombreError = \\"\\";\\n    fCuerpoError = \\"\\";\\n\\n    if (!fNombre.trim()) {\\n      fNombreError = \\"El nombre es obligatorio.\\";\\n      return;\\n    }\\n    if (!fCuerpo.trim()) {\\n      fCuerpoError = \\"El contenido no puede estar vacío.\\";\\n      return;\\n    }\\n\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(\\"/admin/plantillas/api\\", {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          nombre: fNombre.trim(),\\n          tipo_documental: fTipoDocumental.trim() || null,\\n          cuerpo: fCuerpo,\\n          activo: fActivo,\\n        }),\\n      });\\n\\n      const dataRes = await res.json();\\n      if (!res.ok) {\\n        notifyError(dataRes.detail ?? \\"Error al crear la plantilla.\\");\\n        return;\\n      }\\n\\n      plantillas = [dataRes, ...plantillas];\\n      modalCreate = false;\\n      notifySuccess(\\"Plantilla creada.\\");\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Confirmación de borrado\\n  // ---------------------------------------------------------------------------\\n  /** @type {import('$lib/types/api.js').PlantillaResponse|null} */\\n  let deletingPlantilla = null;\\n  let modalDelete = false;\\n\\n  function confirmDelete(plantilla) {\\n    deletingPlantilla = plantilla;\\n    modalDelete = true;\\n  }\\n\\n  async function deletePlantilla() {\\n    if (!deletingPlantilla) return;\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(\`/admin/plantillas/api/\${deletingPlantilla.id}\`, {\\n        method: \\"DELETE\\",\\n      });\\n\\n      if (res.status === 204 || res.ok) {\\n        plantillas = plantillas.filter((p) => p.id !== deletingPlantilla.id);\\n        notifySuccess(\\"Plantilla eliminada.\\");\\n        modalDelete = false;\\n        deletingPlantilla = null;\\n      } else {\\n        const err = await res.json().catch(() => ({}));\\n        notifyError(err.detail ?? \\"No se pudo eliminar la plantilla.\\");\\n      }\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers\\n  // ---------------------------------------------------------------------------\\n  function formatDate(iso) {\\n    if (!iso) return \\"—\\";\\n    try {\\n      return new Intl.DateTimeFormat(\\"es-CO\\", {\\n        day: \\"2-digit\\",\\n        month: \\"2-digit\\",\\n        year: \\"numeric\\",\\n      }).format(new Date(iso));\\n    } catch {\\n      return iso.slice(0, 10);\\n    }\\n  }\\n\\n  $: isAdmin = $permissions.includes(\\"USUA_PERM_ADMIN\\");\\n\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Administración\\", href: \\"/admin\\" }, { label: \\"Plantillas\\" }]);\\n  });\\n<\/script>\\n\\n<svelte:head>\\n  <title>Plantillas de contenido — OrpycaMCP</title>\\n</svelte:head>\\n\\n<div class=\\"plantillas-page\\">\\n  <!-- Cabecera -->\\n  <div class=\\"plantillas-page__header\\">\\n    <h1 class=\\"plantillas-page__title\\">Plantillas de contenido</h1>\\n    {#if isAdmin}\\n      <Button icon=\\"fas fa-plus\\" on:click={openCreate}>Nueva plantilla</Button>\\n    {/if}\\n  </div>\\n\\n  <!-- Error de carga -->\\n  {#if data.loadError}\\n    <div class=\\"plantillas-page__error\\" role=\\"alert\\">\\n      <i class=\\"fas fa-exclamation-triangle\\" aria-hidden=\\"true\\"></i>\\n      {data.loadError}\\n    </div>\\n  {/if}\\n\\n  <!-- Lista vacía -->\\n  {#if plantillas.length === 0 && !data.loadError}\\n    <EmptyState\\n      icon=\\"fa-copy\\"\\n      title=\\"Sin plantillas\\"\\n      description=\\"Crea la primera plantilla de contenido con el botón «Nueva plantilla».\\"\\n    />\\n  {:else if plantillas.length > 0}\\n    <!-- Tabla -->\\n    <div class=\\"plantillas-table-wrap\\" role=\\"region\\" aria-label=\\"Lista de plantillas\\">\\n      <table class=\\"plantillas-table\\" aria-label=\\"Plantillas de contenido\\">\\n        <thead>\\n          <tr>\\n            <th scope=\\"col\\">Nombre</th>\\n            <th scope=\\"col\\">Tipo documental</th>\\n            <th scope=\\"col\\">Estado</th>\\n            <th scope=\\"col\\">Creada</th>\\n            {#if isAdmin}\\n              <th scope=\\"col\\"><span class=\\"sr-only\\">Acciones</span></th>\\n            {/if}\\n          </tr>\\n        </thead>\\n        <tbody>\\n          {#each plantillas as plantilla (plantilla.id)}\\n            <tr class=\\"plantillas-table__row\\">\\n              <td class=\\"plantillas-table__nombre\\">{plantilla.nombre}</td>\\n              <td>{plantilla.tipo_documental ?? \\"—\\"}</td>\\n              <td>\\n                <StatusChip\\n                  status={plantilla.activo ? \\"success\\" : \\"neutral\\"}\\n                  label={plantilla.activo ? \\"Activa\\" : \\"Inactiva\\"}\\n                  dot\\n                />\\n              </td>\\n              <td class=\\"plantillas-table__date\\">{formatDate(plantilla.created_at)}</td>\\n              {#if isAdmin}\\n                <td class=\\"plantillas-table__actions\\">\\n                  <Button\\n                    variant=\\"ghost\\"\\n                    size=\\"sm\\"\\n                    icon=\\"fas fa-trash\\"\\n                    aria-label=\\"Eliminar plantilla {plantilla.nombre}\\"\\n                    on:click={() => confirmDelete(plantilla)}\\n                  >\\n                    Eliminar\\n                  </Button>\\n                </td>\\n              {/if}\\n            </tr>\\n          {/each}\\n        </tbody>\\n      </table>\\n    </div>\\n  {/if}\\n</div>\\n\\n<!-- =========================================================================\\n     Modal: crear plantilla\\n     ========================================================================= -->\\n<Modal open={modalCreate} title=\\"Nueva plantilla\\" size=\\"lg\\" on:close={() => (modalCreate = false)}>\\n  <form\\n    class=\\"plantilla-form\\"\\n    on:submit|preventDefault={crearPlantilla}\\n    aria-label=\\"Formulario de nueva plantilla\\"\\n    novalidate\\n  >\\n    <FormField\\n      id=\\"plantilla-nombre\\"\\n      label=\\"Nombre\\"\\n      type=\\"text\\"\\n      bind:value={fNombre}\\n      placeholder=\\"Identificador de la plantilla\\"\\n      required\\n      error={fNombreError}\\n    />\\n\\n    <FormField\\n      id=\\"plantilla-tipo\\"\\n      label=\\"Tipo documental\\"\\n      type=\\"text\\"\\n      bind:value={fTipoDocumental}\\n      placeholder=\\"Ej. Circular, Oficio, Resolución (opcional)\\"\\n    />\\n\\n    <div class=\\"plantilla-form__check-row\\">\\n      <label class=\\"plantilla-form__check-label\\" for=\\"plantilla-activo\\">\\n        <input\\n          id=\\"plantilla-activo\\"\\n          type=\\"checkbox\\"\\n          class=\\"plantilla-form__check\\"\\n          bind:checked={fActivo}\\n        />\\n        Activa (visible en el selector de borradores)\\n      </label>\\n    </div>\\n\\n    <div class=\\"plantilla-form__editor\\">\\n      <!--\\n        MEDIO #3: El id del mensaje de error se pasa a RichEditor vía ariaDescribedby\\n        para que el contenteditable de TipTap quede asociado al mensaje cuando hay error.\\n      -->\\n      <RichEditor\\n        id=\\"plantilla-cuerpo\\"\\n        label=\\"Contenido de la plantilla\\"\\n        bind:value={fCuerpo}\\n        placeholder=\\"Escriba el contenido reutilizable de la plantilla…\\"\\n        ariaDescribedby={fCuerpoError ? \\"plantilla-cuerpo-error\\" : undefined}\\n      />\\n      <p\\n        id=\\"plantilla-cuerpo-error\\"\\n        class=\\"plantilla-form__editor-error\\"\\n        role=\\"alert\\"\\n        aria-live=\\"polite\\"\\n        aria-atomic=\\"true\\"\\n      >\\n        {fCuerpoError || \\"\\"}\\n      </p>\\n    </div>\\n  </form>\\n\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (modalCreate = false)}>Cancelar</Button>\\n    <Button icon=\\"fas fa-save\\" loading={actionLoading} on:click={crearPlantilla}>\\n      Crear plantilla\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- =========================================================================\\n     Modal: confirmar borrado\\n     ========================================================================= -->\\n<Modal\\n  open={modalDelete}\\n  title=\\"Eliminar plantilla\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalDelete = false)}\\n>\\n  <p class=\\"modal-confirm__text\\">\\n    ¿Está seguro de que desea eliminar la plantilla\\n    <strong>{deletingPlantilla?.nombre}</strong>? Esta acción no puede deshacerse.\\n  </p>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (modalDelete = false)}>Cancelar</Button>\\n    <Button variant=\\"danger\\" icon=\\"fas fa-trash\\" loading={actionLoading} on:click={deletePlantilla}>\\n      Eliminar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<style lang=\\"scss\\">:root {\\n  --op-primary: #2a8c3a;\\n  --op-primary-dark: #1c6b33;\\n  --op-primary-light: #dceed7;\\n  --op-on-primary: #ffffff;\\n  --op-accent: #8dbf3c;\\n  --op-brand-700: #173a22;\\n  --op-brand-400: #4fa84b;\\n  --op-text: #232624;\\n  --op-text-secondary: #6e7370;\\n  --op-border: #e7eae2;\\n  --op-bg: #fbfcf8;\\n  --op-surface: #ffffff;\\n  --op-success: #2a8c3a;\\n  --op-warning: #c8881c;\\n  --op-error: #c4452c;\\n  --op-info: #2b6cb0;\\n  --op-success-bg: #dceed7;\\n  --op-success-fg: #1c6b33;\\n  --op-warning-bg: #fdf3dc;\\n  --op-warning-fg: #7a5700;\\n  --op-error-bg: #fce8e8;\\n  --op-error-fg: #8b1a1a;\\n  --op-info-bg: #e0eef7;\\n  --op-info-fg: #1e4e6b;\\n  --op-error-strong-bg: #3b0000;\\n  --op-error-strong-fg: #ffdddd;\\n  --op-focus-ring: rgba(42, 140, 58, 0.15);\\n  --op-error-ring: rgba(196, 69, 44, 0.15);\\n  --op-font-family: Public Sans, Segoe UI, system-ui, -apple-system, sans-serif;\\n  --op-font-display: Space Grotesk, Segoe UI, system-ui, -apple-system, sans-serif;\\n  --op-font-size-base: 15px;\\n  --op-line-height: 1.6;\\n  --op-font-xs: 12px;\\n  --op-font-sm: 13px;\\n  --op-font-base: 15px;\\n  --op-font-md: 16px;\\n  --op-font-lg: 18px;\\n  --op-font-xl: 22px;\\n  --op-font-2xl: 28px;\\n  --op-font-size-display: 46px;\\n  --op-font-mono: 12px;\\n  --op-weight-regular: 400;\\n  --op-weight-medium: 500;\\n  --op-weight-bold: 600;\\n  --op-weight-display: 700;\\n  --op-space-1: 4px;\\n  --op-space-2: 8px;\\n  --op-space-3: 12px;\\n  --op-space-4: 16px;\\n  --op-space-5: 24px;\\n  --op-space-6: 32px;\\n  --op-space-7: 48px;\\n  --op-space-8: 64px;\\n  --op-radius-sm: 6px;\\n  --op-radius: 10px;\\n  --op-radius-lg: 16px;\\n  --op-radius-full: 999px;\\n  --op-shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);\\n  --op-shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.05);\\n  --op-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);\\n  --op-shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.06);\\n  --op-transition: 200ms cubic-bezier(0.2, 0.8, 0.2, 1);\\n  --op-motion-fast: 120ms;\\n  --op-motion-base: 200ms;\\n  --op-motion-slow: 300ms;\\n  --op-motion-easing: cubic-bezier(0.2, 0.8, 0.2, 1);\\n  --op-sidebar-width: 264px;\\n  --op-sidebar-collapsed-width: 64px;\\n  --op-topbar-height: 52px;\\n  --op-breadcrumb-height: 36px;\\n}\\n\\n@media (prefers-reduced-motion: reduce) {\\n  :root {\\n    --op-motion-fast: 0ms;\\n    --op-motion-base: 0ms;\\n    --op-motion-slow: 0ms;\\n    --op-transition: 0ms;\\n  }\\n  *,\\n  *::before,\\n  *::after {\\n    animation-duration: 0.01ms !important;\\n    animation-iteration-count: 1 !important;\\n    transition-duration: 0.01ms !important;\\n    scroll-behavior: auto !important;\\n  }\\n}\\n.plantillas-page {\\n  padding: var(--op-space-5);\\n  max-width: 1000px;\\n}\\n.plantillas-page__header {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-3);\\n  margin-bottom: var(--op-space-5);\\n}\\n.plantillas-page__title {\\n  font-size: var(--op-font-lg);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n.plantillas-page__error {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  padding: var(--op-space-3) var(--op-space-4);\\n  background: var(--op-error-bg);\\n  color: var(--op-error-fg);\\n  border-radius: var(--op-radius);\\n  font-size: var(--op-font-sm);\\n  margin-bottom: var(--op-space-4);\\n}\\n\\n.plantillas-table-wrap {\\n  overflow-x: auto;\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n}\\n\\n.plantillas-table {\\n  width: 100%;\\n  border-collapse: collapse;\\n  font-size: var(--op-font-sm);\\n}\\n.plantillas-table th,\\n.plantillas-table td {\\n  padding: var(--op-space-3) var(--op-space-4);\\n  text-align: left;\\n  border-bottom: 1px solid var(--op-border);\\n}\\n.plantillas-table th {\\n  background: var(--op-bg);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text-secondary);\\n  font-size: var(--op-font-xs);\\n  text-transform: uppercase;\\n  letter-spacing: 0.04em;\\n}\\n.plantillas-table tbody tr:last-child td {\\n  border-bottom: none;\\n}\\n.plantillas-table__row:hover td {\\n  background: var(--op-primary-light);\\n}\\n.plantillas-table__nombre {\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text);\\n}\\n.plantillas-table__date {\\n  color: var(--op-text-secondary);\\n  white-space: nowrap;\\n}\\n.plantillas-table__actions {\\n  width: 100px;\\n}\\n\\n.plantilla-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}\\n.plantilla-form__check-row {\\n  display: flex;\\n  align-items: center;\\n}\\n.plantilla-form__check-label {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  cursor: pointer;\\n  user-select: none;\\n}\\n.plantilla-form__check {\\n  width: 16px;\\n  height: 16px;\\n  accent-color: var(--op-primary);\\n  cursor: pointer;\\n}\\n.plantilla-form__editor {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-1);\\n}\\n.plantilla-form__editor-error {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-error);\\n  margin: 0;\\n}\\n\\n.modal-confirm__text {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  line-height: var(--op-line-height);\\n  margin: 0;\\n}</style>\\n"],"names":[],"mappings":"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"}`
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
  let isAdmin;
  let $permissions, $$unsubscribe_permissions;
  $$unsubscribe_permissions = subscribe(permissions, (value) => $permissions = value);
  let { data } = $$props;
  let plantillas = data.plantillas ?? [];
  let actionLoading = false;
  let modalCreate = false;
  let fNombre = "";
  let fTipoDocumental = "";
  let fCuerpo = "";
  let fActivo = true;
  let fNombreError = "";
  let deletingPlantilla = null;
  let modalDelete = false;
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    isAdmin = $permissions.includes("USUA_PERM_ADMIN");
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-x0r8gr_START -->${$$result.title = `<title>Plantillas de contenido — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-x0r8gr_END -->`, ""} <div class="plantillas-page svelte-1ymhsf6"> <div class="plantillas-page__header svelte-1ymhsf6"><h1 class="plantillas-page__title svelte-1ymhsf6" data-svelte-h="svelte-42l7h7">Plantillas de contenido</h1> ${isAdmin ? `${validate_component(Button, "Button").$$render($$result, { icon: "fas fa-plus" }, {}, {
      default: () => {
        return `Nueva plantilla`;
      }
    })}` : ``}</div>  ${data.loadError ? `<div class="plantillas-page__error svelte-1ymhsf6" role="alert"><i class="fas fa-exclamation-triangle svelte-1ymhsf6" aria-hidden="true"></i> ${escape(data.loadError)}</div>` : ``}  ${plantillas.length === 0 && !data.loadError ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fa-copy",
        title: "Sin plantillas",
        description: "Crea la primera plantilla de contenido con el botón «Nueva plantilla»."
      },
      {},
      {}
    )}` : `${plantillas.length > 0 ? ` <div class="plantillas-table-wrap svelte-1ymhsf6" role="region" aria-label="Lista de plantillas"><table class="plantillas-table svelte-1ymhsf6" aria-label="Plantillas de contenido"><thead class="svelte-1ymhsf6"><tr class="svelte-1ymhsf6"><th scope="col" class="svelte-1ymhsf6" data-svelte-h="svelte-uiykxa">Nombre</th> <th scope="col" class="svelte-1ymhsf6" data-svelte-h="svelte-zn3ug1">Tipo documental</th> <th scope="col" class="svelte-1ymhsf6" data-svelte-h="svelte-c5iyzt">Estado</th> <th scope="col" class="svelte-1ymhsf6" data-svelte-h="svelte-pqpx9v">Creada</th> ${isAdmin ? `<th scope="col" class="svelte-1ymhsf6" data-svelte-h="svelte-xhrulm"><span class="sr-only svelte-1ymhsf6">Acciones</span></th>` : ``}</tr></thead> <tbody class="svelte-1ymhsf6">${each(plantillas, (plantilla) => {
      return `<tr class="plantillas-table__row svelte-1ymhsf6"><td class="plantillas-table__nombre svelte-1ymhsf6">${escape(plantilla.nombre)}</td> <td class="svelte-1ymhsf6">${escape(plantilla.tipo_documental ?? "—")}</td> <td class="svelte-1ymhsf6">${validate_component(StatusChip, "StatusChip").$$render(
        $$result,
        {
          status: plantilla.activo ? "success" : "neutral",
          label: plantilla.activo ? "Activa" : "Inactiva",
          dot: true
        },
        {},
        {}
      )}</td> <td class="plantillas-table__date svelte-1ymhsf6">${escape(formatDate(plantilla.created_at))}</td> ${isAdmin ? `<td class="plantillas-table__actions svelte-1ymhsf6">${validate_component(Button, "Button").$$render(
        $$result,
        {
          variant: "ghost",
          size: "sm",
          icon: "fas fa-trash",
          "aria-label": "Eliminar plantilla " + plantilla.nombre
        },
        {},
        {
          default: () => {
            return `Eliminar
                  `;
          }
        }
      )} </td>` : ``} </tr>`;
    })}</tbody></table></div>` : ``}`}</div>  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalCreate,
        title: "Nueva plantilla",
        size: "lg"
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
              icon: "fas fa-save",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `Crear plantilla`;
              }
            }
          )} `;
        },
        default: () => {
          return `<form class="plantilla-form svelte-1ymhsf6" aria-label="Formulario de nueva plantilla" novalidate>${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "plantilla-nombre",
              label: "Nombre",
              type: "text",
              placeholder: "Identificador de la plantilla",
              required: true,
              error: fNombreError,
              value: fNombre
            },
            {
              value: ($$value) => {
                fNombre = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "plantilla-tipo",
              label: "Tipo documental",
              type: "text",
              placeholder: "Ej. Circular, Oficio, Resolución (opcional)",
              value: fTipoDocumental
            },
            {
              value: ($$value) => {
                fTipoDocumental = $$value;
                $$settled = false;
              }
            },
            {}
          )} <div class="plantilla-form__check-row svelte-1ymhsf6"><label class="plantilla-form__check-label svelte-1ymhsf6" for="plantilla-activo"><input id="plantilla-activo" type="checkbox" class="plantilla-form__check svelte-1ymhsf6"${add_attribute("checked", fActivo, 1)}>
        Activa (visible en el selector de borradores)</label></div> <div class="plantilla-form__editor svelte-1ymhsf6"> ${validate_component(RichEditor, "RichEditor").$$render(
            $$result,
            {
              id: "plantilla-cuerpo",
              label: "Contenido de la plantilla",
              placeholder: "Escriba el contenido reutilizable de la plantilla…",
              ariaDescribedby: void 0,
              value: fCuerpo
            },
            {
              value: ($$value) => {
                fCuerpo = $$value;
                $$settled = false;
              }
            },
            {}
          )} <p id="plantilla-cuerpo-error" class="plantilla-form__editor-error svelte-1ymhsf6" role="alert" aria-live="polite" aria-atomic="true">${escape("")}</p></div></form>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalDelete,
        title: "Eliminar plantilla",
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
              variant: "danger",
              icon: "fas fa-trash",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `Eliminar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-confirm__text svelte-1ymhsf6">¿Está seguro de que desea eliminar la plantilla
    <strong class="svelte-1ymhsf6">${escape(deletingPlantilla?.nombre)}</strong>? Esta acción no puede deshacerse.</p>`;
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
