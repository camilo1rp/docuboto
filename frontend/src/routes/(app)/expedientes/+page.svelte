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
  /expedientes — Listado de expedientes (T-12, RF-UI-05).

  Todo el estado de la vista (filtros, página) vive en la URL: los cambios usan
  goto() y re-ejecutan +page.server.js#load (navegación cliente de SvelteKit,
  sin recarga completa). El listado en sí es SSR-puro; el ÚNICO endpoint
  proxy bajo /expedientes/api es GET metadata-templates (D-05), usado por el
  Modal de creación al elegir una serie TRD (T-12 Form Builder, §1 del
  contrato orfeo-architect) — el access_token nunca sale del servidor.

  Filtros soportados por /api/v1/expedientes/search:
    - q (texto libre, opcional; se omite del backend si tiene <2 caracteres)
    - status: open | closed | transferred
    - date_from, date_to (ISO date)
    - page (size fijo en 20, no configurable desde la UI)

  Acción de fila: solo "Abrir" (enlace al workspace del expediente, T-10).
  NO hay cerrar/transferir aquí: el backend los exige con crud>=3 pero
  /auth/me expone permissions[] sin nivel crud — no se puede espejar esa
  condición sin renderizar botones que el backend rechazaría (peor que no
  mostrarlos). Cerrar/transferir viven en el workspace de expediente.

  Modal de creación (T-12 Form Builder): al elegir una serie TRD se carga su
  plantilla de metadatos ACTIVA (json_schema) y se renderizan sus campos
  dinámicos vía SchemaField.svelte — ver $lib/utils/schemaField.js para el
  mapeo tipo-de-schema -> control. Constructos no soportados (object/array/
  oneOf/$ref) no se renderizan; se avisa con un contador.

  nivel_seguridad: NO se muestra en el listado ni en el formulario de
  creación (fijo en 1/PÚBLICA) — ver nota en +page.server.js#actions.create.
