import { c as create_ssr_component, v as validate_component, e as escape } from "../../../../chunks/ssr.js";
import { B as Button } from "../../../../chunks/Button.js";
import { C as Card } from "../../../../chunks/Card.js";
import { D as DataTable } from "../../../../chunks/DataTable.js";
import { F as FormField } from "../../../../chunks/FormField.js";
import { M as Modal } from "../../../../chunks/Modal.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
import { L as Loader } from "../../../../chunks/Loader.js";
import { D as Drawer } from "../../../../chunks/Drawer.js";
import { S as Stepper } from "../../../../chunks/Stepper.js";
const css = {
  code: ".kit-page.svelte-z4pi24.svelte-z4pi24{max-width:960px;margin:0 auto}.kit-page__header.svelte-z4pi24.svelte-z4pi24{margin-bottom:var(--op-space-6)}.kit-page__title.svelte-z4pi24.svelte-z4pi24{font-size:var(--op-font-xl);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0 0 var(--op-space-2)}.kit-page__subtitle.svelte-z4pi24.svelte-z4pi24{margin:0}.kit-section.svelte-z4pi24.svelte-z4pi24{margin-bottom:var(--op-space-7)}.kit-section__title.svelte-z4pi24.svelte-z4pi24{font-size:var(--op-font-lg);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0 0 var(--op-space-4);padding-bottom:var(--op-space-2);border-bottom:2px solid var(--op-primary-light)}.kit-row.svelte-z4pi24.svelte-z4pi24{display:flex;flex-wrap:wrap;align-items:center;gap:var(--op-space-3)}.kit-dl.svelte-z4pi24.svelte-z4pi24{display:grid;grid-template-columns:max-content 1fr;gap:var(--op-space-2) var(--op-space-4);font-size:var(--op-font-sm)}.kit-dl.svelte-z4pi24 dt.svelte-z4pi24{font-weight:var(--op-weight-medium);color:var(--op-text-secondary)}.kit-dl.svelte-z4pi24 dd.svelte-z4pi24{color:var(--op-text);margin:0}.columns.svelte-z4pi24.svelte-z4pi24{display:flex;gap:var(--op-space-4);flex-wrap:wrap}.columns.svelte-z4pi24 .column.svelte-z4pi24{flex:1;min-width:200px}",
  map: `{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /_kit/+page.svelte — Catálogo de componentes del design system Orpyca.\\n\\n  Sirve de verificación visual de T-06: todo componente de ui/ está instanciado\\n  aquí con sus variantes y estados relevantes. No es visible en producción para\\n  usuarios finales (no tiene enlace en SidebarNav), pero la ruta existe y es útil\\n  para revisión con svelte-ux-reviewer.\\n-->\\n<script>\\n  import { onMount } from \\"svelte\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { notify } from \\"$lib/stores/notifications.js\\";\\n\\n  // Componentes UI\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import Card from \\"$lib/components/ui/Card.svelte\\";\\n  import DataTable from \\"$lib/components/ui/DataTable.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Loader from \\"$lib/components/ui/Loader.svelte\\";\\n  import Drawer from \\"$lib/components/ui/Drawer.svelte\\";\\n  import Stepper from \\"$lib/components/ui/Stepper.svelte\\";\\n\\n  onMount(() => {\\n    setBreadcrumbs([\\n      { label: \\"Panel principal\\", href: \\"/dashboard\\" },\\n      { label: \\"Catálogo de componentes\\" },\\n    ]);\\n  });\\n\\n  // --- Modal ---\\n  let modalOpen = false;\\n\\n  // --- Drawer ---\\n  let drawerOpen = false;\\n\\n  // --- Stepper ---\\n  let stepperCurrent = 1;\\n  const stepperSteps = [\\"Tipo\\", \\"Metadatos\\", \\"Anexos\\", \\"Clasificación TRD\\", \\"Flujo\\"];\\n\\n  // --- DataTable ---\\n  const tableColumns = [\\n    { key: \\"radicado\\", label: \\"Radicado\\", width: \\"160px\\" },\\n    { key: \\"tipo\\", label: \\"Tipo\\" },\\n    { key: \\"dependencia\\", label: \\"Dependencia\\" },\\n    { key: \\"estado\\", label: \\"Estado\\", width: \\"120px\\" },\\n    { key: \\"fecha\\", label: \\"Fecha\\", width: \\"120px\\" },\\n    { key: \\"_acciones\\", label: \\"\\", width: \\"80px\\" },\\n  ];\\n  const tableRows = [\\n    {\\n      radicado: \\"2024-ICETEX-E-000001\\",\\n      tipo: \\"Entrada\\",\\n      dependencia: \\"Ventanilla central\\",\\n      estado: \\"active\\",\\n      fecha: \\"2024-01-15\\",\\n    },\\n    {\\n      radicado: \\"2024-ICETEX-S-000042\\",\\n      tipo: \\"Salida\\",\\n      dependencia: \\"Subdirección Técnica\\",\\n      estado: \\"pending\\",\\n      fecha: \\"2024-01-20\\",\\n    },\\n    {\\n      radicado: \\"2024-ICETEX-I-000007\\",\\n      tipo: \\"Interno\\",\\n      dependencia: \\"Gestión Humana\\",\\n      estado: \\"closed\\",\\n      fecha: \\"2024-01-22\\",\\n    },\\n  ];\\n\\n  // --- DataTable (capacidades avanzadas del Sistema de Diseño §12) ---\\n  /** @type {Array<{key: string; label: string; width?: string; sortable?: boolean; filterType?: 'text'|'select'|'dateRange'; filterOptions?: Array<{value:string;label:string}>; editable?: boolean; type?: 'text'|'number'|'date'}>} */\\n  const advancedTableColumns = [\\n    { key: \\"radicado\\", label: \\"Radicado\\", width: \\"170px\\", sortable: true },\\n    { key: \\"asunto\\", label: \\"Asunto\\", sortable: true, editable: true, type: \\"text\\" },\\n    {\\n      key: \\"estado\\",\\n      label: \\"Estado\\",\\n      width: \\"130px\\",\\n      filterType: \\"select\\",\\n      filterOptions: [\\n        { value: \\"active\\", label: \\"Activo\\" },\\n        { value: \\"pending\\", label: \\"Pendiente\\" },\\n        { value: \\"closed\\", label: \\"Cerrado\\" },\\n      ],\\n    },\\n    { key: \\"fecha\\", label: \\"Fecha\\", width: \\"130px\\", sortable: true, filterType: \\"dateRange\\" },\\n  ];\\n  const advancedTableRows = [\\n    {\\n      id: \\"1\\",\\n      radicado: \\"2024-ICETEX-E-000001\\",\\n      asunto: \\"Solicitud de traslado\\",\\n      estado: \\"active\\",\\n      fecha: \\"2024-01-15\\",\\n    },\\n    {\\n      id: \\"2\\",\\n      radicado: \\"2024-ICETEX-S-000042\\",\\n      asunto: \\"Respuesta a derecho de petición\\",\\n      estado: \\"pending\\",\\n      fecha: \\"2024-01-20\\",\\n    },\\n    {\\n      id: \\"3\\",\\n      radicado: \\"2024-ICETEX-I-000007\\",\\n      asunto: \\"Memorando interno\\",\\n      estado: \\"closed\\",\\n      fecha: \\"2024-01-22\\",\\n    },\\n  ];\\n  /** @type {Array<string|number>} */\\n  let advancedTableSelected = [];\\n\\n  // --- FormField ---\\n  let fieldValue = \\"\\";\\n  let selectValue = \\"\\";\\n  let textareaValue = \\"\\";\\n  let _fieldError = \\"\\";\\n\\n  const selectOptions = [\\n    { value: \\"entrada\\", label: \\"Entrada\\" },\\n    { value: \\"salida\\", label: \\"Salida\\" },\\n    { value: \\"interno\\", label: \\"Interno\\" },\\n  ];\\n\\n  function triggerToast(type) {\\n    const messages = {\\n      success: \\"Radicado creado exitosamente.\\",\\n      error: \\"No fue posible completar la operación.\\",\\n      warning: \\"El término de respuesta vence en 2 días.\\",\\n      info: \\"El asistente está disponible en la Fase 3.\\",\\n    };\\n    notify({ type, message: messages[type] });\\n  }\\n<\/script>\\n\\n<svelte:head>\\n  <title>Catálogo de componentes — OrpycaMCP</title>\\n</svelte:head>\\n\\n<div class=\\"kit-page\\">\\n  <header class=\\"kit-page__header\\">\\n    <h1 class=\\"kit-page__title\\">Catálogo de componentes</h1>\\n    <p class=\\"kit-page__subtitle text-secondary text-sm\\">\\n      Design system Orpyca — verificación visual de T-06. Cada componente se muestra con sus\\n      variantes y estados relevantes.\\n    </p>\\n  </header>\\n\\n  <!-- ── 1. Button ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-button\\">\\n    <h2 id=\\"section-button\\" class=\\"kit-section__title\\">1. Button</h2>\\n    <Card title=\\"Variantes y tamaños\\">\\n      <div class=\\"kit-row\\">\\n        <Button variant=\\"primary\\" icon=\\"fas fa-file-import\\">Radicar entrada</Button>\\n        <Button variant=\\"secondary\\" icon=\\"fas fa-search\\">Buscar</Button>\\n        <Button variant=\\"danger\\" icon=\\"fas fa-ban\\">Anular</Button>\\n        <Button variant=\\"ghost\\">Cancelar</Button>\\n      </div>\\n      <div class=\\"kit-row mt-4\\">\\n        <Button variant=\\"primary\\" size=\\"sm\\">Pequeño</Button>\\n        <Button variant=\\"primary\\" size=\\"md\\">Mediano</Button>\\n        <Button variant=\\"primary\\" size=\\"lg\\">Grande</Button>\\n      </div>\\n      <div class=\\"kit-row mt-4\\">\\n        <Button variant=\\"primary\\" loading>Cargando…</Button>\\n        <Button variant=\\"primary\\" disabled>Desactivado</Button>\\n        <Button variant=\\"primary\\" href=\\"/dashboard\\">Enlace-botón</Button>\\n      </div>\\n    </Card>\\n  </section>\\n\\n  <!-- ── 2. Card ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-card\\">\\n    <h2 id=\\"section-card\\" class=\\"kit-section__title\\">2. Card</h2>\\n    <div class=\\"columns\\">\\n      <div class=\\"column\\">\\n        <Card title=\\"Radicado 2024-ICETEX-E-000001\\">\\n          <p class=\\"text-sm\\">Carta de solicitud de crédito educativo — Ventanilla central.</p>\\n          <svelte:fragment slot=\\"footer\\">\\n            <Button variant=\\"secondary\\" size=\\"sm\\">Ver detalle</Button>\\n          </svelte:fragment>\\n        </Card>\\n      </div>\\n      <div class=\\"column\\">\\n        <Card>\\n          <svelte:fragment slot=\\"header\\">\\n            <span class=\\"font-bold text-sm\\">Cabecera personalizada</span>\\n            <span class=\\"ml-auto\\"><StatusChip status=\\"active\\" /></span>\\n          </svelte:fragment>\\n          <p class=\\"text-sm\\">Contenido del card sin padding propio de título.</p>\\n        </Card>\\n      </div>\\n    </div>\\n  </section>\\n\\n  <!-- ── 3. StatusChip ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-status\\">\\n    <h2 id=\\"section-status\\" class=\\"kit-section__title\\">3. StatusChip</h2>\\n    <Card>\\n      <div class=\\"kit-row flex-wrap\\">\\n        <StatusChip status=\\"active\\" label=\\"Activo\\" dot />\\n        <StatusChip status=\\"pending\\" label=\\"Pendiente\\" dot />\\n        <StatusChip status=\\"closed\\" label=\\"Cerrado\\" dot />\\n        <StatusChip status=\\"info\\" label=\\"En revisión\\" dot />\\n        <StatusChip status=\\"neutral\\" label=\\"Anulado\\" dot />\\n        <StatusChip status=\\"open\\" label=\\"Abierto\\" />\\n        <StatusChip status=\\"error\\" label=\\"Error\\" />\\n      </div>\\n    </Card>\\n  </section>\\n\\n  <!-- ── 4. FormField ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-form\\">\\n    <h2 id=\\"section-form\\" class=\\"kit-section__title\\">4. FormField (input / select / textarea)</h2>\\n    <Card>\\n      <div class=\\"columns\\">\\n        <div class=\\"column\\">\\n          <FormField\\n            id=\\"campo-texto\\"\\n            label=\\"Asunto del radicado\\"\\n            type=\\"text\\"\\n            placeholder=\\"Ingrese el asunto…\\"\\n            bind:value={fieldValue}\\n            required\\n            hint=\\"Máximo 200 caracteres.\\"\\n          />\\n        </div>\\n        <div class=\\"column\\">\\n          <FormField\\n            id=\\"campo-select\\"\\n            label=\\"Tipo documental\\"\\n            type=\\"select\\"\\n            placeholder=\\"Seleccione un tipo…\\"\\n            bind:value={selectValue}\\n            options={selectOptions}\\n            required\\n          />\\n        </div>\\n      </div>\\n      <div class=\\"mt-4\\">\\n        <FormField\\n          id=\\"campo-textarea\\"\\n          label=\\"Observaciones\\"\\n          type=\\"textarea\\"\\n          placeholder=\\"Ingrese observaciones adicionales…\\"\\n          bind:value={textareaValue}\\n          rows={4}\\n        />\\n      </div>\\n      <div class=\\"mt-4\\">\\n        <FormField\\n          id=\\"campo-error\\"\\n          label=\\"Campo con error\\"\\n          type=\\"text\\"\\n          value=\\"valor inválido\\"\\n          error=\\"Este campo es obligatorio y no puede contener caracteres especiales.\\"\\n        />\\n      </div>\\n    </Card>\\n  </section>\\n\\n  <!-- ── 5. DataTable ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-table\\">\\n    <h2 id=\\"section-table\\" class=\\"kit-section__title\\">5. DataTable (paginada)</h2>\\n    <Card padding={false}>\\n      <DataTable\\n        columns={tableColumns}\\n        rows={tableRows}\\n        totalCount={3}\\n        page={1}\\n        pageSize={20}\\n        caption=\\"Bandeja de radicados de ejemplo\\"\\n      >\\n        <svelte:fragment slot=\\"row\\" let:row>\\n          <td class=\\"op-datatable__td\\">\\n            <span class=\\"text-mono\\">{row.radicado}</span>\\n          </td>\\n          <td class=\\"op-datatable__td\\">{row.tipo}</td>\\n          <td class=\\"op-datatable__td\\">{row.dependencia}</td>\\n          <td class=\\"op-datatable__td\\">\\n            <StatusChip status={row.estado} />\\n          </td>\\n          <td class=\\"op-datatable__td\\">{row.fecha}</td>\\n          <td class=\\"op-datatable__td\\" style=\\"text-align:right\\">\\n            <Button variant=\\"ghost\\" size=\\"sm\\" icon=\\"fas fa-eye\\">Ver</Button>\\n          </td>\\n        </svelte:fragment>\\n      </DataTable>\\n    </Card>\\n    <div class=\\"mt-4\\">\\n      <Card title=\\"DataTable — estado vacío\\">\\n        <DataTable columns={tableColumns} rows={[]} totalCount={0} caption=\\"Tabla vacía\\" />\\n      </Card>\\n    </div>\\n    <div class=\\"mt-4\\">\\n      <Card title=\\"DataTable — skeleton de carga\\">\\n        <DataTable\\n          columns={tableColumns}\\n          rows={[]}\\n          totalCount={0}\\n          loading\\n          caption=\\"Tabla cargando\\"\\n        />\\n      </Card>\\n    </div>\\n    <div class=\\"mt-4\\">\\n      <Card\\n        padding={false}\\n        title=\\"DataTable — capacidades avanzadas (§12: filtros, orden, exportación, columnas, vistas, selección, edición)\\"\\n      >\\n        <DataTable\\n          tableId=\\"kit-demo-avanzado\\"\\n          columns={advancedTableColumns}\\n          rows={advancedTableRows}\\n          totalCount={advancedTableRows.length}\\n          pageSize={20}\\n          caption=\\"Bandeja de radicados — demo de capacidades avanzadas\\"\\n          enableFilters\\n          enableSort\\n          enableExport\\n          enableColumnToggle\\n          enableSavedViews\\n          enableSelection\\n          bind:selected={advancedTableSelected}\\n        />\\n      </Card>\\n    </div>\\n  </section>\\n\\n  <!-- ── 6. Loader ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-loader\\">\\n    <h2 id=\\"section-loader\\" class=\\"kit-section__title\\">6. Loader / Skeleton</h2>\\n    <Card>\\n      <div class=\\"kit-row items-center\\">\\n        <div>\\n          <p class=\\"text-xs text-secondary mb-2\\">Spinner sm</p>\\n          <Loader size=\\"sm\\" />\\n        </div>\\n        <div>\\n          <p class=\\"text-xs text-secondary mb-2\\">Spinner md</p>\\n          <Loader size=\\"md\\" />\\n        </div>\\n        <div>\\n          <p class=\\"text-xs text-secondary mb-2\\">Spinner lg</p>\\n          <Loader size=\\"lg\\" />\\n        </div>\\n        <div style=\\"flex: 1\\">\\n          <p class=\\"text-xs text-secondary mb-2\\">Skeleton (3 líneas)</p>\\n          <Loader type=\\"skeleton\\" lines={3} />\\n        </div>\\n      </div>\\n      <div class=\\"mt-5\\">\\n        <p class=\\"text-xs text-secondary mb-2\\">Estado de página completa</p>\\n        <Loader type=\\"page\\" label=\\"Cargando expedientes…\\" />\\n      </div>\\n    </Card>\\n  </section>\\n\\n  <!-- ── 7. EmptyState ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-empty\\">\\n    <h2 id=\\"section-empty\\" class=\\"kit-section__title\\">7. EmptyState</h2>\\n    <Card>\\n      <EmptyState\\n        icon=\\"fas fa-inbox\\"\\n        title=\\"Bandeja vacía\\"\\n        description=\\"No hay radicados pendientes en su bandeja. Cuando lleguen documentos asignados aparecerán aquí.\\"\\n        action=\\"Radicar documento\\"\\n        actionHref=\\"/radicar\\"\\n      />\\n    </Card>\\n  </section>\\n\\n  <!-- ── 8. Toast / Notification ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-toast\\">\\n    <h2 id=\\"section-toast\\" class=\\"kit-section__title\\">8. Toast / Notification</h2>\\n    <Card title=\\"Disparar toasts\\">\\n      <div class=\\"kit-row\\">\\n        <Button\\n          variant=\\"secondary\\"\\n          size=\\"sm\\"\\n          icon=\\"fas fa-check-circle\\"\\n          on:click={() => triggerToast(\\"success\\")}>Éxito</Button\\n        >\\n        <Button\\n          variant=\\"danger\\"\\n          size=\\"sm\\"\\n          icon=\\"fas fa-exclamation-circle\\"\\n          on:click={() => triggerToast(\\"error\\")}>Error</Button\\n        >\\n        <Button\\n          variant=\\"secondary\\"\\n          size=\\"sm\\"\\n          icon=\\"fas fa-exclamation-triangle\\"\\n          on:click={() => triggerToast(\\"warning\\")}>Advertencia</Button\\n        >\\n        <Button\\n          variant=\\"ghost\\"\\n          size=\\"sm\\"\\n          icon=\\"fas fa-info-circle\\"\\n          on:click={() => triggerToast(\\"info\\")}>Info</Button\\n        >\\n      </div>\\n      <p class=\\"text-sm text-secondary mt-3\\">\\n        Los toasts aparecen en la esquina inferior derecha y se auto-eliminan.\\n      </p>\\n    </Card>\\n  </section>\\n\\n  <!-- ── 9. Modal ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-modal\\">\\n    <h2 id=\\"section-modal\\" class=\\"kit-section__title\\">9. Modal</h2>\\n    <Card>\\n      <Button variant=\\"primary\\" icon=\\"fas fa-ban\\" on:click={() => (modalOpen = true)}>\\n        Abrir modal de anulación\\n      </Button>\\n    </Card>\\n  </section>\\n\\n  <!-- ── 10. Drawer ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-drawer\\">\\n    <h2 id=\\"section-drawer\\" class=\\"kit-section__title\\">10. Drawer</h2>\\n    <Card>\\n      <Button variant=\\"secondary\\" icon=\\"fas fa-eye\\" on:click={() => (drawerOpen = true)}>\\n        Ver detalle de radicado\\n      </Button>\\n    </Card>\\n  </section>\\n\\n  <!-- ── 11. Stepper ── -->\\n  <section class=\\"kit-section\\" aria-labelledby=\\"section-stepper\\">\\n    <h2 id=\\"section-stepper\\" class=\\"kit-section__title\\">11. Stepper</h2>\\n    <Card title=\\"Flujo de radicación guiada\\">\\n      <Stepper steps={stepperSteps} bind:current={stepperCurrent} />\\n      <div class=\\"kit-row mt-5\\">\\n        <Button\\n          variant=\\"ghost\\"\\n          size=\\"sm\\"\\n          disabled={stepperCurrent === 0}\\n          on:click={() => stepperCurrent--}>Atrás</Button\\n        >\\n        <Button\\n          variant=\\"primary\\"\\n          size=\\"sm\\"\\n          disabled={stepperCurrent === stepperSteps.length - 1}\\n          on:click={() => stepperCurrent++}>Continuar</Button\\n        >\\n      </div>\\n      <p class=\\"text-sm text-secondary mt-3\\">\\n        Paso actual: <strong>{stepperSteps[stepperCurrent]}</strong>\\n        ({stepperCurrent + 1} de {stepperSteps.length})\\n      </p>\\n    </Card>\\n  </section>\\n</div>\\n\\n<!-- Modal de anulación (ejemplo de uso) -->\\n<Modal\\n  open={modalOpen}\\n  title=\\"Solicitar anulación de radicado\\"\\n  size=\\"md\\"\\n  on:close={() => (modalOpen = false)}\\n>\\n  <FormField\\n    id=\\"modal-causal\\"\\n    label=\\"Causal de anulación\\"\\n    type=\\"select\\"\\n    placeholder=\\"Seleccione la causal…\\"\\n    options={[\\n      { value: \\"duplicado\\", label: \\"Radicado duplicado\\" },\\n      { value: \\"error_datos\\", label: \\"Error en los datos ingresados\\" },\\n      { value: \\"retiro\\", label: \\"Retiro voluntario del solicitante\\" },\\n    ]}\\n  />\\n  <div class=\\"mt-4\\">\\n    <FormField\\n      id=\\"modal-observacion\\"\\n      label=\\"Observación\\"\\n      type=\\"textarea\\"\\n      placeholder=\\"Describa el motivo con detalle…\\"\\n      rows={3}\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (modalOpen = false)}>Cancelar</Button>\\n    <Button\\n      variant=\\"danger\\"\\n      icon=\\"fas fa-ban\\"\\n      on:click={() => {\\n        modalOpen = false;\\n        notify({\\n          type: \\"success\\",\\n          message: \\"Solicitud de anulación enviada al jefe de dependencia.\\",\\n        });\\n      }}>Solicitar anulación</Button\\n    >\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- Drawer de detalle (ejemplo de uso) -->\\n<Drawer\\n  open={drawerOpen}\\n  title=\\"Radicado 2024-ICETEX-E-000001\\"\\n  on:close={() => (drawerOpen = false)}\\n>\\n  <dl class=\\"kit-dl\\">\\n    <dt>Tipo</dt>\\n    <dd>Entrada</dd>\\n    <dt>Fecha de radicación</dt>\\n    <dd>2024-01-15</dd>\\n    <dt>Dependencia destino</dt>\\n    <dd>Ventanilla central</dd>\\n    <dt>Asunto</dt>\\n    <dd>Solicitud de crédito educativo para pregrado semestre 2024-1</dd>\\n    <dt>Estado</dt>\\n    <dd><StatusChip status=\\"active\\" label=\\"Activo\\" /></dd>\\n  </dl>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"ghost\\" on:click={() => (drawerOpen = false)}>Cerrar</Button>\\n    <Button variant=\\"primary\\" icon=\\"fas fa-edit\\">Tramitar</Button>\\n  </svelte:fragment>\\n</Drawer>\\n\\n<style lang=\\"scss\\">.kit-page {\\n  max-width: 960px;\\n  margin: 0 auto;\\n}\\n.kit-page__header {\\n  margin-bottom: var(--op-space-6);\\n}\\n.kit-page__title {\\n  font-size: var(--op-font-xl);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0 0 var(--op-space-2);\\n}\\n.kit-page__subtitle {\\n  margin: 0;\\n}\\n\\n.kit-section {\\n  margin-bottom: var(--op-space-7);\\n}\\n.kit-section__title {\\n  font-size: var(--op-font-lg);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0 0 var(--op-space-4);\\n  padding-bottom: var(--op-space-2);\\n  border-bottom: 2px solid var(--op-primary-light);\\n}\\n\\n.kit-row {\\n  display: flex;\\n  flex-wrap: wrap;\\n  align-items: center;\\n  gap: var(--op-space-3);\\n}\\n\\n.kit-dl {\\n  display: grid;\\n  grid-template-columns: max-content 1fr;\\n  gap: var(--op-space-2) var(--op-space-4);\\n  font-size: var(--op-font-sm);\\n}\\n.kit-dl dt {\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text-secondary);\\n}\\n.kit-dl dd {\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n\\n.columns {\\n  display: flex;\\n  gap: var(--op-space-4);\\n  flex-wrap: wrap;\\n}\\n.columns .column {\\n  flex: 1;\\n  min-width: 200px;\\n}</style>\\n"],"names":[],"mappings":"AA4hBmB,qCAAU,CAC3B,SAAS,CAAE,KAAK,CAChB,MAAM,CAAE,CAAC,CAAC,IACZ,CACA,6CAAkB,CAChB,aAAa,CAAE,IAAI,YAAY,CACjC,CACA,4CAAiB,CACf,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,MAAM,CAAE,CAAC,CAAC,CAAC,CAAC,IAAI,YAAY,CAC9B,CACA,+CAAoB,CAClB,MAAM,CAAE,CACV,CAEA,wCAAa,CACX,aAAa,CAAE,IAAI,YAAY,CACjC,CACA,+CAAoB,CAClB,SAAS,CAAE,IAAI,YAAY,CAAC,CAC5B,WAAW,CAAE,IAAI,gBAAgB,CAAC,CAClC,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,MAAM,CAAE,CAAC,CAAC,CAAC,CAAC,IAAI,YAAY,CAAC,CAC7B,cAAc,CAAE,IAAI,YAAY,CAAC,CACjC,aAAa,CAAE,GAAG,CAAC,KAAK,CAAC,IAAI,kBAAkB,CACjD,CAEA,oCAAS,CACP,OAAO,CAAE,IAAI,CACb,SAAS,CAAE,IAAI,CACf,WAAW,CAAE,MAAM,CACnB,GAAG,CAAE,IAAI,YAAY,CACvB,CAEA,mCAAQ,CACN,OAAO,CAAE,IAAI,CACb,qBAAqB,CAAE,WAAW,CAAC,GAAG,CACtC,GAAG,CAAE,IAAI,YAAY,CAAC,CAAC,IAAI,YAAY,CAAC,CACxC,SAAS,CAAE,IAAI,YAAY,CAC7B,CACA,qBAAO,CAAC,gBAAG,CACT,WAAW,CAAE,IAAI,kBAAkB,CAAC,CACpC,KAAK,CAAE,IAAI,mBAAmB,CAChC,CACA,qBAAO,CAAC,gBAAG,CACT,KAAK,CAAE,IAAI,SAAS,CAAC,CACrB,MAAM,CAAE,CACV,CAEA,oCAAS,CACP,OAAO,CAAE,IAAI,CACb,GAAG,CAAE,IAAI,YAAY,CAAC,CACtB,SAAS,CAAE,IACb,CACA,sBAAQ,CAAC,qBAAQ,CACf,IAAI,CAAE,CAAC,CACP,SAAS,CAAE,KACb"}`
};
const Page = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let modalOpen = false;
  let drawerOpen = false;
  let stepperCurrent = 1;
  const stepperSteps = ["Tipo", "Metadatos", "Anexos", "Clasificación TRD", "Flujo"];
  const tableColumns = [
    {
      key: "radicado",
      label: "Radicado",
      width: "160px"
    },
    { key: "tipo", label: "Tipo" },
    { key: "dependencia", label: "Dependencia" },
    {
      key: "estado",
      label: "Estado",
      width: "120px"
    },
    {
      key: "fecha",
      label: "Fecha",
      width: "120px"
    },
    {
      key: "_acciones",
      label: "",
      width: "80px"
    }
  ];
  const tableRows = [
    {
      radicado: "2024-ICETEX-E-000001",
      tipo: "Entrada",
      dependencia: "Ventanilla central",
      estado: "active",
      fecha: "2024-01-15"
    },
    {
      radicado: "2024-ICETEX-S-000042",
      tipo: "Salida",
      dependencia: "Subdirección Técnica",
      estado: "pending",
      fecha: "2024-01-20"
    },
    {
      radicado: "2024-ICETEX-I-000007",
      tipo: "Interno",
      dependencia: "Gestión Humana",
      estado: "closed",
      fecha: "2024-01-22"
    }
  ];
  const advancedTableColumns = [
    {
      key: "radicado",
      label: "Radicado",
      width: "170px",
      sortable: true
    },
    {
      key: "asunto",
      label: "Asunto",
      sortable: true,
      editable: true,
      type: "text"
    },
    {
      key: "estado",
      label: "Estado",
      width: "130px",
      filterType: "select",
      filterOptions: [
        { value: "active", label: "Activo" },
        { value: "pending", label: "Pendiente" },
        { value: "closed", label: "Cerrado" }
      ]
    },
    {
      key: "fecha",
      label: "Fecha",
      width: "130px",
      sortable: true,
      filterType: "dateRange"
    }
  ];
  const advancedTableRows = [
    {
      id: "1",
      radicado: "2024-ICETEX-E-000001",
      asunto: "Solicitud de traslado",
      estado: "active",
      fecha: "2024-01-15"
    },
    {
      id: "2",
      radicado: "2024-ICETEX-S-000042",
      asunto: "Respuesta a derecho de petición",
      estado: "pending",
      fecha: "2024-01-20"
    },
    {
      id: "3",
      radicado: "2024-ICETEX-I-000007",
      asunto: "Memorando interno",
      estado: "closed",
      fecha: "2024-01-22"
    }
  ];
  let advancedTableSelected = [];
  let fieldValue = "";
  let selectValue = "";
  let textareaValue = "";
  const selectOptions = [
    { value: "entrada", label: "Entrada" },
    { value: "salida", label: "Salida" },
    { value: "interno", label: "Interno" }
  ];
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-4wxjv7_START -->${$$result.title = `<title>Catálogo de componentes — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-4wxjv7_END -->`, ""} <div class="kit-page svelte-z4pi24"><header class="kit-page__header svelte-z4pi24" data-svelte-h="svelte-17sycb4"><h1 class="kit-page__title svelte-z4pi24">Catálogo de componentes</h1> <p class="kit-page__subtitle text-secondary text-sm svelte-z4pi24">Design system Orpyca — verificación visual de T-06. Cada componente se muestra con sus
      variantes y estados relevantes.</p></header>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-button"><h2 id="section-button" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-5xays">1. Button</h2> ${validate_component(Card, "Card").$$render($$result, { title: "Variantes y tamaños" }, {}, {
      default: () => {
        return `<div class="kit-row svelte-z4pi24">${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "primary",
            icon: "fas fa-file-import"
          },
          {},
          {
            default: () => {
              return `Radicar entrada`;
            }
          }
        )} ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "secondary",
            icon: "fas fa-search"
          },
          {},
          {
            default: () => {
              return `Buscar`;
            }
          }
        )} ${validate_component(Button, "Button").$$render($$result, { variant: "danger", icon: "fas fa-ban" }, {}, {
          default: () => {
            return `Anular`;
          }
        })} ${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
          default: () => {
            return `Cancelar`;
          }
        })}</div> <div class="kit-row mt-4 svelte-z4pi24">${validate_component(Button, "Button").$$render($$result, { variant: "primary", size: "sm" }, {}, {
          default: () => {
            return `Pequeño`;
          }
        })} ${validate_component(Button, "Button").$$render($$result, { variant: "primary", size: "md" }, {}, {
          default: () => {
            return `Mediano`;
          }
        })} ${validate_component(Button, "Button").$$render($$result, { variant: "primary", size: "lg" }, {}, {
          default: () => {
            return `Grande`;
          }
        })}</div> <div class="kit-row mt-4 svelte-z4pi24">${validate_component(Button, "Button").$$render($$result, { variant: "primary", loading: true }, {}, {
          default: () => {
            return `Cargando…`;
          }
        })} ${validate_component(Button, "Button").$$render($$result, { variant: "primary", disabled: true }, {}, {
          default: () => {
            return `Desactivado`;
          }
        })} ${validate_component(Button, "Button").$$render($$result, { variant: "primary", href: "/dashboard" }, {}, {
          default: () => {
            return `Enlace-botón`;
          }
        })}</div>`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-card"><h2 id="section-card" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-1fm1pst">2. Card</h2> <div class="columns svelte-z4pi24"><div class="column svelte-z4pi24">${validate_component(Card, "Card").$$render($$result, { title: "Radicado 2024-ICETEX-E-000001" }, {}, {
      footer: () => {
        return `${validate_component(Button, "Button").$$render($$result, { variant: "secondary", size: "sm" }, {}, {
          default: () => {
            return `Ver detalle`;
          }
        })} `;
      },
      default: () => {
        return `<p class="text-sm" data-svelte-h="svelte-3k4rwr">Carta de solicitud de crédito educativo — Ventanilla central.</p>`;
      }
    })}</div> <div class="column svelte-z4pi24">${validate_component(Card, "Card").$$render($$result, {}, {}, {
      header: () => {
        return `<span class="font-bold text-sm" data-svelte-h="svelte-8boegp">Cabecera personalizada</span> <span class="ml-auto">${validate_component(StatusChip, "StatusChip").$$render($$result, { status: "active" }, {}, {})}</span> `;
      },
      default: () => {
        return `<p class="text-sm" data-svelte-h="svelte-f4nua5">Contenido del card sin padding propio de título.</p>`;
      }
    })}</div></div></section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-status"><h2 id="section-status" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-5ab9k4">3. StatusChip</h2> ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `<div class="kit-row flex-wrap svelte-z4pi24">${validate_component(StatusChip, "StatusChip").$$render(
          $$result,
          {
            status: "active",
            label: "Activo",
            dot: true
          },
          {},
          {}
        )} ${validate_component(StatusChip, "StatusChip").$$render(
          $$result,
          {
            status: "pending",
            label: "Pendiente",
            dot: true
          },
          {},
          {}
        )} ${validate_component(StatusChip, "StatusChip").$$render(
          $$result,
          {
            status: "closed",
            label: "Cerrado",
            dot: true
          },
          {},
          {}
        )} ${validate_component(StatusChip, "StatusChip").$$render(
          $$result,
          {
            status: "info",
            label: "En revisión",
            dot: true
          },
          {},
          {}
        )} ${validate_component(StatusChip, "StatusChip").$$render(
          $$result,
          {
            status: "neutral",
            label: "Anulado",
            dot: true
          },
          {},
          {}
        )} ${validate_component(StatusChip, "StatusChip").$$render($$result, { status: "open", label: "Abierto" }, {}, {})} ${validate_component(StatusChip, "StatusChip").$$render($$result, { status: "error", label: "Error" }, {}, {})}</div>`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-form"><h2 id="section-form" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-1n0uoou">4. FormField (input / select / textarea)</h2> ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `<div class="columns svelte-z4pi24"><div class="column svelte-z4pi24">${validate_component(FormField, "FormField").$$render(
          $$result,
          {
            id: "campo-texto",
            label: "Asunto del radicado",
            type: "text",
            placeholder: "Ingrese el asunto…",
            required: true,
            hint: "Máximo 200 caracteres.",
            value: fieldValue
          },
          {
            value: ($$value) => {
              fieldValue = $$value;
              $$settled = false;
            }
          },
          {}
        )}</div> <div class="column svelte-z4pi24">${validate_component(FormField, "FormField").$$render(
          $$result,
          {
            id: "campo-select",
            label: "Tipo documental",
            type: "select",
            placeholder: "Seleccione un tipo…",
            options: selectOptions,
            required: true,
            value: selectValue
          },
          {
            value: ($$value) => {
              selectValue = $$value;
              $$settled = false;
            }
          },
          {}
        )}</div></div> <div class="mt-4">${validate_component(FormField, "FormField").$$render(
          $$result,
          {
            id: "campo-textarea",
            label: "Observaciones",
            type: "textarea",
            placeholder: "Ingrese observaciones adicionales…",
            rows: 4,
            value: textareaValue
          },
          {
            value: ($$value) => {
              textareaValue = $$value;
              $$settled = false;
            }
          },
          {}
        )}</div> <div class="mt-4">${validate_component(FormField, "FormField").$$render(
          $$result,
          {
            id: "campo-error",
            label: "Campo con error",
            type: "text",
            value: "valor inválido",
            error: "Este campo es obligatorio y no puede contener caracteres especiales."
          },
          {},
          {}
        )}</div>`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-table"><h2 id="section-table" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-ut7z94">5. DataTable (paginada)</h2> ${validate_component(Card, "Card").$$render($$result, { padding: false }, {}, {
      default: () => {
        return `${validate_component(DataTable, "DataTable").$$render(
          $$result,
          {
            columns: tableColumns,
            rows: tableRows,
            totalCount: 3,
            page: 1,
            pageSize: 20,
            caption: "Bandeja de radicados de ejemplo"
          },
          {},
          {
            row: ({ row }) => {
              return `<td class="op-datatable__td"><span class="text-mono">${escape(row.radicado)}</span></td> <td class="op-datatable__td">${escape(row.tipo)}</td> <td class="op-datatable__td">${escape(row.dependencia)}</td> <td class="op-datatable__td">${validate_component(StatusChip, "StatusChip").$$render($$result, { status: row.estado }, {}, {})}</td> <td class="op-datatable__td">${escape(row.fecha)}</td> <td class="op-datatable__td" style="text-align:right">${validate_component(Button, "Button").$$render(
                $$result,
                {
                  variant: "ghost",
                  size: "sm",
                  icon: "fas fa-eye"
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
        )}`;
      }
    })} <div class="mt-4">${validate_component(Card, "Card").$$render($$result, { title: "DataTable — estado vacío" }, {}, {
      default: () => {
        return `${validate_component(DataTable, "DataTable").$$render(
          $$result,
          {
            columns: tableColumns,
            rows: [],
            totalCount: 0,
            caption: "Tabla vacía"
          },
          {},
          {}
        )}`;
      }
    })}</div> <div class="mt-4">${validate_component(Card, "Card").$$render($$result, { title: "DataTable — skeleton de carga" }, {}, {
      default: () => {
        return `${validate_component(DataTable, "DataTable").$$render(
          $$result,
          {
            columns: tableColumns,
            rows: [],
            totalCount: 0,
            loading: true,
            caption: "Tabla cargando"
          },
          {},
          {}
        )}`;
      }
    })}</div> <div class="mt-4">${validate_component(Card, "Card").$$render(
      $$result,
      {
        padding: false,
        title: "DataTable — capacidades avanzadas (§12: filtros, orden, exportación, columnas, vistas, selección, edición)"
      },
      {},
      {
        default: () => {
          return `${validate_component(DataTable, "DataTable").$$render(
            $$result,
            {
              tableId: "kit-demo-avanzado",
              columns: advancedTableColumns,
              rows: advancedTableRows,
              totalCount: advancedTableRows.length,
              pageSize: 20,
              caption: "Bandeja de radicados — demo de capacidades avanzadas",
              enableFilters: true,
              enableSort: true,
              enableExport: true,
              enableColumnToggle: true,
              enableSavedViews: true,
              enableSelection: true,
              selected: advancedTableSelected
            },
            {
              selected: ($$value) => {
                advancedTableSelected = $$value;
                $$settled = false;
              }
            },
            {}
          )}`;
        }
      }
    )}</div></section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-loader"><h2 id="section-loader" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-uoeneh">6. Loader / Skeleton</h2> ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `<div class="kit-row items-center svelte-z4pi24"><div><p class="text-xs text-secondary mb-2" data-svelte-h="svelte-1acq5l9">Spinner sm</p> ${validate_component(Loader, "Loader").$$render($$result, { size: "sm" }, {}, {})}</div> <div><p class="text-xs text-secondary mb-2" data-svelte-h="svelte-gm72l6">Spinner md</p> ${validate_component(Loader, "Loader").$$render($$result, { size: "md" }, {}, {})}</div> <div><p class="text-xs text-secondary mb-2" data-svelte-h="svelte-1ngag56">Spinner lg</p> ${validate_component(Loader, "Loader").$$render($$result, { size: "lg" }, {}, {})}</div> <div style="flex: 1"><p class="text-xs text-secondary mb-2" data-svelte-h="svelte-fm278l">Skeleton (3 líneas)</p> ${validate_component(Loader, "Loader").$$render($$result, { type: "skeleton", lines: 3 }, {}, {})}</div></div> <div class="mt-5"><p class="text-xs text-secondary mb-2" data-svelte-h="svelte-7uddh4">Estado de página completa</p> ${validate_component(Loader, "Loader").$$render(
          $$result,
          {
            type: "page",
            label: "Cargando expedientes…"
          },
          {},
          {}
        )}</div>`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-empty"><h2 id="section-empty" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-18j97i3">7. EmptyState</h2> ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `${validate_component(EmptyState, "EmptyState").$$render(
          $$result,
          {
            icon: "fas fa-inbox",
            title: "Bandeja vacía",
            description: "No hay radicados pendientes en su bandeja. Cuando lleguen documentos asignados aparecerán aquí.",
            action: "Radicar documento",
            actionHref: "/radicar"
          },
          {},
          {}
        )}`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-toast"><h2 id="section-toast" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-1r2gwjn">8. Toast / Notification</h2> ${validate_component(Card, "Card").$$render($$result, { title: "Disparar toasts" }, {}, {
      default: () => {
        return `<div class="kit-row svelte-z4pi24">${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "secondary",
            size: "sm",
            icon: "fas fa-check-circle"
          },
          {},
          {
            default: () => {
              return `Éxito`;
            }
          }
        )} ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "danger",
            size: "sm",
            icon: "fas fa-exclamation-circle"
          },
          {},
          {
            default: () => {
              return `Error`;
            }
          }
        )} ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "secondary",
            size: "sm",
            icon: "fas fa-exclamation-triangle"
          },
          {},
          {
            default: () => {
              return `Advertencia`;
            }
          }
        )} ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "ghost",
            size: "sm",
            icon: "fas fa-info-circle"
          },
          {},
          {
            default: () => {
              return `Info`;
            }
          }
        )}</div> <p class="text-sm text-secondary mt-3" data-svelte-h="svelte-14nlb8z">Los toasts aparecen en la esquina inferior derecha y se auto-eliminan.</p>`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-modal"><h2 id="section-modal" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-17zx8tu">9. Modal</h2> ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `${validate_component(Button, "Button").$$render($$result, { variant: "primary", icon: "fas fa-ban" }, {}, {
          default: () => {
            return `Abrir modal de anulación`;
          }
        })}`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-drawer"><h2 id="section-drawer" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-22clh2">10. Drawer</h2> ${validate_component(Card, "Card").$$render($$result, {}, {}, {
      default: () => {
        return `${validate_component(Button, "Button").$$render($$result, { variant: "secondary", icon: "fas fa-eye" }, {}, {
          default: () => {
            return `Ver detalle de radicado`;
          }
        })}`;
      }
    })}</section>  <section class="kit-section svelte-z4pi24" aria-labelledby="section-stepper"><h2 id="section-stepper" class="kit-section__title svelte-z4pi24" data-svelte-h="svelte-1hhnaeb">11. Stepper</h2> ${validate_component(Card, "Card").$$render($$result, { title: "Flujo de radicación guiada" }, {}, {
      default: () => {
        return `${validate_component(Stepper, "Stepper").$$render(
          $$result,
          {
            steps: stepperSteps,
            current: stepperCurrent
          },
          {
            current: ($$value) => {
              stepperCurrent = $$value;
              $$settled = false;
            }
          },
          {}
        )} <div class="kit-row mt-5 svelte-z4pi24">${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "ghost",
            size: "sm",
            disabled: stepperCurrent === 0
          },
          {},
          {
            default: () => {
              return `Atrás`;
            }
          }
        )} ${validate_component(Button, "Button").$$render(
          $$result,
          {
            variant: "primary",
            size: "sm",
            disabled: stepperCurrent === stepperSteps.length - 1
          },
          {},
          {
            default: () => {
              return `Continuar`;
            }
          }
        )}</div> <p class="text-sm text-secondary mt-3">Paso actual: <strong>${escape(stepperSteps[stepperCurrent])}</strong>
        (${escape(stepperCurrent + 1)} de ${escape(stepperSteps.length)})</p>`;
      }
    })}</section></div>  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalOpen,
        title: "Solicitar anulación de radicado",
        size: "md"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Cancelar`;
            }
          })} ${validate_component(Button, "Button").$$render($$result, { variant: "danger", icon: "fas fa-ban" }, {}, {
            default: () => {
              return `Solicitar anulación`;
            }
          })} `;
        },
        default: () => {
          return `${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "modal-causal",
              label: "Causal de anulación",
              type: "select",
              placeholder: "Seleccione la causal…",
              options: [
                {
                  value: "duplicado",
                  label: "Radicado duplicado"
                },
                {
                  value: "error_datos",
                  label: "Error en los datos ingresados"
                },
                {
                  value: "retiro",
                  label: "Retiro voluntario del solicitante"
                }
              ]
            },
            {},
            {}
          )} <div class="mt-4">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "modal-observacion",
              label: "Observación",
              type: "textarea",
              placeholder: "Describa el motivo con detalle…",
              rows: 3
            },
            {},
            {}
          )}</div>`;
        }
      }
    )}  ${validate_component(Drawer, "Drawer").$$render(
      $$result,
      {
        open: drawerOpen,
        title: "Radicado 2024-ICETEX-E-000001"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Cerrar`;
            }
          })} ${validate_component(Button, "Button").$$render($$result, { variant: "primary", icon: "fas fa-edit" }, {}, {
            default: () => {
              return `Tramitar`;
            }
          })} `;
        },
        default: () => {
          return `<dl class="kit-dl svelte-z4pi24"><dt class="svelte-z4pi24" data-svelte-h="svelte-1rgr6mo">Tipo</dt><dd class="svelte-z4pi24" data-svelte-h="svelte-1sagiuz">Entrada</dd><dt class="svelte-z4pi24" data-svelte-h="svelte-1oie973">Fecha de radicación</dt><dd class="svelte-z4pi24" data-svelte-h="svelte-1yhrwgf">2024-01-15</dd><dt class="svelte-z4pi24" data-svelte-h="svelte-3s9j62">Dependencia destino</dt><dd class="svelte-z4pi24" data-svelte-h="svelte-md74hd">Ventanilla central</dd><dt class="svelte-z4pi24" data-svelte-h="svelte-o7fjvk">Asunto</dt><dd class="svelte-z4pi24" data-svelte-h="svelte-1rl174b">Solicitud de crédito educativo para pregrado semestre 2024-1</dd><dt class="svelte-z4pi24" data-svelte-h="svelte-tvljmw">Estado</dt><dd class="svelte-z4pi24">${validate_component(StatusChip, "StatusChip").$$render($$result, { status: "active", label: "Activo" }, {}, {})}</dd></dl>`;
        }
      }
    )}`;
  } while (!$$settled);
  return $$rendered;
});
export {
  Page as default
};
