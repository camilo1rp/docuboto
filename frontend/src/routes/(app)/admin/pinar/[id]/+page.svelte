<script>
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import { formatearFecha, formatearFechaHora } from "$lib/utils/fecha.js";
  import { invalidateAll } from "$app/navigation";
  import { tick } from "svelte";

  import Card from "$lib/components/ui/Card.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Stepper from "$lib/components/ui/Stepper.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";

  import { permissions } from "$lib/stores/auth.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: plan = data.plan;
  $: tablero = data.tablero;

  $: canManage = $permissions.includes("USUA_PERM_ADMIN");

  // ---------------------------------------------------------------------------
  // Ciclo de vida (espejo de pinar_service.py — la UI nunca ofrece lo que el
  // backend rechazaría: aprobar solo desde 'borrador', ejecutar solo desde
  // 'aprobado', cerrar desde 'aprobado' o 'en_ejecucion')
  // ---------------------------------------------------------------------------
  const ESTADOS = ["borrador", "aprobado", "en_ejecucion", "cerrado"];
  const ESTADO_LABELS = {
    borrador: "Borrador",
    aprobado: "Aprobado",
    en_ejecucion: "En ejecución",
    cerrado: "Cerrado",
  };
  const ESTADO_CHIP_MAP = {
    borrador: "neutral",
    aprobado: "info",
    en_ejecucion: "active",
    cerrado: "neutral",
  };
  const STEPS = ["Borrador", "Aprobado", "En ejecución", "Cerrado"];

  $: stepperCurrent = Math.max(0, ESTADOS.indexOf(plan?.estado));
  $: canAprobar = canManage && plan?.estado === "borrador";
  $: canEjecutar = canManage && plan?.estado === "aprobado";
  $: canCerrar = canManage && (plan?.estado === "aprobado" || plan?.estado === "en_ejecucion");

  /** @param {any} err @param {string} fallback */
  function friendlyActionError(err, fallback) {
    if (codigoError(err) === "forbidden") {
      return "No tiene el nivel de permiso necesario para esta acción; consulte con el administrador de su entidad.";
    }
    return mensajeError(err, fallback);
  }

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    return formatearFecha(iso, { estilo: "medio" });
  }

  /** @param {string|null|undefined} iso */
  function formatDateTime(iso) {
    if (!iso) return "—";
    return formatearFechaHora(iso);
  }

  // ---------------------------------------------------------------------------
  // Pestañas
  // ---------------------------------------------------------------------------
  const TABS = [
    { id: "resumen", label: "Resumen", icon: "fa-map" },
    { id: "tablero", label: "Tablero", icon: "fa-gauge-high" },
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
    }
  }

  // ---------------------------------------------------------------------------
  // Tablero: refresco bajo demanda (proxy dedicado) sin recargar toda la vista
  // ---------------------------------------------------------------------------
  let tableroRefreshing = false;

  async function refrescarTablero() {
    tableroRefreshing = true;
    try {
      const res = await fetch(`/admin/pinar/api/planes/${plan.id}/tablero`);
      const result = await res.json();
      if (!res.ok) throw result;
      tablero = { data: result, error: null };
    } catch (err) {
      tablero = {
        data: null,
        error: { detail: mensajeError(err, "No se pudo actualizar el tablero.") },
      };
    } finally {
      tableroRefreshing = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Acciones de ciclo de vida (irreversibles) — siempre con Modal de
  // confirmación previo, patrón de expedientes/[id]/+page.svelte
  // ---------------------------------------------------------------------------
  let showAprobarModal = false;
  let showEjecutarModal = false;
  let showCerrarModal = false;
  let actionLoading = false;
  let fActoAdministrativo = "";

  async function focusTitleAfterAction() {
    await tick();
    document.getElementById("pinar-workspace-title")?.focus();
  }

  async function handleAprobar() {
    if (!fActoAdministrativo.trim()) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/pinar/api/planes/${plan.id}/aprobar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ acto_administrativo: fActoAdministrativo.trim() }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(
        `Plan PINAR v${plan.version} aprobado. El contenido estructural queda congelado.`,
      );
      showAprobarModal = false;
      fActoAdministrativo = "";
      await invalidateAll();
      await focusTitleAfterAction();
    } catch (err) {
      notifyError(friendlyActionError(err, "No se pudo aprobar el plan PINAR."));
      showAprobarModal = false;
    } finally {
      actionLoading = false;
    }
  }

  async function handleEjecutar() {
    actionLoading = true;
    try {
      const res = await fetch(`/admin/pinar/api/planes/${plan.id}/ejecutar`, { method: "POST" });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Plan PINAR v${plan.version} pasó a ejecución.`);
      showEjecutarModal = false;
      await invalidateAll();
      await focusTitleAfterAction();
    } catch (err) {
      notifyError(friendlyActionError(err, "No se pudo pasar el plan PINAR a ejecución."));
      showEjecutarModal = false;
    } finally {
      actionLoading = false;
    }
  }

  async function handleCerrar() {
    actionLoading = true;
    try {
      const res = await fetch(`/admin/pinar/api/planes/${plan.id}/cerrar`, { method: "POST" });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Plan PINAR v${plan.version} cerrado.`);
      showCerrarModal = false;
      await invalidateAll();
      await focusTitleAfterAction();
    } catch (err) {
      notifyError(friendlyActionError(err, "No se pudo cerrar el plan PINAR."));
      showCerrarModal = false;
    } finally {
      actionLoading = false;
    }
  }
