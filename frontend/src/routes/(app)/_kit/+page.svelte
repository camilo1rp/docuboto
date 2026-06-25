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
  /_kit/+page.svelte — Catálogo de componentes del design system Orpyca.

  Sirve de verificación visual de T-06: todo componente de ui/ está instanciado
  aquí con sus variantes y estados relevantes. No es visible en producción para
  usuarios finales (no tiene enlace en SidebarNav), pero la ruta existe y es útil
  para revisión con svelte-ux-reviewer.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notify } from "$lib/stores/notifications.js";

  // Componentes UI
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import DataTable from "$lib/components/ui/DataTable.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Stepper from "$lib/components/ui/Stepper.svelte";

  onMount(() => {
    setBreadcrumbs([
      { label: "Panel principal", href: "/dashboard" },
      { label: "Catálogo de componentes" },
    ]);
  });

  // --- Modal ---
  let modalOpen = false;

  // --- Drawer ---
  let drawerOpen = false;

  // --- Stepper ---
  let stepperCurrent = 1;
  const stepperSteps = ["Tipo", "Metadatos", "Anexos", "Clasificación TRD", "Flujo"];

  // --- DataTable ---
  const tableColumns = [
    { key: "radicado", label: "Radicado", width: "160px" },
    { key: "tipo", label: "Tipo" },
    { key: "dependencia", label: "Dependencia" },
    { key: "estado", label: "Estado", width: "120px" },
    { key: "fecha", label: "Fecha", width: "120px" },
    { key: "_acciones", label: "", width: "80px" },
  ];
  const tableRows = [
    {
      radicado: "2024-ICETEX-E-000001",
      tipo: "Entrada",
      dependencia: "Ventanilla central",
      estado: "active",
      fecha: "2024-01-15",
    },
    {
      radicado: "2024-ICETEX-S-000042",
      tipo: "Salida",
      dependencia: "Subdirección Técnica",
      estado: "pending",
      fecha: "2024-01-20",
    },
    {
      radicado: "2024-ICETEX-I-000007",
      tipo: "Interno",
      dependencia: "Gestión Humana",
      estado: "closed",
      fecha: "2024-01-22",
    },
  ];

  // --- FormField ---
  let fieldValue = "";
  let selectValue = "";
  let textareaValue = "";
  let _fieldError = "";

  const selectOptions = [
    { value: "entrada", label: "Entrada" },
    { value: "salida", label: "Salida" },
    { value: "interno", label: "Interno" },
  ];

  function triggerToast(type) {
    const messages = {
      success: "Radicado creado exitosamente.",
      error: "No fue posible completar la operación.",
      warning: "El término de respuesta vence en 2 días.",
      info: "El asistente está disponible en la Fase 3.",
    };
    notify({ type, message: messages[type] });
  }
</script>

<svelte:head>
  <title>Catálogo de componentes — OrpycaMCP</title>
</svelte:head>

