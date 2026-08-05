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
  /admin/interoperabilidad — Exportación/importación de paquetes interoperables
  (E11, RF-INT-01/RF-INT-05) y carga masiva de documentos/expedientes (Fase 8,
  cierre del desfase API↔UI).

  Cuatro pestañas, DOS permisos reales distintos (el gate real difiere por
  pestaña — revisión UX 2026-08-02, punto 2 del lote 1 — un solo `permission`
  de tarjeta no puede representar las cinco):
    1. Exportar                    — POST /api/v1/export            → USUA_PERM_EXPEDIENTE
       (`services/document-service/app/routers/export.py`)
    2. Importar                    — POST /api/v1/import             → USUA_PERM_EXPEDIENTE
       (`services/document-service/app/routers/import_.py`)
    3. Carga masiva de documentos  — POST/GET /api/v1/batch/documents → PERM_RADI
       (`services/document-service/app/routers/batch.py`)
    4. Carga masiva de expedientes — POST/GET /api/v1/batch/expedientes → USUA_PERM_EXPEDIENTE
       (`services/archive-service/app/routers/batch.py`)

  La pantalla se abre con el permiso MENOS restrictivo que da acceso a alguna
  pestaña (USUA_PERM_EXPEDIENTE || PERM_RADI); cada pestaña se filtra además
  con su propio permiso — así un radicador (solo PERM_RADI) ve "Carga masiva
  de documentos" sin ver las otras tres, y viceversa.

  Puntos críticos de esta pantalla (ver informe de cierre de Fase 8):
    - La importación es TODO-O-NADA: un 422 trae en `detail` el nombre exacto
      del archivo/checksum que falló. Ese texto se muestra TAL CUAL en el
      paso "Resultado" — nunca se colapsa en "no se pudo importar".
    - El sondeo de los jobs de carga masiva va SIEMPRE contra el proxy propio
      (D-05); si la consulta de estado falla, se declara explícitamente (no
      se pinta "0 procesados" ni se congela el último dato como si siguiera
      vigente).
    - La carga masiva es una escritura en bloque sobre documentos/expedientes
      oficiales: se exige un modal de confirmación con el número exacto de
      ítems antes de ejecutar.

  RBAC: cada pestaña se filtra por su permiso real ($permissions); el backend
  re-valida cada endpoint de forma independiente (defensa en profundidad,
  D-05/D-14).
