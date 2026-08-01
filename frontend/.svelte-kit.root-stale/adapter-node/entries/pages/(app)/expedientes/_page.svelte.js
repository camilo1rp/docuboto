import { c as create_ssr_component, s as subscribe, v as validate_component, e as escape, b as add_attribute, d as each } from "../../../../chunks/ssr.js";
import "../../../../chunks/client.js";
import { n as navigating } from "../../../../chunks/stores.js";
import { p as permissions } from "../../../../chunks/auth.js";
import { D as DataTable } from "../../../../chunks/DataTable.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
import { B as Button } from "../../../../chunks/Button.js";
import { F as FormField } from "../../../../chunks/FormField.js";
import { M as Modal } from "../../../../chunks/Modal.js";
import { S as SchemaField } from "../../../../chunks/SchemaField2.js";
import { r as resolveSchemaControl } from "../../../../chunks/schemaField.js";
const css = {
  code: ".expedientes.svelte-cdm6il{padding:var(--op-space-6);max-width:1200px;margin:0 auto;display:flex;flex-direction:column;gap:var(--op-space-4)}.expedientes__header.svelte-cdm6il{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:var(--op-space-3);padding-bottom:var(--op-space-4);border-bottom:1px solid var(--op-border)}.expedientes__title.svelte-cdm6il{font-size:var(--op-font-xl);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}.expedientes__filters.svelte-cdm6il{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);padding:var(--op-space-4) var(--op-space-5)}.filters-form.svelte-cdm6il{display:flex;align-items:flex-end;gap:var(--op-space-4);flex-wrap:wrap}.filters-form__fields.svelte-cdm6il{display:flex;gap:var(--op-space-3);flex-wrap:wrap;flex:1}.filters-form__actions.svelte-cdm6il{display:flex;gap:var(--op-space-2);align-items:flex-end;margin-bottom:2px}.expedientes__table-wrap.svelte-cdm6il{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);overflow:hidden}.expediente-code.svelte-cdm6il{font-family:monospace;font-size:var(--op-font-xs);font-weight:var(--op-weight-medium);color:var(--op-primary-dark);background:var(--op-primary-light);padding:2px var(--op-space-2);border-radius:var(--op-radius-sm);white-space:nowrap}.action-link.svelte-cdm6il{display:inline-flex;align-items:center;gap:var(--op-space-1);font-size:var(--op-font-xs);font-weight:var(--op-weight-medium);color:var(--op-primary-dark);text-decoration:none;padding:3px var(--op-space-2);border-radius:var(--op-radius-sm);border:1px solid var(--op-primary-light);background:var(--op-primary-light);transition:background var(--op-transition), color var(--op-transition)}.action-link.svelte-cdm6il:hover{background:var(--op-primary);color:var(--op-surface);border-color:var(--op-primary)}.action-link.svelte-cdm6il:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}.modal-form.svelte-cdm6il{display:flex;flex-direction:column;gap:var(--op-space-3)}.modal-form__meta.svelte-cdm6il{display:flex;flex-direction:column;gap:var(--op-space-3);padding:var(--op-space-3);border:1px dashed var(--op-border);border-radius:var(--op-radius);background:var(--op-bg)}.modal-form__section-label.svelte-cdm6il{display:flex;align-items:center;gap:var(--op-space-2);margin:0;font-size:var(--op-font-xs);font-weight:var(--op-weight-medium);color:var(--op-text-secondary);text-transform:uppercase;letter-spacing:0.03em}.modal-form__meta-loading.svelte-cdm6il{display:flex;align-items:center;gap:var(--op-space-2);margin:0;font-size:var(--op-font-sm);color:var(--op-text-secondary)}.modal-form__meta-warning.svelte-cdm6il{margin:0;font-size:var(--op-font-xs);color:var(--op-warning-fg)}.modal-error.svelte-cdm6il{background:var(--op-error-bg);color:var(--op-error-fg);border:1px solid var(--op-error);border-radius:var(--op-radius);padding:var(--op-space-3) var(--op-space-4);font-size:var(--op-font-sm);margin:0 0 var(--op-space-4)}@media(max-width: 768px){.expedientes.svelte-cdm6il{padding:var(--op-space-4)}}",
  map: '{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /expedientes — Listado de expedientes (T-12, RF-UI-05).\\n\\n  Todo el estado de la vista (filtros, página) vive en la URL: los cambios usan\\n  goto() y re-ejecutan +page.server.js#load (navegación cliente de SvelteKit,\\n  sin recarga completa). El listado en sí es SSR-puro; el ÚNICO endpoint\\n  proxy bajo /expedientes/api es GET metadata-templates (D-05), usado por el\\n  Modal de creación al elegir una serie TRD (T-12 Form Builder, §1 del\\n  contrato orfeo-architect) — el access_token nunca sale del servidor.\\n\\n  Filtros soportados por /api/v1/expedientes/search:\\n    - q (texto libre, opcional; se omite del backend si tiene <2 caracteres)\\n    - status: open | closed | transferred\\n    - date_from, date_to (ISO date)\\n    - page (size fijo en 20, no configurable desde la UI)\\n\\n  Acción de fila: solo \\"Abrir\\" (enlace al workspace del expediente, T-10).\\n  NO hay cerrar/transferir aquí: el backend los exige con crud>=3 pero\\n  /auth/me expone permissions[] sin nivel crud — no se puede espejar esa\\n  condición sin renderizar botones que el backend rechazaría (peor que no\\n  mostrarlos). Cerrar/transferir viven en el workspace de expediente.\\n\\n  Modal de creación (T-12 Form Builder): al elegir una serie TRD se carga su\\n  plantilla de metadatos ACTIVA (json_schema) y se renderizan sus campos\\n  dinámicos vía SchemaField.svelte — ver $lib/utils/schemaField.js para el\\n  mapeo tipo-de-schema -> control. Constructos no soportados (object/array/\\n  oneOf/$ref) no se renderizan; se avisa con un contador.\\n\\n  nivel_seguridad: NO se muestra en el listado ni en el formulario de\\n  creación (fijo en 1/PÚBLICA) — ver nota en +page.server.js#actions.create.\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { goto, invalidateAll } from \\"$app/navigation\\";\\n  import { enhance } from \\"$app/forms\\";\\n  import { navigating } from \\"$app/stores\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { permissions } from \\"$lib/stores/auth.js\\";\\n\\n  import DataTable from \\"$lib/components/ui/DataTable.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import SchemaField from \\"$lib/components/ui/SchemaField.svelte\\";\\n  import { resolveSchemaControl } from \\"$lib/utils/schemaField.js\\";\\n\\n  /** @type {import(\'./$types\').PageData} */\\n  export let data;\\n  /** @type {import(\'./$types\').ActionData} */\\n  export let form;\\n\\n  const pageSize = 20;\\n\\n  // ---------------------------------------------------------------------------\\n  // Filtros — estado editable del formulario, resincronizado desde la URL en\\n  // cada carga (M-4): sin esto, navegar atrás/adelante cambia la tabla pero\\n  // deja los inputs con los valores de la navegación anterior.\\n  // ---------------------------------------------------------------------------\\n  let filterQ = \\"\\";\\n  let filterStatus = \\"\\";\\n  let filterDateFrom = \\"\\";\\n  let filterDateTo = \\"\\";\\n  let dateRangeError = \\"\\";\\n\\n  /** @param {{q?: string, status?: string, dateFrom?: string, dateTo?: string}|undefined} filters */\\n  function syncFiltersFromUrl(filters) {\\n    filterQ = filters?.q ?? \\"\\";\\n    filterStatus = filters?.status ?? \\"\\";\\n    filterDateFrom = filters?.dateFrom ?? \\"\\";\\n    filterDateTo = filters?.dateTo ?? \\"\\";\\n  }\\n\\n  $: syncFiltersFromUrl(data.initialFilters);\\n\\n  // ---------------------------------------------------------------------------\\n  // Datos de la tabla — siempre reflejan la última carga SSR (goto() los renueva)\\n  // ---------------------------------------------------------------------------\\n  $: expedientes = data.expedientes ?? [];\\n  $: total = data.total ?? 0;\\n  $: currentPage = data.initialFilters?.page ?? 1;\\n\\n  // $navigating: solo mientras SvelteKit resuelve una navegación HACIA esta\\n  // misma vista (filtro/página/reintentar). Una navegación de salida (p. ej.\\n  // \\"Abrir\\" a /expedientes/{id}) no debe hacer parpadear la tabla a esqueleto.\\n  $: loading = $navigating?.to?.route?.id === \\"/(app)/expedientes\\";\\n\\n  // ---------------------------------------------------------------------------\\n  // Autorización (espejo del RBAC; el backend siempre re-valida — ADR-013/020)\\n  // ---------------------------------------------------------------------------\\n  $: canManage = $permissions.includes(\\"USUA_PERM_EXPEDIENTE\\");\\n\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Expedientes\\" }]);\\n  });\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de presentación\\n  // ---------------------------------------------------------------------------\\n  const STATUS_LABELS = { open: \\"Abierto\\", closed: \\"Cerrado\\", transferred: \\"Transferido\\" };\\n  // \\"closed\\" (cerrado) es un estado normal del ciclo de vida, no un error:\\n  // StatusChip mapea closed→rojo, así que se pide explícitamente \\"neutral\\" (gris).\\n  const STATUS_CHIP_MAP = { open: \\"active\\", closed: \\"neutral\\", transferred: \\"info\\" };\\n\\n  const TABLE_COLUMNS = [\\n    { key: \\"code\\", label: \\"Código\\", width: \\"160px\\" },\\n    { key: \\"name\\", label: \\"Nombre\\" },\\n    { key: \\"status\\", label: \\"Estado\\", width: \\"140px\\" },\\n    { key: \\"opened_at\\", label: \\"Apertura\\", width: \\"130px\\" },\\n    { key: \\"actions\\", label: \\"Acciones\\", width: \\"110px\\" },\\n  ];\\n\\n  /** @param {string|null|undefined} iso */\\n  function formatDate(iso) {\\n    if (!iso) return \\"—\\";\\n    return new Date(iso).toLocaleDateString(\\"es-CO\\", {\\n      year: \\"numeric\\",\\n      month: \\"2-digit\\",\\n      day: \\"2-digit\\",\\n    });\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Filtros y paginación — todo pasa por goto() (URL = fuente de verdad)\\n  // ---------------------------------------------------------------------------\\n\\n  /** @param {number} page */\\n  function buildParams(page = 1) {\\n    const params = new URLSearchParams();\\n    if (filterQ.trim().length >= 2) params.set(\\"q\\", filterQ.trim());\\n    if (filterStatus) params.set(\\"status\\", filterStatus);\\n    if (filterDateFrom) params.set(\\"date_from\\", filterDateFrom);\\n    if (filterDateTo) params.set(\\"date_to\\", filterDateTo);\\n    if (page > 1) params.set(\\"page\\", String(page));\\n    return params;\\n  }\\n\\n  function applyFilters() {\\n    dateRangeError = \\"\\";\\n    if (filterDateFrom && filterDateTo && filterDateFrom > filterDateTo) {\\n      dateRangeError = \\"La fecha inicio no puede ser posterior a la fecha fin.\\";\\n      return;\\n    }\\n    goto(`/expedientes?${buildParams(1).toString()}`, { keepFocus: true });\\n  }\\n\\n  function clearFilters() {\\n    filterQ = \\"\\";\\n    filterStatus = \\"\\";\\n    filterDateFrom = \\"\\";\\n    filterDateTo = \\"\\";\\n    dateRangeError = \\"\\";\\n    goto(\\"/expedientes\\", { keepFocus: true });\\n  }\\n\\n  /** @param {CustomEvent<{page: number}>} e */\\n  function onPageChange(e) {\\n    goto(`/expedientes?${buildParams(e.detail.page).toString()}`);\\n  }\\n\\n  $: hasActiveFilters = !!(filterQ.trim() || filterStatus || filterDateFrom || filterDateTo);\\n\\n  // ---------------------------------------------------------------------------\\n  // Modal de creación (actions.create — form action nativo, sin +server.js)\\n  // ---------------------------------------------------------------------------\\n  let showCreateModal = false;\\n  let createName = \\"\\";\\n  let createDescription = \\"\\";\\n  let createTrdSerieId = \\"\\";\\n  let creating = false;\\n\\n  // ---------------------------------------------------------------------------\\n  // Form Builder (T-12): campos dinámicos de la plantilla de metadatos de la\\n  // serie TRD elegida. Se traen del proxy GET /expedientes/api/metadata-templates\\n  // (D-05, contrato §1) al cambiar `createTrdSerieId` — no del `load` SSR:\\n  // el Modal tiene estado local y un reload SSR a mitad de formulario lo\\n  // perdería.\\n  // ---------------------------------------------------------------------------\\n  /** @type {any} plantilla activa de la serie (ExpedienteTemplateResponse) */\\n  let activeTemplate = null;\\n  let loadingTemplate = false;\\n  /** @type {Record<string, string>} valores de los campos dinámicos, por clave del schema */\\n  let metadataValues = {};\\n\\n  $: serieOptions = [\\n    { value: \\"\\", label: \\"— sin serie / sin metadatos —\\" },\\n    ...(data.trdSeries ?? []).map((s) => ({ value: s.id, label: `${s.code} — ${s.name}` })),\\n  ];\\n\\n  $: templateProperties =\\n    activeTemplate?.json_schema?.properties && typeof activeTemplate.json_schema.properties === \\"object\\"\\n      ? Object.entries(activeTemplate.json_schema.properties)\\n      : [];\\n  $: templateRequired = activeTemplate?.json_schema?.required ?? [];\\n  // SchemaField ya decide por sí mismo si un constructo es soportado, pero la\\n  // página necesita filtrar ANTES de renderizar para poder contar los que no\\n  // lo son (aviso) — resolveSchemaControl es la misma función que usa\\n  // SchemaField internamente (fuente única de verdad, $lib/utils/schemaField.js).\\n  $: supportedTemplateProperties = templateProperties.filter(\\n    ([, propSchema]) => resolveSchemaControl(propSchema) !== null,\\n  );\\n  $: unsupportedFieldCount = templateProperties.length - supportedTemplateProperties.length;\\n\\n  /**\\n   * Trae la plantilla ACTIVA de metadatos de la serie elegida y prepara\\n   * `metadataValues`. `presetValues`, si se pasa, repuebla los campos tras un\\n   * 422 del backend (mismo espíritu que M-7 para name/description/trd).\\n   * @param {Record<string, string>|null} [presetValues]\\n   */\\n  async function onSerieChange(presetValues = null) {\\n    activeTemplate = null;\\n    metadataValues = {};\\n    if (!createTrdSerieId) return;\\n\\n    loadingTemplate = true;\\n    try {\\n      const params = new URLSearchParams({ trd_serie_id: createTrdSerieId });\\n      const res = await fetch(`/expedientes/api/metadata-templates?${params}`);\\n      /** @type {any[]} */\\n      const templates = await res.json();\\n      if (!res.ok) throw templates;\\n      const found = Array.isArray(templates) ? templates.find((t) => t?.activo) : null;\\n      activeTemplate = found ?? null;\\n      if (activeTemplate?.json_schema?.properties) {\\n        metadataValues = Object.fromEntries(\\n          Object.keys(activeTemplate.json_schema.properties).map((k) => [\\n            k,\\n            presetValues?.[k] ?? \\"\\",\\n          ]),\\n        );\\n      }\\n    } catch {\\n      // Sin plantilla activa (o error de red) es válido; el backend valida\\n      // `metadata` de todas formas al crear.\\n      activeTemplate = null;\\n    } finally {\\n      loadingTemplate = false;\\n    }\\n  }\\n\\n  function openCreateModal() {\\n    createName = \\"\\";\\n    createDescription = \\"\\";\\n    createTrdSerieId = \\"\\";\\n    activeTemplate = null;\\n    metadataValues = {};\\n    showCreateModal = true;\\n  }\\n\\n  // Si la creación falla (fail() del action), el modal debe quedar/volver abierto\\n  // para mostrar el error — SvelteKit no navega en un fail(), solo actualiza `form`.\\n  // También repuebla el formulario con los `values` que devuelve fail() (M-7):\\n  // sin esto, un 422 del backend borra lo que el usuario ya había escrito.\\n  $: if (form?.error) {\\n    showCreateModal = true;\\n    if (form.values) {\\n      createName = form.values.name ?? \\"\\";\\n      createDescription = form.values.description ?? \\"\\";\\n      createTrdSerieId = form.values.trdSerieId ?? \\"\\";\\n      // Si la plantilla ya estaba cargada (caso normal con JS: use:enhance no\\n      // remonta el componente, metadataValues nunca se perdió), solo se\\n      // fusionan los valores crudos por si acaso. Si NO hay plantilla en\\n      // memoria (mount fresco tras un reload sin JS), se refetch — sin el\\n      // schema no hay dónde repoblar los campos dinámicos.\\n      if (createTrdSerieId && !activeTemplate && !loadingTemplate) {\\n        onSerieChange(form.values.meta ?? null);\\n      } else if (form.values.meta) {\\n        metadataValues = { ...metadataValues, ...form.values.meta };\\n      }\\n    }\\n  }\\n\\n  // Mapeo heurístico (best-effort) del 422 metadata_validation_failed a un\\n  // campo concreto: el backend solo da el `message` de jsonschema (p. ej.\\n  // \\"\'monto_contrato\' is a required property\\"), sin ruta estructurada — ver\\n  // contrato §5. Si el mensaje empieza por \'clave\' y esa clave está en el\\n  // schema renderizado, se resalta ESE campo; si no hay match, el error\\n  // queda solo en el aviso general del Modal (role=\\"alert\\").\\n  $: metaFieldError = (() => {\\n    if (form?.error !== \\"metadata_validation_failed\\" || typeof form?.detail !== \\"string\\") return {};\\n    const match = /^\'([^\']+)\'/.exec(form.detail);\\n    const key = match?.[1];\\n    if (!key || !templateProperties.some(([k]) => k === key)) return {};\\n    return { [key]: form.detail };\\n  })();\\n\\n  // El error de validación de `name` (\\"obligatorio\\" / \\"máximo 500 caracteres\\")\\n  // se anexa también al campo (aria-invalid + aria-describedby vía FormField,\\n  // M-7): antes solo vivía en el aviso general del modal (role=\\"alert\\", que\\n  // se mantiene arriba para todos los tipos de error) y un usuario de lector\\n  // de pantalla que tabulaba directo al campo no percibía que estaba\\n  // inválido. Otros errores (upstream_error, connection_error,\\n  // metadata_validation_failed, unauthenticated) no son atribuibles a un\\n  // campo concreto y solo aparecen en el aviso general.\\n  $: createNameError = form?.error === \\"validation_error\\" ? (form.detail ?? \\"\\") : \\"\\";\\n<\/script>\\n\\n<svelte:head>\\n  <title>Expedientes — OrpycaMCP</title>\\n</svelte:head>\\n\\n<div class=\\"expedientes\\">\\n  <!-- -----------------------------------------------------------------------\\n    Encabezado\\n  ----------------------------------------------------------------------- -->\\n  <header class=\\"expedientes__header\\">\\n    <h1 class=\\"expedientes__title\\">Expedientes</h1>\\n    {#if canManage}\\n      <Button variant=\\"primary\\" size=\\"sm\\" icon=\\"fas fa-plus\\" on:click={openCreateModal}>\\n        Crear expediente\\n      </Button>\\n    {/if}\\n  </header>\\n\\n  <!-- -----------------------------------------------------------------------\\n    Filtros\\n  ----------------------------------------------------------------------- -->\\n  <!-- Sin aria-label: el <form role=\\"search\\"> de dentro ya es el landmark\\n    (dos landmarks \\"region\\" anidados casi redundantes eran ruido para lectores\\n    de pantalla). -->\\n  <section class=\\"expedientes__filters\\">\\n    <form\\n      class=\\"filters-form\\"\\n      role=\\"search\\"\\n      aria-label=\\"Buscar expedientes\\"\\n      on:submit|preventDefault={applyFilters}\\n    >\\n      <div class=\\"filters-form__fields\\">\\n        <FormField\\n          label=\\"Buscar\\"\\n          type=\\"text\\"\\n          id=\\"filter-q\\"\\n          bind:value={filterQ}\\n          placeholder=\\"Nombre o código del expediente…\\"\\n          hint=\\"Mínimo 2 caracteres para buscar por texto.\\"\\n        />\\n        <FormField\\n          label=\\"Estado\\"\\n          type=\\"select\\"\\n          id=\\"filter-status\\"\\n          bind:value={filterStatus}\\n          options={[\\n            { value: \\"\\", label: \\"Todos los estados\\" },\\n            { value: \\"open\\", label: \\"Abierto\\" },\\n            { value: \\"closed\\", label: \\"Cerrado\\" },\\n            { value: \\"transferred\\", label: \\"Transferido\\" },\\n          ]}\\n        />\\n        <FormField\\n          label=\\"Fecha desde\\"\\n          type=\\"date\\"\\n          id=\\"filter-date-from\\"\\n          bind:value={filterDateFrom}\\n          error={dateRangeError}\\n        />\\n        <FormField label=\\"Fecha hasta\\" type=\\"date\\" id=\\"filter-date-to\\" bind:value={filterDateTo} />\\n      </div>\\n\\n      <div class=\\"filters-form__actions\\">\\n        <Button type=\\"submit\\" variant=\\"primary\\" size=\\"sm\\" icon=\\"fas fa-filter\\">Filtrar</Button>\\n        {#if hasActiveFilters}\\n          <Button\\n            type=\\"button\\"\\n            variant=\\"ghost\\"\\n            size=\\"sm\\"\\n            icon=\\"fas fa-times\\"\\n            on:click={clearFilters}\\n          >\\n            Limpiar\\n          </Button>\\n        {/if}\\n      </div>\\n    </form>\\n  </section>\\n\\n  <!-- -----------------------------------------------------------------------\\n    Listado\\n  ----------------------------------------------------------------------- -->\\n  {#if data.loadError && expedientes.length === 0 && !loading}\\n    <EmptyState\\n      icon=\\"fas fa-exclamation-circle\\"\\n      title=\\"Error al cargar los expedientes\\"\\n      description={data.loadError}\\n    >\\n      <Button variant=\\"primary\\" on:click={invalidateAll}>Reintentar</Button>\\n    </EmptyState>\\n  {:else if loading || expedientes.length > 0}\\n    <div class=\\"expedientes__table-wrap\\">\\n      <DataTable\\n        columns={TABLE_COLUMNS}\\n        rows={expedientes}\\n        totalCount={total}\\n        page={currentPage}\\n        {pageSize}\\n        {loading}\\n        caption=\\"Listado de expedientes\\"\\n        on:pageChange={onPageChange}\\n      >\\n        <svelte:fragment slot=\\"row\\" let:row>\\n          <td class=\\"op-datatable__td\\">\\n            <code class=\\"expediente-code\\">{row.code}</code>\\n          </td>\\n          <td class=\\"op-datatable__td\\">{row.name}</td>\\n          <td class=\\"op-datatable__td\\">\\n            <StatusChip\\n              status={STATUS_CHIP_MAP[row.status] ?? \\"neutral\\"}\\n              label={STATUS_LABELS[row.status] ?? row.status}\\n              dot={true}\\n            />\\n          </td>\\n          <td class=\\"op-datatable__td\\">\\n            <time datetime={row.opened_at}>{formatDate(row.opened_at)}</time>\\n          </td>\\n          <td class=\\"op-datatable__td\\">\\n            <a\\n              href=\\"/expedientes/{row.id}\\"\\n              class=\\"action-link\\"\\n              aria-label=\\"Abrir expediente {row.name}\\"\\n            >\\n              <i class=\\"fas fa-folder-open\\" aria-hidden=\\"true\\"></i>\\n              Abrir\\n            </a>\\n          </td>\\n        </svelte:fragment>\\n      </DataTable>\\n    </div>\\n  {:else}\\n    <EmptyState\\n      icon=\\"fas fa-folder-open\\"\\n      title={hasActiveFilters ? \\"Sin resultados\\" : \\"Sin expedientes\\"}\\n      description={hasActiveFilters\\n        ? \\"No hay expedientes que coincidan con los filtros aplicados.\\"\\n        : \\"Aún no se han creado expedientes.\\"}\\n    >\\n      {#if hasActiveFilters}\\n        <Button variant=\\"ghost\\" on:click={clearFilters}>Limpiar filtros</Button>\\n      {:else if canManage}\\n        <Button variant=\\"primary\\" on:click={openCreateModal}>Crear expediente</Button>\\n      {/if}\\n    </EmptyState>\\n  {/if}\\n</div>\\n\\n{#if canManage}\\n  <!-- =========================================================================\\n  Modal: crear expediente (form action nativo — actions.create)\\n========================================================================= -->\\n  <Modal\\n    open={showCreateModal}\\n    title=\\"Crear expediente\\"\\n    size=\\"md\\"\\n    on:close={() => (showCreateModal = false)}\\n  >\\n    {#if form?.error}\\n      <p class=\\"modal-error\\" role=\\"alert\\">{form.detail}</p>\\n    {/if}\\n    <form\\n      id=\\"expediente-create-form\\"\\n      method=\\"POST\\"\\n      action=\\"?/create\\"\\n      use:enhance={() => {\\n        creating = true;\\n        return async ({ update }) => {\\n          await update();\\n          creating = false;\\n        };\\n      }}\\n    >\\n      <div class=\\"modal-form\\">\\n        <FormField\\n          id=\\"create-name\\"\\n          name=\\"name\\"\\n          label=\\"Nombre\\"\\n          type=\\"text\\"\\n          required={true}\\n          bind:value={createName}\\n          placeholder=\\"Asunto o nombre del expediente\\"\\n          hint=\\"Obligatorio, máximo 500 caracteres.\\"\\n          error={createNameError}\\n        />\\n        <FormField\\n          id=\\"create-description\\"\\n          name=\\"description\\"\\n          label=\\"Descripción (opcional)\\"\\n          type=\\"textarea\\"\\n          bind:value={createDescription}\\n          rows={3}\\n        />\\n        <FormField\\n          id=\\"create-trd\\"\\n          name=\\"trd_serie_id\\"\\n          label=\\"Serie TRD (opcional)\\"\\n          type=\\"select\\"\\n          bind:value={createTrdSerieId}\\n          options={serieOptions}\\n          hint=\\"Elegir una serie con plantilla de metadatos activa habilita campos adicionales.\\"\\n          on:change={() => onSerieChange()}\\n        />\\n\\n        <!-- Campos dinámicos de la plantilla de metadatos de la serie (T-12 Form Builder) -->\\n        {#if createTrdSerieId}\\n          <div class=\\"modal-form__meta\\">\\n            {#if loadingTemplate}\\n              <p class=\\"modal-form__meta-loading\\">\\n                <i class=\\"fas fa-spinner fa-spin\\" aria-hidden=\\"true\\"></i>\\n                Cargando metadatos de la serie…\\n              </p>\\n            {:else if supportedTemplateProperties.length > 0}\\n              <p class=\\"modal-form__section-label\\">\\n                <i class=\\"fas fa-tags\\" aria-hidden=\\"true\\"></i>\\n                Metadatos de la serie\\n              </p>\\n              {#each supportedTemplateProperties as [key, propSchema] (key)}\\n                <SchemaField\\n                  propKey={key}\\n                  schema={propSchema}\\n                  required={templateRequired.includes(key)}\\n                  bind:value={metadataValues[key]}\\n                  error={metaFieldError[key] ?? \\"\\"}\\n                />\\n              {/each}\\n            {/if}\\n            {#if unsupportedFieldCount > 0}\\n              <p class=\\"modal-form__meta-warning\\" role=\\"status\\">\\n                {unsupportedFieldCount}\\n                {unsupportedFieldCount === 1 ? \\"campo\\" : \\"campos\\"} de la plantilla no se\\n                {unsupportedFieldCount === 1 ? \\"puede editar\\" : \\"pueden editar\\"} en este formulario.\\n              </p>\\n            {/if}\\n          </div>\\n        {/if}\\n\\n        <!--\\n        Nivel de seguridad: NO seleccionable en v1. SessionUser (JWT + /auth/me)\\n        no expone el clearance del usuario en el front, así que no hay forma de\\n        acotar este <select> a los niveles que el usuario podría leer de vuelta\\n        (decisión de orfeo-architect, E22 T-12). Se crea siempre en nivel 1\\n        (PÚBLICA) desde +page.server.js#actions.create. Cuando el clearance esté\\n        disponible en el front, descomentar y acotar `options` a los niveles\\n        <= clearance del usuario:\\n\\n        <FormField\\n          id=\\"create-nivel\\"\\n          name=\\"nivel_seguridad\\"\\n          label=\\"Nivel de seguridad\\"\\n          type=\\"select\\"\\n          bind:value={createNivelSeguridad}\\n          options={NIVEL_OPTIONS}\\n        />\\n      -->\\n      </div>\\n    </form>\\n    <svelte:fragment slot=\\"footer\\">\\n      <Button\\n        type=\\"submit\\"\\n        form=\\"expediente-create-form\\"\\n        variant=\\"primary\\"\\n        size=\\"sm\\"\\n        loading={creating}\\n        disabled={creating || !createName.trim()}\\n      >\\n        <i class=\\"fas fa-save\\" aria-hidden=\\"true\\"></i>\\n        Crear\\n      </Button>\\n      <Button\\n        variant=\\"ghost\\"\\n        size=\\"sm\\"\\n        disabled={creating}\\n        on:click={() => (showCreateModal = false)}\\n      >\\n        Cancelar\\n      </Button>\\n    </svelte:fragment>\\n  </Modal>\\n{/if}\\n\\n<style lang=\\"scss\\">.expedientes {\\n  padding: var(--op-space-6);\\n  max-width: 1200px;\\n  margin: 0 auto;\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}\\n\\n.expedientes__header {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  flex-wrap: wrap;\\n  gap: var(--op-space-3);\\n  padding-bottom: var(--op-space-4);\\n  border-bottom: 1px solid var(--op-border);\\n}\\n\\n.expedientes__title {\\n  font-size: var(--op-font-xl);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n\\n.expedientes__filters {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  padding: var(--op-space-4) var(--op-space-5);\\n}\\n\\n.filters-form {\\n  display: flex;\\n  align-items: flex-end;\\n  gap: var(--op-space-4);\\n  flex-wrap: wrap;\\n}\\n\\n.filters-form__fields {\\n  display: flex;\\n  gap: var(--op-space-3);\\n  flex-wrap: wrap;\\n  flex: 1;\\n}\\n\\n.filters-form__actions {\\n  display: flex;\\n  gap: var(--op-space-2);\\n  align-items: flex-end;\\n  margin-bottom: 2px;\\n}\\n\\n.expedientes__table-wrap {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  overflow: hidden;\\n}\\n\\n.expediente-code {\\n  font-family: monospace;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n  background: var(--op-primary-light);\\n  padding: 2px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  white-space: nowrap;\\n}\\n\\n.action-link {\\n  display: inline-flex;\\n  align-items: center;\\n  gap: var(--op-space-1);\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n  text-decoration: none;\\n  padding: 3px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  border: 1px solid var(--op-primary-light);\\n  background: var(--op-primary-light);\\n  transition: background var(--op-transition), color var(--op-transition);\\n}\\n.action-link:hover {\\n  background: var(--op-primary);\\n  color: var(--op-surface);\\n  border-color: var(--op-primary);\\n}\\n.action-link:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n\\n.modal-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n}\\n\\n.modal-form__meta {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-3);\\n  padding: var(--op-space-3);\\n  border: 1px dashed var(--op-border);\\n  border-radius: var(--op-radius);\\n  background: var(--op-bg);\\n}\\n\\n.modal-form__section-label {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  margin: 0;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text-secondary);\\n  text-transform: uppercase;\\n  letter-spacing: 0.03em;\\n}\\n\\n.modal-form__meta-loading {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  margin: 0;\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n}\\n\\n.modal-form__meta-warning {\\n  margin: 0;\\n  font-size: var(--op-font-xs);\\n  color: var(--op-warning-fg);\\n}\\n\\n.modal-error {\\n  background: var(--op-error-bg);\\n  color: var(--op-error-fg);\\n  border: 1px solid var(--op-error);\\n  border-radius: var(--op-radius);\\n  padding: var(--op-space-3) var(--op-space-4);\\n  font-size: var(--op-font-sm);\\n  margin: 0 0 var(--op-space-4);\\n}\\n\\n@media (max-width: 768px) {\\n  .expedientes {\\n    padding: var(--op-space-4);\\n  }\\n}</style>\\n"],"names":[],"mappings":"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"}'
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
  let expedientes;
  let total;
  let currentPage;
  let loading;
  let canManage;
  let hasActiveFilters;
  let serieOptions;
  let templateProperties;
  let templateRequired;
  let supportedTemplateProperties;
  let unsupportedFieldCount;
  let metaFieldError;
  let createNameError;
  let $permissions, $$unsubscribe_permissions;
  let $navigating, $$unsubscribe_navigating;
  $$unsubscribe_permissions = subscribe(permissions, (value) => $permissions = value);
  $$unsubscribe_navigating = subscribe(navigating, (value) => $navigating = value);
  let { data } = $$props;
  let { form } = $$props;
  let filterQ = "";
  let filterStatus = "";
  let filterDateFrom = "";
  let filterDateTo = "";
  let dateRangeError = "";
  function syncFiltersFromUrl(filters) {
    filterQ = filters?.q ?? "";
    filterStatus = filters?.status ?? "";
    filterDateFrom = filters?.dateFrom ?? "";
    filterDateTo = filters?.dateTo ?? "";
  }
  const STATUS_LABELS = {
    open: "Abierto",
    closed: "Cerrado",
    transferred: "Transferido"
  };
  const STATUS_CHIP_MAP = {
    open: "active",
    closed: "neutral",
    transferred: "info"
  };
  const TABLE_COLUMNS = [
    {
      key: "code",
      label: "Código",
      width: "160px"
    },
    { key: "name", label: "Nombre" },
    {
      key: "status",
      label: "Estado",
      width: "140px"
    },
    {
      key: "opened_at",
      label: "Apertura",
      width: "130px"
    },
    {
      key: "actions",
      label: "Acciones",
      width: "110px"
    }
  ];
  let showCreateModal = false;
  let createName = "";
  let createDescription = "";
  let createTrdSerieId = "";
  let creating = false;
  let activeTemplate = null;
  let loadingTemplate = false;
  let metadataValues = {};
  async function onSerieChange(presetValues = null) {
    activeTemplate = null;
    metadataValues = {};
    if (!createTrdSerieId) return;
    loadingTemplate = true;
    try {
      const params = new URLSearchParams({ trd_serie_id: createTrdSerieId });
      const res = await fetch(`/expedientes/api/metadata-templates?${params}`);
      const templates = await res.json();
      if (!res.ok) throw templates;
      const found = Array.isArray(templates) ? templates.find((t) => t?.activo) : null;
      activeTemplate = found ?? null;
      if (activeTemplate?.json_schema?.properties) {
        metadataValues = Object.fromEntries(Object.keys(activeTemplate.json_schema.properties).map((k) => [k, presetValues?.[k] ?? ""]));
      }
    } catch {
      activeTemplate = null;
    } finally {
      loadingTemplate = false;
    }
  }
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  if ($$props.form === void 0 && $$bindings.form && form !== void 0) $$bindings.form(form);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    {
      syncFiltersFromUrl(data.initialFilters);
    }
    expedientes = data.expedientes ?? [];
    total = data.total ?? 0;
    currentPage = data.initialFilters?.page ?? 1;
    loading = $navigating?.to?.route?.id === "/(app)/expedientes";
    canManage = $permissions.includes("USUA_PERM_EXPEDIENTE");
    hasActiveFilters = !!(filterQ.trim() || filterStatus || filterDateFrom || filterDateTo);
    serieOptions = [
      {
        value: "",
        label: "— sin serie / sin metadatos —"
      },
      ...(data.trdSeries ?? []).map((s) => ({
        value: s.id,
        label: `${s.code} — ${s.name}`
      }))
    ];
    templateProperties = activeTemplate?.json_schema?.properties && typeof activeTemplate.json_schema.properties === "object" ? Object.entries(activeTemplate.json_schema.properties) : [];
    templateRequired = activeTemplate?.json_schema?.required ?? [];
    supportedTemplateProperties = templateProperties.filter(([, propSchema]) => resolveSchemaControl(propSchema) !== null);
    unsupportedFieldCount = templateProperties.length - supportedTemplateProperties.length;
    {
      if (form?.error) {
        showCreateModal = true;
        if (form.values) {
          createName = form.values.name ?? "";
          createDescription = form.values.description ?? "";
          createTrdSerieId = form.values.trdSerieId ?? "";
          if (createTrdSerieId && !activeTemplate && !loadingTemplate) {
            onSerieChange(form.values.meta ?? null);
          } else if (form.values.meta) {
            metadataValues = { ...metadataValues, ...form.values.meta };
          }
        }
      }
    }
    metaFieldError = (() => {
      if (form?.error !== "metadata_validation_failed" || typeof form?.detail !== "string") return {};
      const match = /^'([^']+)'/.exec(form.detail);
      const key = match?.[1];
      if (!key || !templateProperties.some(([k]) => k === key)) return {};
      return { [key]: form.detail };
    })();
    createNameError = form?.error === "validation_error" ? form.detail ?? "" : "";
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-15sfbvp_START -->${$$result.title = `<title>Expedientes — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-15sfbvp_END -->`, ""} <div class="expedientes svelte-cdm6il"> <header class="expedientes__header svelte-cdm6il"><h1 class="expedientes__title svelte-cdm6il" data-svelte-h="svelte-o7d94r">Expedientes</h1> ${canManage ? `${validate_component(Button, "Button").$$render(
      $$result,
      {
        variant: "primary",
        size: "sm",
        icon: "fas fa-plus"
      },
      {},
      {
        default: () => {
          return `Crear expediente`;
        }
      }
    )}` : ``}</header>   <section class="expedientes__filters svelte-cdm6il"><form class="filters-form svelte-cdm6il" role="search" aria-label="Buscar expedientes"><div class="filters-form__fields svelte-cdm6il">${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Buscar",
        type: "text",
        id: "filter-q",
        placeholder: "Nombre o código del expediente…",
        hint: "Mínimo 2 caracteres para buscar por texto.",
        value: filterQ
      },
      {
        value: ($$value) => {
          filterQ = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Estado",
        type: "select",
        id: "filter-status",
        options: [
          { value: "", label: "Todos los estados" },
          { value: "open", label: "Abierto" },
          { value: "closed", label: "Cerrado" },
          {
            value: "transferred",
            label: "Transferido"
          }
        ],
        value: filterStatus
      },
      {
        value: ($$value) => {
          filterStatus = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Fecha desde",
        type: "date",
        id: "filter-date-from",
        error: dateRangeError,
        value: filterDateFrom
      },
      {
        value: ($$value) => {
          filterDateFrom = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Fecha hasta",
        type: "date",
        id: "filter-date-to",
        value: filterDateTo
      },
      {
        value: ($$value) => {
          filterDateTo = $$value;
          $$settled = false;
        }
      },
      {}
    )}</div> <div class="filters-form__actions svelte-cdm6il">${validate_component(Button, "Button").$$render(
      $$result,
      {
        type: "submit",
        variant: "primary",
        size: "sm",
        icon: "fas fa-filter"
      },
      {},
      {
        default: () => {
          return `Filtrar`;
        }
      }
    )} ${hasActiveFilters ? `${validate_component(Button, "Button").$$render(
      $$result,
      {
        type: "button",
        variant: "ghost",
        size: "sm",
        icon: "fas fa-times"
      },
      {},
      {
        default: () => {
          return `Limpiar`;
        }
      }
    )}` : ``}</div></form></section>  ${data.loadError && expedientes.length === 0 && !loading ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-exclamation-circle",
        title: "Error al cargar los expedientes",
        description: data.loadError
      },
      {},
      {
        default: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "primary" }, {}, {
            default: () => {
              return `Reintentar`;
            }
          })}`;
        }
      }
    )}` : `${loading || expedientes.length > 0 ? `<div class="expedientes__table-wrap svelte-cdm6il">${validate_component(DataTable, "DataTable").$$render(
      $$result,
      {
        columns: TABLE_COLUMNS,
        rows: expedientes,
        totalCount: total,
        page: currentPage,
        pageSize,
        loading,
        caption: "Listado de expedientes"
      },
      {},
      {
        row: ({ row }) => {
          return `<td class="op-datatable__td"><code class="expediente-code svelte-cdm6il">${escape(row.code)}</code></td> <td class="op-datatable__td">${escape(row.name)}</td> <td class="op-datatable__td">${validate_component(StatusChip, "StatusChip").$$render(
            $$result,
            {
              status: STATUS_CHIP_MAP[row.status] ?? "neutral",
              label: STATUS_LABELS[row.status] ?? row.status,
              dot: true
            },
            {},
            {}
          )}</td> <td class="op-datatable__td"><time${add_attribute("datetime", row.opened_at, 0)}>${escape(formatDate(row.opened_at))}</time></td> <td class="op-datatable__td"><a href="${"/expedientes/" + escape(row.id, true)}" class="action-link svelte-cdm6il" aria-label="${"Abrir expediente " + escape(row.name, true)}"><i class="fas fa-folder-open" aria-hidden="true"></i>
              Abrir</a></td>`;
        }
      }
    )}</div>` : `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-folder-open",
        title: hasActiveFilters ? "Sin resultados" : "Sin expedientes",
        description: hasActiveFilters ? "No hay expedientes que coincidan con los filtros aplicados." : "Aún no se han creado expedientes."
      },
      {},
      {
        default: () => {
          return `${hasActiveFilters ? `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Limpiar filtros`;
            }
          })}` : `${canManage ? `${validate_component(Button, "Button").$$render($$result, { variant: "primary" }, {}, {
            default: () => {
              return `Crear expediente`;
            }
          })}` : ``}`}`;
        }
      }
    )}`}`}</div> ${canManage ? ` ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: showCreateModal,
        title: "Crear expediente",
        size: "md"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render(
            $$result,
            {
              type: "submit",
              form: "expediente-create-form",
              variant: "primary",
              size: "sm",
              loading: creating,
              disabled: !createName.trim()
            },
            {},
            {
              default: () => {
                return `<i class="fas fa-save" aria-hidden="true"></i>
        Crear`;
              }
            }
          )} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              disabled: creating
            },
            {},
            {
              default: () => {
                return `Cancelar`;
              }
            }
          )} `;
        },
        default: () => {
          return `${form?.error ? `<p class="modal-error svelte-cdm6il" role="alert">${escape(form.detail)}</p>` : ``} <form id="expediente-create-form" method="POST" action="?/create"><div class="modal-form svelte-cdm6il">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "create-name",
              name: "name",
              label: "Nombre",
              type: "text",
              required: true,
              placeholder: "Asunto o nombre del expediente",
              hint: "Obligatorio, máximo 500 caracteres.",
              error: createNameError,
              value: createName
            },
            {
              value: ($$value) => {
                createName = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "create-description",
              name: "description",
              label: "Descripción (opcional)",
              type: "textarea",
              rows: 3,
              value: createDescription
            },
            {
              value: ($$value) => {
                createDescription = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "create-trd",
              name: "trd_serie_id",
              label: "Serie TRD (opcional)",
              type: "select",
              options: serieOptions,
              hint: "Elegir una serie con plantilla de metadatos activa habilita campos adicionales.",
              value: createTrdSerieId
            },
            {
              value: ($$value) => {
                createTrdSerieId = $$value;
                $$settled = false;
              }
            },
            {}
          )}  ${createTrdSerieId ? `<div class="modal-form__meta svelte-cdm6il">${loadingTemplate ? `<p class="modal-form__meta-loading svelte-cdm6il" data-svelte-h="svelte-lf9s7m"><i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
                Cargando metadatos de la serie…</p>` : `${supportedTemplateProperties.length > 0 ? `<p class="modal-form__section-label svelte-cdm6il" data-svelte-h="svelte-1cyjgtj"><i class="fas fa-tags" aria-hidden="true"></i>
                Metadatos de la serie</p> ${each(supportedTemplateProperties, ([key, propSchema]) => {
            return `${validate_component(SchemaField, "SchemaField").$$render(
              $$result,
              {
                propKey: key,
                schema: propSchema,
                required: templateRequired.includes(key),
                error: metaFieldError[key] ?? "",
                value: metadataValues[key]
              },
              {
                value: ($$value) => {
                  metadataValues[key] = $$value;
                  $$settled = false;
                }
              },
              {}
            )}`;
          })}` : ``}`} ${unsupportedFieldCount > 0 ? `<p class="modal-form__meta-warning svelte-cdm6il" role="status">${escape(unsupportedFieldCount)} ${escape(unsupportedFieldCount === 1 ? "campo" : "campos")} de la plantilla no se
                ${escape(unsupportedFieldCount === 1 ? "puede editar" : "pueden editar")} en este formulario.</p>` : ``}</div>` : ``} </div></form>`;
        }
      }
    )}` : ``}`;
  } while (!$$settled);
  $$unsubscribe_permissions();
  $$unsubscribe_navigating();
  return $$rendered;
});
export {
  Page as default
};
