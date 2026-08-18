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
  WorkflowBoard.svelte — Tablero de distribución de flujos (T-Fase1, RF-FLU-03).

  Fuente de datos: `GET /bandeja/api/inbox` (pasos activos asignados a mí/mi
  dependencia) o `GET /bandeja/api/pending` (pasos pendientes del equipo,
  `source="pending"`). Ambos devuelven `FlowStepResponse[]` — NO metadatos
  completos del documento (no hay `subject`/asunto en este contrato; ver
  `workflow-service/app/schemas/workflow.py::FlowStepResponse`). Las tarjetas
  muestran lo que el contrato realmente ofrece: número de radicado, tipo,
  dependencias de origen/destino, estado del paso, semáforo de vencimiento y
  notas — el asunto completo se consulta en la vista Lista o en el drawer de
  detalle, que sí usa `GET /documents`.

  Columnas (reales, por `status` del paso — los únicos dos valores que puede
  traer /inbox y /pending, ver `workflow_repository.py::list_inbox`):
    - "Pendientes"  → status = pending
    - "En trámite"  → status = in_progress

  Cada tarjeta expone CUATRO acciones, siempre como botones visibles (no un
  menú oculto) para que el operativo de teclado sea idéntico al de mouse:
    - Trasladar   → abre TrasladarModal
    - Trámite     → abre TransaccionModal
    - Completar   → PATCH directo (no requiere datos adicionales — no hay
                    modal "CompletarModal" en el alcance de esta pantalla)
    - Revertir    → abre RevertirModal (destructivo)

  "Asignar a flujo" NO vive aquí: este tablero solo lista pasos que YA
  existen (GET /workflows/inbox|pending); un radicado sin paso no aparece en
  ninguna columna, así que ofrecer "Asignar" como acción global de este
  tablero era un modal sin radicado real detrás (obligaba a teclear el UUID
  a mano). El punto de entrada correcto es la vista Lista de /bandeja — un
  radicado ya en contexto — ver AsignarModal.svelte y bandeja/+page.svelte.

  El ARRASTRE es una MEJORA sobre lo anterior, no un mecanismo alternativo:
  una tarjeta en "Pendientes" se puede soltar sobre la columna "En trámite"
  para ejecutar el trámite de catálogo `marcar_leido` (el único tipo cuyo
  `target_step_status` es `in_progress` — ver migración
  `workflow-service/migrations/tenant/007_transaction_types.sql`). La MISMA
  acción está disponible sin arrastrar: cada tarjeta en "Pendientes" tiene un
  botón "Marcar en trámite". Trasladar/Trámite/Revertir necesitan datos que un
  arrastre no puede transportar (dependencia destino, tipo de trámite, motivo)
  y por eso viven solo en los botones — accesibles por teclado de extremo a
  extremo, igual que el resto de la pantalla.

  Props:
    source        'inbox'|'pending'     — endpoint de origen (default 'inbox')
    dependencias  Array<{value,label}>  — catálogo SSR de dependencias activas
    canTramitar   boolean (default true) — espejo de PERM_TRAMITAR (D-05); gatea
                  las acciones de ejecución de cada WorkflowCard, nunca "Ver detalle"

  Eventos:
    on:verDetalle  — se pidió ver el detalle de un radicado (Ver la reseña de
                     revisión UX, hallazgo A3); detail = FlowStepResponse. El
                     padre resuelve la apertura del cajón (openDrawer en
                     bandeja/+page.svelte ya sabe hacerlo).