</script>

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
  admin/pinar/[id] — Workspace de un plan PINAR (RF-ADM-08, Ac. AGN 003/2015,
  Fase 7 cierre de desfase API↔UI).

  Ciclo de vida (Stepper puramente informativo — NO navegable por clic, las
  transiciones son actos administrativos con botones + confirmación propios).
  Flechas con «→» a propósito: guion-guion-mayor cierra el comentario HTML y
  vuelca el resto del texto en la página (ver InstrumentLifecycleActions.svelte,
  donde el mismo diagrama llegó a mostrarse al usuario en /admin/trd).

    borrador  →(aprobar)→  aprobado  →(ejecutar)→  en_ejecucion  →(cerrar)→  cerrado

  Honestidad de la interfaz: "aprobar" congela el contenido estructural del plan
  (audit_log inmutable) y NO tiene vuelta atrás — el Modal de confirmación lo
  dice ANTES de ejecutar, no después de un error 409. Lo mismo para "ejecutar"
  (no hay transición de retorno) y "cerrar" (estado terminal). Reformular = crear
  una versión nueva desde el listado.

  Pestañas (tablist ARIA, mismo patrón que expedientes/[id]):
    0 — Resumen  → metadatos del plan
    1 — Tablero  → avance por objetivo / por eje (RF-ADM-08 §mapa de ruta)
-->

<svelte:head>
  <title>Plan PINAR v{plan?.version} — Administración — OrpycaMCP</title>
</svelte:head>