<div class="kit-page">
  <header class="kit-page__header">
    <h1 class="kit-page__title">Catálogo de componentes</h1>
    <p class="kit-page__subtitle text-secondary text-sm">
      Design system Orpyca — verificación visual de T-06. Cada componente se muestra con sus
      variantes y estados relevantes.
    </p>
  </header>

  <!-- ── 1. Button ── -->
  <section class="kit-section" aria-labelledby="section-button">
    <h2 id="section-button" class="kit-section__title">1. Button</h2>
    <Card title="Variantes y tamaños">
      <div class="kit-row">
        <Button variant="primary" icon="fas fa-file-import">Radicar entrada</Button>
        <Button variant="secondary" icon="fas fa-search">Buscar</Button>
        <Button variant="danger" icon="fas fa-ban">Anular</Button>
        <Button variant="ghost">Cancelar</Button>
      </div>
      <div class="kit-row mt-4">
        <Button variant="primary" size="sm">Pequeño</Button>
        <Button variant="primary" size="md">Mediano</Button>
        <Button variant="primary" size="lg">Grande</Button>
      </div>
      <div class="kit-row mt-4">
        <Button variant="primary" loading>Cargando…</Button>
        <Button variant="primary" disabled>Desactivado</Button>
        <Button variant="primary" href="/dashboard">Enlace-botón</Button>
      </div>
    </Card>
  </section>

  <!-- ── 2. Card ── -->
  <section class="kit-section" aria-labelledby="section-card">
    <h2 id="section-card" class="kit-section__title">2. Card</h2>
    <div class="columns">
      <div class="column">
        <Card title="Radicado 2024-ICETEX-E-000001">
          <p class="text-sm">Carta de solicitud de crédito educativo — Ventanilla central.</p>
          <svelte:fragment slot="footer">
            <Button variant="secondary" size="sm">Ver detalle</Button>
          </svelte:fragment>
        </Card>
      </div>
      <div class="column">
        <Card>
          <svelte:fragment slot="header">
            <span class="font-bold text-sm">Cabecera personalizada</span>
            <span class="ml-auto"><StatusChip status="active" /></span>
          </svelte:fragment>
          <p class="text-sm">Contenido del card sin padding propio de título.</p>
        </Card>
      </div>
    </div>
  </section>

  <!-- ── 3. StatusChip ── -->
  <section class="kit-section" aria-labelledby="section-status">
    <h2 id="section-status" class="kit-section__title">3. StatusChip</h2>
    <Card>
      <div class="kit-row flex-wrap">
        <StatusChip status="active" label="Activo" dot />
        <StatusChip status="pending" label="Pendiente" dot />
        <StatusChip status="closed" label="Cerrado" dot />
        <StatusChip status="info" label="En revisión" dot />
        <StatusChip status="neutral" label="Anulado" dot />
        <StatusChip status="open" label="Abierto" />
        <StatusChip status="error" label="Error" />
      </div>
    </Card>
  </section>

  <!-- ── 4. FormField ── -->
  <section class="kit-section" aria-labelledby="section-form">
    <h2 id="section-form" class="kit-section__title">4. FormField (input / select / textarea)</h2>
    <Card>
      <div class="columns">
        <div class="column">
          <FormField
            id="campo-texto"
            label="Asunto del radicado"
            type="text"
            placeholder="Ingrese el asunto…"
            bind:value={fieldValue}
            required
            hint="Máximo 200 caracteres."
          />
        </div>
        <div class="column">
          <FormField
            id="campo-select"
            label="Tipo documental"
            type="select"
            placeholder="Seleccione un tipo…"
            bind:value={selectValue}
            options={selectOptions}
            required
          />
        </div>
      </div>
      <div class="mt-4">
        <FormField
          id="campo-textarea"
          label="Observaciones"
          type="textarea"
          placeholder="Ingrese observaciones adicionales…"
          bind:value={textareaValue}
          rows={4}
        />
      </div>
      <div class="mt-4">
        <FormField
          id="campo-error"
          label="Campo con error"
          type="text"
          value="valor inválido"
          error="Este campo es obligatorio y no puede contener caracteres especiales."
        />
      </div>
    </Card>
  </section>

  <!-- ── 5. DataTable ── -->
  <section class="kit-section" aria-labelledby="section-table">
    <h2 id="section-table" class="kit-section__title">5. DataTable (paginada)</h2>
    <Card padding={false}>
      <DataTable
        columns={tableColumns}
        rows={tableRows}
        totalCount={3}
        page={1}
        pageSize={20}
        caption="Bandeja de radicados de ejemplo"
      >
        <svelte:fragment slot="row" let:row>
          <td class="op-datatable__td">
            <span class="text-mono">{row.radicado}</span>
          </td>
          <td class="op-datatable__td">{row.tipo}</td>
          <td class="op-datatable__td">{row.dependencia}</td>
          <td class="op-datatable__td">
            <StatusChip status={row.estado} />
          </td>
          <td class="op-datatable__td">{row.fecha}</td>
          <td class="op-datatable__td" style="text-align:right">
            <Button variant="ghost" size="sm" icon="fas fa-eye">Ver</Button>
          </td>
        </svelte:fragment>
      </DataTable>
    </Card>
    <div class="mt-4">
      <Card title="DataTable — estado vacío">
        <DataTable columns={tableColumns} rows={[]} totalCount={0} caption="Tabla vacía" />
      </Card>
    </div>
    <div class="mt-4">
      <Card title="DataTable — skeleton de carga">
        <DataTable
          columns={tableColumns}
          rows={[]}
          totalCount={0}
          loading
          caption="Tabla cargando"
        />
      </Card>
    </div>
  </section>

  <!-- ── 6. Loader ── -->
  <section class="kit-section" aria-labelledby="section-loader">
    <h2 id="section-loader" class="kit-section__title">6. Loader / Skeleton</h2>
    <Card>
      <div class="kit-row items-center">
        <div>
          <p class="text-xs text-secondary mb-2">Spinner sm</p>
          <Loader size="sm" />
        </div>
        <div>
          <p class="text-xs text-secondary mb-2">Spinner md</p>
          <Loader size="md" />
        </div>
        <div>
          <p class="text-xs text-secondary mb-2">Spinner lg</p>
          <Loader size="lg" />
        </div>
        <div style="flex: 1">
          <p class="text-xs text-secondary mb-2">Skeleton (3 líneas)</p>
          <Loader type="skeleton" lines={3} />
        </div>
      </div>
      <div class="mt-5">
        <p class="text-xs text-secondary mb-2">Estado de página completa</p>
        <Loader type="page" label="Cargando expedientes…" />
      </div>
    </Card>
  </section>

  <!-- ── 7. EmptyState ── -->
  <section class="kit-section" aria-labelledby="section-empty">
    <h2 id="section-empty" class="kit-section__title">7. EmptyState</h2>
    <Card>
      <EmptyState
        icon="fas fa-inbox"
        title="Bandeja vacía"
        description="No hay radicados pendientes en su bandeja. Cuando lleguen documentos asignados aparecerán aquí."
        action="Radicar documento"
        actionHref="/radicar"
      />
    </Card>
  </section>

  <!-- ── 8. Toast / Notification ── -->
  <section class="kit-section" aria-labelledby="section-toast">
    <h2 id="section-toast" class="kit-section__title">8. Toast / Notification</h2>
    <Card title="Disparar toasts">
      <div class="kit-row">
        <Button
          variant="secondary"
          size="sm"
          icon="fas fa-check-circle"
          on:click={() => triggerToast("success")}>Éxito</Button
        >
        <Button
          variant="danger"
          size="sm"
          icon="fas fa-exclamation-circle"
          on:click={() => triggerToast("error")}>Error</Button
        >
        <Button
          variant="secondary"
          size="sm"
          icon="fas fa-exclamation-triangle"
          on:click={() => triggerToast("warning")}>Advertencia</Button
        >
        <Button
          variant="ghost"
          size="sm"
          icon="fas fa-info-circle"
          on:click={() => triggerToast("info")}>Info</Button
        >
      </div>
      <p class="text-sm text-secondary mt-3">
        Los toasts aparecen en la esquina inferior derecha y se auto-eliminan.
      </p>
    </Card>
  </section>

  <!-- ── 9. Modal ── -->
  <section class="kit-section" aria-labelledby="section-modal">
    <h2 id="section-modal" class="kit-section__title">9. Modal</h2>
    <Card>
      <Button variant="primary" icon="fas fa-ban" on:click={() => (modalOpen = true)}>
        Abrir modal de anulación
      </Button>
    </Card>
  </section>

  <!-- ── 10. Drawer ── -->
  <section class="kit-section" aria-labelledby="section-drawer">
    <h2 id="section-drawer" class="kit-section__title">10. Drawer</h2>
    <Card>
      <Button variant="secondary" icon="fas fa-eye" on:click={() => (drawerOpen = true)}>
        Ver detalle de radicado
      </Button>
    </Card>
  </section>

  <!-- ── 11. Stepper ── -->
  <section class="kit-section" aria-labelledby="section-stepper">
    <h2 id="section-stepper" class="kit-section__title">11. Stepper</h2>
    <Card title="Flujo de radicación guiada">
      <Stepper steps={stepperSteps} bind:current={stepperCurrent} />
      <div class="kit-row mt-5">
        <Button
          variant="ghost"
          size="sm"
          disabled={stepperCurrent === 0}
          on:click={() => stepperCurrent--}>Atrás</Button
        >
        <Button
          variant="primary"
          size="sm"
          disabled={stepperCurrent === stepperSteps.length - 1}
          on:click={() => stepperCurrent++}>Continuar</Button
        >
      </div>
      <p class="text-sm text-secondary mt-3">
        Paso actual: <strong>{stepperSteps[stepperCurrent]}</strong>
        ({stepperCurrent + 1} de {stepperSteps.length})
      </p>
    </Card>
  </section>