-->
<script>
  import { onMount, tick, createEventDispatcher } from "svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import TrasladarModal from "./TrasladarModal.svelte";
  import TransaccionModal from "./TransaccionModal.svelte";
  import RevertirModal from "./RevertirModal.svelte";
  import WorkflowCard from "./WorkflowCard.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {'inbox'|'pending'} */
  export let source = "inbox";
  /** @type {Array<{value: string; label: string}>} */
  export let dependencias = [];
  export let canTramitar = true;

  const dispatch = createEventDispatcher();

  const ENDPOINT = { inbox: "/bandeja/api/inbox", pending: "/bandeja/api/pending" };
  // Límite pedido a /inbox|/pending (A4, revisión UX): esta vista NO pagina —
  // si el backend tiene más pasos que este tamaño de página, se avisa en vez
  // de fingir que el tablero está completo (ver `truncated` más abajo).
  const PAGE_SIZE = 100;

  /** @type {Array<{value: 'entrada'|'salida'|'internos'|''; label: string}>} */
  const BOX_OPTIONS = [
    { value: "", label: "Todos" },
    { value: "entrada", label: "Entrada" },
    { value: "salida", label: "Salida" },
    { value: "internos", label: "Internos" },
  ];

  /** @type {'entrada'|'salida'|'internos'|''} */
  let box = "";
  let loading = true;
  let loadError = "";
  /** @type {import('$lib/types/api.js').FlowStepResponse[]} */
  let items = [];
  let total = 0;
  let mounted = false;

  // Anuncio para lectores de pantalla de cambios de estado (drag y acciones).
  // Se limpia sola tras ~1s: dos acciones iguales seguidas producirían el
  // mismo texto y el lector de pantalla podría no repetirlo (A11y).
  let liveMessage = "";
  let liveMessageTimer;

  // Referencias a los títulos de columna: destino estable del foco tras una
  // acción que destruye la tarjeta enfocada (A12, revisión UX).
  let pendientesHeadingEl;
  let enTramiteHeadingEl;
  let otrosHeadingEl;
  let boardEl;

  // -------------------------------------------------------------------------
  // Modales
  // -------------------------------------------------------------------------
  let modalTrasladar = false;
  let modalTramite = false;
  let modalRevertir = false;
  /** @type {import('$lib/types/api.js').FlowStepResponse | null} */
  let activeStep = null;
  let busyStepId = "";

  onMount(() => {
    mounted = true;
    return () => clearTimeout(liveMessageTimer);
  });

  $: if (mounted) loadBoard(box);

  async function loadBoard(currentBox) {
    loading = true;
    loadError = "";
    try {
      const params = new URLSearchParams({ page: "1", size: String(PAGE_SIZE) });
      if (source === "inbox" && currentBox) params.set("box", currentBox);
      const res = await fetch(`${ENDPOINT[source]}?${params}`);
      const result = await res.json();
      if (!res.ok) throw result;
      items = result.items ?? [];
      total = result.total ?? items.length;
    } catch (err) {
      items = [];
      loadError = mensajeError(err, "No se pudo cargar el tablero de distribución.");
    } finally {
      loading = false;
    }
  }

  $: pendientes = items.filter((s) => s.status === "pending");
  $: enTramite = items.filter((s) => s.status === "in_progress");
  // Defensivo: /inbox y /pending solo devuelven pending|in_progress hoy, pero si
  // el contrato cambiara no queremos que las tarjetas desaparezcan en silencio.
  $: otros = items.filter((s) => s.status !== "pending" && s.status !== "in_progress");

  // A4 (revisión UX): esta vista no pagina — si el backend tiene más pasos
  // que los que trajo la página, hay que avisarlo en vez de fingir que el
  // tablero está completo con un contador que ya no coincide con las tarjetas.
  $: truncated = total > items.length;
  $: currentBoxLabel = BOX_OPTIONS.find((o) => o.value === box)?.label ?? "";

  function announce(msg) {
    liveMessage = msg;
    clearTimeout(liveMessageTimer);
    liveMessageTimer = setTimeout(() => {
      liveMessage = "";
    }, 1000);
  }

  async function refresh() {
    await loadBoard(box);
  }

  /**
   * Refresca el tablero y devuelve el foco a un destino ESTABLE (A12): el
   * título de la columna donde terminó el radicado tras la acción, o el de
   * "Pendientes" si ya no aparece en ninguna columna visible. Nunca se deja
   * el foco caer al principio del documento.
   * @param {string} [radicadoId]
   */
  async function refreshAndFocus(radicadoId) {
    await refresh();
    await tick();
    let target = null;
    if (radicadoId) {
      if (pendientes.some((s) => s.radicado_id === radicadoId)) target = pendientesHeadingEl;
      else if (enTramite.some((s) => s.radicado_id === radicadoId)) target = enTramiteHeadingEl;
      else if (otros.some((s) => s.radicado_id === radicadoId)) target = otrosHeadingEl;
    }
    (target ?? pendientesHeadingEl ?? boardEl)?.focus();
  }

  // -------------------------------------------------------------------------
  // Abrir modales
  // -------------------------------------------------------------------------
  function openTrasladar(step) {
    activeStep = step;
    modalTrasladar = true;
  }
  function openTramite(step) {
    activeStep = step;
    modalTramite = true;
  }
  function openRevertir(step) {
    activeStep = step;
    modalRevertir = true;
  }

  /** A3 (revisión UX): la única acción de consulta de la tarjeta — el padre
   * (bandeja/+page.svelte) resuelve la apertura del cajón de detalle. */
  function verDetalle(step) {
    dispatch("verDetalle", step);
  }

  async function onModalSuccess() {
    const radicadoId = activeStep?.radicado_id;
    modalTrasladar = false;
    modalTramite = false;
    modalRevertir = false;
    activeStep = null;
    await refreshAndFocus(radicadoId);
  }

  // -------------------------------------------------------------------------
  // Completar (sin modal — FlowStepComplete solo acepta `notes` opcional)
  // -------------------------------------------------------------------------
  async function completar(step) {
    if (busyStepId) return;
    busyStepId = step.id;
    try {
      const res = await fetch(`/bandeja/api/steps/${encodeURIComponent(step.id)}/complete`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Paso completado para ${step.tracking_number}.`);
      announce(`Radicado ${step.tracking_number} marcado como completado.`);
      await refreshAndFocus(step.radicado_id);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo completar el paso."));
    } finally {
      busyStepId = "";
    }
  }

  // -------------------------------------------------------------------------
  // Marcar en trámite (transacción de catálogo `marcar_leido`) — disponible
  // por botón Y por arrastre a la columna "En trámite" (misma función).
  // -------------------------------------------------------------------------
  async function marcarEnTramite(step) {
    if (busyStepId) return;
    busyStepId = step.id;
    try {
      const res = await fetch(`/bandeja/api/${encodeURIComponent(step.radicado_id)}/transactions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tipo_tx: "marcar_leido" }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`${step.tracking_number} pasó a "En trámite".`);
      announce(`Radicado ${step.tracking_number} movido a En trámite.`);
      await refreshAndFocus(step.radicado_id);
    } catch (err) {
      if (err?.status === 403) {
        notifyError("No tiene permiso para marcar este radicado en trámite.");
      } else {
        notifyError(mensajeError(err, "No se pudo mover el radicado a En trámite."));
      }
    } finally {
      busyStepId = "";
    }
  }

  // -------------------------------------------------------------------------
  // Arrastre (mejora — ver cabecera). El equivalente de teclado es el botón
  // "Marcar en trámite" de cada tarjeta en Pendientes (misma función).
  // -------------------------------------------------------------------------
  /** @type {string} */
  let draggingId = "";

  /** @param {DragEvent} e @param {any} step */
  function onDragStart(e, step) {
    if (step.status !== "pending") return;
    draggingId = step.id;
    e.dataTransfer?.setData("text/plain", step.id);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
  }
  function onDragEnd() {
    draggingId = "";
  }
  /** @param {DragEvent} e */
  function onDropEnTramite(e) {
    e.preventDefault();
    const step = pendientes.find((s) => s.id === draggingId);
    draggingId = "";
    if (step) marcarEnTramite(step);
  }
  /** @param {DragEvent} e */
  function allowDrop(e) {
    if (draggingId) e.preventDefault();
  }
