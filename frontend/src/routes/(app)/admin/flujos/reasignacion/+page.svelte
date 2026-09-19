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
  /admin/flujos/reasignacion — Reasignación en cascada (RF-FLU-08).

  Traslada TODOS los pasos activos (`status IN ('pending','in_progress')`) de
  un usuario o dependencia de origen a un nuevo responsable. Sirve para
  cuando alguien se va de la entidad o cambia de área. Acción MASIVA E
  IRREVERSIBLE (`workflow_repository.py::reassign_cascade` no tiene
  deshacer) → doble resguardo:
    1. Vista previa ESTIMADA de cuántos pasos se verían afectados (ver nota
       de límite abajo — el backend no ofrece un endpoint de simulación).
    2. Confirmación ESCRITA (teclear "REASIGNAR", no un clic en "Aceptar").

  LÍMITE DECLARADO DE LA VISTA PREVIA: `workflow-service` no expone un modo
  de simulación para `POST /workflows/reassign/cascade` (se verificó
  `app/routers/workflow.py` y `app/services/workflow_service.py` — no
  existe). La vista previa de esta pantalla reutiliza `GET /workflows/pending`
  (mismo predicado WHERE que usa la reasignación real, ver
  `api/preview/+server.js`), pero ese endpoint filtra ADEMÁS por el
  clearance del llamante (RF-SEG-08 no-read-up) — un paso clasificado por
  encima del clearance del administrador que hace la consulta no se cuenta
  en la vista previa, pero SÍ se reasignaría al ejecutar. El número se
  etiqueta explícitamente como "estimado" en la UI, nunca como el resultado
  exacto (el mensaje de la sección 3 explica esto en lenguaje llano, sin
  remitir al administrador al código de esta pantalla).

  RBAC: requiere USUA_PERM_ADMIN (backend: `dependencies=[Depends(
  require_permission("USUA_PERM_ADMIN"))]` en `reassign/cascade` — a
  diferencia de las reglas de enrutamiento, este endpoint SÍ está gateado).

  D-05 — access_token NUNCA en cliente: la ejecución va a
  /admin/flujos/reasignacion/api; la vista previa a
  /admin/flujos/reasignacion/api/preview.
-->
<script>
  import { onMount, tick } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError } from "$lib/stores/notifications.js";
  import { isReassignConfirmed, REASSIGN_CONFIRM_PHRASE } from "$lib/utils/reassignConfirm.js";

  import UserPicker from "$lib/components/ui/UserPicker.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: users = data.users ?? [];
  // Desplegable de dependencias activas (T-08c A10): antes esta pantalla
  // pedía la dependencia como texto libre, referenciando el nombre de una
  // columna del backend ("to_dept") — ahora ofrece el catálogo real, igual
  // que `bandeja/+page.svelte`.
  $: dependenciaOptions = (data.dependencias ?? []).map((d) => ({
    value: d.nombre,
    label: `${d.sigla ? d.sigla + " — " : ""}${d.nombre}`,
  }));
  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  /** @type {'usuario'|'dependencia'} */
  let originType = "usuario";
  /** @type {string[]} — UserPicker en modo selección única (max=1) */
  let fromUserIds = [];
  let fromDept = "";
  /** @type {string[]} — UserPicker en modo selección única (max=1) */
  let toUserIds = [];
  let confirmText = "";

  let previewLoading = false;
  let previewError = "";
  /** @type {number|null} */
  let previewCount = null;
  let previewedFor = "";

  let executing = false;
  let resultMessage = "";
  /** @type {HTMLElement|null} — recibe el foco tras ejecutar (A12: no perderlo tras la acción irreversible). */
  let resultEl = null;

  $: originValue = originType === "usuario" ? fromUserIds[0] : fromDept.trim();
  $: originReady = originType === "usuario" ? fromUserIds.length === 1 : fromDept.trim().length > 0;
  $: destinationReady = toUserIds.length === 1;
  $: sameOriginAndDestination =
    originType === "usuario" && originReady && destinationReady && fromUserIds[0] === toUserIds[0];
  $: confirmReady = isReassignConfirmed(confirmText);
  $: canExecute = originReady && destinationReady && !sameOriginAndDestination && confirmReady;

  // Qué falta para poder ejecutar — el botón tiene 4 requisitos y, cuando
  // está deshabilitado, el administrador no puede adivinar cuál incumple.
  // El caso "origen == destino" ya tiene su propio mensaje dedicado (sección
  // 2, `sameOriginAndDestination`), así que no se repite aquí.
  $: missingRequirements = [
    !originReady ? "seleccione el origen" : null,
    !destinationReady ? "seleccione el destino" : null,
    !confirmReady ? `escriba "${REASSIGN_CONFIRM_PHRASE}" para confirmar` : null,
  ].filter(Boolean);

  // La estimación queda obsoleta si cambia el origen tras haberla pedido.
  $: if (previewedFor && previewedFor !== `${originType}:${originValue ?? ""}`) {
    previewCount = null;
    previewError = "";
  }

  async function fetchPreview() {
    if (!originReady) return;
    previewLoading = true;
    previewError = "";
    try {
      const params = new URLSearchParams();
      if (originType === "usuario") params.set("assigned_to", fromUserIds[0]);
      else params.set("dept", fromDept.trim());

      const res = await fetch(`/admin/flujos/reasignacion/api/preview?${params}`);
      const resData = await res.json();
      if (!res.ok) {
        previewError = mensajeError(resData, "No se pudo estimar el impacto.");
        previewCount = null;
        return;
      }
      previewCount = resData.count;
      previewedFor = `${originType}:${originValue ?? ""}`;
    } catch {
      previewError = "No se pudo conectar al servidor.";
      previewCount = null;
    } finally {
      previewLoading = false;
    }
  }

  async function executeReassign() {
    resultMessage = "";
    if (!canExecute) return;

    executing = true;
    try {
      const payload = {
        to_assigned_to: toUserIds[0],
        confirm: confirmText.trim(),
      };
      if (originType === "usuario") payload.from_assigned_to = fromUserIds[0];
      else payload.from_dept = fromDept.trim();

      const res = await fetch("/admin/flujos/reasignacion/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo ejecutar la reasignación."));
        return;
      }
      resultMessage = `Se reasignaron ${resData.reassigned} paso(s) pendiente(s).`;
      // Limpia el formulario tras una ejecución exitosa — evita un doble
      // envío accidental por un segundo clic.
      fromUserIds = [];
      fromDept = "";
      toUserIds = [];
      confirmText = "";
      previewCount = null;
      previewedFor = "";
      // A12 — el botón enfocado se deshabilita al limpiar el formulario;
      // sin esto el foco caería al principio del documento justo después de
      // una reasignación masiva. Se anuncia una sola vez (este `role="status"`
      // con `aria-live`); ya no se duplica con un toast (mismo patrón que
      // `bandeja/+page.svelte` con su título).
      await tick();
      resultEl?.focus();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      executing = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([
      { label: "Administración", href: "/admin" },
      { label: "Flujos y reglas", href: "/admin/flujos/reglas" },
      { label: "Reasignación en cascada" },
    ]);
  });