-->
<script>
  import { onMount } from "svelte";
  import { goto, invalidateAll } from "$app/navigation";
  import { enhance } from "$app/forms";
  import { navigating } from "$app/stores";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import SchemaField from "$lib/components/ui/SchemaField.svelte";
  import { resolveSchemaControl } from "$lib/utils/schemaField.js";

  /** @type {import('./$types').PageData} */
  export let data;
  /** @type {import('./$types').ActionData} */
  export let form;

  const pageSize = 20;

  // ---------------------------------------------------------------------------
  // Filtros — estado editable del formulario, resincronizado desde la URL en
  // cada carga (M-4): sin esto, navegar atrás/adelante cambia la tabla pero
  // deja los inputs con los valores de la navegación anterior.
  // ---------------------------------------------------------------------------
  let filterQ = "";
  let filterStatus = "";
  let filterDateFrom = "";
  let filterDateTo = "";
  let dateRangeError = "";

  /** @param {{q?: string, status?: string, dateFrom?: string, dateTo?: string}|undefined} filters */
  function syncFiltersFromUrl(filters) {
    filterQ = filters?.q ?? "";
    filterStatus = filters?.status ?? "";
    filterDateFrom = filters?.dateFrom ?? "";
    filterDateTo = filters?.dateTo ?? "";
  }

  $: syncFiltersFromUrl(data.initialFilters);

  // ---------------------------------------------------------------------------
  // Datos de la tabla — siempre reflejan la última carga SSR (goto() los renueva)
  // ---------------------------------------------------------------------------
  $: expedientes = data.expedientes ?? [];
  $: total = data.total ?? 0;
  $: currentPage = data.initialFilters?.page ?? 1;

  // $navigating: solo mientras SvelteKit resuelve una navegación HACIA esta
  // misma vista (filtro/página/reintentar). Una navegación de salida (p. ej.
  // "Abrir" a /expedientes/{id}) no debe hacer parpadear la tabla a esqueleto.
  $: loading = $navigating?.to?.route?.id === "/(app)/expedientes";

  // ---------------------------------------------------------------------------
  // Autorización (espejo del RBAC; el backend siempre re-valida — ADR-013/020)
  // ---------------------------------------------------------------------------
  $: canManage = $permissions.includes("USUA_PERM_EXPEDIENTE");

  onMount(() => {
    setBreadcrumbs([{ label: "Expedientes" }]);
  });

  // ---------------------------------------------------------------------------
  // Helpers de presentación
  // ---------------------------------------------------------------------------
  const STATUS_LABELS = { open: "Abierto", closed: "Cerrado", transferred: "Transferido" };
  // "closed" (cerrado) es un estado normal del ciclo de vida, no un error:
  // StatusChip mapea closed→rojo, así que se pide explícitamente "neutral" (gris).
  const STATUS_CHIP_MAP = { open: "active", closed: "neutral", transferred: "info" };

  const TABLE_COLUMNS = [
    { key: "code", label: "Código", width: "160px" },
    { key: "name", label: "Nombre" },
    { key: "status", label: "Estado", width: "140px" },
    { key: "opened_at", label: "Apertura", width: "130px" },
    { key: "actions", label: "Acciones", width: "110px" },
  ];

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }

  // ---------------------------------------------------------------------------
  // Filtros y paginación — todo pasa por goto() (URL = fuente de verdad)
  // ---------------------------------------------------------------------------

  /** @param {number} page */
  function buildParams(page = 1) {
    const params = new URLSearchParams();
    if (filterQ.trim().length >= 2) params.set("q", filterQ.trim());
    if (filterStatus) params.set("status", filterStatus);
    if (filterDateFrom) params.set("date_from", filterDateFrom);
    if (filterDateTo) params.set("date_to", filterDateTo);
    if (page > 1) params.set("page", String(page));
    return params;
  }

  function applyFilters() {
    dateRangeError = "";
    if (filterDateFrom && filterDateTo && filterDateFrom > filterDateTo) {
      dateRangeError = "La fecha inicio no puede ser posterior a la fecha fin.";
      return;
    }
    goto(`/expedientes?${buildParams(1).toString()}`, { keepFocus: true });
  }

  function clearFilters() {
    filterQ = "";
    filterStatus = "";
    filterDateFrom = "";
    filterDateTo = "";
    dateRangeError = "";
    goto("/expedientes", { keepFocus: true });
  }

  /** @param {CustomEvent<{page: number}>} e */
  function onPageChange(e) {
    goto(`/expedientes?${buildParams(e.detail.page).toString()}`);
  }

  $: hasActiveFilters = !!(filterQ.trim() || filterStatus || filterDateFrom || filterDateTo);

  // ---------------------------------------------------------------------------
  // Modal de creación (actions.create — form action nativo, sin +server.js)
  // ---------------------------------------------------------------------------
  let showCreateModal = false;
  let createName = "";
  let createDescription = "";
  let createTrdSerieId = "";
  // ADR-026: `origen` decide bajo qué instrumento se clasifica y, en fondo
  // acumulado, desde qué fecha se cuenta la retención. No es un filtro de
  // presentación: cambia qué catálogo alimenta el selector y qué campos son
  // obligatorios.
  let createOrigen = "corriente";
  let createFechaExtremaInicial = "";
  let createFechaExtremaFinal = "";
  let creating = false;

  $: esFondoAcumulado = createOrigen === "fondo_acumulado";

  // ---------------------------------------------------------------------------
  // Form Builder (T-12): campos dinámicos de la plantilla de metadatos de la
  // serie TRD elegida. Se traen del proxy GET /expedientes/api/metadata-templates
  // (D-05, contrato §1) al cambiar `createTrdSerieId` — no del `load` SSR:
  // el Modal tiene estado local y un reload SSR a mitad de formulario lo
  // perdería.
  // ---------------------------------------------------------------------------
  /** @type {any} plantilla activa de la serie (ExpedienteTemplateResponse) */
  let activeTemplate = null;
  let loadingTemplate = false;
  /** @type {Record<string, string>} valores de los campos dinámicos, por clave del schema */
  let metadataValues = {};

  // El selector de instrumento cambia de CATÁLOGO, no de etiqueta: una TRD
  // ordena producción futura y una TVD valora un fondo ya producido, y el
  // backend rechaza en el motor cruzarlos (trigger de la Capa 4, ADR-026). Un
  // expediente corriente nunca ve agrupaciones de TVD ni al revés, así que la
  // interfaz no puede ofrecer una combinación que va a fallar.
  $: serieOptions = esFondoAcumulado
    ? [
        { value: "", label: "— elegir agrupación de TVD —" },
        ...(data.tvdAgrupaciones ?? []).map((s) => ({
          value: s.id,
          label: `${s.code} — ${s.name}`,
        })),
      ]
    : [
        { value: "", label: "— sin serie / sin metadatos —" },
        ...(data.trdSeries ?? []).map((s) => ({ value: s.id, label: `${s.code} — ${s.name}` })),
      ];

  /** El catálogo de TVD puede venir vacío porque el tenant no tiene ninguna
   * registrada, o porque la petición falló en el `load`. La interfaz no puede
   * distinguirlos, así que lo dice sin afirmar la causa — nunca cae al
   * catálogo de TRD, que sería ofrecer justo lo que el backend rechaza. */
  $: sinAgrupacionesTvd = esFondoAcumulado && (data.tvdAgrupaciones ?? []).length === 0;

  /** Cambiar de origen invalida el instrumento ya elegido (pertenece al otro
   * catálogo) y, con él, la plantilla de metadatos que se hubiera cargado.
   * Sin esto quedaría un `trd_serie_id` del catálogo anterior y el backend
   * respondería con una violación de trigger difícil de leer. */
  function onOrigenChange() {
    createTrdSerieId = "";
    activeTemplate = null;
    metadataValues = {};
    if (!esFondoAcumulado) {
      createFechaExtremaInicial = "";
      createFechaExtremaFinal = "";
    }
  }

  $: templateProperties =
    activeTemplate?.json_schema?.properties &&
    typeof activeTemplate.json_schema.properties === "object"
      ? Object.entries(activeTemplate.json_schema.properties)
      : [];
  $: templateRequired = activeTemplate?.json_schema?.required ?? [];
  // SchemaField ya decide por sí mismo si un constructo es soportado, pero la
  // página necesita filtrar ANTES de renderizar para poder contar los que no
  // lo son (aviso) — resolveSchemaControl es la misma función que usa
  // SchemaField internamente (fuente única de verdad, $lib/utils/schemaField.js).
  $: supportedTemplateProperties = templateProperties.filter(
    ([, propSchema]) => resolveSchemaControl(propSchema) !== null,
  );
  $: unsupportedFieldCount = templateProperties.length - supportedTemplateProperties.length;

  /**
   * Trae la plantilla ACTIVA de metadatos de la serie elegida y prepara
   * `metadataValues`. `presetValues`, si se pasa, repuebla los campos tras un
   * 422 del backend (mismo espíritu que M-7 para name/description/trd).
   * @param {Record<string, string>|null} [presetValues]
   */
  async function onSerieChange(presetValues = null) {
    activeTemplate = null;
    metadataValues = {};
    if (!createTrdSerieId) return;

    loadingTemplate = true;
    try {
      const params = new URLSearchParams({ trd_serie_id: createTrdSerieId });
      const res = await fetch(`/expedientes/api/metadata-templates?${params}`);
      /** @type {any[]} */
      const templates = await res.json();
      if (!res.ok) throw templates;
      const found = Array.isArray(templates) ? templates.find((t) => t?.activo) : null;
      activeTemplate = found ?? null;
      if (activeTemplate?.json_schema?.properties) {
        metadataValues = Object.fromEntries(
          Object.keys(activeTemplate.json_schema.properties).map((k) => [
            k,
            presetValues?.[k] ?? "",
          ]),
        );
      }
    } catch {
      // Sin plantilla activa (o error de red) es válido; el backend valida
      // `metadata` de todas formas al crear.
      activeTemplate = null;
    } finally {
      loadingTemplate = false;
    }
  }

  function openCreateModal() {
    createName = "";
    createDescription = "";
    createTrdSerieId = "";
    createOrigen = "corriente";
    createFechaExtremaInicial = "";
    createFechaExtremaFinal = "";
    activeTemplate = null;
    metadataValues = {};
    showCreateModal = true;
  }

  // Si la creación falla (fail() del action), el modal debe quedar/volver abierto
  // para mostrar el error — SvelteKit no navega en un fail(), solo actualiza `form`.
  // También repuebla el formulario con los `values` que devuelve fail() (M-7):
  // sin esto, un 422 del backend borra lo que el usuario ya había escrito.
  $: if (form?.error) {
    showCreateModal = true;
    if (form.values) {
      createName = form.values.name ?? "";
      createDescription = form.values.description ?? "";
      createTrdSerieId = form.values.trdSerieId ?? "";
      createOrigen = form.values.origen ?? "corriente";
      createFechaExtremaInicial = form.values.fechaExtremaInicial ?? "";
      createFechaExtremaFinal = form.values.fechaExtremaFinal ?? "";
      // Si la plantilla ya estaba cargada (caso normal con JS: use:enhance no
      // remonta el componente, metadataValues nunca se perdió), solo se
      // fusionan los valores crudos por si acaso. Si NO hay plantilla en
      // memoria (mount fresco tras un reload sin JS), se refetch — sin el
      // schema no hay dónde repoblar los campos dinámicos.
      if (createTrdSerieId && !activeTemplate && !loadingTemplate) {
        onSerieChange(form.values.meta ?? null);
      } else if (form.values.meta) {
        metadataValues = { ...metadataValues, ...form.values.meta };
      }
    }
  }

  // Mapeo heurístico (best-effort) del 422 metadata_validation_failed a un
  // campo concreto: el backend solo da el `message` de jsonschema (p. ej.
  // "'monto_contrato' is a required property"), sin ruta estructurada — ver
  // contrato §5. Si el mensaje empieza por 'clave' y esa clave está en el
  // schema renderizado, se resalta ESE campo; si no hay match, el error
  // queda solo en el aviso general del Modal (role="alert").
  $: metaFieldError = (() => {
    if (form?.error !== "metadata_validation_failed" || typeof form?.detail !== "string") return {};
    const match = /^'([^']+)'/.exec(form.detail);
    const key = match?.[1];
    if (!key || !templateProperties.some(([k]) => k === key)) return {};
    return { [key]: form.detail };
  })();

  // El error de validación de `name` ("obligatorio" / "máximo 500 caracteres")
  // se anexa también al campo (aria-invalid + aria-describedby vía FormField,
  // M-7): antes solo vivía en el aviso general del modal (role="alert", que
  // se mantiene arriba para todos los tipos de error) y un usuario de lector
  // de pantalla que tabulaba directo al campo no percibía que estaba
  // inválido. Otros errores (upstream_error, connection_error,
  // metadata_validation_failed, unauthenticated) no son atribuibles a un
  // campo concreto y solo aparecen en el aviso general.
  $: createNameError = form?.error === "validation_error" ? (form.detail ?? "") : "";