-->
<script>
  import { onMount, onDestroy } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Stepper from "$lib/components/ui/Stepper.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";

  // Gate real por pestaña — ver comentario de cabecera. "Carga masiva de
  // documentos" exige PERM_RADI (document-service); las otras tres exigen
  // USUA_PERM_EXPEDIENTE (export/import en document-service, batch de
  // expedientes en archive-service).
  $: hasExpedientePerm = $permissions.includes("USUA_PERM_EXPEDIENTE");
  $: hasRadiPerm = $permissions.includes("PERM_RADI");
  $: hasPerm = hasExpedientePerm || hasRadiPerm;

  // ---------------------------------------------------------------------------
  // Pestañas
  // ---------------------------------------------------------------------------
  const ALL_TABS = [
    { id: "exportar", label: "Exportar", icon: "fas fa-file-export", permKey: "expediente" },
    { id: "importar", label: "Importar", icon: "fas fa-file-import", permKey: "expediente" },
    {
      id: "lote-documentos",
      label: "Carga masiva de documentos",
      icon: "fas fa-layer-group",
      permKey: "radi",
    },
    {
      id: "lote-expedientes",
      label: "Carga masiva de expedientes",
      icon: "fas fa-folder-plus",
      permKey: "expediente",
    },
  ];
  $: TABS = ALL_TABS.filter((tab) =>
    tab.permKey === "radi" ? hasRadiPerm : hasExpedientePerm,
  );
  let activeTab = "exportar";
  // Si la pestaña activa deja de estar visible (permisos aún no resueltos al
  // montar, o cambian en caliente), reencuadra a la primera pestaña visible.
  $: if (TABS.length > 0 && !TABS.some((t) => t.id === activeTab)) {
    activeTab = TABS[0].id;
  }

  const DOC_TYPE_OPTIONS = [
    { value: "E", label: "Entrada" },
    { value: "S", label: "Salida" },
    { value: "I", label: "Interno" },
  ];

  // =============================================================================
  // 1) EXPORTAR
  // =============================================================================
  let expComentario = "";
  let expYear = "";
  let expDocType = "";
  let expLoading = false;
  let expSummary = null; // { count, excluded }
  let expError = "";

  async function exportarPaquete() {
    expLoading = true;
    expError = "";
    expSummary = null;
    try {
      const body = { comentario: expComentario.trim() || undefined };
      if (expYear) body.year = Number(expYear);
      if (expDocType) body.doc_type = expDocType;

      const res = await fetch("/admin/interoperabilidad/api/exportar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        expError = errBody.detail ?? `No se pudo exportar (HTTP ${res.status}).`;
        return;
      }

      const count = res.headers.get("X-OrpycaMCP-Export-Count");
      const excluded = res.headers.get("X-OrpycaMCP-Export-Excluded");
      const disposition = res.headers.get("Content-Disposition") ?? "";
      const match = /filename="?([^"]+)"?/.exec(disposition);
      const filename = match?.[1] ?? "orpyca-export.zip";

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);

      expSummary = {
        count: count !== null ? Number(count) : null,
        excluded: excluded !== null ? Number(excluded) : null,
      };
      notifySuccess("Exportación generada y descargada.");
    } catch {
      expError = "No se pudo conectar al servidor de exportación.";
    } finally {
      expLoading = false;
    }
  }

  // =============================================================================
  // 2) IMPORTAR — Stepper subir → validar → confirmar → resultado
  // =============================================================================
  const IMPORT_STEPS = ["Subir", "Validación", "Confirmar", "Resultado"];
  let importStep = 0;
  /** @type {File|null} */
  let importFile = null;
  let importFileError = "";
  let importConfirmChecked = false;
  let importLoading = false;
  /** @type {import('$lib/types/api.js').ImportResult|null} */
  let importResult = null;
  /** El `detail` EXACTO del 422 (qué archivo/checksum falló y por qué) — se
   * muestra tal cual, nunca se resume. */
  let importFailureDetail = "";
  let importConnectionError = "";

  function resetImport() {
    importStep = 0;
    importFile = null;
    importFileError = "";
    importConfirmChecked = false;
    importResult = null;
    importFailureDetail = "";
    importConnectionError = "";
  }

  /** Verificación rápida client-side de la firma ZIP ("PK") — NO sustituye la
   * validación de fixity/esquema del backend (esa es la única autoridad);
   * solo evita subir un archivo que evidentemente no es un ZIP. */
  function magicHeaderIsZip(bytes) {
    return bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b;
  }

  async function onImportFileChange(e) {
    importFileError = "";
    const file = e.target.files?.[0] ?? null;
    importFile = null;
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      importFileError = "Seleccione un archivo .zip.";
      return;
    }
    const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
    if (!magicHeaderIsZip(head)) {
      importFileError = "El archivo no tiene una cabecera ZIP válida.";
      return;
    }
    importFile = file;
  }

  function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MiB`;
  }

  function importGoNext() {
    if (importStep === 0 && !importFile) {
      importFileError = "Seleccione un paquete ZIP antes de continuar.";
      return;
    }
    importStep = Math.min(importStep + 1, IMPORT_STEPS.length - 1);
  }

  function importGoBack() {
    importStep = Math.max(importStep - 1, 0);
  }

  async function ejecutarImportacion() {
    if (!importFile) return;
    importLoading = true;
    importResult = null;
    importFailureDetail = "";
    importConnectionError = "";
    try {
      const res = await fetch("/admin/interoperabilidad/api/importar", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: importFile,
      });
      const data = await res.json().catch(() => ({}));

      if (res.status === 422) {
        // Todo-o-nada: se muestra el detalle EXACTO (qué archivo/checksum
        // falló) — nunca un mensaje genérico de "importación fallida".
        importFailureDetail = data.detail ?? "El paquete no pasó la validación.";
      } else if (!res.ok) {
        importConnectionError = data.detail ?? `No se pudo importar (HTTP ${res.status}).`;
      } else {
        importResult = data;
        notifySuccess(`Importación completada: ${data.importados} radicado(s) ingresado(s).`);
      }
    } catch {
      importConnectionError = "No se pudo conectar al servidor de importación.";
    } finally {
      importLoading = false;
      importStep = IMPORT_STEPS.length - 1;
    }
  }

  // =============================================================================
  // Sondeo de jobs de carga masiva (compartido documentos/expedientes)
  // =============================================================================

  /**
   * Consulta una vez el proxy propio de sondeo. `kind` decide qué proxy usar;
   * NUNCA se llama al gateway directamente desde el cliente (D-05) — siempre
   * /admin/interoperabilidad/api/lotes/<kind>/<jobId>.
   *
   * Devuelve `{ pollError, status }`: si `pollError` no está vacío la
   * consulta FALLÓ (se declara explícitamente, sin sintetizar un `status`) —
   * distingue "no se pudo consultar" de "aún sin resultados".
   */
  async function pollJobStatusOnce(kind, jobId) {
    try {
      const res = await fetch(`/admin/interoperabilidad/api/lotes/${kind}/${jobId}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return {
          pollError: data.detail ?? `No se pudo consultar el estado (HTTP ${res.status}).`,
          status: undefined,
        };
      }
      return { pollError: "", status: data };
    } catch {
      return { pollError: "No se pudo consultar el estado del job (sin conexión).", status: undefined };
    }
  }

  // Nota Svelte 4 (bug D-##, revisión UX 2026-08-02): mutar propiedades de un
  // objeto plano nunca reasignado NO invalida la reactividad — el panel de
  // progreso se congelaba y jamás mostraba el resultado. Por eso el estado de
  // cada job vive en variables `let` de nivel de componente, reasignadas
  // directamente (nunca mutadas en sitio).
  let docsJobId = null;
  /** @type {import('$lib/types/api.js').BatchJobStatusResponse|null} */
  let docsJobStatus = null;
  let docsJobPollError = "";
  let docsJobTimer = null;

  async function docsPollOnce() {
    if (!docsJobId) return;
    const result = await pollJobStatusOnce("documentos", docsJobId);
    if (result.pollError) {
      docsJobPollError = result.pollError;
      return;
    }
    docsJobPollError = "";
    docsJobStatus = result.status;
    if (docsJobStatus?.status === "completed" || docsJobStatus?.status === "failed") {
      docsJobStop();
    }
  }
  function docsJobStart(jobId) {
    docsJobStop();
    docsJobId = jobId;
    docsJobStatus = null;
    docsJobPollError = "";
    docsPollOnce();
    docsJobTimer = setInterval(docsPollOnce, 2000);
  }
  function docsJobStop() {
    if (docsJobTimer) {
      clearInterval(docsJobTimer);
      docsJobTimer = null;
    }
  }

  let expJobId = null;
  /** @type {import('$lib/types/api.js').BatchJobStatusResponse|null} */
  let expJobStatus = null;
  let expJobPollError = "";
  let expJobTimer = null;

  async function expPollOnce() {
    if (!expJobId) return;
    const result = await pollJobStatusOnce("expedientes", expJobId);
    if (result.pollError) {
      expJobPollError = result.pollError;
      return;
    }
    expJobPollError = "";
    expJobStatus = result.status;
    if (expJobStatus?.status === "completed" || expJobStatus?.status === "failed") {
      expJobStop();
    }
  }
  function expJobStart(jobId) {
    expJobStop();
    expJobId = jobId;
    expJobStatus = null;
    expJobPollError = "";
    expPollOnce();
    expJobTimer = setInterval(expPollOnce, 2000);
  }
  function expJobStop() {
    if (expJobTimer) {
      clearInterval(expJobTimer);
      expJobTimer = null;
    }
  }

  onDestroy(() => {
    docsJobStop();
    expJobStop();
  });

  // =============================================================================
  // 3) CARGA MASIVA DE DOCUMENTOS
  // =============================================================================
  function blankDocRow() {
    return {
      doc_type: "E",
      subject: "",
      doc_class: "",
      dest_dept: "",
      sender_name: "",
      sender_entity: "",
      pages: "",
      observations: "",
    };
  }

  let loteDocs = [blankDocRow()];
  let loteDocsSubmitting = false;
  let loteDocsConfirmOpen = false;
  let loteDocsValidationError = "";

  function agregarDocRow() {
    if (loteDocs.length >= 1000) return;
    loteDocs = [...loteDocs, blankDocRow()];
  }
  function quitarDocRow(idx) {
    loteDocs = loteDocs.filter((_, i) => i !== idx);
    if (loteDocs.length === 0) loteDocs = [blankDocRow()];
  }

  function abrirConfirmLoteDocs() {
    loteDocsValidationError = "";
    const invalid = loteDocs.some((d) => !d.doc_type || !d.subject.trim());
    if (invalid) {
      loteDocsValidationError = "Cada documento requiere tipo (E/S/I) y asunto.";
      return;
    }
    loteDocsConfirmOpen = true;
  }

  async function crearLoteDocs() {
    loteDocsSubmitting = true;
    try {
      const documents = loteDocs.map((d) => ({
        doc_type: d.doc_type,
        subject: d.subject.trim(),
        doc_class: d.doc_class.trim() || null,
        dest_dept: d.dest_dept.trim() || null,
        sender_name: d.sender_name.trim() || null,
        sender_entity: d.sender_entity.trim() || null,
        pages: d.pages ? Number(d.pages) : null,
        observations: d.observations.trim() || null,
      }));

      const res = await fetch("/admin/interoperabilidad/api/lotes/documentos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ documents }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(data.detail ?? `No se pudo crear el lote (HTTP ${res.status}).`);
        return;
      }
      notifySuccess(`Lote creado: ${documents.length} documento(s) en proceso.`);
      loteDocsConfirmOpen = false;
      loteDocs = [blankDocRow()];
      docsJobStart(data.job_id);
    } catch {
      notifyError("No se pudo conectar al servidor de carga masiva.");
    } finally {
      loteDocsSubmitting = false;
    }
  }

  // =============================================================================
  // 4) CARGA MASIVA DE EXPEDIENTES
  // =============================================================================
  function blankExpRow() {
    return { name: "", description: "", tracking_numbers: "" };
  }

  let loteExps = [blankExpRow()];
  let loteExpsSubmitting = false;
  let loteExpsConfirmOpen = false;
  let loteExpsValidationError = "";

  function agregarExpRow() {
    if (loteExps.length >= 100) return;
    loteExps = [...loteExps, blankExpRow()];
  }
  function quitarExpRow(idx) {
    loteExps = loteExps.filter((_, i) => i !== idx);
    if (loteExps.length === 0) loteExps = [blankExpRow()];
  }

  function abrirConfirmLoteExps() {
    loteExpsValidationError = "";
    const invalid = loteExps.some((e) => !e.name.trim());
    if (invalid) {
      loteExpsValidationError = "Cada expediente requiere un nombre.";
      return;
    }
    loteExpsConfirmOpen = true;
  }

  async function crearLoteExps() {
    loteExpsSubmitting = true;
    try {
      const expedientes = loteExps.map((e) => ({
        name: e.name.trim(),
        description: e.description.trim() || null,
        tracking_numbers: e.tracking_numbers
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      }));

      const res = await fetch("/admin/interoperabilidad/api/lotes/expedientes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expedientes }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(data.detail ?? `No se pudo crear el lote (HTTP ${res.status}).`);
        return;
      }
      notifySuccess(`Lote creado: ${expedientes.length} expediente(s) en proceso.`);
      loteExpsConfirmOpen = false;
      loteExps = [blankExpRow()];
      expJobStart(data.job_id);
    } catch {
      notifyError("No se pudo conectar al servidor de carga masiva.");
    } finally {
      loteExpsSubmitting = false;
    }
  }

  const JOB_STATUS_CHIP = {
    pending: "pending",
    processing: "pending",
    completed: "success",
    failed: "error",
  };
  const JOB_STATUS_LABEL = {
    pending: "Pendiente",
    processing: "Procesando",
    completed: "Completado",
    failed: "Con errores",
  };

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Interoperabilidad" }]);
  });
