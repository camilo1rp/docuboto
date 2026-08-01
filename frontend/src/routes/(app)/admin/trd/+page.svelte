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
  /admin/trd — Series TRD/CCD y tipos documentales (E04, Fase 5 cierre de
  desfase API↔UI).

  Dos pestañas (patrón ARIA tablist/tab/tabpanel, ver expedientes/[id]):
    - "Series TRD/CCD": el CCD es jerárquico (trd_series.parent_id) — se
      renderiza como ÁRBOL indentado, nunca como lista plana (la jerarquía es
      el dato: perderla en la UI es perder la clasificación archivística).
    - "Tipos documentales": tercer nivel del TRD, cuelgan opcionalmente de
      una serie (filtro por serie en la barra de herramientas).

  RBAC (espejo real, ver +page.server.js): el backend gatea creación/edición
  con USUA_PERM_TRD (no USUA_PERM_ADMIN) — se usa ese permiso real para los
  botones de escritura de esta pantalla.

  D-05 — access_token NUNCA en cliente: todas las escrituras van a
  /admin/trd/api/* que leen locals.accessToken.

  Nota: el backend no expone borrado de series (solo alta + PATCH, el código
  es inmutable); la tabla de series no tiene columna de borrado — no es una
  omisión de UI, el endpoint no existe.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import CatalogCrudPanel from "$lib/components/features/admin/CatalogCrudPanel.svelte";
  import InstrumentLifecycleActions from "$lib/components/features/admin/InstrumentLifecycleActions.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";

  // Circuito de convalidación (ADR-025/ADR-026): mismos estados/chips que
  // /admin/tvd -- componente compartido `InstrumentLifecycleActions`.
  const ESTADO_LABELS = {
    borrador: "Borrador",
    aprobada: "Aprobada",
    convalidada: "Convalidada",
    migrada: "Migrada",
    derogada: "Derogada",
  };
  const ESTADO_CHIP_MAP = {
    borrador: "neutral",
    aprobada: "pending",
    convalidada: "success",
    migrada: "info",
    derogada: "closed",
  };

  /** @type {import('./$types').PageData} */
  export let data;

  $: canWrite = $permissions.includes("USUA_PERM_TRD");

  let series = data.series ?? [];
  let tipos = data.tipos ?? [];

  // ---------------------------------------------------------------------------
  // Árbol de series: orden padre→hijos, con profundidad para indentación.
  // Series huérfanas (parent_id apunta a algo fuera de la página cargada, o
  // ciclos defensivos) se listan al final en vez de desaparecer.
  // ---------------------------------------------------------------------------
  function buildTree(items) {
    const byParent = new Map();
    const ids = new Set(items.map((s) => s.id));
    for (const s of items) {
      const key = s.parent_id && ids.has(s.parent_id) ? s.parent_id : null;
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key).push(s);
    }
    const out = [];
    const visited = new Set();
    function walk(parentKey, depth) {
      for (const s of byParent.get(parentKey) ?? []) {
        if (visited.has(s.id)) continue;
        visited.add(s.id);
        out.push({ ...s, depth });
        walk(s.id, depth + 1);
      }
    }
    walk(null, 0);
    // Defensivo: cualquier serie no visitada (referencia circular improbable)
    // se añade al final en profundidad 0, para que no desaparezca del listado.
    for (const s of items) {
      if (!visited.has(s.id)) out.push({ ...s, depth: 0 });
    }
    return out;
  }

  $: seriesTree = buildTree(series);
  $: serieOptions = series.map((s) => ({ value: s.id, label: `${s.code} — ${s.name}` }));

  const DISPOSICIONES = [
    { value: "CT", label: "CT — Conservación Total" },
    { value: "E", label: "E — Eliminación" },
    { value: "S", label: "S — Selección" },
    { value: "M", label: "M — Microfilmación/Digitalización" },
    { value: "eliminate", label: "Eliminar" },
    { value: "conserve", label: "Conservar" },
    { value: "digitize", label: "Digitalizar" },
    { value: "select", label: "Seleccionar" },
  ];
  const PDFA_PROFILES = [
    { value: "1a", label: "PDF/A-1a" },
    { value: "1b", label: "PDF/A-1b" },
    { value: "2a", label: "PDF/A-2a" },
    { value: "2b", label: "PDF/A-2b" },
    { value: "2u", label: "PDF/A-2u" },
    { value: "3a", label: "PDF/A-3a" },
    { value: "3b", label: "PDF/A-3b" },
    { value: "3u", label: "PDF/A-3u" },
  ];

  // ---------------------------------------------------------------------------
  // Pestañas
  // ---------------------------------------------------------------------------
  const TABS = [
    { id: "series", label: "Series TRD/CCD", icon: "fa-folder-tree" },
    { id: "tipos", label: "Tipos documentales", icon: "fa-file-lines" },
  ];
  let activeTab = "series";

  function handleTabKeydown(e, tabId) {
    const idx = TABS.findIndex((t) => t.id === tabId);
    if (e.key === "ArrowRight") {
      e.preventDefault();
      activeTab = TABS[(idx + 1) % TABS.length].id;
      document.getElementById(`tab-${activeTab}`)?.focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      activeTab = TABS[(idx - 1 + TABS.length) % TABS.length].id;
      document.getElementById(`tab-${activeTab}`)?.focus();
    }
  }

  // ---------------------------------------------------------------------------
  // Series: crear / editar
  // ---------------------------------------------------------------------------
  let actionLoading = false;
  let serieCreateOpen = false;
  let serieEditOpen = false;
  let serieEditTarget = null;

  let sCode = "";
  let sName = "";
  let sDescription = "";
  let sParentId = "";
  let sRetentionYears = "5";
  let sTotalRetention = "10";
  let sDisposition = "E";
  let sArchivoGestion = "";
  let sArchivoCentral = "";
  let sPdfaProfile = "";
  let sCodeError = "";
  let sNameError = "";

  function openSerieCreate() {
    sCode = "";
    sName = "";
    sDescription = "";
    sParentId = "";
    sRetentionYears = "5";
    sTotalRetention = "10";
    sDisposition = "E";
    sArchivoGestion = "";
    sArchivoCentral = "";
    sPdfaProfile = "";
    sCodeError = "";
    sNameError = "";
    serieCreateOpen = true;
  }

  async function crearSerie() {
    sCodeError = "";
    sNameError = "";
    if (!sCode.trim()) {
      sCodeError = "El código es obligatorio.";
      return;
    }
    if (!sName.trim()) {
      sNameError = "El nombre es obligatorio.";
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch("/admin/trd/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: sCode.trim(),
          name: sName.trim(),
          description: sDescription.trim() || null,
          parent_id: sParentId || null,
          retention_years: Number(sRetentionYears),
          total_retention: Number(sTotalRetention),
          disposition: sDisposition,
          archivo_gestion_years: sArchivoGestion ? Number(sArchivoGestion) : null,
          archivo_central_years: sArchivoCentral ? Number(sArchivoCentral) : null,
          pdfa_profile: sPdfaProfile || null,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo crear la serie.");
        return;
      }
      series = [...series, resData];
      serieCreateOpen = false;
      notifySuccess("Serie TRD creada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  let eName = "";
  let eDescription = "";
  let eRetentionYears = "";
  let eTotalRetention = "";
  let eDisposition = "";
  let ePdfaProfile = "";
  let eIsActive = true;

  function openSerieEdit(serie) {
    serieEditTarget = serie;
    eName = serie.name ?? "";
    eDescription = serie.description ?? "";
    eRetentionYears = String(serie.retention_years ?? "");
    eTotalRetention = String(serie.total_retention ?? "");
    eDisposition = serie.disposition ?? "E";
    ePdfaProfile = serie.pdfa_profile ?? "";
    eIsActive = serie.is_active !== false;
    serieEditOpen = true;
  }

  async function guardarSerie() {
    if (!serieEditTarget) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/trd/api/${serieEditTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: eName.trim(),
          description: eDescription.trim() || null,
          retention_years: Number(eRetentionYears),
          total_retention: Number(eTotalRetention),
          disposition: eDisposition,
          pdfa_profile: ePdfaProfile || null,
          is_active: eIsActive,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo actualizar la serie.");
        return;
      }
      series = series.map((s) => (s.id === resData.id ? resData : s));
      serieEditOpen = false;
      serieEditTarget = null;
      notifySuccess("Serie TRD actualizada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Tipos documentales: crear / editar / borrar
  // ---------------------------------------------------------------------------
  let filtroSerie = "";
  $: tiposFiltrados = filtroSerie ? tipos.filter((t) => t.trd_serie_id === filtroSerie) : tipos;

  let tipoCreateOpen = false;
  let tipoEditOpen = false;
  let tipoEditTarget = null;
  let tipoDeleteOpen = false;
  let tipoDeleteTarget = null;

  let tCode = "";
  let tNombre = "";
  let tSerieId = "";
  let tObligatorio = false;
  let tMetadataTemplate = "";
  let tCodeError = "";
  let tNombreError = "";

  function openTipoCreate() {
    tCode = "";
    tNombre = "";
    tSerieId = filtroSerie || "";
    tObligatorio = false;
    tMetadataTemplate = "";
    tCodeError = "";
    tNombreError = "";
    tipoCreateOpen = true;
  }

  async function crearTipo() {
    tCodeError = "";
    tNombreError = "";
    if (!tCode.trim()) {
      tCodeError = "El código es obligatorio.";
      return;
    }
    if (!tNombre.trim()) {
      tNombreError = "El nombre es obligatorio.";
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch("/admin/trd/api/tipos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: tCode.trim(),
          nombre: tNombre.trim(),
          trd_serie_id: tSerieId || null,
          obligatorio: tObligatorio,
          metadata_template: tMetadataTemplate.trim() || null,
          activo: true,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo crear el tipo documental.");
        return;
      }
      tipos = [resData, ...tipos];
      tipoCreateOpen = false;
      notifySuccess("Tipo documental creado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  let etNombre = "";
  let etSerieId = "";
  let etObligatorio = false;
  let etMetadataTemplate = "";
  let etActivo = true;

  function openTipoEdit(tipo) {
    tipoEditTarget = tipo;
    etNombre = tipo.nombre ?? "";
    etSerieId = tipo.trd_serie_id ?? "";
    etObligatorio = !!tipo.obligatorio;
    etMetadataTemplate = tipo.metadata_template ?? "";
    etActivo = tipo.activo !== false;
    tipoEditOpen = true;
  }

  async function guardarTipo() {
    if (!tipoEditTarget) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/trd/api/tipos/${tipoEditTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nombre: etNombre.trim(),
          trd_serie_id: etSerieId || null,
          obligatorio: etObligatorio,
          metadata_template: etMetadataTemplate.trim() || null,
          activo: etActivo,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo actualizar el tipo documental.");
        return;
      }
      tipos = tipos.map((t) => (t.id === resData.id ? resData : t));
      tipoEditOpen = false;
      tipoEditTarget = null;
      notifySuccess("Tipo documental actualizado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  async function confirmarBorrarTipo(event) {
    const tipo = event.detail.item;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/trd/api/tipos/${tipo.id}`, { method: "DELETE" });
      if (res.status === 204 || res.ok) {
        tipos = tipos.filter((t) => t.id !== tipo.id);
        notifySuccess("Tipo documental eliminado.");
        tipoDeleteOpen = false;
        tipoDeleteTarget = null;
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(err.detail ?? "No se pudo eliminar el tipo documental.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  function serieNombre(id) {
    return series.find((s) => s.id === id)?.name ?? null;
  }

  // Circuito de convalidación: reemplaza/inserta EN SITIO (mismo patrón que
  // /admin/tvd), sin recargar todo el árbol.
  function onLifecycleChanged(event) {
    const { item, isNewVersion } = event.detail;
    if (isNewVersion) {
      series = [...series, item];
    } else {
      series = series.map((s) => (s.id === item.id ? item : s));
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "TRD / CCD" }]);
  });
</script>

<svelte:head>
  <title>TRD / CCD — OrpycaMCP</title>
</svelte:head>

<div class="trd-page">
  <header class="trd-page__header">
    <h1 class="trd-page__title">TRD / CCD</h1>
    <p class="trd-page__subtitle">Tabla de Retención Documental y Cuadro de Clasificación (E04).</p>
  </header>

  <div class="trd-tabs" role="tablist" aria-label="Secciones de TRD">
    {#each TABS as tab (tab.id)}
      <button
        id="tab-{tab.id}"
        class="trd-tabs__tab"
        class:trd-tabs__tab--active={activeTab === tab.id}
        role="tab"
        aria-selected={activeTab === tab.id}
        aria-controls="panel-{tab.id}"
        tabindex={activeTab === tab.id ? 0 : -1}
        on:click={() => (activeTab = tab.id)}
        on:keydown={(e) => handleTabKeydown(e, tab.id)}
      >
        <i class="fas {tab.icon}" aria-hidden="true"></i>
        {tab.label}
      </button>
    {/each}
  </div>

  <!-- ===================================================================
       Panel: Series TRD/CCD (árbol)
       =================================================================== -->
  <div id="panel-series" role="tabpanel" aria-labelledby="tab-series" hidden={activeTab !== "series"} tabindex="0">
    <CatalogCrudPanel
      ariaLabel="Árbol de series TRD/CCD"
      items={seriesTree}
      rowKey={(s) => s.id}
      loadError={data.seriesError}
      canCreate={canWrite}
      createLabel="Nueva serie"
      emptyIcon="fas fa-folder-tree"
      emptyTitle="Sin series TRD"
      emptyDescription='Crea la primera serie con "Nueva serie".'
      bind:createOpen={serieCreateOpen}
      bind:editOpen={serieEditOpen}
      editTarget={serieEditTarget}
    >
      <svelte:fragment slot="thead">
        <th scope="col">Código</th>
        <th scope="col">Nombre</th>
        <th scope="col">Disposición</th>
        <th scope="col">Retención (gest./cent.)</th>
        <th scope="col">Estado del instrumento</th>
      </svelte:fragment>

      <svelte:fragment slot="row" let:item>
        <td><code>{item.code}</code> <span class="trd-page__version">v{item.version}</span></td>
        <td>
          <span class="trd-page__indent" style="--depth: {item.depth}">
            {#if item.depth > 0}<i class="fas fa-turn-up trd-page__branch" aria-hidden="true"></i>{/if}
            {item.name}
          </span>
        </td>
        <td>{item.disposition}</td>
        <td>{item.retention_years}a / {item.total_retention}a</td>
        <td>
          <StatusChip status={ESTADO_CHIP_MAP[item.estado] ?? "neutral"} label={ESTADO_LABELS[item.estado] ?? item.estado} dot />
        </td>
      </svelte:fragment>

      <!-- svelte:fragment no puede envolverse en {#if} (debe ser hijo directo
           del componente) -- el gate de permiso va DENTRO del fragmento. Con
           canWrite=false la columna de acciones queda presente pero vacía;
           es preferible a duplicar el CatalogCrudPanel por permiso. -->
      <svelte:fragment slot="row-actions" let:item>
        {#if canWrite}
          <Button variant="ghost" size="sm" icon="fas fa-pen" on:click={() => openSerieEdit(item)}>Editar</Button>
        {/if}
        <InstrumentLifecycleActions
          {item}
          {canWrite}
          basePath="/admin/trd/api"
          instrumentLabel="TRD"
          on:changed={onLifecycleChanged}
        />
      </svelte:fragment>

      <svelte:fragment slot="create-form">
        <form class="trd-form" on:submit|preventDefault={crearSerie} novalidate>
          <FormField id="serie-code" label="Código" required bind:value={sCode} error={sCodeError} />
          <FormField id="serie-name" label="Nombre" required bind:value={sName} error={sNameError} />
          <FormField id="serie-description" label="Descripción" type="textarea" bind:value={sDescription} />
          <FormField
            id="serie-parent"
            label="Serie padre (subserie)"
            type="select"
            bind:value={sParentId}
            options={serieOptions}
            placeholder="— ninguna (serie raíz) —"
          />
          <div class="trd-form__row">
            <FormField id="serie-retention" label="Años archivo de gestión (retención)" type="number" bind:value={sRetentionYears} />
            <FormField id="serie-total" label="Años retención total" type="number" bind:value={sTotalRetention} />
          </div>
          <FormField id="serie-disposition" label="Disposición final" type="select" bind:value={sDisposition} options={DISPOSICIONES} />
          <div class="trd-form__row">
            <FormField id="serie-ag" label="Archivo de gestión (años, opcional)" type="number" bind:value={sArchivoGestion} />
            <FormField id="serie-ac" label="Archivo central (años, opcional)" type="number" bind:value={sArchivoCentral} />
          </div>
          <FormField
            id="serie-pdfa"
            label="Perfil PDF/A (opcional)"
            type="select"
            bind:value={sPdfaProfile}
            options={PDFA_PROFILES}
            placeholder="— usar el default global —"
            hint="Política de preservación del AIP para esta serie (E10)."
          />
        </form>
      </svelte:fragment>
      <svelte:fragment slot="create-footer">
        <Button variant="ghost" on:click={() => (serieCreateOpen = false)}>Cancelar</Button>
        <Button icon="fas fa-save" loading={actionLoading} on:click={crearSerie}>Crear serie</Button>
      </svelte:fragment>

      <svelte:fragment slot="edit-form" let:item>
        <form class="trd-form" on:submit|preventDefault={guardarSerie} novalidate>
          <p class="trd-form__hint">
            Código <code>{item.code}</code> — inmutable, no se puede editar tras crear la serie.
          </p>
          <FormField id="serie-edit-name" label="Nombre" required bind:value={eName} />
          <FormField id="serie-edit-description" label="Descripción" type="textarea" bind:value={eDescription} />
          <div class="trd-form__row">
            <FormField id="serie-edit-retention" label="Años archivo de gestión" type="number" bind:value={eRetentionYears} />
            <FormField id="serie-edit-total" label="Años retención total" type="number" bind:value={eTotalRetention} />
          </div>
          <FormField id="serie-edit-disposition" label="Disposición final" type="select" bind:value={eDisposition} options={DISPOSICIONES} />
          <FormField
            id="serie-edit-pdfa"
            label="Perfil PDF/A"
            type="select"
            bind:value={ePdfaProfile}
            options={PDFA_PROFILES}
            placeholder="— usar el default global —"
          />
          <label class="trd-form__check">
            <input type="checkbox" bind:checked={eIsActive} />
            Serie activa
          </label>
        </form>
      </svelte:fragment>
      <svelte:fragment slot="edit-footer">
        <Button variant="ghost" on:click={() => (serieEditOpen = false)}>Cancelar</Button>
        <Button icon="fas fa-save" loading={actionLoading} on:click={guardarSerie}>Guardar</Button>
      </svelte:fragment>
    </CatalogCrudPanel>
  </div>

  <!-- ===================================================================
       Panel: Tipos documentales
       =================================================================== -->
  <div id="panel-tipos" role="tabpanel" aria-labelledby="tab-tipos" hidden={activeTab !== "tipos"} tabindex="0">
    <CatalogCrudPanel
      ariaLabel="Tipos documentales"
      items={tiposFiltrados}
      rowKey={(t) => t.id}
      loadError={data.tiposError}
      canCreate={canWrite}
      createLabel="Nuevo tipo"
      emptyIcon="fas fa-file-lines"
      emptyTitle="Sin tipos documentales"
      emptyDescription='Crea el primero con "Nuevo tipo".'
      bind:createOpen={tipoCreateOpen}
      bind:editOpen={tipoEditOpen}
      editTarget={tipoEditTarget}
      bind:deleteOpen={tipoDeleteOpen}
      deleteTarget={tipoDeleteTarget}
      deleteConfirmText={(t) => `¿Eliminar el tipo documental "${t.nombre}"? Esta acción no puede deshacerse.`}
      deleteLoading={actionLoading}
      on:confirmDelete={confirmarBorrarTipo}
    >
      <svelte:fragment slot="toolbar-extra">
        <FormField
          id="tipos-filtro-serie"
          label=""
          type="select"
          bind:value={filtroSerie}
          options={serieOptions}
          placeholder="Todas las series"
        />
      </svelte:fragment>

      <svelte:fragment slot="thead">
        <th scope="col">Código</th>
        <th scope="col">Nombre</th>
        <th scope="col">Serie TRD</th>
        <th scope="col">Obligatorio</th>
        <th scope="col">Estado</th>
      </svelte:fragment>

      <svelte:fragment slot="row" let:item>
        <td><code>{item.code}</code></td>
        <td>{item.nombre}</td>
        <td>{serieNombre(item.trd_serie_id) ?? "—"}</td>
        <td>{item.obligatorio ? "Sí" : "No"}</td>
        <td>
          <StatusChip status={item.activo ? "success" : "neutral"} label={item.activo ? "Activo" : "Inactivo"} dot />
        </td>
      </svelte:fragment>

      <svelte:fragment slot="row-actions" let:item>
        {#if canWrite}
          <Button variant="ghost" size="sm" icon="fas fa-pen" on:click={() => openTipoEdit(item)}>Editar</Button>
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-trash"
            on:click={() => {
              tipoDeleteTarget = item;
              tipoDeleteOpen = true;
            }}
          >
            Eliminar
          </Button>
        {/if}
      </svelte:fragment>

      <svelte:fragment slot="create-form">
        <form class="trd-form" on:submit|preventDefault={crearTipo} novalidate>
          <FormField id="tipo-code" label="Código" required bind:value={tCode} error={tCodeError} />
          <FormField id="tipo-nombre" label="Nombre" required bind:value={tNombre} error={tNombreError} />
          <FormField
            id="tipo-serie"
            label="Serie TRD (opcional)"
            type="select"
            bind:value={tSerieId}
            options={serieOptions}
            placeholder="— sin serie —"
          />
          <label class="trd-form__check">
            <input type="checkbox" bind:checked={tObligatorio} />
            Obligatorio en el expediente
          </label>
          <FormField
            id="tipo-metadata-template"
            label="Plantilla de metadatos asociada (opcional)"
            bind:value={tMetadataTemplate}
            hint='Identificador libre (tipo_documental) usado en /admin/metadatos para la plantilla de documento.'
          />
        </form>
      </svelte:fragment>
      <svelte:fragment slot="create-footer">
        <Button variant="ghost" on:click={() => (tipoCreateOpen = false)}>Cancelar</Button>
        <Button icon="fas fa-save" loading={actionLoading} on:click={crearTipo}>Crear tipo</Button>
      </svelte:fragment>

      <svelte:fragment slot="edit-form" let:item>
        <form class="trd-form" on:submit|preventDefault={guardarTipo} novalidate>
          <p class="trd-form__hint">Código <code>{item.code}</code> — inmutable.</p>
          <FormField id="tipo-edit-nombre" label="Nombre" required bind:value={etNombre} />
          <FormField
            id="tipo-edit-serie"
            label="Serie TRD (opcional)"
            type="select"
            bind:value={etSerieId}
            options={serieOptions}
            placeholder="— sin serie —"
          />
          <label class="trd-form__check">
            <input type="checkbox" bind:checked={etObligatorio} />
            Obligatorio en el expediente
          </label>
          <FormField id="tipo-edit-metadata-template" label="Plantilla de metadatos asociada" bind:value={etMetadataTemplate} />
          <label class="trd-form__check">
            <input type="checkbox" bind:checked={etActivo} />
            Activo
          </label>
        </form>
      </svelte:fragment>
      <svelte:fragment slot="edit-footer">
        <Button variant="ghost" on:click={() => (tipoEditOpen = false)}>Cancelar</Button>
        <Button icon="fas fa-save" loading={actionLoading} on:click={guardarTipo}>Guardar</Button>
      </svelte:fragment>
    </CatalogCrudPanel>
  </div>
</div>

<style lang="scss">
  .trd-page {
    padding: var(--op-space-5);
    max-width: 1200px;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__title {
      font-size: var(--op-font-xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__subtitle {
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__indent {
      padding-left: calc(var(--depth, 0) * var(--op-space-5));
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
    }

    &__branch {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
    }

    &__version {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
    }
  }

  .trd-tabs {
    display: flex;
    background: var(--op-surface);
    border-bottom: 2px solid var(--op-border);
    overflow-x: auto;

    &__tab {
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

      &:hover {
        color: var(--op-text);
        background: var(--op-bg);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: -2px;
      }

      &--active {
        color: var(--op-primary-dark);
        border-bottom-color: var(--op-primary);
        font-weight: var(--op-weight-bold);
      }
    }
  }

  .trd-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-3);
    }

    &__hint {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__check {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
    }
  }
</style>