</div>

<!-- Modal de anulación (ejemplo de uso) -->
<Modal
  open={modalOpen}
  title="Solicitar anulación de radicado"
  size="md"
  on:close={() => (modalOpen = false)}
>
  <FormField
    id="modal-causal"
    label="Causal de anulación"
    type="select"
    placeholder="Seleccione la causal…"
    options={[
      { value: "duplicado", label: "Radicado duplicado" },
      { value: "error_datos", label: "Error en los datos ingresados" },
      { value: "retiro", label: "Retiro voluntario del solicitante" },
    ]}
  />
  <div class="mt-4">
    <FormField
      id="modal-observacion"
      label="Observación"
      type="textarea"
      placeholder="Describa el motivo con detalle…"
      rows={3}
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalOpen = false)}>Cancelar</Button>
    <Button
      variant="danger"
      icon="fas fa-ban"
      on:click={() => {
        modalOpen = false;
        notify({
          type: "success",
          message: "Solicitud de anulación enviada al jefe de dependencia.",
        });
      }}>Solicitar anulación</Button
    >
  </svelte:fragment>
</Modal>

<!-- Drawer de detalle (ejemplo de uso) -->
<Drawer
  open={drawerOpen}
  title="Radicado 2024-ICETEX-E-000001"
  on:close={() => (drawerOpen = false)}
