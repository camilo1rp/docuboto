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
  radicar/+page.svelte — Radicación guiada E/S/I (T-09, RF-UI-04).

  Stepper en una sola vista — sin multi-pestaña:
    Paso 0: Tipo de radicado (E / S / I)
    Paso 1: Metadatos (asunto, remitente, dependencias, campos extra del tipo documental)
    Paso 2: Anexos (drag-drop svelte-dnd-action)
    Paso 3: Clasificación TRD/CCD
    Paso 4: Flujo / destino
    [Confirmación atómica → POST /api/v1/documents]

  El número de radicado lo asigna el backend. Se muestra al confirmar.

  Nota: El cuerpo de Salida/Interno usa el editor rico TipTap (RichEditor, T-12b);
        el HTML se sanea en el proxy SSR antes de persistir. Las Entradas mantienen
        observaciones de texto plano.
-->

<script>
  import { goto } from "$app/navigation";
  import { dndzone } from "svelte-dnd-action";
  import { can } from "$lib/stores/auth.js";
  import { notify } from "$lib/stores/notifications.js";
  import Stepper from "$lib/components/ui/Stepper.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import RichEditor from "$lib/components/editor/RichEditor.svelte";
  import SchemaField from "$lib/components/ui/SchemaField.svelte";
  import RagSuggestButton from "$lib/components/features/rag/RagSuggestButton.svelte";
  import TrdSuggestChips from "$lib/components/features/rag/TrdSuggestChips.svelte";
  import { resolveSchemaControl, coerceSchemaValue } from "$lib/utils/schemaField.js";
  import { appendGeneratedHtml } from "$lib/utils/ragInsert.js";
  // Las llamadas al gateway (subida de archivos, creación de radicado, plantillas)
  // se realizan a través de los endpoints locales /radicar/api/* que leen la
  // cookie httpOnly en el servidor — el token nunca alcanza el JS del cliente (D-05).

  /** @type {{ user: any, dependencias: any[], trdSeries: any[], tiposDocumentales: any[] }} */
  export let data;

  // ---------------------------------------------------------------------------
  // Stepper
  // ---------------------------------------------------------------------------
  const STEP_LABELS = ["Tipo", "Metadatos", "Anexos", "Clasificación TRD", "Flujo / Destino"];
  let currentStep = 0;

  // ---------------------------------------------------------------------------
  // Paso 0 — Tipo de radicado
  // ---------------------------------------------------------------------------
  /** @type {'E'|'S'|'I'|''} */
  let docType = "";

  const DOC_TYPE_OPTIONS = [
    {
      value: "E",
      label: "Entrada",
      icon: "fa-inbox",
      desc: "Documento recibido de fuera de la entidad",
    },
    {
      value: "S",
      label: "Salida",
      icon: "fa-paper-plane",
      desc: "Documento enviado hacia el exterior",
    },
    {
      value: "I",
      label: "Interno",
      icon: "fa-exchange-alt",
      desc: "Comunicación entre dependencias internas",
    },
  ];

  // ---------------------------------------------------------------------------
  // Paso 1 — Metadatos
  // ---------------------------------------------------------------------------
  let subject = "";
  let senderName = "";
  let senderEntity = "";
  let originDeptCode = "";
  let destDeptCode = "";
  let pages = "0";
  let observations = "";
  /** @type {string} nivel de seguridad como string (el select bind:value devuelve string) */
  let nivelSeguridad = "1";
  let responseDays = "";
  /** @type {string} doc_class (código del tipo documental) */
  let docClass = "";
  /** @type {Record<string, any>} metadatos extra del tipo documental (E03) */
  let extraMetadata = {};
  /** @type {any} plantilla activa del tipo documental (null = sin plantilla) */
  let activeTemplate = null;
  /** @type {boolean} */
  let loadingTemplate = false;

  // Errores de validación por campo
  let errors = /** @type {Record<string, string>} */ ({});

  // Campos dinámicos de la plantilla de metadatos del tipo documental (E03).
  // SchemaField ya decide por sí mismo si un constructo es soportado, pero la
  // página necesita filtrar ANTES de renderizar para poder contar los que no
  // lo son (aviso) — resolveSchemaControl es la misma función que usa
  // SchemaField internamente (fuente única de verdad, $lib/utils/schemaField.js).
  $: templateProperties =
    activeTemplate?.schema?.properties && typeof activeTemplate.schema.properties === "object"
      ? Object.entries(activeTemplate.schema.properties)
      : [];
  $: supportedTemplateProperties = templateProperties.filter(
    ([, propSchema]) => resolveSchemaControl(propSchema) !== null,
  );
  $: unsupportedFieldCount = templateProperties.length - supportedTemplateProperties.length;

  // Dependencias como opciones para select
  $: dependenciaOptions = [
    { value: "", label: "— seleccionar —" },
    ...data.dependencias.map((d) => ({
      value: String(d.codigo ?? d.id),
      label: `${d.sigla ? d.sigla + " — " : ""}${d.nombre}`,
    })),
  ];

  // Tipos documentales filtrados por tipo de radicado (E/S/I)
  // El backend no filtra por doc_type aquí; mostramos todos los activos
  $: tiposOptions = [
    { value: "", label: "— sin clasificar —" },
    ...data.tiposDocumentales.map((t) => ({
      value: t.code ?? t.id,
      label: t.name,
    })),
  ];

  // Cuando cambia el tipo documental, cargamos la plantilla de metadatos.
  // Usa el endpoint local /radicar/api/templates que lee la cookie httpOnly en el servidor.
  async function onDocClassChange() {
    activeTemplate = null;
    extraMetadata = {};
    errors = { ...errors };
    if (!docClass) return;

    loadingTemplate = true;
    try {
      const params = new URLSearchParams();
      params.set("tipo_documental", docClass);
      const res = await fetch(`/radicar/api/templates?${params}`);
      /** @type {any[]} */
      const templates = await res.json();
      if (!res.ok) throw templates;
      // Tomar la plantilla activa (si existe)
      /** @type {any} */
      const found = Array.isArray(templates) ? templates.find((t) => t.activo) : null;
      activeTemplate = found ?? null;
      // Inicializar los campos extra con valores vacíos
      if (activeTemplate?.schema?.properties) {
        extraMetadata = Object.fromEntries(
          Object.keys(activeTemplate.schema.properties).map((k) => [k, ""]),
        );
      }
    } catch {
      // Sin plantilla activa es válido; el backend valida de todas formas
      activeTemplate = null;
    } finally {
      loadingTemplate = false;
    }
  }

  // Nivel de seguridad — opciones
  const nivelSeguridadOptions = [
    { value: "1", label: "1 — Pública" },
    { value: "2", label: "2 — Reservada" },
    { value: "3", label: "3 — Clasificada" },
  ];

  // ---------------------------------------------------------------------------
  // Paso 2 — Anexos (drag-drop)
  // ---------------------------------------------------------------------------
  /**
   * @typedef {{ id: string, file: File, name: string, size: number, mime: string, uploading: boolean, uploaded: boolean, error: string, fileId: string|null }} AnexoItem
   */
  /** @type {AnexoItem[]} */
  let anexos = [];
  let uploadingAll = false;
  let dragOver = false;

  let _anexoSeq = 0;

  function addFiles(files) {
    for (const file of files) {
      anexos = [
        ...anexos,
        {
          id: `anexo-${++_anexoSeq}`,
          file,
          name: file.name,
          size: file.size,
          mime: file.type,
          uploading: false,
          uploaded: false,
          error: "",
          fileId: null,
        },
      ];
    }
  }

  function handleFileDrop(e) {
    e.preventDefault();
    dragOver = false;
    const dt = e.dataTransfer;
    if (dt?.files) addFiles(Array.from(dt.files));
  }

  function handleFileInput(e) {
    const input = /** @type {HTMLInputElement} */ (e.currentTarget);
    if (input.files) addFiles(Array.from(input.files));
    input.value = "";
  }

  function removeAnexo(id) {
    anexos = anexos.filter((a) => a.id !== id);
  }

  // Subida de todos los anexos pendientes al storage-service.
  // Usa el endpoint local /radicar/api/storage que inyecta el token + userId desde el servidor.
  async function uploadPendingAnexos() {
    const pending = anexos.filter((a) => !a.uploaded && !a.uploading);
    if (!pending.length) return;

    uploadingAll = true;
    await Promise.allSettled(
      pending.map(async (item) => {
        anexos = anexos.map((a) => (a.id === item.id ? { ...a, uploading: true } : a));
        try {
          const form = new FormData();
          form.append("file", item.file);
          const res = await fetch("/radicar/api/storage", { method: "POST", body: form });
          const fileResponse = await res.json();
          if (!res.ok) throw fileResponse;
          anexos = anexos.map((a) =>
            a.id === item.id
              ? { ...a, uploading: false, uploaded: true, fileId: fileResponse.id, error: "" }
              : a,
          );
        } catch (err) {
          const detail = err?.detail ?? "Error al subir el archivo";
          anexos = anexos.map((a) =>
            a.id === item.id ? { ...a, uploading: false, error: detail } : a,
          );
        }
      }),
    );
    uploadingAll = false;
  }

  // Reorder via dnd
  function handleDndConsider(e) {
    anexos = e.detail.items;
  }
  function handleDndFinalize(e) {
    anexos = e.detail.items;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  }

  // ---------------------------------------------------------------------------
  // Paso 3 — Clasificación TRD/CCD
  // ---------------------------------------------------------------------------
  let selectedSerieId = "";

  $: trdSerieOptions = [
    { value: "", label: "— sin clasificar por ahora —" },
    ...data.trdSeries.map((s) => ({
      value: s.id,
      label: `${s.code} — ${s.name}`,
    })),
  ];

  // ---------------------------------------------------------------------------
  // Paso 4 — Flujo / Destino
  // ---------------------------------------------------------------------------
  // El workflow-service crea el flujo después de radicar; aquí solo registramos
  // la dependencia destino (dest_dept_code) que ya está en el paso de metadatos.
  // No existe un endpoint de "asignar flujo en la misma llamada de radicar".
  // El destino se captura en el paso 1 y se confirma aquí.
  // TODO: cuando exista un endpoint de asignación automática de flujo, integrarlo aquí.

  let destDeptCodeFlujo = "";

  // Sincroniza destDeptCodeFlujo con el del paso 1 al llegar al paso 4
  $: if (currentStep === 4) {
    if (!destDeptCodeFlujo && destDeptCode) destDeptCodeFlujo = destDeptCode;
  }

  // ---------------------------------------------------------------------------
  // Confirmación atómica
  // ---------------------------------------------------------------------------
  let showConfirmModal = false;
  let submitting = false;
  /** @type {import('$lib/types/api.js').DocumentResponse|null} */
  let createdDocument = null;

  function openConfirmModal() {
    showConfirmModal = true;
  }

  function closeConfirmModal() {
    if (!submitting) showConfirmModal = false;
  }

  async function submitRadicacion() {
    submitting = true;

    // Construir lista de anexos ya subidos
    const anexosPayload = anexos
      .filter((a) => a.uploaded && a.fileId)
      .map((a, idx) => ({
        file_id: a.fileId,
        filename: a.name,
        file_size: a.size,
        mime_type: a.mime || null,
        tipo_anexo: null,
        es_principal: idx === 0,
        folios: null,
      }));

    // Preparar metadatos extra del tipo documental — allowlist campo a campo
    // por las claves del schema (nunca reenviar `extraMetadata` íntegro) más
    // coerción por tipo declarado (number/boolean tipados, no strings): el
    // backend valida con jsonschema estricto (Draft 2020-12) y rechaza un
    // boolean/enum enviado como string de `<select>` — ver schemaField.js.
    // `coerceSchemaValue` devuelve `undefined` para vacíos/no soportados/
    // coerción inválida, y esas claves no se asignan.
    const templateSchemaProperties = activeTemplate?.schema?.properties ?? {};
    const cleanMetadata = {};
    for (const [key, propSchema] of Object.entries(templateSchemaProperties)) {
      const coerced = coerceSchemaValue(propSchema, extraMetadata[key]);
      if (coerced !== undefined) cleanMetadata[key] = coerced;
    }

    /** @type {import('$lib/types/api.js').DocumentCreate} */
    const payload = {
      doc_type: /** @type {'E'|'S'|'I'} */ (docType),
      subject: subject.trim(),
      doc_class: docClass || null,
      origin_dept: null,
      dest_dept: null,
      origin_dept_code: originDeptCode ? Number(originDeptCode) : null,
      dest_dept_code:
        destDeptCodeFlujo || destDeptCode ? Number(destDeptCodeFlujo || destDeptCode) : null,
      sender_name: senderName.trim() || null,
      sender_entity: senderEntity.trim() || null,
      pages: Number(pages) || 0,
      observations: observations.trim() || null,
      nivel_seguridad: /** @type {1|2|3} */ (Number(nivelSeguridad) || 1),
      response_days: responseDays ? Number(responseDays) : null,
      metadata: cleanMetadata,
      anexos: anexosPayload,
    };

    try {
      // Usa el endpoint local /radicar/api/documents que inyecta el token desde el servidor.
      const res = await fetch("/radicar/api/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const doc = await res.json();
      if (!res.ok) throw doc;
      createdDocument = doc;
      showConfirmModal = false;
      notify({ type: "success", message: `Radicado creado: ${doc.tracking_number}` });
    } catch (err) {
      const detail = err?.detail ?? "Error al crear el radicado";
      notify({ type: "error", message: detail });
      // Mapear error de campo si el backend lo indica
      if (err?.error === "metadata_validation_failed") {
        errors = { ...errors, extraMetadata: detail };
      } else if (err?.error === "invalid_dependencia") {
        errors = { ...errors, destDeptCode: detail };
      }
    } finally {
      submitting = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Validación por paso
  // ---------------------------------------------------------------------------
  function validateStep(step) {
    errors = {};
    switch (step) {
      case 0:
        if (!docType) {
          errors.docType = "Selecciona el tipo de radicado";
          return false;
        }
        break;
      case 1:
        if (!subject.trim()) {
          errors.subject = "El asunto es obligatorio";
          return false;
        }
        if (docType === "E" && !senderName.trim()) {
          errors.senderName = "El nombre del remitente es obligatorio para Entradas";
          return false;
        }
        break;
      case 2: {
        // Verificar que todos los anexos agregados estén subidos
        const pending = anexos.filter((a) => !a.uploaded && !a.error);
        if (pending.length > 0) {
          errors.anexos = `Hay ${pending.length} archivo(s) sin subir. Usa "Subir archivos" antes de continuar.`;
          return false;
        }
        const failed = anexos.filter((a) => a.error);
        if (failed.length > 0) {
          errors.anexos = `Hay ${failed.length} archivo(s) con error. Elimínalos o reinténtalos.`;
          return false;
        }
        break;
      }
      case 3:
        // TRD es opcional; no se bloquea
        break;
      case 4:
        // Flujo/destino — sin validación adicional
        break;
    }
    return true;
  }

  function goNext() {
    if (!validateStep(currentStep)) return;
    if (currentStep < STEP_LABELS.length - 1) currentStep++;
  }

  function goBack() {
    if (currentStep > 0) currentStep--;
  }

  // Resetear el formulario para una nueva radicación
  function resetForm() {
    docType = "";
    subject = "";
    senderName = "";
    senderEntity = "";
    originDeptCode = "";
    destDeptCode = "";
    pages = "0";
    observations = "";
    nivelSeguridad = "1";
    responseDays = "";
    docClass = "";
    extraMetadata = {};
    activeTemplate = null;
    anexos = [];
    selectedSerieId = "";
    destDeptCodeFlujo = "";
    errors = {};
    createdDocument = null;
    currentStep = 0;
  }

  // Anuncio de accesibilidad al cambiar de paso
  let stepAnnounce = "";
  $: stepAnnounce = `Paso ${currentStep + 1} de ${STEP_LABELS.length}: ${STEP_LABELS[currentStep]}`;

  // Permiso de radicación
  // D-07: RADICAR_* → PERM_RADI_* (nombres reales del catálogo RBAC, migración 004)
  $: canRadicar = can("PERM_RADI_ENTRADA") || can("PERM_RADI_SALIDA") || can("PERM_RADI_INTERNO");
</script>

<svelte:head>
  <title>Radicar documento — OrpycaMCP</title>
</svelte:head>

<!-- Región ARIA para anuncios de cambio de paso -->
<div class="sr-only" role="status" aria-live="polite" aria-atomic="true">
  {stepAnnounce}
</div>

<div class="radicar-page">
  {#if !canRadicar}
    <!-- Acceso denegado en cliente (el backend re-valida) -->
    <div class="radicar-page__denied">
      <i class="fas fa-lock" aria-hidden="true"></i>
      <p>No tienes permisos para radicar documentos.</p>
    </div>
  {:else if createdDocument}
    <!-- -------------------------------------------------------------------- -->
    <!-- Resultado: radicado creado con éxito                                   -->
    <!-- -------------------------------------------------------------------- -->
    <section class="radicar-success" aria-labelledby="success-title">
      <div class="radicar-success__icon" aria-hidden="true">
        <i class="fas fa-check-circle"></i>
      </div>
      <h1 id="success-title" class="radicar-success__title">Radicado exitosamente</h1>
      <p class="radicar-success__tracking">{createdDocument.tracking_number}</p>
      <p class="radicar-success__subject">{createdDocument.subject}</p>
      <dl class="radicar-success__meta">
        <div>
          <dt>Tipo</dt>
          <dd>
            {#if createdDocument.doc_type === "E"}Entrada
            {:else if createdDocument.doc_type === "S"}Salida
            {:else}Interno
            {/if}
          </dd>
        </div>
        <div>
          <dt>Estado</dt>
          <dd>{createdDocument.status}</dd>
        </div>
        <div>
          <dt>Registrado</dt>
          <dd>{new Date(createdDocument.registered_at).toLocaleString("es-CO")}</dd>
        </div>
        {#if createdDocument.due_date}
          <div>
            <dt>Vence</dt>
            <dd>{createdDocument.due_date}</dd>
          </div>
        {/if}
      </dl>
      <div class="radicar-success__actions">
        <Button variant="primary" on:click={() => goto("/bandeja")}>
          <i class="fas fa-list" aria-hidden="true"></i>
          Ir a la bandeja
        </Button>
        <Button variant="secondary" on:click={resetForm}>
          <i class="fas fa-plus" aria-hidden="true"></i>
          Radicar otro
        </Button>
      </div>
    </section>
  {:else}
    <!-- -------------------------------------------------------------------- -->
    <!-- Stepper principal                                                       -->
    <!-- -------------------------------------------------------------------- -->
    <div class="radicar-header">
      <h1 class="radicar-header__title">
        <i class="fas fa-file-alt" aria-hidden="true"></i>
        Radicar documento
      </h1>
    </div>

    <div class="radicar-stepper-wrap">
      <Stepper steps={STEP_LABELS} bind:current={currentStep} canGoBack={true} />
    </div>

    <div class="radicar-content">
      <!-- ================================================================ -->
      <!-- PASO 0 — Tipo de radicado                                        -->
      <!-- ================================================================ -->
      {#if currentStep === 0}
        <section class="radicar-step" aria-labelledby="step0-title">
          <h2 id="step0-title" class="radicar-step__title">
            ¿Qué tipo de documento vas a radicar?
          </h2>

          <div
            class="doc-type-grid"
            role="radiogroup"
            aria-label="Tipo de radicado"
            aria-required="true"
          >
            {#each DOC_TYPE_OPTIONS as opt}
              <label class="doc-type-card" class:doc-type-card--selected={docType === opt.value}>
                <input
                  type="radio"
                  name="doc_type"
                  value={opt.value}
                  bind:group={docType}
                  class="sr-only"
                  aria-describedby="doc-type-desc-{opt.value}"
                />
                <i class="fas {opt.icon} doc-type-card__icon" aria-hidden="true"></i>
                <span class="doc-type-card__label">{opt.label}</span>
                <span id="doc-type-desc-{opt.value}" class="doc-type-card__desc">{opt.desc}</span>
                {#if docType === opt.value}
                  <i class="fas fa-check-circle doc-type-card__check" aria-hidden="true"></i>
                {/if}
              </label>
            {/each}
          </div>

          {#if errors.docType}
            <p class="radicar-step__error" role="alert">{errors.docType}</p>
          {/if}
        </section>

        <!-- ================================================================ -->
        <!-- PASO 1 — Metadatos                                               -->
        <!-- ================================================================ -->
      {:else if currentStep === 1}
        <section class="radicar-step" aria-labelledby="step1-title">
          <h2 id="step1-title" class="radicar-step__title">
            Datos del documento
            <span class="radicar-step__badge radicar-step__badge--{docType.toLowerCase()}">
              {docType === "E" ? "Entrada" : docType === "S" ? "Salida" : "Interno"}
            </span>
          </h2>

          <div class="radicar-step__grid">
            <!-- Asunto -->
            <div class="radicar-step__field radicar-step__field--full">
              <FormField
                id="subject"
                label="Asunto"
                type="text"
                bind:value={subject}
                required={true}
                placeholder="Descripción concisa del documento"
                error={errors.subject ?? ""}
              />
            </div>

            <!-- Tipo documental (doc_class) -->
            <div class="radicar-step__field">
              <FormField
                id="doc_class"
                label="Tipo documental"
                type="select"
                bind:value={docClass}
                options={tiposOptions}
                hint="Clasifica el documento según el catálogo"
                on:change={onDocClassChange}
              />
              {#if loadingTemplate}
                <Loader size="sm" />
              {/if}
              <!-- F6: sugerencia de tipo documental basada en precedentes (RAG,
                   Patrón A) — el chip propone, nunca aplica solo (regla dura #2). -->
              <TrdSuggestChips
                endpoint="/radicar/api/rag/antecedentes"
                query={subject}
                on:apply={(e) => {
                  docClass = e.detail.docClass;
                  onDocClassChange();
                }}
              />
            </div>

            <!-- Nivel de seguridad -->
            <div class="radicar-step__field">
              <FormField
                id="nivel_seguridad"
                label="Nivel de seguridad"
                type="select"
                bind:value={nivelSeguridad}
                options={nivelSeguridadOptions}
                hint="1=Pública por defecto (RF-SEG-08)"
              />
            </div>

            <!-- Remitente / entidad (solo E y S) -->
            {#if docType === "E" || docType === "S"}
              <div class="radicar-step__field">
                <FormField
                  id="sender_name"
                  label={docType === "E" ? "Nombre del remitente" : "Nombre del destinatario"}
                  type="text"
                  bind:value={senderName}
                  required={docType === "E"}
                  placeholder="Nombre completo"
                  error={errors.senderName ?? ""}
                />
              </div>
              <div class="radicar-step__field">
                <FormField
                  id="sender_entity"
                  label={docType === "E" ? "Entidad del remitente" : "Entidad del destinatario"}
                  type="text"
                  bind:value={senderEntity}
                  placeholder="Empresa u organismo"
                />
              </div>
            {/if}

            <!-- Dependencia origen -->
            <div class="radicar-step__field">
              <FormField
                id="origin_dept_code"
                label="Dependencia origen"
                type="select"
                bind:value={originDeptCode}
                options={dependenciaOptions}
                error={errors.originDeptCode ?? ""}
              />
            </div>

            <!-- Dependencia destino -->
            <div class="radicar-step__field">
              <FormField
                id="dest_dept_code"
                label="Dependencia destino"
                type="select"
                bind:value={destDeptCode}
                options={dependenciaOptions}
                error={errors.destDeptCode ?? ""}
              />
            </div>

            <!-- Folios -->
            <div class="radicar-step__field">
              <FormField
                id="pages"
                label="Folios"
                type="number"
                bind:value={pages}
                placeholder="0"
                hint="Número de páginas físicas"
              />
            </div>

            <!-- Plazo de respuesta (días hábiles) -->
            {#if docType === "E"}
              <div class="radicar-step__field">
                <FormField
                  id="response_days"
                  label="Plazo de respuesta (días hábiles)"
                  type="number"
                  bind:value={responseDays}
                  placeholder="ej. 15"
                  hint="Deja vacío si no aplica plazo (RF-RAD-04)"
                />
              </div>
            {/if}

            <!-- Cuerpo del documento (solo Salida e Interno) — editor rico T-12b -->
            {#if docType === "S" || docType === "I"}
              <div class="radicar-step__field radicar-step__field--full">
                <!-- F6: ayuda de redacción con RAG — opcional, degrada sin romper
                     el paso si el servicio de conocimiento no responde. -->
                <RagSuggestButton
                  endpoint="/radicar/api/rag/suggest"
                  query={subject}
                  on:insert={(e) => (observations = appendGeneratedHtml(observations, e.detail.html))}
                />
                <RichEditor
                  id="observations"
                  label="Contenido / cuerpo del documento"
                  bind:value={observations}
                  placeholder="Redacte el cuerpo del documento…"
                  hint="El formato se conserva; el servidor sanea el contenido antes de guardarlo."
                />
              </div>
            {:else}
              <!-- Observaciones para Entradas -->
              <div class="radicar-step__field radicar-step__field--full">
                <FormField
                  id="observations"
                  label="Observaciones"
                  type="textarea"
                  bind:value={observations}
                  rows={3}
                  placeholder="Notas adicionales (opcional)"
                />
              </div>
            {/if}

            <!-- Campos extra del tipo documental (E03 — plantilla de metadatos, SchemaField T-12) -->
            {#if templateProperties.length > 0}
              <div class="radicar-step__field radicar-step__field--full">
                <p class="radicar-step__section-label">
                  <i class="fas fa-tags" aria-hidden="true"></i>
                  Metadatos del tipo documental
                </p>
                <div class="radicar-step__grid">
                  {#each supportedTemplateProperties as [key, propSchema] (key)}
                    <div class="radicar-step__field">
                      <SchemaField
                        propKey={key}
                        schema={propSchema}
                        required={activeTemplate.schema.required?.includes(key)}
                        bind:value={extraMetadata[key]}
                        error={errors[`meta_${key}`] ?? ""}
                      />
                    </div>
                  {/each}
                </div>
                {#if unsupportedFieldCount > 0}
                  <p class="radicar-step__meta-warning" role="status">
                    {unsupportedFieldCount}
                    {unsupportedFieldCount === 1 ? "campo" : "campos"} de la plantilla no se
                    {unsupportedFieldCount === 1 ? "puede editar" : "pueden editar"} en este formulario.
                  </p>
                {/if}
              </div>
            {/if}

            {#if errors.extraMetadata}
              <div class="radicar-step__field radicar-step__field--full">
                <p class="radicar-step__error" role="alert">{errors.extraMetadata}</p>
              </div>
            {/if}
          </div>
        </section>

        <!-- ================================================================ -->
        <!-- PASO 2 — Anexos                                                  -->
        <!-- ================================================================ -->
      {:else if currentStep === 2}
        <section class="radicar-step" aria-labelledby="step2-title">
          <h2 id="step2-title" class="radicar-step__title">Anexos</h2>
          <p class="radicar-step__subtitle">
            Arrastra y suelta archivos o haz clic para seleccionarlos. El checksum SHA-256 lo
            calcula el servidor.
          </p>

          <!-- Zona de drop -->
          <div
            class="drop-zone"
            class:drop-zone--over={dragOver}
            role="region"
            aria-label="Zona de carga de archivos"
            on:dragover|preventDefault={() => (dragOver = true)}
            on:dragleave={() => (dragOver = false)}
            on:drop={handleFileDrop}
          >
            <i class="fas fa-cloud-upload-alt drop-zone__icon" aria-hidden="true"></i>
            <p class="drop-zone__text">Arrastra archivos aquí o</p>
            <label class="drop-zone__label">
              <span class="button is-small">Seleccionar archivos</span>
              <input
                type="file"
                multiple
                class="sr-only"
                aria-label="Seleccionar archivos para adjuntar"
                on:change={handleFileInput}
              />
            </label>
          </div>

          {#if errors.anexos}
            <p class="radicar-step__error" role="alert">{errors.anexos}</p>
          {/if}

          <!-- Lista de archivos con drag-and-drop para reordenar -->
          {#if anexos.length > 0}
            <div
              class="anexos-list"
              use:dndzone={{ items: anexos, flipDurationMs: 200 }}
              on:consider={handleDndConsider}
              on:finalize={handleDndFinalize}
              role="list"
              aria-label="Lista de archivos adjuntos"
            >
              {#each anexos as item (item.id)}
                <div class="anexo-item" role="listitem">
                  <i class="fas fa-grip-vertical anexo-item__grip" aria-hidden="true"></i>
                  <div class="anexo-item__info">
                    <span class="anexo-item__name" title={item.name}>{item.name}</span>
                    <span class="anexo-item__size">{formatSize(item.size)}</span>
                    {#if item.mime}
                      <span class="anexo-item__mime">{item.mime}</span>
                    {/if}
                  </div>
                  <div class="anexo-item__status" aria-live="polite" aria-atomic="true">
                    {#if item.uploading}
                      <Loader size="sm" />
                      <span class="sr-only">Subiendo...</span>
                    {:else if item.uploaded}
                      <i
                        class="fas fa-check-circle anexo-item__ok"
                        aria-label="Subido correctamente"
                      ></i>
                    {:else if item.error}
                      <span class="anexo-item__error" role="alert" title={item.error}>
                        <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
                        Error
                      </span>
                    {:else}
                      <span class="anexo-item__pending">Pendiente</span>
                    {/if}
                  </div>
                  <button
                    type="button"
                    class="anexo-item__remove"
                    aria-label="Eliminar {item.name}"
                    on:click={() => removeAnexo(item.id)}
                  >
                    <i class="fas fa-times" aria-hidden="true"></i>
                  </button>
                </div>
              {/each}
            </div>

            <div class="anexos-actions">
              <Button
                variant="secondary"
                disabled={uploadingAll || anexos.every((a) => a.uploaded || !!a.error)}
                on:click={uploadPendingAnexos}
              >
                {#if uploadingAll}
                  <Loader size="sm" />
                  Subiendo...
                {:else}
                  <i class="fas fa-upload" aria-hidden="true"></i>
                  Subir archivos
                {/if}
              </Button>
              <span class="anexos-actions__count">
                {anexos.filter((a) => a.uploaded).length} / {anexos.length} subidos
              </span>
            </div>
          {:else}
            <p class="radicar-step__empty">
              No hay archivos adjuntos. Puedes continuar sin anexos.
            </p>
          {/if}
        </section>

        <!-- ================================================================ -->
        <!-- PASO 3 — Clasificación TRD/CCD                                  -->
        <!-- ================================================================ -->
      {:else if currentStep === 3}
        <section class="radicar-step" aria-labelledby="step3-title">
          <h2 id="step3-title" class="radicar-step__title">Clasificación TRD/CCD</h2>
          <p class="radicar-step__subtitle">
            Asocia el documento a una serie de la Tabla de Retención Documental (opcional).
          </p>

          {#if data.trdSeries.length === 0}
            <div class="radicar-step__empty">
              <i class="fas fa-info-circle" aria-hidden="true"></i>
              No hay series TRD configuradas para este tenant. Puedes continuar sin clasificar.
            </div>
          {:else}
            <div class="radicar-step__field">
              <FormField
                id="trd_serie"
                label="Serie TRD"
                type="select"
                bind:value={selectedSerieId}
                options={trdSerieOptions}
                hint="La clasificación TRD permite la retención y disposición documental (E04)"
              />
            </div>

            {#if selectedSerieId}
              {@const serie = data.trdSeries.find((s) => s.id === selectedSerieId)}
              {#if serie}
                <Card>
                  <p slot="header">Datos de la serie seleccionada</p>
                  <dl class="trd-detail">
                    <div>
                      <dt>Código</dt>
                      <dd>{serie.code}</dd>
                    </div>
                    <div>
                      <dt>Retención gestión</dt>
                      <dd>{serie.archivo_gestion_years ?? serie.retention_years} años</dd>
                    </div>
                    <div>
                      <dt>Retención central</dt>
                      <dd>{serie.archivo_central_years ?? "—"} años</dd>
                    </div>
                    <div>
                      <dt>Disposición</dt>
                      <dd>
                        {#if serie.disposition === "CT"}Conservar total
                        {:else if serie.disposition === "E"}Eliminar
                        {:else if serie.disposition === "S"}Selección
                        {:else if serie.disposition === "M"}Microfilmar
                        {:else}{serie.disposition}
                        {/if}
                      </dd>
                    </div>
                  </dl>
                </Card>
              {/if}
            {/if}
          {/if}
        </section>

        <!-- ================================================================ -->
        <!-- PASO 4 — Flujo / Destino                                         -->
        <!-- ================================================================ -->
      {:else if currentStep === 4}
        <section class="radicar-step" aria-labelledby="step4-title">
          <h2 id="step4-title" class="radicar-step__title">Flujo y destino</h2>
          <p class="radicar-step__subtitle">
            Confirma la dependencia destinataria. El flujo de distribución se asigna tras radicar
            desde la bandeja.
          </p>

          <div class="radicar-step__field">
            <FormField
              id="dest_dept_code_flujo"
              label="Dependencia destinataria"
              type="select"
              bind:value={destDeptCodeFlujo}
              options={dependenciaOptions}
              hint="Puedes cambiarla si difiere de la indicada en el paso de metadatos"
              error={errors.destDeptCode ?? ""}
            />
          </div>

          <!-- Resumen de la radicación antes de confirmar -->
          <div class="radicar-resumen" aria-labelledby="resumen-title">
            <h3 id="resumen-title" class="radicar-resumen__title">
              <i class="fas fa-clipboard-check" aria-hidden="true"></i>
              Resumen antes de radicar
            </h3>
            <dl class="radicar-resumen__dl">
              <div>
                <dt>Tipo</dt>
                <dd>
                  {docType === "E" ? "Entrada" : docType === "S" ? "Salida" : "Interno"}
                </dd>
              </div>
              <div>
                <dt>Asunto</dt>
                <dd>{subject || "—"}</dd>
              </div>
              {#if senderName}
                <div>
                  <dt>{docType === "E" ? "Remitente" : "Destinatario"}</dt>
                  <dd>{senderName}{senderEntity ? ` (${senderEntity})` : ""}</dd>
                </div>
              {/if}
              {#if docClass}
                <div>
                  <dt>Tipo documental</dt>
                  <dd>{docClass}</dd>
                </div>
              {/if}
              <div>
                <dt>Nivel de seguridad</dt>
                <dd>
                  {nivelSeguridad === "1"
                    ? "Pública"
                    : nivelSeguridad === "2"
                      ? "Reservada"
                      : "Clasificada"}
                </dd>
              </div>
              <div>
                <dt>Folios</dt>
                <dd>{pages || 0}</dd>
              </div>
              <div>
                <dt>Anexos</dt>
                <dd>{anexos.filter((a) => a.uploaded).length} archivo(s) subido(s)</dd>
              </div>
              {#if selectedSerieId}
                {@const serie = data.trdSeries.find((s) => s.id === selectedSerieId)}
                {#if serie}
                  <div>
                    <dt>Serie TRD</dt>
                    <dd>{serie.code} — {serie.name}</dd>
                  </div>
                {/if}
              {/if}
              {#if responseDays}
                <div>
                  <dt>Plazo</dt>
                  <dd>{responseDays} días hábiles</dd>
                </div>
              {/if}
            </dl>
          </div>

          <p class="radicar-step__note">
            <i class="fas fa-info-circle" aria-hidden="true"></i>
            Al confirmar, el sistema asignará un número de radicado único e inmutable.
          </p>
        </section>
      {/if}

      <!-- ---------------------------------------------------------------- -->
      <!-- Navegación del stepper                                             -->
      <!-- ---------------------------------------------------------------- -->
      <div class="radicar-nav">
        <Button variant="secondary" disabled={currentStep === 0} on:click={goBack}>
          <i class="fas fa-arrow-left" aria-hidden="true"></i>
          Anterior
        </Button>

        {#if currentStep < STEP_LABELS.length - 1}
          <Button variant="primary" on:click={goNext}>
            Siguiente
            <i class="fas fa-arrow-right" aria-hidden="true"></i>
          </Button>
        {:else}
          <!-- Último paso: botón de confirmación -->
          <Button variant="primary" on:click={openConfirmModal} disabled={submitting}>
            <i class="fas fa-check" aria-hidden="true"></i>
            Radicar
          </Button>
        {/if}
      </div>
    </div>
  {/if}
</div>

<!-- ======================================================================== -->
<!-- Modal de confirmación atómica                                              -->
<!-- ======================================================================== -->
<!-- BUGFIX (hallazgo incidental, fuera del alcance de la migración SchemaField):
     el diálogo de confirmación de radicación NUNCA era visible en producción --
     `Modal` gatea su markup tras `{#if open}` y no se le pasaba `open`. Se controla
     la visibilidad SOLO con `open` (mismo patrón que el Modal de expedientes; sin un
     `{#if}` externo que desmonte el componente antes de que devuelva el foco al
     abridor al cerrar). -->
<Modal open={showConfirmModal} title="Confirmar radicación" on:close={closeConfirmModal}>
  <p>
    Estás a punto de radicar un documento de
    <strong>{docType === "E" ? "Entrada" : docType === "S" ? "Salida" : "Interno"}</strong>
    con asunto:
  </p>
  <blockquote class="radicar-modal__subject">"{subject}"</blockquote>
  {#if anexos.filter((a) => a.uploaded).length > 0}
    <p>
      <i class="fas fa-paperclip" aria-hidden="true"></i>
      {anexos.filter((a) => a.uploaded).length} anexo(s) adjunto(s).
    </p>
  {/if}
  <p class="radicar-modal__note">
    El número de radicado se asignará de forma inmutable. Esta acción no se puede deshacer (solo es
    posible solicitar anulación posterior con causal).
  </p>

  <div slot="footer" class="radicar-modal__footer">
    <Button variant="secondary" on:click={closeConfirmModal} disabled={submitting}>Cancelar</Button>
    <Button variant="primary" on:click={submitRadicacion} disabled={submitting}>
      {#if submitting}
        <Loader size="sm" />
        Radicando...
      {:else}
        <i class="fas fa-check" aria-hidden="true"></i>
        Confirmar radicación
      {/if}
    </Button>
  </div>
</Modal>

<style lang="scss">
  .radicar-page {
    max-width: 860px;
    margin: 0 auto;
    padding: var(--op-space-5);

    &__denied {
      text-align: center;
      padding: var(--op-space-8);
      color: var(--op-text-secondary);

      i {
        font-size: 2rem;
        color: var(--op-error);
        margin-bottom: var(--op-space-3);
        display: block;
      }
    }
  }

  .radicar-header {
    margin-bottom: var(--op-space-5);

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

  .radicar-stepper-wrap {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4) var(--op-space-5);
    margin-bottom: var(--op-space-5);
  }

  .radicar-content {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-5);
  }

  .radicar-step {
    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin-bottom: var(--op-space-4);
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
    }

    &__subtitle {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      margin-bottom: var(--op-space-4);
    }

    &__badge {
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
      padding: 2px var(--op-space-2);
      border-radius: var(--op-radius-full);
      background: var(--op-primary-light);
      color: var(--op-primary-dark);

      &--e {
        background: var(--op-info-bg);
        color: var(--op-info-fg);
      }
      &--s {
        background: var(--op-warning-bg);
        color: var(--op-warning-fg);
      }
      &--i {
        background: var(--op-bg);
        color: var(--op-text-secondary);
        border: 1px solid var(--op-border);
      }
    }

    &__grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-4);

      @media (max-width: 640px) {
        grid-template-columns: 1fr;
      }
    }

    &__field {
      &--full {
        grid-column: 1 / -1;
      }
    }

    &__section-label {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      margin-bottom: var(--op-space-2);

      i {
        color: var(--op-primary);
        margin-right: var(--op-space-1);
      }
    }

    &__error {
      color: var(--op-error);
      font-size: var(--op-font-sm);
      margin-top: var(--op-space-2);
    }

    &__meta-warning {
      margin: var(--op-space-2) 0 0;
      font-size: var(--op-font-xs);
      color: var(--op-warning-fg);
    }

    &__empty {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      padding: var(--op-space-4);
      text-align: center;
      background: var(--op-bg);
      border-radius: var(--op-radius);
      border: 1px dashed var(--op-border);

      i {
        margin-right: var(--op-space-1);
        color: var(--op-info);
      }
    }

    &__note {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-top: var(--op-space-4);
      padding: var(--op-space-3);
      background: var(--op-bg);
      border-radius: var(--op-radius);
      border-left: 3px solid var(--op-primary);

      i {
        color: var(--op-primary);
        margin-right: var(--op-space-1);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Paso 0 — Selector de tipo
  // ---------------------------------------------------------------------------
  .doc-type-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--op-space-4);

    @media (max-width: 600px) {
      grid-template-columns: 1fr;
    }
  }

  .doc-type-card {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-5) var(--op-space-3);
    border: 2px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    cursor: pointer;
    background: var(--op-surface);
    transition:
      border-color var(--op-transition),
      box-shadow var(--op-transition),
      background var(--op-transition);

    &:hover {
      border-color: var(--op-primary);
      background: var(--op-primary-light);
    }

    &--selected {
      border-color: var(--op-primary);
      background: var(--op-primary-light);
      box-shadow: 0 0 0 3px var(--op-focus-ring);
    }

    &__icon {
      font-size: 1.75rem;
      color: var(--op-primary);
    }

    &__label {
      font-size: var(--op-font-base);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
    }

    &__desc {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      text-align: center;
      line-height: 1.4;
    }

    &__check {
      position: absolute;
      top: var(--op-space-2);
      right: var(--op-space-2);
      color: var(--op-primary);
      font-size: 1rem;
    }
  }

  // ---------------------------------------------------------------------------
  // Paso 2 — Zona de drop
  // ---------------------------------------------------------------------------
  .drop-zone {
    border: 2px dashed var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-6);
    text-align: center;
    background: var(--op-bg);
    transition:
      border-color var(--op-transition),
      background var(--op-transition);
    margin-bottom: var(--op-space-4);

    &--over {
      border-color: var(--op-primary);
      background: var(--op-primary-light);
    }

    &__icon {
      font-size: 2rem;
      color: var(--op-text-secondary);
      display: block;
      margin-bottom: var(--op-space-2);
    }

    &__text {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin-bottom: var(--op-space-2);
    }

    &__label {
      cursor: pointer;

      .button {
        background: var(--op-primary);
        color: var(--op-surface);
        border: none;

        &:hover {
          background: var(--op-primary-dark);
        }
      }
    }
  }

  .anexos-list {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    margin-bottom: var(--op-space-4);
  }

  .anexo-item {
    display: flex;
    align-items: center;
    gap: var(--op-space-3);
    padding: var(--op-space-3);
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    cursor: grab;

    &:active {
      cursor: grabbing;
    }

    &__grip {
      color: var(--op-border);
      font-size: var(--op-font-sm);
      flex-shrink: 0;
    }

    &__info {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-1);
      align-items: center;
    }

    &__name {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      font-weight: var(--op-weight-medium);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 240px;
    }

    &__size,
    &__mime {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__status {
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
      font-size: var(--op-font-xs);
    }

    &__ok {
      color: var(--op-success);
      font-size: 1rem;
    }

    &__error {
      color: var(--op-error);
    }

    &__pending {
      color: var(--op-text-secondary);
    }

    &__remove {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--op-text-secondary);
      padding: var(--op-space-1);
      border-radius: var(--op-radius);
      flex-shrink: 0;
      transition: color var(--op-transition);

      &:hover {
        color: var(--op-error);
      }
      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }
  }

  .anexos-actions {
    display: flex;
    align-items: center;
    gap: var(--op-space-3);

    &__count {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }
  }

  // ---------------------------------------------------------------------------
  // Paso 3 — TRD
  // ---------------------------------------------------------------------------
  .trd-detail {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--op-space-3);
    font-size: var(--op-font-sm);

    dt {
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
    }
    dd {
      color: var(--op-text);
    }
  }

  // ---------------------------------------------------------------------------
  // Paso 4 — Resumen
  // ---------------------------------------------------------------------------
  .radicar-resumen {
    background: var(--op-bg);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4);
    margin-top: var(--op-space-4);

    &__title {
      font-size: var(--op-font-base);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin-bottom: var(--op-space-3);

      i {
        color: var(--op-primary);
        margin-right: var(--op-space-1);
      }
    }

    &__dl {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-2) var(--op-space-5);
      font-size: var(--op-font-sm);

      @media (max-width: 540px) {
        grid-template-columns: 1fr;
      }

      dt {
        font-weight: var(--op-weight-medium);
        color: var(--op-text-secondary);
      }
      dd {
        color: var(--op-text);
        word-break: break-word;
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Navegación del stepper
  // ---------------------------------------------------------------------------
  .radicar-nav {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: var(--op-space-6);
    padding-top: var(--op-space-4);
    border-top: 1px solid var(--op-border);
  }

  // ---------------------------------------------------------------------------
  // Modal de confirmación
  // ---------------------------------------------------------------------------
  .radicar-modal {
    &__subject {
      margin: var(--op-space-3) 0;
      padding: var(--op-space-3);
      border-left: 3px solid var(--op-primary);
      background: var(--op-bg);
      border-radius: 0 var(--op-radius) var(--op-radius) 0;
      font-style: italic;
      color: var(--op-text);
    }

    &__note {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-top: var(--op-space-3);
    }

    &__footer {
      display: flex;
      justify-content: flex-end;
      gap: var(--op-space-3);
    }
  }

  // ---------------------------------------------------------------------------
  // Pantalla de éxito
  // ---------------------------------------------------------------------------
  .radicar-success {
    text-align: center;
    padding: var(--op-space-8) var(--op-space-5);

    &__icon {
      font-size: 3rem;
      color: var(--op-success);
      margin-bottom: var(--op-space-4);
    }

    &__title {
      font-size: var(--op-font-xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin-bottom: var(--op-space-2);
    }

    &__tracking {
      font-size: 1.5rem;
      font-weight: var(--op-weight-bold);
      color: var(--op-primary);
      font-family: monospace;
      letter-spacing: 0.05em;
      margin-bottom: var(--op-space-1);
      padding: var(--op-space-2) var(--op-space-4);
      background: var(--op-primary-light);
      border-radius: var(--op-radius-lg);
      display: inline-block;
    }

    &__subject {
      color: var(--op-text-secondary);
      margin-bottom: var(--op-space-5);
    }

    &__meta {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: var(--op-space-3);
      max-width: 560px;
      margin: 0 auto var(--op-space-6);
      text-align: left;
      font-size: var(--op-font-sm);

      dt {
        font-weight: var(--op-weight-medium);
        color: var(--op-text-secondary);
      }
      dd {
        color: var(--op-text);
      }
    }

    &__actions {
      display: flex;
      justify-content: center;
      gap: var(--op-space-3);
      flex-wrap: wrap;
    }
  }

  // ---------------------------------------------------------------------------
  // Utilitarios
  // ---------------------------------------------------------------------------
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
