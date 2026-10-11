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
  /admin/parametros — Parámetros de tenant, calendario de festivos y
  calculadora de días hábiles (E14, Fase 5 cierre de desfase API↔UI).

  Tres secciones (pestañas):
    - "Parámetros": pares clave→valor JSON arbitrario (sgd_param_admin).
    - "Festivos": calendario de no-hábiles (weekday<5 se excluye aparte, es
      cálculo, no dato); alta puntual + precarga por rango de años (Ley
      51/1983, algoritmo de Pascua/Emiliani, sin llamada externa).
    - "Calculadora de días hábiles": es la herramienta que justifica los
      plazos de respuesta (RF-RAD-04) — por eso NO se limita a mostrar el
      número de días, muestra el DESGLOSE de qué días se descontaron y por
      qué (fin de semana / festivo con su descripción). El backend
      (`BusinessDaysResult`) solo devuelve la fecha resultante; el desglose
      se reconstruye aquí enumerando el rango con el mismo catálogo de
      festivos ya cargado — dato real derivado, no inventado.

  RBAC: ver el hallazgo de dominio en +page.server.js — ningún endpoint de
  este router exige permiso hoy; USUA_PERM_ADMIN se aplica solo del lado del
  cliente, como medida conservadora (nunca menos restrictivo que el backend).