<div class="pinar-workspace">
  <header class="pinar-workspace__header">
    <div>
      <h1 id="pinar-workspace-title" class="pinar-workspace__title" tabindex="-1">
        Plan PINAR — versión {plan.version}
      </h1>
      <StatusChip
        status={ESTADO_CHIP_MAP[plan.estado] ?? "neutral"}
        label={ESTADO_LABELS[plan.estado] ?? plan.estado}
      />
    </div>
    <div class="pinar-workspace__actions">
      {#if canAprobar}
        <Button variant="primary" icon="fas fa-stamp" on:click={() => (showAprobarModal = true)}>
          Aprobar
        </Button>
      {/if}
      {#if canEjecutar}
        <Button variant="primary" icon="fas fa-play" on:click={() => (showEjecutarModal = true)}>
          Pasar a ejecución
        </Button>
      {/if}
      {#if canCerrar}
        <Button variant="secondary" icon="fas fa-lock" on:click={() => (showCerrarModal = true)}>
          Cerrar plan
        </Button>
      {/if}
    </div>
  </header>

  <div class="pinar-workspace__stepper">
    <Stepper steps={STEPS} current={stepperCurrent} canGoBack={false} />
  </div>

  <!-- Tablist -->
  <div class="pinar-tabs" role="tablist" aria-label="Facetas del plan PINAR">
    {#each TABS as tab (tab.id)}
      <button
        id="tab-{tab.id}"
        class="pinar-tabs__tab"
        class:pinar-tabs__tab--active={activeTab === tab.id}
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

  <div class="pinar-workspace__panels">
    <!-- Panel: Resumen -->
    <div
      id="panel-resumen"
      role="tabpanel"
      aria-labelledby="tab-resumen"
      hidden={activeTab !== "resumen"}
      tabindex="0"
    >
      <Card>
        <h2 slot="header" class="op-card-title">Metadatos del plan</h2>
        <dl class="pinar-meta-list">
          <div>
            <dt>Visión</dt>
            <dd>{plan.vision ?? "—"}</dd>
          </div>
          <div>
            <dt>Vigencia</dt>
            <dd>
              {#if plan.vigencia_desde || plan.vigencia_hasta}
                {formatDate(plan.vigencia_desde)} – {formatDate(plan.vigencia_hasta)}
              {:else}
                —
              {/if}
            </dd>
          </div>
          <div>
            <dt>Acto administrativo</dt>
            <dd>{plan.acto_administrativo ?? "—"}</dd>
          </div>
          <div>
            <dt>Aprobado</dt>
            <dd>{plan.aprobado_at ? formatDateTime(plan.aprobado_at) : "Aún no aprobado"}</dd>
          </div>
          <div>
            <dt>Creado</dt>
            <dd>{formatDateTime(plan.created_at)}</dd>
          </div>
        </dl>
        {#if plan.estado === "borrador"}
          <p class="pinar-workspace__hint">
            <i class="fas fa-circle-info" aria-hidden="true"></i>
            Mientras el plan esté en <strong>borrador</strong> puede completarse su contenido. Al
            <strong>aprobarlo</strong>, el contenido estructural queda congelado de forma permanente
            (el asiento se registra en la auditoría inmutable) — no podrá volver a editarse; para
            cambios posteriores se crea una versión nueva desde el listado.
          </p>
        {/if}
      </Card>
    </div>

    <!-- Panel: Tablero -->
    <div
      id="panel-tablero"
      role="tabpanel"
      aria-labelledby="tab-tablero"
      hidden={activeTab !== "tablero"}
      tabindex="0"
    >
      <div class="pinar-workspace__tablero-toolbar">
        <Button
          variant="ghost"
          size="sm"
          icon="fas fa-rotate"
          loading={tableroRefreshing}
          on:click={refrescarTablero}
        >
          Actualizar
        </Button>
      </div>

      {#if tablero.error}
        <div class="pinar-workspace__error" role="alert">
          <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
          No se pudo cargar el tablero: {tablero.error.detail}
        </div>
      {:else if !tablero.data}
        <Loader type="skeleton" lines={4} label="Cargando tablero…" />
      {:else}
        <div class="pinar-tablero-grid">
          <Card>
            <h3 slot="header" class="op-card-title">Avance por objetivo</h3>
            {#if tablero.data.por_objetivo.length === 0}
              <EmptyState
                icon="fas fa-bullseye"
                title="Sin objetivos con proyectos"
                description="El avance por objetivo aparecerá cuando el plan tenga objetivos con proyectos asociados (fase posterior de PINAR)."
              />
            {:else}
              <table class="pinar-tablero-table" aria-label="Avance por objetivo">
                <thead>
                  <tr>
                    <th scope="col">Objetivo</th>
                    <th scope="col">Proyectos</th>
                    <th scope="col">Avance</th>
                  </tr>
                </thead>
                <tbody>
                  {#each tablero.data.por_objetivo as fila (fila.objetivo_id ?? fila.descripcion)}
                    <tr>
                      <td>{fila.descripcion ?? "Sin objetivo asignado"}</td>
                      <td>{fila.proyectos}</td>
                      <td>{fila.avance_promedio.toFixed(0)}%</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </Card>

          <Card>
            <h3 slot="header" class="op-card-title">Avance por eje estratégico</h3>
            {#if tablero.data.por_eje.length === 0}
              <EmptyState
                icon="fas fa-chart-line"
                title="Sin ejes con proyectos"
                description="El avance por eje aparecerá cuando haya proyectos vinculados a aspectos priorizados por eje (fase posterior de PINAR)."
              />
            {:else}
              <table class="pinar-tablero-table" aria-label="Avance por eje estratégico">
                <thead>
                  <tr>
                    <th scope="col">Eje</th>
                    <th scope="col">Proyectos</th>
                    <th scope="col">Avance</th>
                  </tr>
                </thead>
                <tbody>
                  {#each tablero.data.por_eje as fila (fila.eje_code)}
                    <tr>
                      <td>{fila.nombre}</td>
                      <td>{fila.proyectos}</td>
                      <td>{fila.avance_promedio.toFixed(0)}%</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </Card>
        </div>
      {/if}
    </div>
  </div>
</div>

<!-- Modal: Confirmar aprobación (IRREVERSIBLE — congela el contenido) -->
<Modal
  open={showAprobarModal}
  title="Aprobar plan PINAR"
  size="sm"
  on:close={() => (showAprobarModal = false)}
>
  <p class="pinar-workspace__warning">
    <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
    Esta acción es <strong>irreversible</strong>: al aprobar, el contenido estructural del plan
    (visión, vigencia, aspectos, objetivos) queda <strong>congelado de forma permanente</strong> y el
    acto queda registrado en la auditoría inmutable. No podrá deshacerse ni volver a "borrador" — para
    cambios posteriores deberá crear una versión nueva.
  </p>
  <FormField
    id="pinar-acto-administrativo"
    label="Acto administrativo"
    required
    hint="Resolución/acuerdo que sustenta la aprobación (máx. 200 caracteres)."
    bind:value={fActoAdministrativo}
  />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showAprobarModal = false)} disabled={actionLoading}
      >Cancelar</Button
    >
    <Button
      variant="primary"
      icon="fas fa-stamp"
      disabled={actionLoading || !fActoAdministrativo.trim()}
      loading={actionLoading}
      on:click={handleAprobar}>Confirmar aprobación</Button
    >
  </svelte:fragment>
</Modal>

<!-- Modal: Confirmar ejecución -->
<Modal
  open={showEjecutarModal}
  title="Pasar a ejecución"
  size="sm"
  on:close={() => (showEjecutarModal = false)}
>
  <p class="pinar-workspace__warning">
    <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
    Esta transición es <strong>irreversible</strong> (no existe un paso atrás a "aprobado"). Solo
    puede haber un plan PINAR <strong>en ejecución</strong> por entidad — si ya existe otro, la operación
    se rechazará.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showEjecutarModal = false)} disabled={actionLoading}
      >Cancelar</Button
    >
    <Button variant="primary" icon="fas fa-play" loading={actionLoading} on:click={handleEjecutar}
      >Confirmar</Button
    >
  </svelte:fragment>
</Modal>

<!-- Modal: Confirmar cierre (TERMINAL) -->
<Modal
  open={showCerrarModal}
  title="Cerrar plan PINAR"
  size="sm"
  on:close={() => (showCerrarModal = false)}
>
  <p class="pinar-workspace__warning">
    <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
    <strong>Cerrado es un estado terminal</strong>: no existe una transición para reabrir este plan.
    Para continuar la gestión archivística deberá crear una versión nueva.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showCerrarModal = false)} disabled={actionLoading}
      >Cancelar</Button
    >
    <Button variant="secondary" icon="fas fa-lock" loading={actionLoading} on:click={handleCerrar}
      >Confirmar cierre</Button
    >
  </svelte:fragment>
</Modal>

<style lang="scss">
  .pinar-workspace {
    padding: var(--op-space-5);
    max-width: 1100px;

    &__header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-4);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-2);

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 3px;
      }
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__stepper {
      margin-bottom: var(--op-space-5);
      max-width: 480px;
    }

    &__hint {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      margin-top: var(--op-space-4);
      padding: var(--op-space-3);
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
      line-height: 1.5;
    }

    &__warning {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      color: var(--op-text);
      font-size: var(--op-font-sm);
      line-height: 1.5;
      margin-bottom: var(--op-space-4);

      i {
        color: var(--op-warning);
        flex-shrink: 0;
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

    &__tablero-toolbar {
      display: flex;
      justify-content: flex-end;
      margin-bottom: var(--op-space-3);
    }

    &__panels {
      padding-top: var(--op-space-4);
    }
  }

  .pinar-meta-list {
    display: grid;
    gap: var(--op-space-3);

    dt {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 2px;
    }

    dd {
      margin: 0;
      color: var(--op-text);
      font-size: var(--op-font-sm);
    }
  }

  .pinar-tabs {
    display: flex;
    background: var(--op-surface);
    border-bottom: 2px solid var(--op-border);
    overflow-x: auto;
    scrollbar-width: none;

    &::-webkit-scrollbar {
      display: none;
    }

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
      transition:
        color 0.15s,
        border-color 0.15s;

      &:hover {
        color: var(--op-primary-dark);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: -2px;
      }

      &--active {
        color: var(--op-primary-dark);
        border-bottom-color: var(--op-primary);
      }
    }
  }

  .pinar-tablero-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: var(--op-space-4);
  }

  .pinar-tablero-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th,
    td {
      padding: var(--op-space-2) var(--op-space-3);
      text-align: left;
      border-bottom: 1px solid var(--op-border);
    }

    thead th {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    tbody tr:last-child td {
      border-bottom: none;
    }
  }
</style>
