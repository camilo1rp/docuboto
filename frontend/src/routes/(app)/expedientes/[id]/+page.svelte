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
  Workspace de expediente (T-10, RF-UI-05).

  Una vista con 5 pestañas internas:
    0 — Resumen       → metadatos + radicados vinculados
    1 — Índice (E15)  → IndiceResponse (JSON y descarga XML)
    2 — Anexos (E07)  → archivos embebidos en los radicados del expediente
    3 — Flujo/Hist.   → eventos del expediente (hoja de ruta E02 §9)
    4 — Físico (E17)  → unidades de conservación + signatura

  Accesibilidad:
    - Patrón ARIA tablist/tab/tabpanel (WCAG 2.1 §4.1.2)
    - Navegación por teclado: flechas ← → entre tabs, Tab hacia el panel
    - Labels y roles ARIA en todos los elementos interactivos

  Acciones guiadas (cierre/transferencia):
    - Solo visibles si el usuario tiene el permiso y el estado lo permite
    - Siempre presentan un Modal de confirmación antes de ejecutar
    - El backend re-valida; la UI no es la única línea de defensa (ADR-013)
-->
<script>
  import { invalidateAll } from "$app/navigation";

  import Card from "$lib/components/ui/Card.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";

  import { permissions } from "$lib/stores/auth.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";
  // Las acciones (cerrar/transferir, descargar XML/anexos) usan los endpoints locales
  // /expedientes/[id]/api/* que leen la cookie httpOnly en el servidor (D-05).
  // El token nunca alcanza el JS del cliente.

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Datos del servidor
  // ---------------------------------------------------------------------------
  $: expediente = data.expediente;
  $: facetas = data.facetas;

  // ---------------------------------------------------------------------------
  // Autorización (espejo del RBAC; backend re-valida siempre)
  // ---------------------------------------------------------------------------
  /** @param {string} p */
  function can(p) {
    return $permissions.includes(p);
  }

  $: canClose = can("CERRAR_EXPEDIENTE") && expediente?.status === "open";
  $: canTransfer = can("TRANSFERIR_EXPEDIENTE") && expediente?.status === "closed";

  // ---------------------------------------------------------------------------
  // Pestañas (tablist ARIA)
  // ---------------------------------------------------------------------------
  const TABS = [
    { id: "resumen", label: "Resumen", icon: "fa-folder-open" },
    { id: "indice", label: "Índice (E15)", icon: "fa-list-ol" },
    { id: "anexos", label: "Anexos", icon: "fa-paperclip" },
    { id: "flujo", label: "Flujo / Historial", icon: "fa-route" },
    { id: "fisico", label: "Ubicación física", icon: "fa-archive" },
  ];
  let activeTab = "resumen";

  function handleTabKeydown(e, tabId) {
    const idx = TABS.findIndex((t) => t.id === tabId);
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const next = TABS[(idx + 1) % TABS.length];
      activeTab = next.id;
      document.getElementById(`tab-${next.id}`)?.focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const prev = TABS[(idx - 1 + TABS.length) % TABS.length];
      activeTab = prev.id;
      document.getElementById(`tab-${prev.id}`)?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      activeTab = TABS[0].id;
      document.getElementById(`tab-${TABS[0].id}`)?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      const last = TABS[TABS.length - 1];
      activeTab = last.id;
      document.getElementById(`tab-${last.id}`)?.focus();
    }
  }

  // ---------------------------------------------------------------------------
  // Modales de acción guiada
  // ---------------------------------------------------------------------------
  let showCloseModal = false;
  let showTransferModal = false;
  let actionLoading = false;

  async function handleClose() {
    actionLoading = true;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/close`, { method: "POST" });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Expediente ${expediente.code} cerrado correctamente.`);
      await invalidateAll();
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo cerrar el expediente.");
    } finally {
      actionLoading = false;
      showCloseModal = false;
    }
  }

  async function handleTransfer() {
    actionLoading = true;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/transfer`, { method: "POST" });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Expediente ${expediente.code} transferido correctamente.`);
      await invalidateAll();
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo transferir el expediente.");
    } finally {
      actionLoading = false;
      showTransferModal = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Descarga de anexo (URL pre-firmada via gateway — E07)
  // ---------------------------------------------------------------------------
  /**
   * Obtiene la URL pre-firmada de descarga vía el endpoint local y la abre en nueva pestaña.
   * El endpoint /expedientes/[id]/api/download/[fileId] lee la cookie httpOnly en el servidor.
   * @param {string} fileId
   * @param {string} filename
   */
  async function handleDownloadAnexo(fileId, filename) {
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/download/${fileId}`);
      const result = await res.json();
      if (!res.ok) throw result;
      const url = result?.url ?? result?.download_url ?? result;
      if (typeof url !== "string") throw new Error("La respuesta no contiene una URL válida.");
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.target = "_blank";
      a.rel = "noreferrer";
      a.click();
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo obtener el enlace de descarga.");
    }
  }

  // ---------------------------------------------------------------------------
  // Descarga XML del índice
  // ---------------------------------------------------------------------------
  let xmlDownloading = false;

  async function handleDownloadXml() {
    xmlDownloading = true;
    try {
      // El endpoint local /expedientes/[id]/api/indice-xml lee la cookie httpOnly.
      const res = await fetch(`/expedientes/${expediente.id}/api/indice-xml`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw err;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `indice_${expediente.code}.xml`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo obtener el XML del índice.");
    } finally {
      xmlDownloading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers de formato
  // ---------------------------------------------------------------------------
  /** @param {string|null|undefined} iso */
  function fmtDate(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  }

  /** @param {string|null|undefined} iso */
  function fmtDatetime(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("es-CO", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  /** @param {number|null|undefined} bytes */
  function fmtBytes(bytes) {
    if (!bytes) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  const STATUS_LABELS = {
    open: "Abierto",
    closed: "Cerrado",
    transferred: "Transferido",
  };
  $: statusLabel = STATUS_LABELS[expediente?.status] ?? expediente?.status;

  // Recopilar todos los anexos desde los radicados vinculados (no hay endpoint agregado)
  $: allAnexos = (expediente?.radicados ?? []).flatMap((r) =>
    (r.anexos ?? []).map((a) => ({ ...a, tracking_number: r.tracking_number })),
  );
</script>

<svelte:head>
  <title>Expediente {expediente?.code ?? ""} — OrpycaMCP</title>
</svelte:head>

<!-- =========================================================================
     Workspace
     ========================================================================= -->
<div class="exp-workspace">
  <!-- Cabecera -->
  <div class="exp-workspace__header">
    <div class="exp-workspace__header-info">
      <nav aria-label="Ubicación" class="exp-workspace__breadcrumb">
        <a href="/bandeja">Bandeja</a>
        <span aria-hidden="true"> / </span>
        <a href="/expedientes">Expedientes</a>
        <span aria-hidden="true"> / </span>
        <span aria-current="page">{expediente?.code}</span>
      </nav>

      <h1 class="exp-workspace__title">
        {expediente?.name}
        <StatusChip status={expediente?.status ?? "neutral"} label={statusLabel} dot />
      </h1>

      {#if expediente?.description}
        <p class="exp-workspace__desc">{expediente.description}</p>
      {/if}
    </div>

    <div class="exp-workspace__actions" role="group" aria-label="Acciones del expediente">
      {#if canClose}
        <Button variant="secondary" on:click={() => (showCloseModal = true)}>
          <i class="fas fa-lock" aria-hidden="true"></i> Cerrar expediente
        </Button>
      {/if}
      {#if canTransfer}
        <Button variant="secondary" on:click={() => (showTransferModal = true)}>
          <i class="fas fa-exchange-alt" aria-hidden="true"></i> Transferir expediente
        </Button>
      {/if}
    </div>
  </div>

  <!-- Tablist -->
  <div class="exp-tabs" role="tablist" aria-label="Facetas del expediente">
    {#each TABS as tab (tab.id)}
      <button
        id="tab-{tab.id}"
        class="exp-tabs__tab"
        class:exp-tabs__tab--active={activeTab === tab.id}
        role="tab"
        aria-selected={activeTab === tab.id}
        aria-controls="panel-{tab.id}"
        tabindex={activeTab === tab.id ? 0 : -1}
        on:click={() => (activeTab = tab.id)}
        on:keydown={(e) => handleTabKeydown(e, tab.id)}
      >
        <i class="fas {tab.icon}" aria-hidden="true"></i>
        <span>{tab.label}</span>
      </button>
    {/each}
  </div>

  <!-- Panels -->
  <div class="exp-workspace__panels">
    <!-- -----------------------------------------------------------------------
         Panel 0 — Resumen
         ----------------------------------------------------------------------- -->
    <div
      id="panel-resumen"
      role="tabpanel"
      aria-labelledby="tab-resumen"
      hidden={activeTab !== "resumen"}
      tabindex="0"
      class="exp-panel"
    >
      <div class="exp-panel__grid">
        <!-- Metadatos principales -->
        <Card>
          <h2 slot="header" class="op-card-title">Metadatos del expediente</h2>
          <dl class="exp-meta-list">
            <div class="exp-meta-list__item">
              <dt>Código</dt>
              <dd>{expediente?.code ?? "—"}</dd>
            </div>
            <div class="exp-meta-list__item">
              <dt>Estado</dt>
              <dd>
                <StatusChip status={expediente?.status ?? "neutral"} label={statusLabel} dot />
              </dd>
            </div>
            <div class="exp-meta-list__item">
              <dt>Fecha apertura</dt>
              <dd>{fmtDate(expediente?.opened_at)}</dd>
            </div>
            {#if expediente?.closed_at}
              <div class="exp-meta-list__item">
                <dt>Fecha cierre</dt>
                <dd>{fmtDate(expediente.closed_at)}</dd>
              </div>
            {/if}
            <div class="exp-meta-list__item">
              <dt>Actualizado</dt>
              <dd>{fmtDate(expediente?.updated_at)}</dd>
            </div>
            {#if expediente?.trd_serie_id}
              <div class="exp-meta-list__item">
                <dt>Serie TRD</dt>
                <dd class="exp-meta-list__code">{expediente.trd_serie_id}</dd>
              </div>
            {/if}
            {#if expediente?.disposition}
              <div class="exp-meta-list__item">
                <dt>Disposición</dt>
                <dd>
                  {expediente.disposition.disposition_label ??
                    expediente.disposition.disposition_code ??
                    "—"}
                  {#if expediente.disposition.fin_archivo_central}
                    <span class="exp-meta-list__sub">
                      hasta {fmtDate(expediente.disposition.fin_archivo_central)}
                    </span>
                  {/if}
                </dd>
              </div>
            {/if}
          </dl>

          {#if expediente?.metadata && Object.keys(expediente.metadata).length}
            <h3 class="exp-section-subtitle">Metadatos adicionales</h3>
            <dl class="exp-meta-list">
              {#each Object.entries(expediente.metadata) as [k, v] (k)}
                <div class="exp-meta-list__item">
                  <dt>{k}</dt>
                  <dd>{String(v)}</dd>
                </div>
              {/each}
            </dl>
          {/if}
        </Card>

        <!-- Radicados vinculados -->
        <Card>
          <h2 slot="header" class="op-card-title">
            Radicados vinculados
            <span class="exp-count-badge">{expediente?.radicados?.length ?? 0}</span>
          </h2>
          {#if !expediente?.radicados?.length}
            <EmptyState
              icon="fa-inbox"
              title="Sin radicados"
              description="Este expediente no tiene radicados vinculados todavía."
            />
          {:else}
            <div class="exp-radicado-list" role="list" aria-label="Radicados del expediente">
              {#each expediente.radicados as rad (rad.tracking_number)}
                <div class="exp-radicado-list__item" role="listitem">
                  <a
                    href="/bandeja?q={rad.tracking_number}"
                    class="exp-radicado-list__number"
                    aria-label="Ver radicado {rad.tracking_number}"
                  >
                    {rad.tracking_number}
                  </a>
                  <span class="exp-radicado-list__date">{fmtDate(rad.added_at)}</span>
                  {#if rad.anexos?.length}
                    <span
                      class="exp-radicado-list__anexos"
                      title="{rad.anexos.length} anexo(s)"
                      aria-label="{rad.anexos.length} anexos"
                    >
                      <i class="fas fa-paperclip" aria-hidden="true"></i>
                      {rad.anexos.length}
                    </span>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </Card>
      </div>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 1 — Índice electrónico E15
         ----------------------------------------------------------------------- -->
    <div
      id="panel-indice"
      role="tabpanel"
      aria-labelledby="tab-indice"
      hidden={activeTab !== "indice"}
      tabindex="0"
      class="exp-panel"
    >
      {#if facetas.indice.error}
        <Card>
          <EmptyState
            icon="fa-exclamation-circle"
            title="No se pudo cargar el índice"
            description={facetas.indice.error.detail}
          />
        </Card>
      {:else if !facetas.indice.data}
        <EmptyState
          icon="fa-list-ol"
          title="Índice no disponible"
          description="El índice electrónico no ha sido generado para este expediente."
        />
      {:else}
        {@const indice = facetas.indice.data}
        <Card>
          <h2 slot="header" class="op-card-title">
            Índice electrónico
            <span class="exp-indice-meta">
              v{indice.version} · {indice.num_documentos} doc(s)
              <StatusChip
                status={indice.estado === "vigente" ? "success" : "warning"}
                label={indice.estado}
              />
            </span>
          </h2>

          <div class="exp-indice-toolbar">
            <Button
              variant="secondary"
              size="sm"
              on:click={handleDownloadXml}
              disabled={xmlDownloading}
            >
              {#if xmlDownloading}
                <i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Descargando…
              {:else}
                <i class="fas fa-file-code" aria-hidden="true"></i> Descargar XML
              {/if}
            </Button>
            <dl class="exp-indice-summary">
              <div>
                <dt>SHA-256</dt>
                <dd class="exp-indice-hash">{indice.xml_sha256}</dd>
              </div>
              <div>
                <dt>Algoritmo</dt>
                <dd>{indice.algoritmo}</dd>
              </div>
              <div>
                <dt>Generado</dt>
                <dd>{fmtDatetime(indice.generado_ts)}</dd>
              </div>
            </dl>
          </div>

          {#if !indice.items?.length}
            <EmptyState
              icon="fa-list-ol"
              title="Sin documentos en el índice"
              description="El índice existe pero no contiene documentos."
            />
          {:else}
            <div class="exp-table-wrap" role="region" aria-label="Documentos del índice">
              <table class="exp-table" aria-label="Ítems del índice electrónico">
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Documento</th>
                    <th scope="col">Tipología</th>
                    <th scope="col">Archivado</th>
                    <th scope="col">Huella (SHA-256)</th>
                  </tr>
                </thead>
                <tbody>
                  {#each indice.items as item (item.document_id)}
                    <tr>
                      <td>{item.orden}</td>
                      <td>
                        {#if item.titulo}
                          {item.titulo}
                        {:else}
                          <span class="exp-uuid">{item.document_id}</span>
                        {/if}
                      </td>
                      <td>{item.tipologia ?? "—"}</td>
                      <td>{item.fecha_archivado ? fmtDate(item.fecha_archivado) : "—"}</td>
                      <td>
                        {#if item.valor_huella}
                          <code class="exp-hash" title={item.valor_huella}
                            >{item.valor_huella.slice(0, 12)}…</code
                          >
                        {:else}
                          —
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </Card>
      {/if}
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 2 — Anexos E07
         ----------------------------------------------------------------------- -->
    <div
      id="panel-anexos"
      role="tabpanel"
      aria-labelledby="tab-anexos"
      hidden={activeTab !== "anexos"}
      tabindex="0"
      class="exp-panel"
    >
      <Card>
        <h2 slot="header" class="op-card-title">
          Anexos
          <span class="exp-count-badge">{allAnexos.length}</span>
        </h2>

        {#if !allAnexos.length}
          <EmptyState
            icon="fa-paperclip"
            title="Sin anexos"
            description="Los radicados de este expediente no tienen archivos adjuntos registrados."
          />
        {:else}
          <p class="exp-panel__info">
            <i class="fas fa-info-circle" aria-hidden="true"></i>
            Los anexos se obtienen de los radicados vinculados. El enlace de descarga usa la URL presignada
            del storage-service (GET /api/v1/storage/files/{"{"}file_id{"}"}/download).
          </p>
          <div class="exp-table-wrap" role="region" aria-label="Listado de anexos">
            <table class="exp-table" aria-label="Anexos del expediente">
              <thead>
                <tr>
                  <th scope="col">Radicado</th>
                  <th scope="col">Archivo</th>
                  <th scope="col">Tipo</th>
                  <th scope="col">Tamaño</th>
                  <th scope="col">Principal</th>
                  <th scope="col">Acción</th>
                </tr>
              </thead>
              <tbody>
                {#each allAnexos as anexo (anexo.file_id)}
                  <tr>
                    <td>
                      <a href="/bandeja?q={anexo.tracking_number}" class="exp-link"
                        >{anexo.tracking_number}</a
                      >
                    </td>
                    <td class="exp-filename" title={anexo.filename}>{anexo.filename}</td>
                    <td>{anexo.mime_type ?? "—"}</td>
                    <td>{fmtBytes(anexo.file_size)}</td>
                    <td>
                      {#if anexo.es_principal}
                        <i class="fas fa-check-circle exp-icon--success" aria-label="Principal"></i>
                      {:else}
                        <span aria-label="No es el documento principal">—</span>
                      {/if}
                    </td>
                    <td>
                      <!-- Descarga vía URL pre-firmada (GET /api/v1/storage/files/{id}/download) -->
                      <button
                        type="button"
                        class="exp-action-link"
                        aria-label="Descargar {anexo.filename}"
                        on:click={() => handleDownloadAnexo(anexo.file_id, anexo.filename)}
                      >
                        <i class="fas fa-download" aria-hidden="true"></i> Descargar
                      </button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Card>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 3 — Flujo / Historial (eventos del expediente E02 §9)
         ----------------------------------------------------------------------- -->
    <div
      id="panel-flujo"
      role="tabpanel"
      aria-labelledby="tab-flujo"
      hidden={activeTab !== "flujo"}
      tabindex="0"
      class="exp-panel"
    >
      {#if facetas.eventos.error}
        <Card>
          <EmptyState
            icon="fa-exclamation-circle"
            title="No se pudo cargar el historial"
            description={facetas.eventos.error.detail}
          />
        </Card>
      {:else if !facetas.eventos.data?.length}
        <EmptyState
          icon="fa-route"
          title="Sin eventos registrados"
          description="Este expediente aún no tiene eventos en su hoja de ruta."
        />
      {:else}
        <Card>
          <h2 slot="header" class="op-card-title">
            Hoja de ruta del expediente
            <span class="exp-count-badge">{facetas.eventos.data.length}</span>
          </h2>

          <p class="exp-panel__info">
            <i class="fas fa-info-circle" aria-hidden="true"></i>
            Hoja de ruta del ciclo de vida del expediente (E02 §9). El historial de flujo por radicado
            se accede desde la bandeja de cada radicado.
          </p>

          <ol class="exp-timeline" aria-label="Eventos del expediente">
            {#each facetas.eventos.data as evento (evento.id ?? evento.ts)}
              <li class="exp-timeline__item">
                <div class="exp-timeline__marker" aria-hidden="true"></div>
                <div class="exp-timeline__content">
                  <div class="exp-timeline__header">
                    <strong class="exp-timeline__accion">{evento.accion}</strong>
                    <time class="exp-timeline__ts" datetime={evento.ts}>
                      {fmtDatetime(evento.ts)}
                    </time>
                  </div>
                  {#if evento.comentario}
                    <p class="exp-timeline__comment">{evento.comentario}</p>
                  {/if}
                  {#if evento.actor}
                    <span class="exp-timeline__actor">
                      <i class="fas fa-user" aria-hidden="true"></i>
                      {evento.actor}
                    </span>
                  {/if}
                  {#if evento.detalles && Object.keys(evento.detalles).length}
                    <details class="exp-timeline__details">
                      <summary>Ver detalles</summary>
                      <pre class="exp-timeline__json">{JSON.stringify(
                          evento.detalles,
                          null,
                          2,
                        )}</pre>
                    </details>
                  {/if}
                </div>
              </li>
            {/each}
          </ol>
        </Card>
      {/if}
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 4 — Ubicación física E17
         ----------------------------------------------------------------------- -->
    <div
      id="panel-fisico"
      role="tabpanel"
      aria-labelledby="tab-fisico"
      hidden={activeTab !== "fisico"}
      tabindex="0"
      class="exp-panel"
    >
      {#if facetas.ubicacion.error}
        <Card>
          <EmptyState
            icon="fa-exclamation-circle"
            title="No se pudo cargar la ubicación"
            description={facetas.ubicacion.error.detail}
          />
        </Card>
      {:else if !facetas.ubicacion.data?.length}
        <EmptyState
          icon="fa-archive"
          title="Sin ubicación física registrada"
          description="Este expediente no ha sido ubicado en ninguna unidad de conservación."
        />
      {:else}
        <Card>
          <h2 slot="header" class="op-card-title">Unidades de conservación</h2>
          <div class="exp-table-wrap" role="region" aria-label="Ubicaciones físicas">
            <table class="exp-table" aria-label="Unidades de conservación del expediente">
              <thead>
                <tr>
                  <th scope="col">Signatura</th>
                  <th scope="col">Unidad (ID)</th>
                  <th scope="col">Folio inicio</th>
                  <th scope="col">Folio fin</th>
                </tr>
              </thead>
              <tbody>
                {#each facetas.ubicacion.data as unidad (unidad.unidad_id)}
                  <tr>
                    <td>{unidad.signatura ?? "—"}</td>
                    <td class="exp-uuid">{unidad.unidad_id}</td>
                    <td>{unidad.folio_inicio ?? "—"}</td>
                    <td>{unidad.folio_fin ?? "—"}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </Card>
      {/if}
    </div>
  </div>
</div>

<!-- =========================================================================
     Modal: Confirmar cierre
     ========================================================================= -->
<Modal
  open={showCloseModal}
  title="Cerrar expediente"
  size="sm"
  on:close={() => (showCloseModal = false)}
>
  <p>
    ¿Confirma el cierre del expediente <strong>{expediente?.code}</strong>? Su estado pasará a
    <strong>Cerrado</strong> y se materializará la disposición TRD.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showCloseModal = false)} disabled={actionLoading}
      >Cancelar</Button
    >
    <Button
      variant="primary"
      on:click={handleClose}
      disabled={actionLoading}
      loading={actionLoading}>Confirmar cierre</Button
    >
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: Confirmar transferencia
     ========================================================================= -->
<Modal
  open={showTransferModal}
  title="Transferir expediente"
  size="sm"
  on:close={() => (showTransferModal = false)}
>
  <p>
    ¿Confirma la transferencia del expediente <strong>{expediente?.code}</strong>? Su estado pasará
    a <strong>Transferido</strong>.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showTransferModal = false)} disabled={actionLoading}
      >Cancelar</Button
    >
    <Button
      variant="primary"
      on:click={handleTransfer}
      disabled={actionLoading}
      loading={actionLoading}>Confirmar transferencia</Button
    >
  </svelte:fragment>
</Modal>

<style lang="scss">
  // =========================================================================
  // Workspace
  // =========================================================================

  .exp-workspace {
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }

  .exp-workspace__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--op-space-4);
    padding: var(--op-space-5) var(--op-space-5) var(--op-space-4);
    background: var(--op-surface);
    border-bottom: 1px solid var(--op-border);
    flex-wrap: wrap;
  }

  .exp-workspace__breadcrumb {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin-bottom: var(--op-space-2);

    a {
      color: var(--op-primary);
      text-decoration: none;

      &:hover {
        text-decoration: underline;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        border-radius: 2px;
      }
    }

    span[aria-hidden] {
      margin: 0 var(--op-space-1);
    }
  }

  .exp-workspace__title {
    font-size: var(--op-font-lg);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-1);
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    flex-wrap: wrap;
  }

  .exp-workspace__desc {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin: 0;
  }

  .exp-workspace__actions {
    display: flex;
    gap: var(--op-space-2);
    flex-shrink: 0;
    flex-wrap: wrap;
    align-items: flex-start;
    padding-top: var(--op-space-5);
  }

  // =========================================================================
  // Tablist
  // =========================================================================

  .exp-tabs {
    display: flex;
    background: var(--op-surface);
    border-bottom: 2px solid var(--op-border);
    overflow-x: auto;
    scrollbar-width: none;
    flex-shrink: 0;

    &::-webkit-scrollbar {
      display: none;
    }
  }

  .exp-tabs__tab {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-3) var(--op-space-4);
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    cursor: pointer;
    white-space: nowrap;
    transition:
      color 0.15s,
      border-color 0.15s;

    i {
      font-size: var(--op-font-xs);
    }

    &:hover {
      color: var(--op-text);
      background: var(--op-bg);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }

    &--active {
      color: var(--op-primary);
      border-bottom-color: var(--op-primary);
      font-weight: var(--op-weight-bold);
    }
  }

  // =========================================================================
  // Panels
  // =========================================================================

  .exp-workspace__panels {
    flex: 1;
    padding: var(--op-space-5);
    background: var(--op-bg);
  }

  .exp-panel {
    outline: none;

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 4px;
      border-radius: var(--op-radius-sm);
    }

    &__grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-4);

      @media (max-width: 900px) {
        grid-template-columns: 1fr;
      }
    }

    &__info {
      font-size: var(--op-font-sm);
      color: var(--op-info-fg);
      background: var(--op-info-bg);
      border-left: 3px solid var(--op-info);
      padding: var(--op-space-2) var(--op-space-3);
      border-radius: 0 var(--op-radius-sm) var(--op-radius-sm) 0;
      margin-bottom: var(--op-space-3);

      i {
        margin-right: var(--op-space-1);
      }
    }
  }

  // =========================================================================
  // Metadatos
  // =========================================================================

  .exp-meta-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--op-space-2) var(--op-space-4);
    margin: 0;

    @media (max-width: 640px) {
      grid-template-columns: 1fr;
    }

    &__item {
      display: flex;
      flex-direction: column;
      gap: 2px;

      dt {
        font-size: var(--op-font-xs);
        color: var(--op-text-secondary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      dd {
        font-size: var(--op-font-sm);
        color: var(--op-text);
        font-weight: var(--op-weight-medium);
        margin: 0;
        display: flex;
        align-items: center;
        gap: var(--op-space-1);
        flex-wrap: wrap;
      }
    }

    &__code {
      font-family: monospace;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      word-break: break-all;
    }

    &__sub {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }
  }

  .exp-section-subtitle {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-text-secondary);
    margin: var(--op-space-4) 0 var(--op-space-2);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  // =========================================================================
  // Radicados
  // =========================================================================

  .exp-count-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--op-primary-light);
    color: var(--op-primary-dark);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-bold);
    border-radius: var(--op-radius-full);
    min-width: 20px;
    height: 20px;
    padding: 0 var(--op-space-1);
    margin-left: var(--op-space-2);
  }

  .exp-radicado-list {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__item {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-bg);
      border-radius: var(--op-radius-sm);
      border: 1px solid var(--op-border);
    }

    &__number {
      font-family: monospace;
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary);
      text-decoration: none;

      &:hover {
        text-decoration: underline;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        border-radius: 2px;
      }
    }

    &__date {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-left: auto;
    }

    &__anexos {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      display: flex;
      align-items: center;
      gap: 4px;
    }
  }

  // =========================================================================
  // Índice
  // =========================================================================

  .exp-indice-meta {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-normal);
    color: var(--op-text-secondary);
    margin-left: auto;
    flex-wrap: wrap;
  }

  .exp-indice-toolbar {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-4);
    margin-bottom: var(--op-space-4);
    flex-wrap: wrap;
  }

  .exp-indice-summary {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);

    div {
      display: flex;
      gap: var(--op-space-2);
    }

    dt {
      font-weight: var(--op-weight-medium);
    }
  }

  .exp-indice-hash {
    font-family: monospace;
    font-size: var(--op-font-xs);
    word-break: break-all;
  }

  // =========================================================================
  // Tablas
  // =========================================================================

  .exp-table-wrap {
    overflow-x: auto;
    border-radius: var(--op-radius-sm);
    border: 1px solid var(--op-border);
  }

  .exp-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-medium);
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: var(--op-space-2) var(--op-space-3);
      text-align: left;
      border-bottom: 1px solid var(--op-border);
    }

    td {
      padding: var(--op-space-2) var(--op-space-3);
      border-bottom: 1px solid var(--op-border);
      color: var(--op-text);
      vertical-align: middle;
    }

    tr:last-child td {
      border-bottom: none;
    }
    tr:hover td {
      background: var(--op-bg);
    }
  }

  .exp-hash {
    font-family: monospace;
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    cursor: help;
  }

  .exp-uuid {
    font-family: monospace;
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }

  .exp-filename {
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .exp-link,
  .exp-action-link {
    color: var(--op-primary);
    text-decoration: none;
    font-size: var(--op-font-xs);

    &:hover {
      text-decoration: underline;
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
      border-radius: 2px;
    }
  }

  .exp-icon--success {
    color: var(--op-success);
  }

  // =========================================================================
  // Timeline (eventos)
  // =========================================================================

  .exp-timeline {
    list-style: none;
    padding: 0;
    margin: 0;
    position: relative;

    &::before {
      content: "";
      position: absolute;
      left: 9px;
      top: 16px;
      bottom: 0;
      width: 2px;
      background: var(--op-border);
    }

    &__item {
      display: flex;
      gap: var(--op-space-3);
      padding-bottom: var(--op-space-4);
      position: relative;
    }

    &__marker {
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: var(--op-primary);
      border: 2px solid var(--op-surface);
      box-shadow: 0 0 0 2px var(--op-primary-light);
      flex-shrink: 0;
      margin-top: 2px;
      z-index: 1;
    }

    &__content {
      flex: 1;
      padding-bottom: var(--op-space-2);
      border-bottom: 1px solid var(--op-border);
    }

    &__header {
      display: flex;
      align-items: baseline;
      gap: var(--op-space-3);
      flex-wrap: wrap;
      margin-bottom: var(--op-space-1);
    }

    &__accion {
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }

    &__ts {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-left: auto;
    }

    &__comment {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: var(--op-space-1) 0;
    }

    &__actor {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
    }

    &__details {
      margin-top: var(--op-space-2);

      summary {
        font-size: var(--op-font-xs);
        color: var(--op-primary);
        cursor: pointer;

        &:focus-visible {
          outline: 2px solid var(--op-primary);
          outline-offset: 2px;
          border-radius: 2px;
        }
      }
    }

    &__json {
      font-size: var(--op-font-xs);
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-2);
      overflow-x: auto;
      margin-top: var(--op-space-1);
      white-space: pre-wrap;
      word-break: break-word;
    }
  }

  // =========================================================================
  // Card title helper (scoped :global para el slot del Card)
  // =========================================================================

  :global(.op-card-title) {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--op-space-2);
  }
</style>