-->
<script>
  import { onMount } from "svelte";
  import { invalidateAll } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions, permissionLevels } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import {
    CLAVE_POLITICA_XADES,
    OPCIONES_POLITICA_XADES,
    MOTIVO_PARAMETRO_MIN,
    NIVEL_ADMIN_PARAMETROS,
    relajaPolitica,
    etiquetaPolitica,
  } from "$lib/utils/firmaInobtenible.js";
  import { clavesPorCrear } from "$lib/utils/paramsAdmitidos.js";
  import { etiquetaDeParam } from "$lib/utils/paramsEtiquetas.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: canWrite = $permissions.includes("USUA_PERM_ADMIN");
  // Escribir un PARÁMETRO exige USUA_PERM_ADMIN a nivel 3 (un administrador en modo lectura
  // o edición recibe 403). Desde FIRMA-2 los festivos (crear, borrar, sembrar) exigen el mismo
  // nivel 3. Fail-closed: sin nivel conocido, no se ofrece.
  $: canWriteParams = ($permissionLevels["USUA_PERM_ADMIN"] ?? 0) >= NIVEL_ADMIN_PARAMETROS;
  $: canWriteHolidays = canWriteParams;

  const TABS = [
    { id: "params", label: "Parámetros", icon: "fa-sliders" },
    { id: "festivos", label: "Festivos", icon: "fa-calendar-days" },
    { id: "calculadora", label: "Días hábiles", icon: "fa-calculator" },
  ];
  let activeTab = "params";

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

  let actionLoading = false;

  // ---------------------------------------------------------------------------
  // Parámetros
  // ---------------------------------------------------------------------------
  let paramEntries = Object.entries(data.params ?? {});
  /** Valor para la persona: nunca «null». @param {unknown} v */
  const mostrarValor = (v) =>
    v === null || v === undefined ? "Sin valor definido" : JSON.stringify(v);

  let editingKey = null;
  let editValueRaw = "";
  let editValueError = "";

  let newKey = "";
  let newValueRaw = "";
  let newKeyError = "";
  // Solo se ofrecen las claves que el backend admite y que el tenant aún no tiene.
  $: clavesDisponibles = clavesPorCrear(paramEntries.map(([k]) => k));
  let newValueError = "";

  // Política XAdES de la entidad: selector cerrado + motivo si se relaja.
  let politicaNueva = "";
  let politicaMotivo = "";
  let politicaError = "";
  let politicaMotivoError = "";

  function startEdit(key, value) {
    editingKey = key;
    editValueRaw = JSON.stringify(value, null, 2);
    editValueError = "";
    politicaNueva = typeof value === "string" ? value : "";
    politicaMotivo = "";
    politicaError = "";
    politicaMotivoError = "";
  }

  /** Mensaje de un fallo al guardar; el 403 se explica con el nivel que exige. */
  function mensajeGuardar(res, resData) {
    if (res.status === 403) {
      return "Cambiar parámetros exige administración a nivel 3 (crear y borrar). Su nivel actual no alcanza.";
    }
    if (codigoError(resData) === "config_param_desconocido") {
      return "Ese parámetro no es configurable. Elija una de las claves de la lista.";
    }
    if (codigoError(resData) === "url_invalida") {
      return "La dirección debe empezar por http:// o https://.";
    }
    if (codigoError(resData) === "valor_nulo") {
      return "El valor no puede quedar vacío: un parámetro no se puede dejar sin valor para que se herede. Escriba un valor concreto.";
    }
    return mensajeError(resData, "No se pudo guardar el parámetro.");
  }

  function parseValue(raw, setError) {
    try {
      const value = JSON.parse(raw);
      // El backend responde 422 `valor_nulo`: un parámetro no se "vuelve a heredar" con null.
      if (value === null) {
        setError("El valor no puede quedar vacío: escriba un valor concreto.");
        return { ok: false };
      }
      return { ok: true, value };
    } catch (err) {
      setError(`JSON inválido: ${err instanceof Error ? err.message : "error de sintaxis"}`);
      return { ok: false };
    }
  }

  async function guardarParam(key, raw, onDone) {
    const parsed = parseValue(raw, (msg) => {
      editValueError = msg;
      if (key === newKey.trim()) newValueError = msg;
    });
    if (!parsed.ok) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/parametros/api/params/${encodeURIComponent(key)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: parsed.value }),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeGuardar(res, resData));
        return;
      }
      paramEntries = paramEntries.some(([k]) => k === key)
        ? paramEntries.map(([k, v]) => (k === key ? [k, resData.value] : [k, v]))
        : [...paramEntries, [key, resData.value]];
      onDone();
      notifySuccess("Parámetro guardado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  async function guardarPolitica() {
    politicaError = "";
    politicaMotivoError = "";
    if (!politicaNueva) {
      politicaError = "Elija una política.";
      return;
    }
    const vigente = paramEntries.find(([k]) => k === CLAVE_POLITICA_XADES)?.[1] ?? null;
    const motivo = politicaMotivo.trim();
    if (relajaPolitica(vigente, politicaNueva) && motivo.length < MOTIVO_PARAMETRO_MIN) {
      politicaMotivoError = `Bajar la exigencia de la firma pide un motivo de al menos ${MOTIVO_PARAMETRO_MIN} caracteres.`;
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch(
        `/admin/parametros/api/params/${encodeURIComponent(CLAVE_POLITICA_XADES)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: politicaNueva, ...(motivo ? { motivo } : {}) }),
        },
      );
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = mensajeGuardar(res, resData);
        if (res.status === 422) politicaMotivoError = msg;
        notifyError(msg);
        return;
      }
      paramEntries = paramEntries.map(([k, v]) =>
        k === CLAVE_POLITICA_XADES ? [k, resData.value ?? politicaNueva] : [k, v],
      );
      editingKey = null;
      notifySuccess("Política de firma guardada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  function guardarEdicion() {
    if (editingKey === CLAVE_POLITICA_XADES) return guardarPolitica();
    guardarParam(editingKey, editValueRaw, () => {
      editingKey = null;
    });
  }

  function crearParam() {
    newKeyError = "";
    newValueError = "";
    if (!newKey.trim()) {
      newKeyError = "Elija una clave.";
      return;
    }
    if (paramEntries.some(([k]) => k === newKey.trim())) {
      newKeyError = "Ya existe un parámetro con esa clave; edítalo en la tabla.";
      return;
    }
    if (!newValueRaw.trim()) {
      newValueError = "El valor es obligatorio (JSON).";
      return;
    }
    guardarParam(newKey.trim(), newValueRaw, () => {
      newKey = "";
      newValueRaw = "";
    });
  }

  // ---------------------------------------------------------------------------
  // Festivos
  // ---------------------------------------------------------------------------
  // REACTIVO, no una copia única. Con `let holidays = data.holidays ?? []` la
  // lista se congelaba en el valor del primer render: `invalidateAll()` volvía a
  // ejecutar el `load` y actualizaba `data`, pero la tabla seguía pintando el
  // array viejo. Se veía al precargar el calendario — 36 festivos escritos en la
  // base y la pantalla insistiendo en «Sin festivos registrados».
  //
  // Las altas y bajas de abajo siguen actualizando `holidays` de forma optimista
  // y además persisten; cuando `data` se refresca, el servidor manda y esto
  // reconcilia con la verdad.
  $: holidays = data.holidays ?? [];
  let hFecha = "";
  let hDescripcion = "";
  let hFechaError = "";
  let hDescripcionError = "";

  async function crearFestivo() {
    hFechaError = "";
    hDescripcionError = "";
    if (!hFecha) {
      hFechaError = "La fecha es obligatoria.";
      return;
    }
    if (!hDescripcion.trim()) {
      hDescripcionError = "La descripción es obligatoria.";
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch("/admin/parametros/api/holidays", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fecha: hFecha,
          descripcion: hDescripcion.trim(),
          tipo: "festivo",
          is_national: true,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo crear el festivo."));
        return;
      }
      holidays = [...holidays, resData].sort((a, b) => a.fecha.localeCompare(b.fecha));
      hFecha = "";
      hDescripcion = "";
      notifySuccess("Festivo creado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  async function borrarFestivo(h) {
    actionLoading = true;
    try {
      const res = await fetch(`/admin/parametros/api/holidays/${h.id}`, { method: "DELETE" });
      if (res.status === 204 || res.ok) {
        holidays = holidays.filter((x) => x.id !== h.id);
        notifySuccess("Festivo eliminado.");
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(mensajeError(err, "No se pudo eliminar el festivo."));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  let seedFrom = String(new Date().getFullYear());
  let seedTo = String(new Date().getFullYear() + 1);
  let seedLoading = false;

  async function precargarFestivos() {
    seedLoading = true;
    try {
      const res = await fetch("/admin/parametros/api/holidays/seed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ year_from: Number(seedFrom), year_to: Number(seedTo) }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo precargar festivos."));
        return;
      }
      // `invalidateAll()` en vez de pedirle a la persona que recargue: la tabla
      // seguía diciendo «Sin festivos registrados» justo después de sembrar 36,
      // que es indistinguible de que la precarga no hubiera hecho nada. El resto
      // de la aplicación ya refresca sola tras una escritura (ver
      // DependencySwitcher, donde está documentado como norma).
      await invalidateAll();
      notifySuccess(`${resData.created ?? 0} festivos precargados.`);
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      seedLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Calculadora de días hábiles
  // ---------------------------------------------------------------------------
  let calcFrom = new Date().toISOString().slice(0, 10);
  let calcDays = "10";
  let calcLoading = false;
  let calcError = "";
  let calcResult = null;
  /** @type {Array<{fecha: string, motivo: string}>} */
  let calcBreakdown = [];

  function addDaysIso(iso, n) {
    const d = new Date(`${iso}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  function isoWeekday(iso) {
    // 0=domingo..6=sábado (UTC, evita desfases de zona horaria del navegador).
    return new Date(`${iso}T00:00:00Z`).getUTCDay();
  }

  function computeBreakdown(fromIso, dueIso) {
    const holidayByFecha = new Map(holidays.map((h) => [h.fecha.slice(0, 10), h.descripcion]));
    const out = [];
    let cursor = fromIso;
    // Recorre (from, due] — from es el día de radicación, no cuenta.
    while (cursor < dueIso) {
      cursor = addDaysIso(cursor, 1);
      const dow = isoWeekday(cursor);
      if (dow === 0 || dow === 6) {
        out.push({ fecha: cursor, motivo: dow === 0 ? "Domingo" : "Sábado" });
      } else if (holidayByFecha.has(cursor)) {
        out.push({ fecha: cursor, motivo: `Festivo — ${holidayByFecha.get(cursor)}` });
      }
    }
    return out;
  }

  async function calcularDiasHabiles() {
    calcError = "";
    calcResult = null;
    calcBreakdown = [];
    if (!calcFrom) {
      calcError = "La fecha de inicio es obligatoria.";
      return;
    }
    const days = Number(calcDays);
    if (!Number.isInteger(days) || days < 0) {
      calcError = "El número de días hábiles debe ser un entero >= 0.";
      return;
    }
    calcLoading = true;
    try {
      const params = new URLSearchParams({ from: calcFrom, days: String(days) });
      const res = await fetch(`/admin/parametros/api/business-days?${params}`);
      const resData = await res.json();
      if (!res.ok) {
        calcError = mensajeError(resData, "No se pudo calcular la fecha de vencimiento.");
        return;
      }
      calcResult = resData;
      calcBreakdown = computeBreakdown(
        resData.from_date.slice(0, 10),
        resData.due_date.slice(0, 10),
      );
    } catch {
      calcError = "No se pudo conectar al servidor.";
    } finally {
      calcLoading = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Parámetros" }]);
  });
</script>

<svelte:head>
  <title>Parámetros — OrpycaMCP</title>
</svelte:head>

<div class="param-page">
  <header class="param-page__header">
    <h1 class="param-page__title">Parámetros</h1>
    <p class="param-page__subtitle">
      Configuración general del tenant, calendario de festivos y plazos (E14).
    </p>
  </header>

  <div class="param-tabs" role="tablist" aria-label="Secciones de parámetros">
    {#each TABS as tab (tab.id)}
      <button
        id="tab-{tab.id}"
        class="param-tabs__tab"
        class:param-tabs__tab--active={activeTab === tab.id}
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
       Panel: Parámetros
       =================================================================== -->
  <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <div
    id="panel-params"
    role="tabpanel"
    aria-labelledby="tab-params"
    hidden={activeTab !== "params"}
    tabindex="0"
  >
    {#if data.paramsError}
      <div class="param-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {data.paramsError}
      </div>
    {:else if paramEntries.length === 0}
      <EmptyState
        icon="fas fa-sliders"
        title="Sin parámetros definidos"
        description="Crea el primero con el formulario de abajo."
      />
    {:else}
      <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
      <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
      <div class="param-table-wrap" role="region" aria-label="Parámetros del tenant" tabindex="0">
        <table class="param-table" aria-label="Parámetros del tenant">
          <thead>
            <tr>
              <th scope="col">Clave</th>
              <th scope="col">Valor</th>
              {#if canWriteParams}<th scope="col"><span class="sr-only">Acciones</span></th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each paramEntries as [key, value] (key)}
              <tr>
                <td><code>{key}</code></td>
                <td>
                  {#if editingKey === key && canWriteParams && key === CLAVE_POLITICA_XADES}
                    <FormField
                      id="param-edit-politica"
                      label="Política de firma personal (XAdES)"
                      type="select"
                      options={[
                        { value: "", label: "Elija una política" },
                        ...OPCIONES_POLITICA_XADES,
                      ]}
                      bind:value={politicaNueva}
                      error={politicaError}
                    />
                    <FormField
                      id="param-edit-politica-motivo"
                      label="Motivo del cambio"
                      type="textarea"
                      rows={2}
                      bind:value={politicaMotivo}
                      error={politicaMotivoError}
                      hint={relajaPolitica(value, politicaNueva)
                        ? `Obligatorio (mínimo ${MOTIVO_PARAMETRO_MIN} caracteres): este cambio baja la exigencia de la firma y queda en el asiento de auditoría.`
                        : "Opcional al endurecer la política."}
                    />
                  {:else if editingKey === key && canWriteParams}
                    <FormField
                      id="param-edit-{key}"
                      label=""
                      type="textarea"
                      rows={3}
                      bind:value={editValueRaw}
                      error={editValueError}
                    />
                  {:else if key === CLAVE_POLITICA_XADES}
                    <code class="param-page__value">{mostrarValor(value)}</code>
                    <span class="param-page__hint">{etiquetaPolitica(value)}</span>
                  {:else}
                    <code class="param-page__value">{mostrarValor(value)}</code>
                  {/if}
                </td>
                {#if canWriteParams}
                  <td class="param-table__actions">
                    {#if editingKey === key}
                      <Button variant="ghost" size="sm" on:click={() => (editingKey = null)}
                        >Cancelar</Button
                      >
                      <Button
                        size="sm"
                        icon="fas fa-save"
                        loading={actionLoading}
                        on:click={guardarEdicion}>Guardar</Button
                      >
                    {:else}
                      <Button
                        variant="ghost"
                        size="sm"
                        icon="fas fa-pen"
                        on:click={() => startEdit(key, value)}>Editar</Button
                      >
                    {/if}
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    {#if canWrite && !canWriteParams}
      <p class="param-page__hint" data-testid="params-solo-lectura">
        Su nivel de administración no permite cambiar parámetros: se necesita nivel 3 (crear y
        borrar). Puede consultarlos.
      </p>
    {/if}

    {#if canWriteParams}
      <form class="param-form" on:submit|preventDefault={crearParam} novalidate>
        <h2 class="param-page__section-title">Nuevo parámetro</h2>
        {#if clavesDisponibles.length === 0}
          <p class="param-page__hint" data-testid="params-sin-claves-nuevas">
            Todos los parámetros configurables ya existen; edítelos en la tabla.
          </p>
        {:else}
          <FormField
            id="new-param-key"
            label="Clave"
            type="select"
            required
            bind:value={newKey}
            error={newKeyError}
            options={[
              { value: "", label: "Elija una clave…" },
              ...clavesDisponibles.map((k) => ({ value: k, label: etiquetaDeParam(k) })),
            ]}
          />
        {/if}
        {#if clavesDisponibles.length > 0}
          <FormField
            id="new-param-value"
            label="Valor (JSON)"
            type="textarea"
            rows={3}
            bind:value={newValueRaw}
            error={newValueError}
            placeholder={'"texto", 123, true, {"a": 1} …'}
          />
          <Button type="submit" icon="fas fa-plus" loading={actionLoading}>Crear parámetro</Button>
        {/if}
      </form>
    {/if}
  </div>

  <!-- ===================================================================
       Panel: Festivos
       =================================================================== -->
  <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <div
    id="panel-festivos"
    role="tabpanel"
    aria-labelledby="tab-festivos"
    hidden={activeTab !== "festivos"}
    tabindex="0"
  >
    {#if data.holidaysError}
      <div class="param-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {data.holidaysError}
      </div>
    {:else if holidays.length === 0}
      <EmptyState
        icon="fas fa-calendar-days"
        title="Sin festivos registrados"
        description="Precarga el calendario colombiano con «Precargar festivos» o crea uno puntual."
      />
    {:else}
      <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
      <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
      <div class="param-table-wrap" role="region" aria-label="Calendario de festivos" tabindex="0">
        <table class="param-table" aria-label="Calendario de festivos">
          <thead>
            <tr>
              <th scope="col">Fecha</th>
              <th scope="col">Descripción</th>
              <th scope="col">Nacional</th>
              {#if canWriteHolidays}<th scope="col"><span class="sr-only">Acciones</span></th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each holidays as h (h.id)}
              <tr>
                <td>{h.fecha}</td>
                <td>{h.descripcion}</td>
                <td>{h.is_national ? "Sí" : "No"}</td>
                {#if canWriteHolidays}
                  <td class="param-table__actions">
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="fas fa-trash"
                      loading={actionLoading}
                      on:click={() => borrarFestivo(h)}
                    >
                      Eliminar
                    </Button>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    {#if canWriteHolidays}
      <form class="param-form" on:submit|preventDefault={crearFestivo} novalidate>
        <h2 class="param-page__section-title">Nuevo festivo puntual</h2>
        <div class="param-form__row">
          <FormField
            id="holiday-fecha"
            label="Fecha"
            type="date"
            required
            bind:value={hFecha}
            error={hFechaError}
          />
          <FormField
            id="holiday-descripcion"
            label="Descripción"
            required
            bind:value={hDescripcion}
            error={hDescripcionError}
          />
        </div>
        <Button type="submit" icon="fas fa-plus" loading={actionLoading}>Crear festivo</Button>
      </form>

      <form class="param-form" on:submit|preventDefault={precargarFestivos} novalidate>
        <h2 class="param-page__section-title">Precargar festivos colombianos (Ley 51/1983)</h2>
        <p class="param-page__hint">
          Calcula y guarda los festivos nacionales (fijos + Emiliani + móviles de Pascua) del rango
          de años indicado. No requiere conexión externa.
        </p>
        <div class="param-form__row">
          <FormField id="seed-from" label="Desde el año" type="number" bind:value={seedFrom} />
          <FormField id="seed-to" label="Hasta el año" type="number" bind:value={seedTo} />
        </div>
        <Button type="submit" variant="secondary" icon="fas fa-calendar-plus" loading={seedLoading}
          >Precargar festivos</Button
        >
      </form>
    {/if}
  </div>

  <!-- ===================================================================
       Panel: Calculadora de días hábiles
       =================================================================== -->
  <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <div
    id="panel-calculadora"
    role="tabpanel"
    aria-labelledby="tab-calculadora"
    hidden={activeTab !== "calculadora"}
    tabindex="0"
  >
    <p class="param-page__hint">
      Calcula la fecha de vencimiento de un plazo en días hábiles (RF-RAD-04) — descuenta fines de
      semana y los festivos registrados en la pestaña anterior.
    </p>
    <form
      class="param-form param-form--calc"
      on:submit|preventDefault={calcularDiasHabiles}
      novalidate
    >
      <div class="param-form__row">
        <FormField
          id="calc-from"
          label="Fecha de radicación"
          type="date"
          required
          bind:value={calcFrom}
        />
        <FormField
          id="calc-days"
          label="Días hábiles del plazo"
          type="number"
          required
          bind:value={calcDays}
        />
      </div>
      <Button type="submit" icon="fas fa-calculator" loading={calcLoading}>Calcular</Button>
    </form>

    {#if calcLoading}
      <Loader size="sm" label="Calculando…" />
    {:else if calcError}
      <div class="param-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {calcError}
      </div>
    {:else if calcResult}
      <div class="calc-result" aria-live="polite">
        <p class="calc-result__due">
          Fecha de vencimiento: <strong>{calcResult.due_date}</strong>
          <span class="calc-result__meta"
            >({calcResult.business_days} días hábiles desde {calcResult.from_date})</span
          >
        </p>
        {#if calcBreakdown.length === 0}
          <p class="param-page__hint">
            No hubo fines de semana ni festivos que descontar en este rango.
          </p>
        {:else}
          <p class="param-page__hint">
            Días descontados dentro del rango (no cuentan como hábiles):
          </p>
          <ul class="calc-result__breakdown">
            {#each calcBreakdown as d (d.fecha)}
              <li><code>{d.fecha}</code> — {d.motivo}</li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}
  </div>
</div>

<style lang="scss">
  .param-page {
    padding: var(--op-space-5);
    max-width: var(--op-content-max);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__title {
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__subtitle {
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__section-title {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: var(--op-space-2) 0 0;
    }

    &__hint {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      background: var(--op-error-bg);
      color: var(--op-error-fg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
    }

    &__value {
      font-size: var(--op-font-xs);
      word-break: break-word;
    }
  }

  .param-tabs {
    display: flex;
    background: var(--op-surface);
    border-bottom: 2px solid var(--op-border);
    overflow-x: auto;
    // `position: relative` NO es cosmético: sin bloque contenedor propio, un
    // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
    // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
    // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
    position: relative;

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

  .param-table-wrap {
    overflow-x: auto;
    // `position: relative` NO es cosmético: sin bloque contenedor propio, un
    // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
    // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
    // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
    position: relative;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    margin-bottom: var(--op-space-4);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }
  }

  .param-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th,
    td {
      padding: var(--op-space-3) var(--op-space-4);
      text-align: left;
      border-bottom: 1px solid var(--op-border);
      vertical-align: top;
    }

    th {
      background: var(--op-bg);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    tbody tr:last-child td {
      border-bottom: none;
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
      white-space: nowrap;
    }
  }

  .param-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding: var(--op-space-4);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-surface);
    margin-bottom: var(--op-space-4);

    &__row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: var(--op-space-3);
    }

    &--calc {
      margin-bottom: var(--op-space-4);
    }
  }

  .calc-result {
    padding: var(--op-space-4);
    border: 1px solid var(--op-primary);
    background: var(--op-primary-light);
    border-radius: var(--op-radius);

    &__due {
      margin: 0 0 var(--op-space-2);
      color: var(--op-text);
    }

    &__meta {
      // Contraste AA (revisión UX lote 2): `--op-text-secondary` sobre el
      // fondo `--op-primary-light` de `.calc-result` mide ~4.0:1, bajo AA.
      // `--op-text` sí cumple sobre ese mismo fondo.
      color: var(--op-text);
      font-size: var(--op-font-sm);
    }

    &__breakdown {
      margin: 0;
      padding-left: var(--op-space-5);
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }
  }
</style>