</script>

<svelte:head>
  <title>Reasignación en cascada — OrpycaMCP</title>
</svelte:head>

<div class="reasig-page">
  <header class="reasig-page__header">
    <h1 class="reasig-page__title">Reasignación en cascada</h1>
    <p class="reasig-page__subtitle">
      Traslada en bloque todos los pasos pendientes de un usuario o una dependencia hacia un nuevo
      responsable. Útil cuando alguien se va de la entidad o cambia de área.
    </p>
  </header>

  {#if !isAdmin}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de administración"
      description="Tu usuario no tiene el permiso de administración necesario para reasignar en cascada."
    />
  {:else}
    <!-- Aviso persistente desde el montaje, no algo que ocurra después —
         `role="alert"` es para lo segundo; un contenedor con estilo de aviso
         basta y no interrumpe al lector de pantalla al cargar la página. -->
    <div class="reasig-page__warning">
      <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
      <div>
        <strong>Acción masiva e irreversible.</strong> Se reasignan todos los pasos activos del origen
        elegido; no hay un "deshacer" automático.
      </div>
    </div>

    {#if data.loadError}
      <div class="reasig-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {data.loadError}
      </div>
    {/if}

    {#if data.usersIncompletos}
      <!-- Reasignar la carga de una dependencia eligiendo de una lista recortada
           movería el trabajo a la persona equivocada por no encontrar a la
           correcta. Un selector incompleto no se ve incompleto: se ve como que
           esa persona no está. -->
      <div class="reasig-page__aviso" role="status">
        <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
        La lista muestra {users.length} de {data.usersTotal} usuarios activos. Si no encuentra a la persona
        de destino, no asuma que no existe.
      </div>
    {/if}

    <section class="reasig-section" aria-labelledby="reasig-origen-title">
      <h2 id="reasig-origen-title" class="reasig-section__title">1. Origen</h2>

      <fieldset class="reasig-origin-type">
        <legend class="sr-only">Tipo de origen</legend>
        <label class="reasig-origin-type__option">
          <input type="radio" bind:group={originType} value="usuario" />
          Por usuario
        </label>
        <label class="reasig-origin-type__option">
          <input type="radio" bind:group={originType} value="dependencia" />
          Por dependencia
        </label>
      </fieldset>

      {#if originType === "usuario"}
        <UserPicker
          id="reasig-origen-usuario"
          label="Usuario de origen (los pasos pendientes asignados a este usuario se reasignan)"
          {users}
          bind:value={fromUserIds}
          max={1}
          listTitle="Usuario de origen"
          hint="Seleccione un único usuario de origen."
        />
      {:else}
        <FormField
          id="reasig-origen-dependencia"
          label="Dependencia de origen"
          type="select"
          placeholder="Seleccione una dependencia"
          options={dependenciaOptions}
          bind:value={fromDept}
          hint="El backend valida que la dependencia elegida exista y esté activa."
        />
      {/if}
    </section>

    <section class="reasig-section" aria-labelledby="reasig-destino-title">
      <h2 id="reasig-destino-title" class="reasig-section__title">2. Destino</h2>
      <UserPicker
        id="reasig-destino-usuario"
        label="Usuario destino (recibe todos los pasos reasignados)"
        {users}
        bind:value={toUserIds}
        max={1}
        listTitle="Usuario destino"
        hint="Seleccione un único usuario destino."
      />
      {#if sameOriginAndDestination}
        <p class="reasig-section__error" role="alert">
          El origen y el destino no pueden ser el mismo usuario.
        </p>
      {/if}
    </section>

    <section class="reasig-section" aria-labelledby="reasig-preview-title">
      <h2 id="reasig-preview-title" class="reasig-section__title">3. Vista previa (estimada)</h2>
      <p class="reasig-section__hint">
        Estimación basada en los pasos pendientes visibles hoy para el origen elegido. Puede
        quedarse corta: los radicados clasificados por encima de tu nivel de acceso no se cuentan
        aquí, pero sí se reasignarán.
      </p>
      <Button
        variant="secondary"
        icon="fas fa-magnifying-glass"
        disabled={!originReady}
        loading={previewLoading}
        on:click={fetchPreview}
      >
        Estimar impacto
      </Button>

      {#if previewError}
        <p class="reasig-section__error" role="alert">{previewError}</p>
      {:else if previewCount !== null}
        <p class="reasig-preview-result" aria-live="polite">
          <i class="fas fa-circle-info" aria-hidden="true"></i>
          <span>
            Se estima que se verían afectados <strong>{previewCount}</strong> paso(s) pendiente(s) (estimado,
            no el conteo exacto).
          </span>
        </p>
      {/if}
    </section>

    <section class="reasig-section" aria-labelledby="reasig-confirm-title">
      <h2 id="reasig-confirm-title" class="reasig-section__title">4. Confirmación</h2>
      <FormField
        id="reasig-confirm"
        label={`Escriba "${REASSIGN_CONFIRM_PHRASE}" para habilitar la ejecución`}
        type="text"
        bind:value={confirmText}
        hint="Un clic en «Ejecutar» no basta para esta acción — debe teclear la palabra de confirmación."
      />

      <Button
        variant="danger"
        icon="fas fa-people-arrows"
        disabled={!canExecute}
        loading={executing}
        on:click={executeReassign}
      >
        Ejecutar reasignación
      </Button>

      {#if !canExecute && missingRequirements.length > 0}
        <p class="reasig-section__hint" id="reasig-missing">
          Falta: {missingRequirements.join("; ")}.
        </p>
      {/if}

      {#if resultMessage}
        <!-- A12 — recibe el foco (tabindex="-1") tras ejecutar: el botón que
             disparó la acción se deshabilita al limpiar el formulario, así
             que sin esto quien navega con teclado quedaría sin posición
             justo después de una reasignación masiva (mismo patrón que
             `bandeja/+page.svelte` con su título). -->
        <p
          class="reasig-result"
          role="status"
          aria-live="polite"
          tabindex="-1"
          bind:this={resultEl}
        >
          {resultMessage}
        </p>
      {/if}
    </section>
  {/if}
</div>

<style lang="scss">
  .reasig-page {
    padding: var(--op-space-5);
    max-width: 720px;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);

    &__header {
      margin-bottom: 0;
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

    &__warning {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-3);
      padding: var(--op-space-3) var(--op-space-4);
      background: var(--op-warning-bg);
      color: var(--op-text);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);

      i {
        // A7 — --op-warning (#c8881c) sobre --op-warning-bg da ~2.72:1, por
        // debajo del 3:1 mínimo para elementos gráficos significativos.
        // --op-warning-fg (#7a5700) sobre el mismo fondo da ~5.96:1.
        color: var(--op-warning-fg);
        margin-top: 2px;
      }
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
  }

  .reasig-page__aviso {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    background: var(--op-bg);
    border-left: 3px solid var(--op-warning);
    padding: var(--op-space-2) var(--op-space-3);
    margin-bottom: var(--op-space-3);
    font-size: var(--op-font-sm);
  }

  .reasig-section {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding: var(--op-space-4);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    background: var(--op-surface);

    &__title {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error {
      font-size: var(--op-font-xs);
      color: var(--op-error);
      margin: 0;
    }
  }

  .reasig-origin-type {
    display: flex;
    gap: var(--op-space-4);
    border: none;
    padding: 0;
    margin: 0;

    &__option {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
    }
  }

  .reasig-preview-result {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    margin: 0;

    i {
      color: var(--op-info);
    }
  }

  .reasig-result {
    font-size: var(--op-font-sm);
    color: var(--op-success);
    font-weight: var(--op-weight-medium);
    margin: 0;
  }
</style>