>
  <dl class="kit-dl">
    <dt>Tipo</dt>
    <dd>Entrada</dd>
    <dt>Fecha de radicación</dt>
    <dd>2024-01-15</dd>
    <dt>Dependencia destino</dt>
    <dd>Ventanilla central</dd>
    <dt>Asunto</dt>
    <dd>Solicitud de crédito educativo para pregrado semestre 2024-1</dd>
    <dt>Estado</dt>
    <dd><StatusChip status="active" label="Activo" /></dd>
  </dl>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (drawerOpen = false)}>Cerrar</Button>
    <Button variant="primary" icon="fas fa-edit">Tramitar</Button>
  </svelte:fragment>
</Drawer>

<style lang="scss">
  .kit-page {
    max-width: 960px;
    margin: 0 auto;

    &__header {
      margin-bottom: var(--op-space-6);
    }

    &__title {
      font-size: var(--op-font-xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-2);
    }

    &__subtitle {
      margin: 0;
    }
  }

  .kit-section {
    margin-bottom: var(--op-space-7);

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-4);
      padding-bottom: var(--op-space-2);
      border-bottom: 2px solid var(--op-primary-light);
    }
  }

  .kit-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--op-space-3);
  }

  .kit-dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: var(--op-space-2) var(--op-space-4);
    font-size: var(--op-font-sm);

    dt {
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
    }

    dd {
      color: var(--op-text);
      margin: 0;
    }
  }

  // Estilos de columnas simples (sin Bulma completa)
  .columns {
    display: flex;
    gap: var(--op-space-4);
    flex-wrap: wrap;

    .column {
      flex: 1;
      min-width: 200px;
    }
  }
</style>
