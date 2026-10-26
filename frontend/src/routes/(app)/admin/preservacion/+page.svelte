<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  /admin/preservacion — Administración de preservación digital (Fase 4,
  cierre de desfase API↔UI, E10, AGN 001/2024).

  Backend real verificado (`services/storage-service/app/routers/
  preservacion.py`, `services/archive-service/app/routers/indice.py`):

    - GET/PUT /preservacion/plan + GET /preservacion/plan/versions
      (RBAC USUA_PERM_EXPEDIENTE, storage-service) — plan tenant-wide,
      versionado (cada PUT crea versión nueva, nunca edita in place).
    - GET /expedientes/indices/pendientes-firma (archive-service) —
      visibilidad operacional de índices sin sellar XAdES, con reintento
      manual POST /expedientes/{id}/indice/firmar.
    - GET /expedientes/indices/pendientes-renovacion (archive-service) —
      visibilidad operacional de artefactos WORM (índice/acta/AIP) de
      Conservación Total cuya retención está agotada o en riesgo.

  CAPACIDADES DELIBERADAMENTE OMITIDAS (reportadas, no simuladas):
    - Empaquetado manual del AIP (`POST /preservacion/aip`) — el payload
      exige el inventario `documentos[]` (document_id + nombre + file_id)
      de TODOS los anexos del expediente, que solo archive-service conoce;
      esta pantalla no tiene ese contexto sin invadir `expedientes/**`
      (fuera de mi alcance en esta fase). El AIP/PREMIS de un expediente
      concreto ya se consulta en su pestaña "Preservación".
    - Protección WORM manual de un artefacto (`POST
      /preservacion/proteger-artefacto`) — requiere el `file_id` exacto del
      artefacto YA FIRMADO; no hay una fuente para ese dato fuera del
      cableado automático (sellado del índice / acta al cierre/recepción).
      La renovación de retención (`renovar-retencion-internal`) es,
      textualmente, "disparo SISTEMA únicamente" — sin ruta pública
      equivalente por diseño (remediación de seguridad declarada en el
      propio schema). Esta pantalla solo puede dar VISIBILIDAD sobre su
      estado, nunca un botón que la dispare.

  WORM es irreversible por definición: un objeto bloqueado no puede
  modificarse ni borrarse hasta que expire su retención — la única acción
  de escritura real de esta pantalla (reintentar el sellado del índice) NO
  es en sí misma la protección WORM, así que no exige el mismo aviso; se
  deja documentado aquí para quien extienda esta pantalla en el futuro.

  RBAC: lectura con USUA_PERM_EXPEDIENTE (mismo gate que el backend);
  reintento de sellado con USUA_PERM_EXPEDIENTE + PERM_FIRMA (backend exige
  AMBOS). D-05: toda escritura va a `/admin/preservacion/api/*`.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { permissions, permissionLevels } from "$lib/stores/auth.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  import SellosArchivo from "$lib/components/features/preservacion/SellosArchivo.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  onMount(() => {
    setBreadcrumbs([
      { label: "Panel principal", href: "/dashboard" },
      { label: "Administración", href: "/admin" },
      { label: "Preservación digital" },
    ]);
  });

  // GET plan/versiones (`get_plan`/`list_plan_versions`, preservacion.py
  // líneas 80/116) son `min_crud=1` (default): presencia basta.
  $: canRead = $permissions.includes("USUA_PERM_EXPEDIENTE");
  // `set_plan` (PUT /plan, línea 99) exige
  // `require_permission("USUA_PERM_EXPEDIENTE", min_crud=3)` — antes solo
  // miraba presencia del permiso (A6, revisión UX).
  $: canWritePlan = ($permissionLevels["USUA_PERM_EXPEDIENTE"] ?? 0) >= 3;
  // Firmar índice va por archive-service `POST /indice/firmar`
  // (USUA_PERM_EXPEDIENTE Y PERM_FIRMA, indice.py líneas 194-195), ambos
  // `min_crud=1` (default) — no se toca.
  $: canFirmarIndice =
    $permissions.includes("USUA_PERM_EXPEDIENTE") && $permissions.includes("PERM_FIRMA");

  // ---------------------------------------------------------------------------
  // Tabs
  // ---------------------------------------------------------------------------
  // Sellos de archivo (C-6): el backend exige USUA_PERM_ADMIN con nivel >= 3
  // (403 sin él). La faceta ni se ofrece por debajo de ese nivel; `data.sellos`
  // llega `null` desde el loader en ese caso, y se comprueban ambos.
  $: veSellos = ($permissionLevels["USUA_PERM_ADMIN"] ?? 0) >= 3 && data.sellos != null;

  const TABS_BASE = [
    { id: "plan", label: "Plan de preservación", icon: "fa-clipboard-list" },
    { id: "firma", label: "Índices pendientes de firma", icon: "fa-file-signature" },
    { id: "worm", label: "Renovación WORM", icon: "fa-lock" },
  ];
  const TAB_SELLOS = { id: "sellos", label: "Sellos de archivo", icon: "fa-stamp" };
  $: TABS = veSellos ? [...TABS_BASE, TAB_SELLOS] : TABS_BASE;
  let activeTab = "plan";

  function handleTabKeydown(e, tabId) {
    const idx = TABS.findIndex((t) => t.id === tabId);
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const next = TABS[(idx + 1) % TABS.length];
      activeTab = next.id;
      document.getElementById(`prsv-tab-${next.id}`)?.focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const prev = TABS[(idx - 1 + TABS.length) % TABS.length];
      activeTab = prev.id;
      document.getElementById(`prsv-tab-${prev.id}`)?.focus();
    }
  }

  // ---------------------------------------------------------------------------
  // Faceta: plan (POR FACETA — error/loading independiente)
  // ---------------------------------------------------------------------------
  $: plan = data.plan?.data ?? null;
  $: planError = data.plan?.error ?? null;
  $: planNoDisponible = planError?.status === 404;
  $: planFalloReal = planError && planError.status !== 404;

  $: versions = data.versions?.data ?? [];
  $: versionsError = data.versions?.error ?? null;

  let planForm = {
    formatos_destino: "",
    formatos_origen: "",
    num_copias: "1",
    periodicidad_fixity_dias: "180",
    politica_migracion: "",
    contingencia: "",
  };
  let planFormInit = false;
  $: if (plan && !planFormInit) {
    planForm = {
      formatos_destino: (plan.formatos_destino ?? []).join(", "),
      formatos_origen: (plan.formatos_origen ?? []).join(", "),
      num_copias: String(plan.num_copias ?? 1),
      periodicidad_fixity_dias: String(plan.periodicidad_fixity_dias ?? 180),
      politica_migracion: plan.politica_migracion ?? "",
      contingencia: plan.contingencia ?? "",
    };
    planFormInit = true;
  }

  let planLocal = null;
  let versionsLocal = null;
  $: planEfectivo = planLocal ?? plan;
  $: versionsEfectivas = versionsLocal ?? versions;

  let savingPlan = false;

  async function guardarPlan() {
    savingPlan = true;
    try {
      const res = await fetch("/admin/preservacion/api/plan", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          formatos_destino: planForm.formatos_destino
            .split(",")
            .map((f) => f.trim())
            .filter(Boolean),
          formatos_origen: planForm.formatos_origen
            .split(",")
            .map((f) => f.trim())
            .filter(Boolean),
          num_copias: planForm.num_copias,
          periodicidad_fixity_dias: planForm.periodicidad_fixity_dias,
          politica_migracion: planForm.politica_migracion.trim() || undefined,
          contingencia: planForm.contingencia.trim() || undefined,
        }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw result;
      notifySuccess(`Nueva versión del plan de preservación creada (v${result.version}).`);
      planLocal = result;
      const vres = await fetch("/admin/preservacion/api/plan/versions");
      if (vres.ok) versionsLocal = await vres.json().catch(() => versionsEfectivas);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo actualizar el plan de preservación."));
    } finally {
      savingPlan = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Faceta: índices pendientes de firma
  // ---------------------------------------------------------------------------
  $: pendientesFirma = data.pendientesFirma?.data ?? [];
  $: pendientesFirmaError = data.pendientesFirma?.error ?? null;

  let pendientesFirmaLocal = null;
  $: pendientesFirmaEfectivas = pendientesFirmaLocal ?? pendientesFirma;
  //  `null` = no se sabe (el gateway no reenvió la cabecera); `0` = se sabe que
  //  ninguno. No se pintan igual: decir «0» sin saberlo sería afirmar que el
  //  compensador nunca ha tenido que actuar.
  $: selladoDiferido = data.selladoDiferido ?? null;

  /** @type {Record<string, boolean>} */
  let firmando = {};

  async function reintentarFirma(expedienteId) {
    firmando = { ...firmando, [expedienteId]: true };
    try {
      const res = await fetch(`/admin/preservacion/api/indices/${expedienteId}/firmar`, {
        method: "POST",
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeError(result, "No se pudo reintentar el sellado del índice."));
        return;
      }
      notifySuccess("Reintento de sellado del índice enviado correctamente.");
      const lres = await fetch("/admin/preservacion/api/indices/pendientes-firma?page=1&size=20");
      if (lres.ok) pendientesFirmaLocal = await lres.json().catch(() => pendientesFirmaEfectivas);
    } catch {
      notifyError("No se pudo conectar con el servidor para reintentar el sellado.");
    } finally {
      firmando = { ...firmando, [expedienteId]: false };
    }
  }

  // ---------------------------------------------------------------------------
  // Faceta: renovación WORM (solo lectura — ver nota en el encabezado)
  // ---------------------------------------------------------------------------
  $: pendientesRenovacion = data.pendientesRenovacion?.data ?? [];
  $: pendientesRenovacionError = data.pendientesRenovacion?.error ?? null;

  const TIPO_ARTEFACTO_LABELS = {
    indice: "Índice electrónico",
    acta_transferencia: "Acta de transferencia",
    aip: "Paquete AIP",
  };

  /** @param {string|null|undefined} iso */
  function fmtDatetime(iso) {
    if (!iso) return "—";
    return formatearFechaHora(iso);
  }
</script>

<svelte:head>
  <title>Preservación digital — Administración — Docuboto</title>
</svelte:head>

<div class="prsv-workspace">
  <div class="prsv-workspace__header">
    <h1 class="prsv-workspace__title">
      <i class="fas fa-box-archive" aria-hidden="true"></i>
      Administración de preservación digital
    </h1>
  </div>

  {#if !canRead}
    <Card>
      <EmptyState
        icon="fas fa-lock"
        title="Sin permiso"
        description="No tiene el permiso necesario (USUA_PERM_EXPEDIENTE) para consultar la administración de preservación."
      />
    </Card>
  {:else}
    <div class="prsv-tabs" role="tablist" aria-label="Facetas de preservación">
      {#each TABS as tab (tab.id)}
        <button
          id="prsv-tab-{tab.id}"
          class="prsv-tabs__tab"
          class:prsv-tabs__tab--active={activeTab === tab.id}
          role="tab"
          aria-selected={activeTab === tab.id}
          aria-controls="prsv-panel-{tab.id}"
          tabindex={activeTab === tab.id ? 0 : -1}
          on:click={() => (activeTab = tab.id)}
          on:keydown={(e) => handleTabKeydown(e, tab.id)}
        >
          <i class="fas {tab.icon}" aria-hidden="true"></i>
          <span>{tab.label}</span>
        </button>
      {/each}
    </div>

    <!-- ===================================================================
         Panel — Plan de preservación
         =================================================================== -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="prsv-panel-plan"
      role="tabpanel"
      aria-labelledby="prsv-tab-plan"
      hidden={activeTab !== "plan"}
      tabindex="0"
      class="prsv-panel"
    >
      <div class="prsv-panel__grid">
        <Card>
          <h2 slot="header" class="op-card-title">Plan vigente</h2>
          {#if planFalloReal}
            <EmptyState
              icon="fas fa-exclamation-triangle"
              title="No se pudo cargar el plan"
              description={mensajeError(planError, "Intenta recargar la página.")}
            />
          {:else if planNoDisponible && !planEfectivo}
            <EmptyState
              icon="fas fa-clipboard-list"
              title="Sin plan de preservación"
              description="Este tenant aún no tiene un plan de preservación digital registrado. Cree la primera versión con el formulario."
            />
          {:else if planEfectivo}
            <dl class="prsv-meta-list">
              <div class="prsv-meta-list__item">
                <dt>Versión</dt>
                <dd>{planEfectivo.version}</dd>
              </div>
              <div class="prsv-meta-list__item">
                <dt>Formatos destino</dt>
                <dd>{(planEfectivo.formatos_destino ?? []).join(", ") || "—"}</dd>
              </div>
              <div class="prsv-meta-list__item prsv-meta-list__item--wide">
                <dt>Formatos en riesgo (se migran)</dt>
                <dd>
                  {#if (planEfectivo.formatos_origen ?? []).length}
                    {planEfectivo.formatos_origen.join(", ")}
                  {:else}
                    Sin declarar — el barrido periódico no migra nada de esta entidad.
                  {/if}
                </dd>
              </div>
              <div class="prsv-meta-list__item">
                <dt>Copias</dt>
                <dd>{planEfectivo.num_copias}</dd>
              </div>
              <div class="prsv-meta-list__item">
                <dt>Periodicidad fixity</dt>
                <dd>{planEfectivo.periodicidad_fixity_dias} días</dd>
              </div>
              <div class="prsv-meta-list__item prsv-meta-list__item--wide">
                <dt>Política de migración</dt>
                <dd>{planEfectivo.politica_migracion ?? "—"}</dd>
              </div>
              <div class="prsv-meta-list__item prsv-meta-list__item--wide">
                <dt>Contingencia</dt>
                <dd>{planEfectivo.contingencia ?? "—"}</dd>
              </div>
              <div class="prsv-meta-list__item">
                <dt>Creado por</dt>
                <dd>{planEfectivo.creado_por ?? "—"}</dd>
              </div>
              <div class="prsv-meta-list__item">
                <dt>Fecha</dt>
                <dd>{fmtDatetime(planEfectivo.created_at)}</dd>
              </div>
            </dl>
          {:else}
            <Loader label="Cargando plan de preservación…" />
          {/if}
        </Card>

        <Card>
          <h2 slot="header" class="op-card-title">Historial de versiones</h2>
          {#if versionsError}
            <EmptyState
              icon="fas fa-exclamation-circle"
              title="No se pudo cargar el historial"
              description={mensajeError(versionsError, "Intenta recargar la página.")}
            />
          {:else if versionsEfectivas.length === 0}
            <EmptyState
              icon="fas fa-history"
              title="Sin versiones"
              description="Aún no se ha registrado ninguna versión del plan."
            />
          {:else}
            <ol class="prsv-timeline" aria-label="Versiones del plan de preservación">
              {#each versionsEfectivas as v (v.id)}
                <li class="prsv-timeline__item">
                  <div class="prsv-timeline__marker" aria-hidden="true"></div>
                  <div class="prsv-timeline__content">
                    <strong>Versión {v.version}</strong>
                    <span class="prsv-timeline__ts">{fmtDatetime(v.created_at)}</span>
                    {#if v.creado_por}
                      <span class="prsv-timeline__actor">{v.creado_por}</span>
                    {/if}
                  </div>
                </li>
              {/each}
            </ol>
          {/if}
        </Card>

        {#if canWritePlan}
          <Card>
            <h2 slot="header" class="op-card-title">Actualizar plan (crea una nueva versión)</h2>
            <p class="prsv-note">
              El plan es versionado: guardar aquí NO edita la versión vigente, crea una versión
              nueva y esa pasa a ser la vigente.
            </p>
            <div class="prsv-form">
              <FormField
                id="prsv-formatos"
                label="Formatos destino (separados por coma)"
                type="text"
                placeholder="pdf/a-2b, tiff"
                bind:value={planForm.formatos_destino}
              />
              <FormField
                id="prsv-origen"
                label="Formatos en riesgo de obsolescencia (tipos MIME separados por coma)"
                type="text"
                placeholder="application/msword, application/vnd.ms-excel"
                hint="El barrido periódico convierte a PDF/A los archivos de estos tipos. Vacío = no se migra nada. El original se conserva intacto."
                bind:value={planForm.formatos_origen}
              />
              <FormField
                id="prsv-copias"
                label="Número de copias"
                type="number"
                bind:value={planForm.num_copias}
              />
              <FormField
                id="prsv-periodicidad"
                label="Periodicidad de verificación de fixity (días)"
                type="number"
                bind:value={planForm.periodicidad_fixity_dias}
              />
              <FormField
                id="prsv-migracion"
                label="Política de migración"
                type="textarea"
                rows={3}
                bind:value={planForm.politica_migracion}
              />
              <FormField
                id="prsv-contingencia"
                label="Plan de contingencia"
                type="textarea"
                rows={3}
                bind:value={planForm.contingencia}
              />
              <div class="prsv-form__actions">
                <Button variant="primary" on:click={guardarPlan} disabled={savingPlan}>
                  {#if savingPlan}<i class="fas fa-spinner fa-spin" aria-hidden="true"></i>{/if}
                  Guardar nueva versión
                </Button>
              </div>
            </div>
          </Card>
        {/if}
      </div>
    </div>

    <!-- ===================================================================
         Panel — Índices pendientes de firma
         =================================================================== -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="prsv-panel-firma"
      role="tabpanel"
      aria-labelledby="prsv-tab-firma"
      hidden={activeTab !== "firma"}
      tabindex="0"
      class="prsv-panel"
    >
      <Card>
        <h2 slot="header" class="op-card-title">
          Índices electrónicos pendientes de sellado XAdES
        </h2>
        <!-- Una cola vacía no dice si nunca hubo cola o si el trabajo de
             reconciliación la vació. Ese trabajo, al firmar, resetea el
             contador de intentos a propósito para no dejar basura aquí — y
             con eso borra el único rastro de que hubo que reparar. Durante dos
             días el sellado del cierre falló SIEMPRE y esta pantalla dijo «sin
             pendientes» con toda la razón. Este número es el instrumento que
             faltaba: cuántos índices los acabó sellando el sistema y no el
             acto del cierre. -->
        {#if selladoDiferido !== null && selladoDiferido > 0}
          <p class="prsv-diferido" role="status">
            <i class="fas fa-clock-rotate-left" aria-hidden="true"></i>
            <span>
              <strong>{selladoDiferido.toLocaleString("es-CO")}</strong>
              {selladoDiferido === 1 ? "índice" : "índices"}
              {selladoDiferido === 1 ? "quedó sellado" : "quedaron sellados"} por el trabajo de reconciliación
              y no en el acto del cierre: en esos casos el sellado inmediato falló y el sello quedó a
              nombre del sistema, no de quien cerró el expediente. Un número que crece indica un fallo
              del sellado síncrono, no un incidente puntual.
            </span>
          </p>
        {/if}
        {#if pendientesFirmaError}
          <EmptyState
            icon="fas fa-exclamation-triangle"
            title="No se pudo cargar el listado"
            description={mensajeError(pendientesFirmaError, "Intenta recargar la página.")}
          />
        {:else if pendientesFirmaEfectivas.length === 0}
          <EmptyState
            icon="fas fa-circle-check"
            title="Sin pendientes"
            description="No hay índices electrónicos visibles para usted pendientes de sellado."
          />
        {:else}
          <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
          <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
          <div
            class="prsv-table-wrap"
            role="region"
            aria-label="Índices pendientes de sellado XAdES"
            tabindex="0"
          >
            <table class="table is-fullwidth is-hoverable">
              <caption class="is-sr-only">Índices pendientes de sellado XAdES</caption>
              <thead>
                <tr>
                  <th scope="col">Expediente</th>
                  <th scope="col">Versión</th>
                  <th scope="col">Intentos</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Último error</th>
                  <th scope="col"><span class="is-sr-only">Acciones</span></th>
                </tr>
              </thead>
              <tbody>
                {#each pendientesFirmaEfectivas as row (row.indice_id)}
                  <tr>
                    <td><code>{row.expediente_code}</code></td>
                    <td>{row.version}</td>
                    <td>{row.reconcile_intentos}</td>
                    <td>
                      {#if row.reconcile_agotado}
                        <StatusChip status="error" label="Agotado — requiere intervención" dot />
                      {:else}
                        <StatusChip status="warning" label="Pendiente" dot />
                      {/if}
                    </td>
                    <td class="prsv-cell-truncate">{row.reconcile_ultimo_error ?? "—"}</td>
                    <td>
                      {#if canFirmarIndice}
                        <Button
                          variant="secondary"
                          size="sm"
                          on:click={() => reintentarFirma(row.expediente_id)}
                          disabled={!!firmando[row.expediente_id]}
                        >
                          {#if firmando[row.expediente_id]}
                            <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
                          {/if}
                          Reintentar firma
                        </Button>
                      {/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Card>
    </div>

    <!-- ===================================================================
         Panel — Renovación WORM (solo lectura)
         =================================================================== -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="prsv-panel-worm"
      role="tabpanel"
      aria-labelledby="prsv-tab-worm"
      hidden={activeTab !== "worm"}
      tabindex="0"
      class="prsv-panel"
    >
      <Card>
        <div slot="header" class="prsv-worm-header">
          <h2 class="op-card-title">Artefactos WORM pendientes de renovación</h2>
          <span class="prsv-worm-header__note">
            <i class="fas fa-circle-info" aria-hidden="true"></i>
            Solo visibilidad — la renovación de retención WORM es un proceso de sistema (reconciliación
            automática), sin acción manual expuesta.
          </span>
        </div>
        {#if pendientesRenovacionError}
          <EmptyState
            icon="fas fa-exclamation-triangle"
            title="No se pudo cargar el listado"
            description={mensajeError(pendientesRenovacionError, "Intenta recargar la página.")}
          />
        {:else if pendientesRenovacion.length === 0}
          <EmptyState
            icon="fas fa-circle-check"
            title="Sin pendientes"
            description="No hay artefactos WORM visibles para usted pendientes de renovación."
          />
        {:else}
          <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
          <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
          <div
            class="prsv-table-wrap"
            role="region"
            aria-label="Artefactos WORM pendientes de renovación de retención"
            tabindex="0"
          >
            <table class="table is-fullwidth is-hoverable">
              <caption class="is-sr-only"
                >Artefactos WORM pendientes de renovación de retención</caption
              >
              <thead>
                <tr>
                  <th scope="col">Expediente</th>
                  <th scope="col">Artefacto</th>
                  <th scope="col">Retención hasta</th>
                  <th scope="col">Intentos</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Último error</th>
                </tr>
              </thead>
              <tbody>
                {#each pendientesRenovacion as row (`${row.expediente_id}-${row.tipo}`)}
                  <tr>
                    <td><code>{row.expediente_code}</code></td>
                    <td>{TIPO_ARTEFACTO_LABELS[row.tipo] ?? row.tipo}</td>
                    <td>{fmtDatetime(row.worm_retain_until)}</td>
                    <td>{row.worm_renovacion_intentos}</td>
                    <td>
                      {#if row.worm_renovacion_agotado}
                        <StatusChip status="error" label="Agotado — URGENTE" dot />
                      {:else}
                        <StatusChip status="warning" label="En riesgo" dot />
                      {/if}
                    </td>
                    <td class="prsv-cell-truncate">{row.worm_renovacion_ultimo_error ?? "—"}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Card>
    </div>

    {#if veSellos}
      <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
      <div
        id="prsv-panel-sellos"
        role="tabpanel"
        aria-labelledby="prsv-tab-sellos"
        hidden={activeTab !== "sellos"}
        tabindex="0"
        class="prsv-panel"
      >
        <Card>
          <SellosArchivo inicial={data.sellos} />
        </Card>
      </div>
    {/if}
  {/if}
</div>

<style lang="scss">
  .prsv-workspace {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
    padding: var(--op-space-5);

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
    }

    &__title {
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

  .prsv-tabs {
    display: flex;
    gap: var(--op-space-1);
    border-bottom: 1px solid var(--op-border);
    flex-wrap: wrap;

    &__tab {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      min-height: 44px;
      background: none;
      border: none;
      border-bottom: 3px solid transparent;
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-bold);
      cursor: pointer;

      &:hover {
        color: var(--op-primary-dark);
      }

      &--active {
        color: var(--op-primary-dark);
        border-bottom-color: var(--op-primary);
      }
    }
  }

  .prsv-panel {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .prsv-panel__grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: var(--op-space-4);
    align-items: start;
  }

  .prsv-meta-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: var(--op-space-3);

    &__item {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);

      &--wide {
        grid-column: 1 / -1;
      }

      dt {
        font-size: var(--op-font-xs);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--op-text-secondary);
      }

      dd {
        color: var(--op-text);
      }
    }
  }

  .prsv-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);

    &__actions {
      display: flex;
      justify-content: flex-end;
    }
  }

  .prsv-note {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin-bottom: var(--op-space-3);
  }

  .prsv-timeline {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    list-style: none;

    &__item {
      display: flex;
      gap: var(--op-space-3);
    }

    &__marker {
      width: 10px;
      height: 10px;
      margin-top: 6px;
      border-radius: 50%;
      background: var(--op-primary);
      flex-shrink: 0;
    }

    &__content {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    &__ts {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__actor {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      font-family: var(--op-font-family-mono);
    }
  }

  .prsv-worm-header {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);
    width: 100%;

    &__note {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);

      i {
        color: var(--op-info);
        margin-right: var(--op-space-1);
      }
    }
  }

  .prsv-table-wrap {
    overflow-x: auto;
    // `position: relative` NO es cosmético: sin bloque contenedor propio, un
    // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
    // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
    // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
    position: relative;

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }

    .table {
      border-collapse: collapse;
      width: 100%;

      th {
        background: var(--op-bg);
        color: var(--op-text-secondary);
        font-size: var(--op-font-xs);
        font-weight: var(--op-weight-bold);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        padding: var(--op-space-2) var(--op-space-3);
        border-bottom: 2px solid var(--op-border);
      }

      td {
        padding: var(--op-space-2) var(--op-space-3);
        border-bottom: 1px solid var(--op-border);
        color: var(--op-text);
        font-size: var(--op-font-sm);
        vertical-align: middle;
      }
    }
  }

  .prsv-cell-truncate {
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .prsv-diferido {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0 0 var(--op-space-4);
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
  }
</style>
