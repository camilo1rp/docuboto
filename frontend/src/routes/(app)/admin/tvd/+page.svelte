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
  /admin/tvd — Tablas de Valoración Documental: fondo acumulado (ADR-026).

  La TVD NO es "otra TRD": valora documentación YA PRODUCIDA (fondo
  acumulado, típicamente de dependencias suprimidas o entidades liquidadas),
  no la producción corriente. Por eso esta pantalla:
    - NO muestra columna de "archivo de gestión" (no existe para TVD --
      mostrarla vacía sugeriría un dato faltante, no una fase inexistente).
    - SÍ muestra fondo/productora, fechas extremas y la justificación de la
      valoración, que es el OBJETO del instrumento (ADR-026 D5), no un
      campo secundario.

  Árbol jerárquico (sección→subsección→serie del fondo, `parent_id`) igual
  que /admin/trd -- mismo mecanismo, otra semántica (ADR-026 tabla D2).

  Circuito de convalidación (aprobar/devolver/convalidar/registrar-rusd/
  derogar): componente COMPARTIDO `InstrumentLifecycleActions`, el MISMO que
  usa /admin/trd -- ADR-026 pide explícitamente no construirlo dos veces.

  RBAC (espejo real): el backend gatea con USUA_PERM_TRD (ADR-026 reutiliza
  el permiso de TRD a propósito -- misma potestad archivística/Comité).

  D-05 — access_token NUNCA en cliente: todas las escrituras van a
  /admin/tvd/api/* que leen locals.accessToken.

  Append-only fuera de "borrador" (ADR-026, mismo mecanismo que TRD/ADR-025):
  el PATCH completo solo se ofrece mientras la versión está en borrador; en
  cualquier otro estado el formulario deshabilita todo salvo el perfil
  PDF/A (única excepción editable en sitio) -- no se ofrece un botón que el
  backend respondería con 409 (regla dura de este proyecto).
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

  /** @type {import('./$types').PageData} */
  export let data;

  $: canWrite = $permissions.includes("USUA_PERM_TRD");

  let agrupaciones = data.agrupaciones ?? [];

  // ---------------------------------------------------------------------------
  // Árbol de agrupaciones: mismo algoritmo que /admin/trd (parent_id).
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
    for (const s of items) {
      if (!visited.has(s.id)) out.push({ ...s, depth: 0 });
    }
    return out;
  }

  $: agrupacionesTree = buildTree(agrupaciones);
  $: agrupacionOptions = agrupaciones.map((s) => ({ value: s.id, label: `${s.code} — ${s.name}` }));

  const DISPOSICIONES = [
    { value: "CT", label: "CT — Conservación Total" },
    { value: "E", label: "E — Eliminación" },
    { value: "S", label: "S — Selección" },
    { value: "M", label: "M — Microfilmación/Digitalización" },
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

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", { year: "numeric", month: "short", day: "numeric" });
  }

  // ---------------------------------------------------------------------------
  // Crear agrupación TVD
  // ---------------------------------------------------------------------------
  let actionLoading = false;
  let createOpen = false;
  let editOpen = false;
  let editTarget = null;

  let cCode = "";
  let cName = "";
  let cDescription = "";
  let cParentId = "";
  let cFondoNombre = "";
  let cFechaInicial = "";
  let cFechaFinal = "";
  let cJustificacion = "";
  let cArchivoCentral = "";
  let cTotalRetention = "10";
  let cDisposition = "CT";
  let cPdfaProfile = "";

  let cCodeError = "";
  let cNameError = "";
  let cFondoError = "";
  let cFechasError = "";
  let cJustificacionError = "";

  function openCreate() {
    cCode = "";
    cName = "";
    cDescription = "";
    cParentId = "";
    cFondoNombre = "";
    cFechaInicial = "";
    cFechaFinal = "";
    cJustificacion = "";
    cArchivoCentral = "";
    cTotalRetention = "10";
    cDisposition = "CT";
    cPdfaProfile = "";
    cCodeError = "";
    cNameError = "";
    cFondoError = "";
    cFechasError = "";
    cJustificacionError = "";
    createOpen = true;
  }

  function validateCreate() {
    cCodeError = cCode.trim() ? "" : "El código es obligatorio.";
    cNameError = cName.trim() ? "" : "El nombre es obligatorio.";
    cFondoError = cFondoNombre.trim() ? "" : "El fondo/productora es obligatorio en una TVD.";
    cJustificacionError = cJustificacion.trim()
      ? ""
      : "La justificación de la valoración es obligatoria: es el objeto del instrumento.";
    if (!cFechaInicial || !cFechaFinal) {
      cFechasError = "Las dos fechas extremas (inicial y final) son obligatorias en una TVD.";
    } else if (cFechaFinal < cFechaInicial) {
      cFechasError = "La fecha extrema final no puede ser anterior a la inicial.";
    } else {
      cFechasError = "";
    }
    return !cCodeError && !cNameError && !cFondoError && !cJustificacionError && !cFechasError;
  }

  async function crearAgrupacion() {
    if (!validateCreate()) return;
    actionLoading = true;
    try {
      const res = await fetch("/admin/tvd/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: cCode.trim(),
          name: cName.trim(),
          description: cDescription.trim() || null,
          parent_id: cParentId || null,
          fondo_nombre: cFondoNombre.trim(),
          fecha_extrema_inicial: cFechaInicial,
          fecha_extrema_final: cFechaFinal,
          justificacion_valoracion: cJustificacion.trim(),
          archivo_central_years: cArchivoCentral ? Number(cArchivoCentral) : null,
          total_retention: Number(cTotalRetention),
          disposition: cDisposition,
          pdfa_profile: cPdfaProfile || null,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo crear la agrupación TVD.");
        return;
      }
      agrupaciones = [...agrupaciones, resData];
      createOpen = false;
      notifySuccess("Agrupación TVD creada en borrador.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Editar agrupación TVD — append-only fuera de "borrador" (D-02 §honestidad):
  // el formulario solo habilita el resto de campos cuando editTarget.estado
  // === 'borrador'; en cualquier otro estado, ÚNICAMENTE pdfa_profile viaja
  // al PATCH (enviar los demás, aunque sin cambios, dispararía un 409:
  // `has_substantive_change` del backend mira "¿viene definido?", no
  // "¿cambió?").
  // ---------------------------------------------------------------------------
  let eName = "";
  let eDescription = "";
  let eFondoNombre = "";
  let eFechaInicial = "";
  let eFechaFinal = "";
  let eJustificacion = "";
  let eArchivoCentral = "";
  let eTotalRetention = "";
  let eDisposition = "";
  let ePdfaProfile = "";

  function openEdit(item) {
    editTarget = item;
    eName = item.name ?? "";
    eDescription = item.description ?? "";
    eFondoNombre = item.fondo_nombre ?? "";
    eFechaInicial = item.fecha_extrema_inicial ?? "";
    eFechaFinal = item.fecha_extrema_final ?? "";
    eJustificacion = item.justificacion_valoracion ?? "";
    eArchivoCentral = item.archivo_central_years != null ? String(item.archivo_central_years) : "";
    eTotalRetention = String(item.total_retention ?? "");
    eDisposition = item.disposition ?? "";
    ePdfaProfile = item.pdfa_profile ?? "";
    editOpen = true;
  }

  async function guardarAgrupacion() {
    if (!editTarget) return;
    const isBorrador = editTarget.estado === "borrador";
    /** @type {Record<string, unknown>} */
    const payload = { pdfa_profile: ePdfaProfile || null };
    if (isBorrador) {
      Object.assign(payload, {
        name: eName.trim(),
        description: eDescription.trim() || null,
        fondo_nombre: eFondoNombre.trim(),
        fecha_extrema_inicial: eFechaInicial,
        fecha_extrema_final: eFechaFinal,
        justificacion_valoracion: eJustificacion.trim(),
        archivo_central_years: eArchivoCentral ? Number(eArchivoCentral) : null,
        total_retention: Number(eTotalRetention),
        disposition: eDisposition,
      });
    }
    actionLoading = true;
    try {
      const res = await fetch(`/admin/tvd/api/${editTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo actualizar la agrupación TVD.");
        return;
      }
      agrupaciones = agrupaciones.map((s) => (s.id === resData.id ? resData : s));
      editOpen = false;
      editTarget = null;
      notifySuccess("Agrupación TVD actualizada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Circuito de convalidación: InstrumentLifecycleActions emite "changed"
  // con la fila actualizada (o, si es una versión nueva, la crea) — se
  // reemplaza/inserta EN SITIO, sin recargar todo el listado.
  // ---------------------------------------------------------------------------
  function onLifecycleChanged(event) {
    const { item, isNewVersion } = event.detail;
    if (isNewVersion) {
      agrupaciones = [...agrupaciones, item];
    } else {
      agrupaciones = agrupaciones.map((s) => (s.id === item.id ? item : s));
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "TVD" }]);
  });
</script>

<svelte:head>
  <title>TVD — Fondo acumulado — OrpycaMCP</title>
</svelte:head>

<div class="tvd-page">
  <header class="tvd-page__header">
    <h1 class="tvd-page__title">TVD — Tablas de Valoración Documental</h1>
    <p class="tvd-page__subtitle">
      Valoración de fondo acumulado: documentación ya producida (dependencias suprimidas o
      entidades liquidadas), sin fase de archivo de gestión. No sustituye la TRD de producción
      corriente (ADR-026).
    </p>
  </header>

  <CatalogCrudPanel
    ariaLabel="Árbol de agrupaciones TVD"
    items={agrupacionesTree}
    rowKey={(s) => s.id}
    loadError={data.agrupacionesError}
    canCreate={canWrite}
    createLabel="Nueva agrupación TVD"
    emptyIcon="fas fa-box-archive"
    emptyTitle="Sin agrupaciones TVD"
    emptyDescription='Crea la primera con "Nueva agrupación TVD".'
    bind:createOpen
    bind:editOpen
    {editTarget}
  >
    <svelte:fragment slot="thead">
      <th scope="col">Código</th>
      <th scope="col">Nombre</th>
      <th scope="col">Fondo / productora</th>
      <th scope="col">Fechas extremas</th>
      <th scope="col">Disposición</th>
      <th scope="col">Estado del instrumento</th>
    </svelte:fragment>

    <svelte:fragment slot="row" let:item>
      <td><code>{item.code}</code> <span class="tvd-page__version">v{item.version}</span></td>
      <td>
        <span class="tvd-page__indent" style="--depth: {item.depth}">
          {#if item.depth > 0}<i class="fas fa-turn-up tvd-page__branch" aria-hidden="true"></i>{/if}
          {item.name}
        </span>
      </td>
      <td>{item.fondo_nombre ?? "—"}</td>
      <td>{formatDate(item.fecha_extrema_inicial)} – {formatDate(item.fecha_extrema_final)}</td>
      <td>{item.disposition}</td>
      <td>
        <StatusChip
          status={ESTADO_CHIP_MAP[item.estado] ?? "neutral"}
          label={ESTADO_LABELS[item.estado] ?? item.estado}
          dot
        />
      </td>
    </svelte:fragment>

    <svelte:fragment slot="row-actions" let:item>
      {#if canWrite}
        <Button variant="ghost" size="sm" icon="fas fa-pen" on:click={() => openEdit(item)}>Editar</Button>
      {/if}
      <InstrumentLifecycleActions
        {item}
        {canWrite}
        basePath="/admin/tvd/api"
        instrumentLabel="TVD"
        on:changed={onLifecycleChanged}
      />
    </svelte:fragment>

    <svelte:fragment slot="create-form">
      <form class="tvd-form" on:submit|preventDefault={crearAgrupacion} novalidate>
        <div class="tvd-form__row">
          <FormField id="tvd-code" label="Código" required bind:value={cCode} error={cCodeError} />
          <FormField id="tvd-name" label="Nombre" required bind:value={cName} error={cNameError} />
        </div>
        <FormField id="tvd-description" label="Descripción" type="textarea" bind:value={cDescription} />
        <FormField
          id="tvd-parent"
          label="Agrupación padre (sección/subsección)"
          type="select"
          bind:value={cParentId}
          options={agrupacionOptions}
          placeholder="— ninguna (raíz del fondo) —"
        />
        <FormField
          id="tvd-fondo"
          label="Fondo / productora"
          required
          bind:value={cFondoNombre}
          error={cFondoError}
          hint='Entidad o dependencia productora, a menudo extinta (ej. "Instituto Departamental de X, liquidado 2003").'
        />
        <div class="tvd-form__row">
          <FormField
            id="tvd-fecha-inicial"
            label="Fecha extrema inicial"
            type="date"
            required
            bind:value={cFechaInicial}
            error={cFechasError}
          />
          <FormField id="tvd-fecha-final" label="Fecha extrema final" type="date" required bind:value={cFechaFinal} />
        </div>
        <FormField
          id="tvd-justificacion"
          label="Justificación de la valoración"
          type="textarea"
          required
          bind:value={cJustificacion}
          error={cJustificacionError}
          hint="Es el OBJETO del instrumento (fundamento de una eventual eliminación) — no una nota secundaria."
        />
        <div class="tvd-form__row">
          <FormField
            id="tvd-archivo-central"
            label="Archivo central (años)"
            type="number"
            bind:value={cArchivoCentral}
            hint="Sin fase de archivo de gestión: la TVD ya está en central/histórico."
          />
          <FormField id="tvd-total-retention" label="Retención total (años)" type="number" bind:value={cTotalRetention} />
        </div>
        <FormField id="tvd-disposition" label="Disposición final" type="select" bind:value={cDisposition} options={DISPOSICIONES} />
        <FormField
          id="tvd-pdfa"
          label="Perfil PDF/A (opcional)"
          type="select"
          bind:value={cPdfaProfile}
          options={PDFA_PROFILES}
          placeholder="— usar el default global —"
        />
      </form>
    </svelte:fragment>
    <svelte:fragment slot="create-footer">
      <Button variant="ghost" on:click={() => (createOpen = false)}>Cancelar</Button>
      <Button icon="fas fa-save" loading={actionLoading} on:click={crearAgrupacion}>Crear agrupación</Button>
    </svelte:fragment>

    <svelte:fragment slot="edit-form" let:item>
      <form class="tvd-form" on:submit|preventDefault={guardarAgrupacion} novalidate>
        <p class="tvd-form__hint">
          Código <code>{item.code}</code> v{item.version} — inmutable.
          {#if item.estado !== "borrador"}
            <strong>Instrumento append-only</strong> en estado "{ESTADO_LABELS[item.estado] ?? item.estado}": solo
            puede cambiarse el perfil PDF/A aquí. Para el resto del contenido, use "Nueva versión".
          {/if}
        </p>
        <FormField id="tvd-edit-name" label="Nombre" required bind:value={eName} disabled={item.estado !== "borrador"} />
        <FormField
          id="tvd-edit-description"
          label="Descripción"
          type="textarea"
          bind:value={eDescription}
          disabled={item.estado !== "borrador"}
        />
        <FormField
          id="tvd-edit-fondo"
          label="Fondo / productora"
          required
          bind:value={eFondoNombre}
          disabled={item.estado !== "borrador"}
        />
        <div class="tvd-form__row">
          <FormField
            id="tvd-edit-fecha-inicial"
            label="Fecha extrema inicial"
            type="date"
            required
            bind:value={eFechaInicial}
            disabled={item.estado !== "borrador"}
          />
          <FormField
            id="tvd-edit-fecha-final"
            label="Fecha extrema final"
            type="date"
            required
            bind:value={eFechaFinal}
            disabled={item.estado !== "borrador"}
          />
        </div>
        <FormField
          id="tvd-edit-justificacion"
          label="Justificación de la valoración"
          type="textarea"
          required
          bind:value={eJustificacion}
          disabled={item.estado !== "borrador"}
        />
        <div class="tvd-form__row">
          <FormField
            id="tvd-edit-archivo-central"
            label="Archivo central (años)"
            type="number"
            bind:value={eArchivoCentral}
            disabled={item.estado !== "borrador"}
          />
          <FormField
            id="tvd-edit-total-retention"
            label="Retención total (años)"
            type="number"
            bind:value={eTotalRetention}
            disabled={item.estado !== "borrador"}
          />
        </div>
        <FormField
          id="tvd-edit-disposition"
          label="Disposición final"
          type="select"
          bind:value={eDisposition}
          options={DISPOSICIONES}
          disabled={item.estado !== "borrador"}
        />
        <FormField
          id="tvd-edit-pdfa"
          label="Perfil PDF/A"
          type="select"
          bind:value={ePdfaProfile}
          options={PDFA_PROFILES}
          placeholder="— usar el default global —"
        />
      </form>
    </svelte:fragment>
    <svelte:fragment slot="edit-footer">
      <Button variant="ghost" on:click={() => (editOpen = false)}>Cancelar</Button>
      <Button icon="fas fa-save" loading={actionLoading} on:click={guardarAgrupacion}>Guardar</Button>
    </svelte:fragment>
  </CatalogCrudPanel>
</div>

<style lang="scss">
  .tvd-page {
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
      max-width: 70ch;
    }

    &__version {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
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
  }

  .tvd-form {
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
      line-height: 1.5;
    }
  }
</style>