</script>

<svelte:head>
  <title>Expedientes — OrpycaMCP</title>
</svelte:head>

<div class="expedientes">
  <!-- -----------------------------------------------------------------------
    Encabezado
  ----------------------------------------------------------------------- -->
  <header class="expedientes__header">
    <h1 class="expedientes__title">Expedientes</h1>
    {#if canManage}
      <Button variant="primary" size="sm" icon="fas fa-plus" on:click={openCreateModal}>
        Crear expediente
      </Button>
    {/if}
  </header>

  <!-- -----------------------------------------------------------------------
    Filtros
  ----------------------------------------------------------------------- -->
  <!-- Sin aria-label: el <form role="search"> de dentro ya es el landmark
    (dos landmarks "region" anidados casi redundantes eran ruido para lectores
    de pantalla). -->
  <section class="expedientes__filters">
    <form
      class="filters-form"
      role="search"
      aria-label="Buscar expedientes"
      on:submit|preventDefault={applyFilters}
    >
      <div class="filters-form__fields">
        <FormField
          label="Buscar"
          type="text"
          id="filter-q"
          bind:value={filterQ}
          placeholder="Nombre o código del expediente…"
          hint="Mínimo 2 caracteres para buscar por texto."
        />
        <FormField
          label="Estado"
          type="select"
          id="filter-status"
          bind:value={filterStatus}
          options={[
            { value: "", label: "Todos los estados" },
            { value: "open", label: "Abierto" },
            { value: "closed", label: "Cerrado" },
            { value: "transferred", label: "Transferido" },
          ]}
        />
        <FormField
          label="Fecha desde"
          type="date"
          id="filter-date-from"
          bind:value={filterDateFrom}
          error={dateRangeError}
        />
        <FormField label="Fecha hasta" type="date" id="filter-date-to" bind:value={filterDateTo} />
      </div>

      <div class="filters-form__actions">
        <Button type="submit" variant="primary" size="sm" icon="fas fa-filter">Filtrar</Button>
        {#if hasActiveFilters}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            icon="fas fa-times"
            on:click={clearFilters}
          >
            Limpiar
          </Button>
        {/if}
      </div>
    </form>
  </section>

  <!-- -----------------------------------------------------------------------
    Listado
  ----------------------------------------------------------------------- -->
  {#if data.loadError && expedientes.length === 0 && !loading}
    <EmptyState
      icon="fas fa-exclamation-circle"
      title="Error al cargar los expedientes"
      description={data.loadError}
    >
      <Button variant="primary" on:click={invalidateAll}>Reintentar</Button>
    </EmptyState>
  {:else if loading || expedientes.length > 0}
    <div class="expedientes__table-wrap">
      <DataTable
        columns={TABLE_COLUMNS}
        rows={expedientes}
        totalCount={total}
        page={currentPage}
        {pageSize}
        {loading}
        caption="Listado de expedientes"
        on:pageChange={onPageChange}
      >
        <svelte:fragment slot="row" let:row>
          <td class="op-datatable__td">
            <code class="expediente-code">{row.code}</code>
          </td>
          <td class="op-datatable__td">{row.name}</td>
          <td class="op-datatable__td">
            <StatusChip
              status={STATUS_CHIP_MAP[row.status] ?? "neutral"}
              label={STATUS_LABELS[row.status] ?? row.status}
              dot={true}
            />
          </td>
          <td class="op-datatable__td">
            <time datetime={row.opened_at}>{formatDate(row.opened_at)}</time>
          </td>
          <td class="op-datatable__td">
            <a
              href="/expedientes/{row.id}"
              class="action-link"
              aria-label="Abrir expediente {row.name}"
            >
              <i class="fas fa-folder-open" aria-hidden="true"></i>
              Abrir
            </a>
          </td>
        </svelte:fragment>
      </DataTable>
    </div>
  {:else}
    <EmptyState
      icon="fas fa-folder-open"
      title={hasActiveFilters ? "Sin resultados" : "Sin expedientes"}
      description={hasActiveFilters
        ? "No hay expedientes que coincidan con los filtros aplicados."
        : "Aún no se han creado expedientes."}
    >
      {#if hasActiveFilters}
        <Button variant="ghost" on:click={clearFilters}>Limpiar filtros</Button>
      {:else if canManage}
        <Button variant="primary" on:click={openCreateModal}>Crear expediente</Button>
      {/if}
    </EmptyState>
  {/if}
</div>

{#if canManage}
  <!-- =========================================================================
  Modal: crear expediente (form action nativo — actions.create)
========================================================================= -->
  <Modal
    open={showCreateModal}
    title="Crear expediente"
    size="md"
    on:close={() => (showCreateModal = false)}
  >
    {#if form?.error}
      <p class="modal-error" role="alert">{form.detail}</p>
    {/if}
    <form
      id="expediente-create-form"
      method="POST"
      action="?/create"
      use:enhance={() => {
        creating = true;
        return async ({ update }) => {
          await update();
          creating = false;
        };
      }}
    >
      <div class="modal-form">
        <FormField
          id="create-name"
          name="name"
          label="Nombre"
          type="text"
          required={true}
          bind:value={createName}
          placeholder="Asunto o nombre del expediente"
          hint="Obligatorio, máximo 500 caracteres."
          error={createNameError}
        />
        <FormField
          id="create-description"
          name="description"
          label="Descripción (opcional)"
          type="textarea"
          bind:value={createDescription}
          rows={3}
        />
        <FormField
          id="create-origen"
          name="origen"
          label="Origen del expediente"
          type="select"
          bind:value={createOrigen}
          options={[
            { value: "corriente", label: "Corriente — documentación en producción" },
            { value: "fondo_acumulado", label: "Fondo acumulado — documentación ya producida" },
          ]}
          hint="Un fondo acumulado se clasifica bajo una TVD y su retención se cuenta desde su fecha extrema final, no desde el cierre."
          on:change={() => onOrigenChange()}
        />

        <FormField
          id="create-trd"
          name="trd_serie_id"
          label={esFondoAcumulado ? "Agrupación de TVD" : "Serie TRD (opcional)"}
          type="select"
          required={esFondoAcumulado}
          bind:value={createTrdSerieId}
          options={serieOptions}
          hint={esFondoAcumulado
            ? "Obligatoria: un fondo acumulado solo puede clasificarse bajo una tabla de valoración."
            : "Elegir una serie con plantilla de metadatos activa habilita campos adicionales."}
          on:change={() => onSerieChange()}
        />

        {#if sinAgrupacionesTvd}
          <p class="modal-form__warning" role="status">
            <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            No hay agrupaciones de TVD disponibles. Regístralas en
            <a href="/admin/tvd">Administración → TVD</a> antes de crear un expediente de fondo acumulado.
          </p>
        {/if}

        {#if esFondoAcumulado}
          <FormField
            id="create-fecha-extrema-inicial"
            name="fecha_extrema_inicial"
            label="Fecha extrema inicial (opcional)"
            type="date"
            bind:value={createFechaExtremaInicial}
            hint="Documento más antiguo del fondo."
          />
          <FormField
            id="create-fecha-extrema-final"
            name="fecha_extrema_final"
            label="Fecha extrema final"
            type="date"
            required={true}
            bind:value={createFechaExtremaFinal}
            hint="Documento más reciente del fondo. Es la fecha desde la que se cuenta la retención, así que el calendario puede resultar ya vencido — eso es lo normal en un fondo acumulado."
          />
        {/if}

        <!-- Campos dinámicos de la plantilla de metadatos de la serie (T-12 Form Builder) -->
        {#if createTrdSerieId}
          <div class="modal-form__meta">
            {#if loadingTemplate}
              <p class="modal-form__meta-loading">
                <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
                Cargando metadatos de la serie…
              </p>
            {:else if supportedTemplateProperties.length > 0}
              <p class="modal-form__section-label">
                <i class="fas fa-tags" aria-hidden="true"></i>
                Metadatos de la serie
              </p>
              {#each supportedTemplateProperties as [key, propSchema] (key)}
                <SchemaField
                  propKey={key}
                  schema={propSchema}
                  required={templateRequired.includes(key)}
                  bind:value={metadataValues[key]}
                  error={metaFieldError[key] ?? ""}
                />
              {/each}
            {/if}
            {#if unsupportedFieldCount > 0}
              <p class="modal-form__meta-warning" role="status">
                {unsupportedFieldCount}
                {unsupportedFieldCount === 1 ? "campo" : "campos"} de la plantilla no se
                {unsupportedFieldCount === 1 ? "puede editar" : "pueden editar"} en este formulario.
              </p>
            {/if}
          </div>
        {/if}

        <!--
        Nivel de seguridad: NO seleccionable en v1. SessionUser (JWT + /auth/me)
        no expone el clearance del usuario en el front, así que no hay forma de
        acotar este <select> a los niveles que el usuario podría leer de vuelta
        (decisión de orfeo-architect, E22 T-12). Se crea siempre en nivel 1
        (PÚBLICA) desde +page.server.js#actions.create. Cuando el clearance esté
        disponible en el front, descomentar y acotar `options` a los niveles
        <= clearance del usuario:

        <FormField
          id="create-nivel"
          name="nivel_seguridad"
          label="Nivel de seguridad"
          type="select"
          bind:value={createNivelSeguridad}
          options={NIVEL_OPTIONS}
        />
      -->
      </div>
    </form>
    <svelte:fragment slot="footer">
      <Button
        type="submit"
        form="expediente-create-form"
        variant="primary"
        size="sm"
        loading={creating}
        disabled={creating || !createName.trim()}
      >
        <i class="fas fa-save" aria-hidden="true"></i>
        Crear
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={creating}
        on:click={() => (showCreateModal = false)}
      >
        Cancelar
      </Button>
    </svelte:fragment>
  </Modal>
{/if}

<style lang="scss">
  // ---------------------------------------------------------------------------
  // Layout principal
  // ---------------------------------------------------------------------------

  .expedientes {
    padding: var(--op-space-6);
    max-width: 1200px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .expedientes__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--op-space-3);
    padding-bottom: var(--op-space-4);
    border-bottom: 1px solid var(--op-border);
  }

  .expedientes__title {
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
  }

  // ---------------------------------------------------------------------------
  // Filtros
  // ---------------------------------------------------------------------------

  .expedientes__filters {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4) var(--op-space-5);
  }

  .filters-form {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-4);
    flex-wrap: wrap;
  }

  .filters-form__fields {
    display: flex;
    gap: var(--op-space-3);
    flex-wrap: wrap;
    flex: 1;
  }

  .filters-form__actions {
    display: flex;
    gap: var(--op-space-2);
    align-items: flex-end;
    margin-bottom: 2px;
  }

  // ---------------------------------------------------------------------------
  // Tabla
  // ---------------------------------------------------------------------------

  .expedientes__table-wrap {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    overflow: hidden;
  }

  .expediente-code {
    font-family: monospace;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    background: var(--op-primary-light);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    white-space: nowrap;
  }

  .action-link {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-1);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    text-decoration: none;
    padding: 3px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    border: 1px solid var(--op-primary-light);
    background: var(--op-primary-light);
    transition:
      background var(--op-transition),
      color var(--op-transition);

    &:hover {
      background: var(--op-primary);
      color: var(--op-surface);
      border-color: var(--op-primary);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal de creación
  // ---------------------------------------------------------------------------

  .modal-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  // Campos dinámicos de la plantilla de metadatos (T-12 Form Builder)
  .modal-form__meta {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding: var(--op-space-3);
    border: 1px dashed var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-bg);
  }

  .modal-form__section-label {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    margin: 0;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .modal-form__meta-loading {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    margin: 0;
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
  }

  .modal-form__meta-warning {
    margin: 0;
    font-size: var(--op-font-xs);
    color: var(--op-warning-fg);
  }

  .modal-form__warning {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0;
    padding: var(--op-space-3) var(--op-space-4);
    border-radius: var(--op-radius);
    background: var(--op-warning-bg);
    color: var(--op-warning-fg);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);

    i {
      flex-shrink: 0;
      margin-top: 2px;
    }

    a {
      color: inherit;
      text-decoration: underline;

      &:focus-visible {
        outline: 2px solid currentColor;
        outline-offset: 2px;
        border-radius: var(--op-radius-sm);
      }
    }
  }

  .modal-error {
    background: var(--op-error-bg);
    color: var(--op-error-fg);
    border: 1px solid var(--op-error);
    border-radius: var(--op-radius);
    padding: var(--op-space-3) var(--op-space-4);
    font-size: var(--op-font-sm);
    margin: 0 0 var(--op-space-4);
  }

  // ---------------------------------------------------------------------------
  // Responsive
  // ---------------------------------------------------------------------------

  @media (max-width: 768px) {
    .expedientes {
      padding: var(--op-space-4);
    }
  }
</style>