</script>

<div class="wf-board" bind:this={boardEl} tabindex="-1">
  <!-- Región viva discreta: anuncia cambios de estado (drag y acciones) -->
  <span class="sr-only" role="status" aria-live="polite" aria-atomic="true">{liveMessage}</span>

  <header class="wf-board__toolbar">
    {#if source === "inbox"}
      <nav class="wf-board__box-tabs" aria-label="Filtrar por tipo de radicado">
        {#each BOX_OPTIONS as opt (opt.value)}
          <button
            type="button"
            class="wf-board__box-tab"
            class:wf-board__box-tab--active={box === opt.value}
            aria-pressed={box === opt.value}
            on:click={() => (box = opt.value)}
          >
            {opt.label}
          </button>
        {/each}
      </nav>
    {/if}
    <span class="wf-board__total" aria-live="polite">
      {#if !loading}{total.toLocaleString("es-CO")} paso{total !== 1 ? "s" : ""}{/if}
    </span>
  </header>

  {#if truncated && !loading && !loadError}
    <!-- A4 (revisión UX): /inbox|/pending se piden con size=100 — si el backend
      tiene más pasos que los que trajo esta página, se avisa en vez de fingir
      que el tablero está completo (no se agrega paginación real). -->
    <p class="wf-board__truncation-notice" role="status">
      <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
      Mostrando los primeros {items.length.toLocaleString("es-CO")} de {total.toLocaleString(
        "es-CO",
      )} pasos{source === "inbox"
        ? "; filtre por tipo para ver el resto."
        : " — no hay más filtros disponibles en esta vista."}
    </p>
  {/if}

  {#if loading}
    <Loader type="skeleton" lines={6} label="Cargando tablero de distribución…" />
  {:else if loadError}
    <EmptyState
      icon="fas fa-exclamation-circle"
      title="Error al cargar el tablero"
      description={loadError}
    >
      <Button variant="primary" on:click={refresh}>Reintentar</Button>
    </EmptyState>
  {:else if items.length === 0}
    {#if source === "inbox" && box}
      <!-- A5 (revisión UX): el filtro por tipo NO es toda la bandeja — el vacío
        solo puede afirmar lo que ESTE filtro devolvió, nunca "no hay nada aquí". -->
      <EmptyState
        icon="fas fa-route"
        title="Sin radicados con este filtro"
        description={`Ningún radicado de tipo ${currentBoxLabel || box} en esta bandeja.`}
      >
        <Button variant="ghost" on:click={() => (box = "")}>Ver todos</Button>
      </EmptyState>
    {:else}
      <EmptyState
        icon="fas fa-route"
        title="Sin pasos activos"
        description={source === "pending"
          ? "No hay pasos pendientes visibles para tu nivel de acceso."
          : "No hay radicados asignados en esta bandeja."}
      />
    {/if}
  {:else}
    <div class="wf-board__columns">
      <!-- Columna: Pendientes -->
      <section class="wf-board__column" aria-labelledby="wf-col-pendientes">
        <h2
          id="wf-col-pendientes"
          class="wf-board__column-title"
          bind:this={pendientesHeadingEl}
          tabindex="-1"
        >
          Pendientes <span class="wf-board__column-count">({pendientes.length})</span>
        </h2>
        {#if pendientes.length === 0}
          <p class="wf-board__column-empty">Sin radicados pendientes.</p>
        {:else}
          <ul class="wf-board__cards" aria-label="Radicados pendientes">
            {#each pendientes as step (step.id)}
              <li
                class="wf-card"
                class:wf-card--dragging={draggingId === step.id}
                draggable="true"
                on:dragstart={(e) => onDragStart(e, step)}
                on:dragend={onDragEnd}
              >
                <WorkflowCard
                  {step}
                  {canTramitar}
                  busy={busyStepId === step.id}
                  boardBusy={!!busyStepId && busyStepId !== step.id}
                  on:verDetalle={() => verDetalle(step)}
                  on:trasladar={() => openTrasladar(step)}
                  on:tramite={() => openTramite(step)}
                  on:completar={() => completar(step)}
                  on:revertir={() => openRevertir(step)}
                  on:enTramite={() => marcarEnTramite(step)}
                />
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <!-- Columna: En trámite (también zona de destino del arrastre) -->
      <section
        class="wf-board__column"
        class:wf-board__column--dropzone={!!draggingId}
        aria-labelledby="wf-col-en-tramite"
        on:dragover={allowDrop}
        on:drop={onDropEnTramite}
      >
        <h2
          id="wf-col-en-tramite"
          class="wf-board__column-title"
          bind:this={enTramiteHeadingEl}
          tabindex="-1"
        >
          En trámite <span class="wf-board__column-count">({enTramite.length})</span>
        </h2>
        {#if enTramite.length === 0}
          <p class="wf-board__column-empty">
            Sin radicados en trámite.
            {#if pendientes.length > 0}
              <!-- El arrastre HTML5 no existe en dispositivos táctiles (no hay
                puntero fino) — el hint solo tiene sentido con mouse/trackpad.
                El botón "Iniciar trámite" de WorkflowCard es la vía universal
                y ya funciona igual en ambos casos. -->
              <span class="wf-board__drag-hint"
                >Suelte aquí una tarjeta de Pendientes para avanzarla.</span
              >
            {/if}
          </p>
        {:else}
          <ul class="wf-board__cards" aria-label="Radicados en trámite">
            {#each enTramite as step (step.id)}
              <li class="wf-card">
                <WorkflowCard
                  {step}
                  {canTramitar}
                  busy={busyStepId === step.id}
                  boardBusy={!!busyStepId && busyStepId !== step.id}
                  on:verDetalle={() => verDetalle(step)}
                  on:trasladar={() => openTrasladar(step)}
                  on:tramite={() => openTramite(step)}
                  on:completar={() => completar(step)}
                  on:revertir={() => openRevertir(step)}
                />
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      {#if otros.length > 0}
        <section class="wf-board__column" aria-labelledby="wf-col-otros">
          <h2
            id="wf-col-otros"
            class="wf-board__column-title"
            bind:this={otrosHeadingEl}
            tabindex="-1"
          >
            Otros <span class="wf-board__column-count">({otros.length})</span>
          </h2>
          <ul class="wf-board__cards" aria-label="Otros radicados">
            {#each otros as step (step.id)}
              <li class="wf-card">
                <WorkflowCard
                  {step}
                  {canTramitar}
                  busy={busyStepId === step.id}
                  boardBusy={!!busyStepId && busyStepId !== step.id}
                  on:verDetalle={() => verDetalle(step)}
                  on:trasladar={() => openTrasladar(step)}
                  on:tramite={() => openTramite(step)}
                  on:completar={() => completar(step)}
                  on:revertir={() => openRevertir(step)}
                />
              </li>
            {/each}
          </ul>
        </section>
      {/if}
    </div>
  {/if}
</div>

<TrasladarModal
  open={modalTrasladar}
  step={activeStep}
  {dependencias}
  on:close={() => (modalTrasladar = false)}
  on:success={onModalSuccess}
/>
<TransaccionModal
  open={modalTramite}
  step={activeStep}
  on:close={() => (modalTramite = false)}
  on:success={onModalSuccess}
/>
<RevertirModal
  open={modalRevertir}
  step={activeStep}
  on:close={() => (modalRevertir = false)}
  on:success={onModalSuccess}
/>

<style lang="scss">
  .wf-board {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &:focus-visible {
      outline: none;
    }

    &__truncation-notice {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      margin: 0;
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
    }

    &__toolbar {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      flex-wrap: wrap;
    }

    &__box-tabs {
      display: flex;
      gap: var(--op-space-1);
      background: var(--op-bg);
      border-radius: var(--op-radius);
      padding: 2px;
    }

    &__box-tab {
      font-family: var(--op-font-family);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      background: transparent;
      border: none;
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-1) var(--op-space-3);
      cursor: pointer;

      &:hover {
        color: var(--op-text);
      }
      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
      &--active {
        background: var(--op-surface);
        color: var(--op-primary-dark);
        box-shadow: var(--op-shadow-sm);
      }
    }

    &__total {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

    &__columns {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: var(--op-space-4);
      align-items: start;

      @media (max-width: 720px) {
        grid-template-columns: 1fr;
      }
    }

    &__column {
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-lg);
      padding: var(--op-space-3);
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      min-height: 120px;
      transition:
        background var(--op-transition),
        border-color var(--op-transition);

      &--dropzone {
        border-color: var(--op-primary);
        background: var(--op-primary-light);

        // Menor (revisión UX): `--op-text-secondary` (#6e7370) sobre
        // `--op-primary-light` (#dceed7) da 3.97:1 — bajo AA (4.5:1 para
        // texto normal). Verificado con la fórmula de contraste relativo
        // WCAG; `--op-text` (#212421) sobre el mismo fondo da 12.89:1.
        // Solo se sube en ESTE estado (el fondo normal `--op-bg` sí cumple
        // con `--op-text-secondary`).
        .wf-board__column-empty {
          color: var(--op-text);
        }
      }
    }

    &__column-title {
      margin: 0;
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      text-transform: uppercase;
      letter-spacing: 0.02em;

      // Destino de foco tras refrescar el tablero (A12) — solo visible cuando
      // llega por teclado/programáticamente, nunca al hacer clic con mouse.
      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        border-radius: var(--op-radius-sm);
      }
    }

    &__column-count {
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-medium);
    }

    &__column-empty {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    // El hint de arrastre solo aplica con puntero fino (mouse/trackpad): en
    // táctil el arrastre HTML5 no funciona y el hint pediría algo imposible.
    &__drag-hint {
      @media (pointer: coarse) {
        display: none;
      }
    }

    &__cards {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }
  }

  .wf-card {
    &--dragging {
      opacity: 0.5;
    }
  }
</style>