</script>

<svelte:head>
  <title>Interoperabilidad — OrpycaMCP</title>
</svelte:head>

<div class="interop-page">
  <header class="interop-page__header">
    <h1 class="interop-page__title">Interoperabilidad</h1>
    <p class="interop-page__subtitle">
      Exportación e importación de paquetes interoperables de radicados (E11) y carga masiva de
      documentos y expedientes.
    </p>
  </header>

  {#if !hasPerm}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso"
      description="Se requiere el permiso USUA_PERM_EXPEDIENTE (exportar/importar/lotes de expedientes) o PERM_RADI (carga masiva de documentos) para usar esta pantalla."
    />
  {:else}
    <div class="interop-tabs" role="tablist" aria-label="Secciones de interoperabilidad">
      {#each TABS as tab (tab.id)}
        <button
          type="button"
          role="tab"
          id="tab-{tab.id}"
          aria-selected={activeTab === tab.id}
          aria-controls="panel-{tab.id}"
          class="interop-tabs__btn"
          class:interop-tabs__btn--active={activeTab === tab.id}
          on:click={() => (activeTab = tab.id)}
        >
          <i class={tab.icon} aria-hidden="true"></i>
          {tab.label}
        </button>
      {/each}
    </div>

    <!-- =====================================================================
         Pestaña: Exportar
         ===================================================================== -->
    {#if activeTab === "exportar"}
      <div
        id="panel-exportar"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="tab-exportar"
        class="interop-panel"
      >
        <p class="interop-panel__hint">
          Genera un paquete ZIP interoperable (índice JSON Schema + manifiesto + anexos). Solo
          incluye radicados que su clearance autoriza a leer; los excluidos por nivel de seguridad
          se reportan como conteo, no se listan (Ley 1712, no-read-up).
        </p>

        <div class="interop-form">
          <FormField
            id="exp-comentario"
            label="Comentario (opcional)"
            type="textarea"
            rows={2}
            bind:value={expComentario}
            placeholder="Motivo de la exportación…"
          />
          <div class="interop-form__row">
            <FormField
              id="exp-year"
              label="Año (opcional)"
              type="number"
              bind:value={expYear}
              placeholder="2024"
            />
            <FormField
              id="exp-doc-type"
              label="Tipo (opcional)"
              type="select"
              bind:value={expDocType}
              placeholder="Todos"
              options={DOC_TYPE_OPTIONS}
            />
          </div>
        </div>

        {#if expError}
          <p class="interop-alert interop-alert--error" role="alert">
            <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            {expError}
          </p>
        {/if}

        {#if expSummary}
          <p class="interop-alert interop-alert--success" role="status">
            <i class="fas fa-circle-check" aria-hidden="true"></i>
            {expSummary.count ?? "—"} radicado(s) exportado(s){#if expSummary.excluded}, {expSummary.excluded}
              excluido(s) por nivel de seguridad{/if}.
          </p>
        {/if}

        <Button icon="fas fa-file-export" loading={expLoading} on:click={exportarPaquete}>
          Exportar y descargar ZIP
        </Button>
      </div>
    {/if}

    <!-- =====================================================================
         Pestaña: Importar (Stepper)
         ===================================================================== -->
    {#if activeTab === "importar"}
      <div
        id="panel-importar"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="tab-importar"
        class="interop-panel"
      >
        <Stepper steps={IMPORT_STEPS} bind:current={importStep} canGoBack={!importLoading} />

        {#if importStep === 0}
          <div class="interop-step">
            <label class="interop-file-label" for="import-file">
              <i class="fas fa-file-zipper" aria-hidden="true"></i>
              Paquete ZIP a importar
            </label>
            <input
              id="import-file"
              type="file"
              accept=".zip,application/zip"
              class="interop-file-input"
              on:change={onImportFileChange}
              aria-describedby={importFileError ? "import-file-error" : undefined}
            />
            {#if importFile}
              <p class="interop-file-info">
                <i class="fas fa-circle-check" aria-hidden="true"></i>
                {importFile.name} ({formatBytes(importFile.size)})
              </p>
            {/if}
            {#if importFileError}
              <p id="import-file-error" class="interop-alert interop-alert--error" role="alert">
                {importFileError}
              </p>
            {/if}
            <div class="interop-step__actions">
              <Button icon="fas fa-arrow-right" on:click={importGoNext}>Siguiente</Button>
            </div>
          </div>
        {:else if importStep === 1}
          <div class="interop-step">
            <h2 class="interop-step__title">Validación de integridad (fixity)</h2>
            <p class="interop-panel__hint">
              El backend verificará, ANTES de escribir cualquier dato: el esquema de cada radicado
              contra el esquema de confianza del servicio, y el <strong
                >SHA-256 de cada binario</strong
              >
              contra el declarado en el manifiesto. Si un solo archivo falla, la importación completa
              se aborta — no queda estado parcial. Esta verificación ocurre al confirmar en el siguiente
              paso; no hay un paso de "solo validar" independiente en el backend.
            </p>
            <p class="interop-file-info">
              <i class="fas fa-file-zipper" aria-hidden="true"></i>
              {importFile?.name} ({importFile ? formatBytes(importFile.size) : "—"})
            </p>
            <div class="interop-step__actions">
              <Button variant="ghost" icon="fas fa-arrow-left" on:click={importGoBack}>Atrás</Button
              >
              <Button icon="fas fa-arrow-right" on:click={importGoNext}>Siguiente</Button>
            </div>
          </div>
        {:else if importStep === 2}
          <div class="interop-step">
            <h2 class="interop-step__title">Confirmar importación</h2>
            <p class="interop-alert interop-alert--warning" role="alert">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              Esta acción es una escritura oficial: reingresará en este tenant los radicados contenidos
              en <strong>{importFile?.name}</strong>, preservando su número original. Un radicado
              que ya exista (mismo número) se omite sin sobreescribir; uno por encima de su nivel de
              autorización se omite sin ingerir.
            </p>
            <label class="interop-checkbox">
              <input type="checkbox" bind:checked={importConfirmChecked} />
              Confirmo que deseo ejecutar la importación de este paquete.
            </label>
            <div class="interop-step__actions">
              <Button
                variant="ghost"
                icon="fas fa-arrow-left"
                on:click={importGoBack}
                disabled={importLoading}
              >
                Atrás
              </Button>
              <Button
                icon="fas fa-file-import"
                loading={importLoading}
                disabled={!importConfirmChecked}
                on:click={ejecutarImportacion}
              >
                Importar
              </Button>
            </div>
          </div>
        {:else if importStep === 3}
          <div class="interop-step">
            <h2 class="interop-step__title">Resultado</h2>

            {#if importFailureDetail}
              <!-- Punto crítico #1: se muestra el detalle EXACTO del backend
                   (qué archivo/checksum falló), nunca un mensaje genérico. -->
              <div class="interop-alert interop-alert--error" role="alert">
                <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
                <div>
                  <strong>El paquete no pasó la validación.</strong>
                  <p class="interop-alert__detail">{importFailureDetail}</p>
                </div>
              </div>
            {:else if importConnectionError}
              <p class="interop-alert interop-alert--error" role="alert">
                <i class="fas fa-plug-circle-xmark" aria-hidden="true"></i>
                {importConnectionError}
              </p>
            {:else if importResult}
              <dl class="interop-result">
                <div class="interop-result__row">
                  <dt>Importados</dt>
                  <dd>{importResult.importados}</dd>
                </div>
                <div class="interop-result__row">
                  <dt>Omitidos — ya existían</dt>
                  <dd>{importResult.omitidos_existentes}</dd>
                </div>
                <div class="interop-result__row">
                  <dt>Omitidos — por nivel de seguridad</dt>
                  <dd>{importResult.omitidos_clearance}</dd>
                </div>
                <div class="interop-result__row">
                  <dt>Relaciones reconstruidas</dt>
                  <dd>{importResult.relaciones_reconstruidas}</dd>
                </div>
              </dl>
              {#if importResult.antecedentes_no_resueltos?.length > 0}
                <div class="interop-alert interop-alert--warning" role="status">
                  <i class="fas fa-link-slash" aria-hidden="true"></i>
                  <div>
                    <strong>Antecedentes no resueltos</strong>
                    <p class="interop-alert__detail">
                      Los siguientes radicados se ingirieron sin poder reconstruir su antecedente
                      declarado (el número referenciado no existe en este tenant):
                    </p>
                    <ul class="interop-list" role="list">
                      {#each importResult.antecedentes_no_resueltos as tn (tn)}
                        <li role="listitem"><code>{tn}</code></li>
                      {/each}
                    </ul>
                  </div>
                </div>
              {/if}
            {/if}

            <div class="interop-step__actions">
              <Button variant="secondary" icon="fas fa-rotate-left" on:click={resetImport}>
                Nueva importación
              </Button>
            </div>
          </div>
        {/if}
      </div>
    {/if}

    <!-- =====================================================================
         Pestaña: Carga masiva de documentos
         ===================================================================== -->
    {#if activeTab === "lote-documentos"}
      <div
        id="panel-lote-documentos"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="tab-lote-documentos"
        class="interop-panel"
      >
        <p class="interop-panel__hint">
          Crea hasta 1000 radicados en un solo lote. El procesamiento es asíncrono: se consulta el
          progreso cada 2 segundos.
        </p>

        {#if loteDocsValidationError}
          <p class="interop-alert interop-alert--error" role="alert">{loteDocsValidationError}</p>
        {/if}

        <div class="interop-rows" role="list" aria-label="Documentos a crear en el lote">
          {#each loteDocs as row, idx (idx)}
            <div class="interop-row" role="listitem">
              <span class="interop-row__index" aria-hidden="true">{idx + 1}</span>
              <div class="interop-row__fields">
                <FormField
                  id="doc-{idx}-tipo"
                  label="Tipo"
                  type="select"
                  bind:value={row.doc_type}
                  required
                  options={DOC_TYPE_OPTIONS}
                />
                <FormField id="doc-{idx}-asunto" label="Asunto" bind:value={row.subject} required />
                <FormField
                  id="doc-{idx}-clase"
                  label="Clase documental (opcional)"
                  bind:value={row.doc_class}
                />
                <FormField
                  id="doc-{idx}-dependencia"
                  label="Dependencia destino (opcional)"
                  bind:value={row.dest_dept}
                />
                <FormField
                  id="doc-{idx}-remitente"
                  label="Remitente (opcional)"
                  bind:value={row.sender_name}
                />
                <FormField
                  id="doc-{idx}-entidad"
                  label="Entidad remitente (opcional)"
                  bind:value={row.sender_entity}
                />
                <FormField
                  id="doc-{idx}-paginas"
                  label="Páginas (opcional)"
                  type="number"
                  bind:value={row.pages}
                />
                <FormField
                  id="doc-{idx}-obs"
                  label="Observaciones (opcional)"
                  type="textarea"
                  rows={2}
                  bind:value={row.observations}
                />
              </div>
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-trash"
                aria-label="Quitar documento {idx + 1}"
                on:click={() => quitarDocRow(idx)}
              >
                Quitar
              </Button>
            </div>
          {/each}
        </div>

        <div class="interop-rows__actions">
          <Button
            variant="ghost"
            icon="fas fa-plus"
            on:click={agregarDocRow}
            disabled={loteDocs.length >= 1000}
          >
            Agregar documento
          </Button>
          <Button icon="fas fa-layer-group" on:click={abrirConfirmLoteDocs}>
            Crear {loteDocs.length} documento{loteDocs.length === 1 ? "" : "s"}
          </Button>
        </div>

        {#if docsJobId}
          <div class="interop-job" aria-live="polite">
            <h3 class="interop-step__title">Progreso del lote</h3>
            {#if docsJobPollError}
              <p class="interop-alert interop-alert--error" role="alert">
                <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
                No se pudo consultar el estado: {docsJobPollError}
                {#if docsJobStatus}
                  (último estado conocido abajo, puede no estar vigente)
                {/if}
              </p>
            {/if}
            {#if docsJobStatus}
              {@const s = docsJobStatus}
              <div class="interop-job__summary">
                <StatusChip
                  status={JOB_STATUS_CHIP[s.status] ?? "neutral"}
                  label={JOB_STATUS_LABEL[s.status] ?? s.status}
                  dot
                />
                <span
                  >{s.processed_items} / {s.total_items} procesados · {s.failed_items} con error</span
                >
              </div>
              {#if s.items?.length > 0}
                <ul class="interop-job__items" role="list">
                  {#each s.items as item (item.item_index)}
                    <li class="interop-job__item" role="listitem">
                      <StatusChip
                        status={item.status === "success" ? "success" : "error"}
                        label={item.status === "success" ? "OK" : "Error"}
                      />
                      <span>#{item.item_index + 1}</span>
                      {#if item.status === "success"}
                        <code>{item.result_id}</code>
                      {:else}
                        <span class="interop-job__item-error">{item.error_message}</span>
                      {/if}
                    </li>
                  {/each}
                </ul>
              {:else if !docsJobPollError}
                <p class="interop-panel__hint">Aún sin resultados de ítems (procesando).</p>
              {/if}
            {:else if !docsJobPollError}
              <p class="interop-panel__hint">Consultando estado…</p>
            {/if}
          </div>
        {/if}
      </div>

      <Modal
        open={loteDocsConfirmOpen}
        title="Confirmar carga masiva"
        size="sm"
        on:close={() => (loteDocsConfirmOpen = false)}
      >
        <p>
          Va a crear <strong>{loteDocs.length}</strong> documento{loteDocs.length === 1 ? "" : "s"} nuevo{loteDocs.length ===
          1
            ? ""
            : "s"}
          en este tenant. Esta acción no puede deshacerse desde la interfaz.
        </p>
        <svelte:fragment slot="footer">
          <Button variant="ghost" on:click={() => (loteDocsConfirmOpen = false)}>Cancelar</Button>
          <Button icon="fas fa-check" loading={loteDocsSubmitting} on:click={crearLoteDocs}
            >Confirmar y crear</Button
          >
        </svelte:fragment>
      </Modal>
    {/if}

    <!-- =====================================================================
         Pestaña: Carga masiva de expedientes
         ===================================================================== -->
    {#if activeTab === "lote-expedientes"}
      <div
        id="panel-lote-expedientes"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="tab-lote-expedientes"
        class="interop-panel"
      >
        <p class="interop-panel__hint">
          Crea hasta 100 expedientes en un solo lote. Los números de radicado (separados por coma)
          se vinculan al crear el expediente.
        </p>

        {#if loteExpsValidationError}
          <p class="interop-alert interop-alert--error" role="alert">{loteExpsValidationError}</p>
        {/if}

        <div class="interop-rows" role="list" aria-label="Expedientes a crear en el lote">
          {#each loteExps as row, idx (idx)}
            <div class="interop-row" role="listitem">
              <span class="interop-row__index" aria-hidden="true">{idx + 1}</span>
              <div class="interop-row__fields">
                <FormField id="exp-{idx}-nombre" label="Nombre" bind:value={row.name} required />
                <FormField
                  id="exp-{idx}-desc"
                  label="Descripción (opcional)"
                  bind:value={row.description}
                />
                <FormField
                  id="exp-{idx}-tracking"
                  label="Números de radicado a vincular (opcional, separados por coma)"
                  bind:value={row.tracking_numbers}
                  placeholder="2024-ICETEX-E-001234, 2024-ICETEX-S-005678"
                />
              </div>
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-trash"
                aria-label="Quitar expediente {idx + 1}"
                on:click={() => quitarExpRow(idx)}
              >
                Quitar
              </Button>
            </div>
          {/each}
        </div>

        <div class="interop-rows__actions">
          <Button
            variant="ghost"
            icon="fas fa-plus"
            on:click={agregarExpRow}
            disabled={loteExps.length >= 100}
          >
            Agregar expediente
          </Button>
          <Button icon="fas fa-folder-plus" on:click={abrirConfirmLoteExps}>
            Crear {loteExps.length} expediente{loteExps.length === 1 ? "" : "s"}
          </Button>
        </div>

        {#if expJobId}
          <div class="interop-job" aria-live="polite">
            <h3 class="interop-step__title">Progreso del lote</h3>
            {#if expJobPollError}
              <p class="interop-alert interop-alert--error" role="alert">
                <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
                No se pudo consultar el estado: {expJobPollError}
                {#if expJobStatus}
                  (último estado conocido abajo, puede no estar vigente)
                {/if}
              </p>
            {/if}
            {#if expJobStatus}
              {@const s = expJobStatus}
              <div class="interop-job__summary">
                <StatusChip
                  status={JOB_STATUS_CHIP[s.status] ?? "neutral"}
                  label={JOB_STATUS_LABEL[s.status] ?? s.status}
                  dot
                />
                <span
                  >{s.processed_items} / {s.total_items} procesados · {s.failed_items} con error</span
                >
              </div>
              {#if s.items?.length > 0}
                <ul class="interop-job__items" role="list">
                  {#each s.items as item (item.item_index)}
                    <li class="interop-job__item" role="listitem">
                      <StatusChip
                        status={item.status === "success" ? "success" : "error"}
                        label={item.status === "success" ? "OK" : "Error"}
                      />
                      <span>#{item.item_index + 1}</span>
                      {#if item.status === "success"}
                        <code>{item.expediente_code ?? item.result_id}</code>
                      {:else}
                        <span class="interop-job__item-error">{item.error_message}</span>
                      {/if}
                    </li>
                  {/each}
                </ul>
              {:else if !expJobPollError}
                <p class="interop-panel__hint">Aún sin resultados de ítems (procesando).</p>
              {/if}
            {:else if !expJobPollError}
              <p class="interop-panel__hint">Consultando estado…</p>
            {/if}
          </div>
        {/if}
      </div>

      <Modal
        open={loteExpsConfirmOpen}
        title="Confirmar carga masiva"
        size="sm"
        on:close={() => (loteExpsConfirmOpen = false)}
      >
        <p>
          Va a crear <strong>{loteExps.length}</strong> expediente{loteExps.length === 1 ? "" : "s"} nuevo{loteExps.length ===
          1
            ? ""
            : "s"}
          en este tenant. Esta acción no puede deshacerse desde la interfaz.
        </p>
        <svelte:fragment slot="footer">
          <Button variant="ghost" on:click={() => (loteExpsConfirmOpen = false)}>Cancelar</Button>
          <Button icon="fas fa-check" loading={loteExpsSubmitting} on:click={crearLoteExps}
            >Confirmar y crear</Button
          >
        </svelte:fragment>
      </Modal>
    {/if}
  {/if}
</div>

<style lang="scss">
  .interop-page {
    padding: var(--op-space-5);
    max-width: 900px;

    &__header {
      margin-bottom: var(--op-space-5);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-1);
    }

    &__subtitle {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }
  }

  .interop-tabs {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-1);
    border-bottom: 1px solid var(--op-border);
    margin-bottom: var(--op-space-4);

    &__btn {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-2) var(--op-space-3);
      min-height: 44px;
      font-size: var(--op-font-sm);
      font-family: var(--op-font-family);
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--op-text-secondary);
      cursor: pointer;

      &:hover {
        color: var(--op-primary-dark);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }

      &--active {
        color: var(--op-primary-dark);
        border-bottom-color: var(--op-primary);
        font-weight: var(--op-weight-bold);
      }
    }
  }

  .interop-panel {
    &__hint {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0 0 var(--op-space-4);
      line-height: 1.5;
    }
  }

  .interop-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
    margin-bottom: var(--op-space-4);

    &__row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-3);

      @media (max-width: 480px) {
        grid-template-columns: 1fr;
      }
    }
  }

  .interop-alert {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    padding: var(--op-space-3);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    margin: 0 0 var(--op-space-4);
    border: 1px solid transparent;

    &__detail {
      margin: var(--op-space-1) 0 0;
    }

    // Pares bg/fg verificados AA en _tokens.scss (op-error-bg↔op-error-fg
    // ~7.9:1, op-warning-bg↔op-warning-fg ~5.96:1) — NUNCA --op-text-secondary
    // sobre estos fondos (por debajo de AA, ver instrucciones de Fase 8).
    &--error {
      background: var(--op-error-bg);
      border-color: var(--op-error);
      color: var(--op-error-fg);
    }

    &--success {
      background: var(--op-primary-light);
      border-color: var(--op-primary);
      color: var(--op-primary-dark);
    }

    &--warning {
      background: var(--op-warning-bg);
      border-color: var(--op-warning);
      color: var(--op-warning-fg);
    }
  }

  .interop-step {
    margin-top: var(--op-space-5);

    &__title {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-3);
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
      margin-top: var(--op-space-4);
    }
  }

  .interop-file-label {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-weight: var(--op-weight-medium);
    color: var(--op-text);
    margin-bottom: var(--op-space-2);
  }

  .interop-file-input {
    display: block;
    font-family: var(--op-font-family);
    font-size: var(--op-font-sm);
    color: var(--op-text);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  .interop-file-info {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-primary-dark);
    margin: var(--op-space-2) 0 0;
  }

  .interop-checkbox {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    cursor: pointer;

    input {
      width: 18px;
      height: 18px;
      accent-color: var(--op-primary);

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }
  }

  .interop-result {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);
    margin: 0 0 var(--op-space-4);

    &__row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: var(--op-space-2);
      padding: var(--op-space-2) 0;
      border-bottom: 1px solid var(--op-border);
      font-size: var(--op-font-sm);

      dt {
        color: var(--op-text-secondary);
      }

      dd {
        margin: 0;
        font-weight: var(--op-weight-bold);
        color: var(--op-text);
      }
    }
  }

  .interop-list {
    margin: var(--op-space-2) 0 0;
    padding-left: var(--op-space-4);
    font-size: var(--op-font-xs);

    code {
      font-family: monospace;
    }
  }

  .interop-rows {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-3);
  }

  .interop-row {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-3);
    padding: var(--op-space-3);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-surface);

    &__index {
      flex-shrink: 0;
      width: 24px;
      height: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
    }

    &__fields {
      flex: 1;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--op-space-3);
      min-width: 0;
    }
  }

  .interop-rows__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-5);
  }

  .interop-job {
    border-top: 1px solid var(--op-border);
    padding-top: var(--op-space-4);

    &__summary {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin-bottom: var(--op-space-3);
    }

    &__items {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      max-height: 320px;
      overflow-y: auto;
    }

    &__item {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-1) var(--op-space-2);
      font-size: var(--op-font-xs);
      border-bottom: 1px solid var(--op-border);

      code {
        font-family: monospace;
        color: var(--op-text-secondary);
      }
    }

    &__item-error {
      color: var(--op-error);
    }
  }
</style>
